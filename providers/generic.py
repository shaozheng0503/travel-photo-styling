#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generic provider：不写代码、纯配置接入任意生图 API。

适用：自建服务、中转站、或任何「POST 提交 → 拿 URL/轮询 → 下载」模式的 API。
模板占位符（config/providers.json 的 templates 段）：
  {prompt} {model} {ratio} {batch} {images_json} {id} {api_key} {nonce} {timestamp}

三种响应模式（response.mode）：
- sync_url     同步返回图 URL（最常见：直接返回 JSON 里带 url 字段）
- sync_b64     同步返回 base64
- poll_task    异步任务：提交返回 task_id，轮询另一端点直到完成

配置示例见 config/providers.json 的 "myapi" 段（自带一个可抄的模板）。
"""
import base64
import datetime
import json
import os
import random
import time
import urllib.parse

from core.http import UA, http_json, http_request, verify_download
from core.utils import slugify
from providers.base import ImageProvider, ProviderError


class GenericProvider(ImageProvider):
    name = "generic"

    def __init__(self, config=None):
        super().__init__(config)
        if not config:
            raise ProviderError("generic provider 需要 config/providers.json 配置")
        self.cfg = config
        self.models = self.cfg.get("models") or {}

    # ---- 模板渲染 ----
    def _render(self, template, ctx):
        out = template
        for k, v in ctx.items():
            out = out.replace("{" + k + "}", str(v))
        return out

    def _headers(self, spec, ctx):
        headers = {"User-Agent": UA}
        for k, v in (spec or {}).items():
            headers[k] = self._render(v, ctx)
        return headers

    # ---- 元信息 ----
    def list_models(self):
        return self.models

    def cost_of(self, model, job_overrides):
        return self.models.get(model, {}).get("cost")

    def supports_image_input(self, model):
        return self.models.get(model, {}).get("ref", 0) > 0

    # ---- 核心三步 ----
    def submit(self, job, model, ratio, batch_size, style):
        t = self.cfg.get("templates", {})
        sub = t.get("submit", {})
        ctx = {
            "prompt": job["prompt"].replace('"', '\\"'),
            "model": model, "ratio": ratio, "batch": batch_size,
            "style": style or "", "api_key": self.cfg.get("api_key", ""),
            "nonce": random.randint(100000, 999999),
            "timestamp": int(time.time()),
            "images_json": json.dumps(job["overrides"].get("images") or []),
        }
        method = (sub.get("method") or "POST").upper()
        url = self._render(sub.get("url", ""), ctx)
        headers = self._headers(sub.get("headers"), ctx)
        body = None
        if method in ("POST", "PUT"):
            if sub.get("body_json"):
                # body_json 是 dict：渲染每个叶子值
                body = json.dumps(self._render_tree(sub["body_json"], ctx)).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            elif sub.get("body_form"):
                body = urllib.parse.urlencode(
                    {k: self._render(v, ctx) for k, v in sub["body_form"].items()}).encode("utf-8")
                headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        timeout = self.cfg.get("request_timeout_seconds", 300)
        status, data = http_json(method, url, headers, body, timeout)
        resp = self.cfg.get("response", {})
        ok_codes = resp.get("ok_codes", [200, 201, 202])
        if status not in ok_codes or data is None:
            raise ProviderError(f"提交失败 HTTP {status}: {str(data)[:300]}")
        # 提取任务 id（poll_task 模式）或直接标记同步完成
        task_key = resp.get("task_id_key", "task_id")
        if resp.get("mode") == "poll_task":
            task_id = self._dig(data, task_key)
            if not task_id:
                raise ProviderError(f"响应中未找到 {task_key}: {str(data)[:200]}")
            return {"id": task_id, "_mode": "poll_task"}
        # 同步模式：整个响应即结果，交给 poll() 提取 URL
        return {"id": "sync", "_sync_data": data}

    def _render_tree(self, node, ctx):
        if isinstance(node, dict):
            return {k: self._render_tree(v, ctx) for k, v in node.items()}
        if isinstance(node, list):
            return [self._render_tree(v, ctx) for v in node]
        if isinstance(node, str):
            return self._render(node, ctx)
        return node

    @staticmethod
    def _dig(data, key_path):
        """按 a.b.0.c 路径从嵌套 dict/list 里取值（数字段为列表索引）。"""
        cur = data
        for k in key_path.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            elif isinstance(cur, list) and k.isdigit() and int(k) < len(cur):
                cur = cur[int(k)]
            else:
                return None
        return cur

    def poll(self, handle, job=None, timeout=600, interval=2):
        resp = self.cfg.get("response", {})
        if resp.get("mode") != "poll_task":
            # 同步模式：handle 里的 _sync_data 即结果
            data = handle.get("_sync_data") or {}
            url = self._dig(data, resp.get("url_key", "data.0.url"))
            if url:
                return "success", [url]
            b64 = self._dig(data, resp.get("b64_key", "data.0.b64_json"))
            if b64:
                return "success", ["data:image/png;base64," + b64]
            return "failed", []
        # 异步轮询模式
        t = self.cfg.get("templates", {}).get("poll", {})
        if not t.get("url"):
            return "failed", []
        ctx = {"id": handle["id"], "api_key": self.cfg.get("api_key", ""),
               "timestamp": int(time.time())}
        start = time.time()
        errors = 0
        while time.time() - start < timeout:
            try:
                method = (t.get("method") or "GET").upper()
                url = self._render(t["url"], ctx)
                headers = self._headers(t.get("headers"), ctx)
                status, data = http_json(method, url, headers, None, 30)
            except Exception:
                errors += 1
                if errors >= 30:
                    return "failed", []
                time.sleep(interval)
                continue
            if status != 200 or data is None:
                errors += 1
                if errors >= 30:
                    return "failed", []
                time.sleep(interval)
                continue
            st = self._dig(data, resp.get("status_key", "status"))
            if st in (resp.get("done_value", "success"), "SUCCEEDED", "succeeded"):
                url = self._dig(data, resp.get("url_key", "output.0.url"))
                if url:
                    return "success", [url]
                b64 = self._dig(data, resp.get("b64_key", "output.0.b64"))
                if b64:
                    return "success", ["data:image/png;base64," + b64]
                return "failed", []
            if st in ("failed", "FAILED", "error", "ERROR"):
                return "failed", []
            time.sleep(interval)
        return "timeout", []

    def download(self, asset, dest_dir, prompt, idx, kind="image"):
        os.makedirs(dest_dir, exist_ok=True)
        if asset.startswith("data:"):
            raw = base64.b64decode(asset.split(",", 1)[1])
        else:
            status, raw = http_request("GET", asset, {"User-Agent": UA}, timeout=120)
            if status != 200 or not raw:
                raise ProviderError(f"下载失败 HTTP {status}")
        err = verify_download(raw, "image")
        if err:
            raise ProviderError(f"下载内容校验失败：{err}")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"{ts}_{slugify(prompt)}_{idx}_{random.randint(100, 999)}.png"
        with open(os.path.join(dest_dir, fname), "wb") as f:
            f.write(raw)
        return fname

    def check(self):
        try:
            t = self.cfg.get("templates", {}).get("submit", {})
            if not t.get("url"):
                return False, "缺少 templates.submit.url 配置"
            return True, f"配置就绪：{t['url'][:60]}"
        except Exception as e:
            return False, str(e)


def register(registry):
    registry["generic"] = GenericProvider
