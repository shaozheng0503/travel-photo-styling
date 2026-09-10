#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI 兼容 provider：一次接入，覆盖所有 OpenAI 兼容生图 API。

适用服务（均走 /v1/images/generations 或 /v1/images/edits）：
- OpenAI 官方（gpt-image-1 / dall-e-3）
- 通义千问 DashScope 兼容模式（wanx / qwen-image）
- SiliconFlow、云雾 API、one-api / new-api 中转站等

配置（config/providers.json）：
{
  "openai_compat": {
    "api_key": "sk-xxx",
    "base_url": "https://api.openai.com/v1",
    "models": {"gpt-image-1": {"cost": null}}
  }
}
"""
import json
import os
import urllib.parse

from core.http import UA, http_json, http_request, verify_download
from core.utils import slugify
from providers.base import ImageProvider, ProviderError


class OpenAICompatProvider(ImageProvider):
    name = "openai_compat"

    def __init__(self, config=None):
        super().__init__(config)
        self.api_key = (self.config or {}).get("api_key") or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = ((self.config or {}).get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.models = (self.config or {}).get("models") or {"gpt-image-1": {"ref": 1, "cost": None}}

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.api_key}", "User-Agent": UA}

    def list_models(self):
        return self.models

    def cost_of(self, model, job_overrides):
        return self.models.get(model, {}).get("cost")

    def supports_image_input(self, model):
        return self.models.get(model, {}).get("ref", 0) > 0

    def submit(self, job, model, ratio, batch_size, style):
        """OpenAI images API 是同步接口：submit 即完成生成，直接返回句柄。"""
        imgs = job["overrides"].get("images") or []
        payload = {"model": model, "prompt": job["prompt"], "n": batch_size}
        if ratio:
            payload["size"] = self._ratio_to_size(ratio)
        path = "/images/edits" if imgs else "/images/generations"
        if imgs:
            # edits 走 multipart（与 imagifly provider 复用编码器）
            from providers.imagifly import ImagiflyProvider
            fields = {"model": model, "prompt": job["prompt"], "n": str(batch_size)}
            raw, fname, mime, w, h = ImagiflyProvider.read_image_source(imgs[0])
            boundary, body = ImagiflyProvider.build_form(
                fields, [("image", fname, mime, raw)])
            headers = self.auth_headers()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            status, data = http_json("POST", self.base_url + path, headers, body,
                                     self.config.get("request_timeout_seconds", 300))
        else:
            headers = dict(self.auth_headers(), **{"Content-Type": "application/json"})
            status, data = http_json("POST", self.base_url + path, headers,
                                     json.dumps(payload).encode("utf-8"),
                                     self.config.get("request_timeout_seconds", 300))
        if status not in (200, 201) or not isinstance(data, dict):
            raise ProviderError(f"提交失败 HTTP {status}: {str(data)[:300]}")
        return data

    def poll(self, handle, job=None, timeout=600, interval=2):
        """同步接口：handle 即结果。status 字段兼容 data[0].revision。"""
        items = handle.get("data") or []
        urls = []
        for it in items:
            if it.get("url"):
                urls.append(it["url"])
            elif it.get("b64_json"):
                urls.append("data:image/png;base64," + it["b64_json"])
        if urls:
            return "success", urls
        return "failed", []

    def download(self, asset, dest_dir, prompt, idx, kind="image"):
        import datetime, random
        os.makedirs(dest_dir, exist_ok=True)
        if asset.startswith("data:"):
            import base64
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

    @staticmethod
    def _ratio_to_size(ratio):
        m = {"1:1": "1024x1024", "3:4": "1024x1536", "4:3": "1536x1024",
             "9:16": "1024x1792", "16:9": "1792x1024", "4:5": "1024x1280",
             "2:3": "1024x1536", "3:2": "1536x1024"}
        return m.get(ratio, "1024x1024")

    def check(self):
        if not self.api_key:
            return False, "未配置 api_key（config/providers.json 或 OPENAI_API_KEY）"
        try:
            status, data = http_json("GET", self.base_url + "/models",
                                     self.auth_headers(), timeout=20)
            if status == 200:
                n = len((data or {}).get("data", [])) if isinstance(data, dict) else "?"
                return True, f"API 可用（{n} 个模型）"
            return False, f"HTTP {status}"
        except Exception as e:
            return False, f"网络异常：{e}"


def register(registry):
    registry["openai_compat"] = OpenAICompatProvider
