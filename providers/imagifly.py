#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Imagifly provider：从 imagifly-batch 实战脚本迁移，行为保持一致。

接口（2026-09 实测有效）：
- 生图：POST /api/images/generate（multipart：prompt/model/size/ratio/style/
  responseFormat/submissionId/requestIndex/requestCount/batchId + referenceImages）
- 轮询：GET  /api/generations/{id} → status=success 取 assets[0].url
- 下载：assets url（cache.imagifly.net 需带 cookie）
- 幂等键：header Idempotency-Key: "{submissionId}:{requestIndex}"（2026-08 起强制）
- 鉴权：仅 Cookie（imagifly_session）
"""
import json
import mimetypes
import os
import struct
import time
import uuid

from core.http import UA, http_json, http_request, verify_download
from core.utils import ext_for, slugify
from providers.base import ImageProvider, ProviderError

BASE = "https://imagifly.net"
MAX_IMAGE_BYTES = 10 * 1024 * 1024

# 离线模型注册表（--list-models 会拉取在线最新；cost 为积分，最终以平台扣费为准）
IMAGE_MODELS = {
    "gpt-image-2":   {"ref": 3, "cost": 3, "tags": "文/图生图·中文"},
    "Wai-SDXL":      {"ref": 0, "cost": 1, "tags": "动漫风格"},
    "Qwen-Image-Edit": {"ref": 3, "cost": 2, "tags": "图生图/中文"},
    "Z-Image-Turbo": {"ref": 0, "cost": 2, "tags": "中文/快"},
    "nano-banana-2": {"ref": 3, "cost": 7, "tags": "文/图生图·高端"},
    "grok-imagine-image-quality": {"ref": 3, "cost": 2, "tags": "文/图生图"},
}


class ImagiflyProvider(ImageProvider):
    name = "imagifly"

    def __init__(self, config=None):
        super().__init__(config)
        self.cookie = self._load_cookie()

    def _load_cookie(self):
        """cookie 来源优先级：providers.json 的 env_cookie > config/cookie.txt"""
        c = (self.config or {}).get("cookie")
        if c:
            return c.strip()
        # 相对仓库根解析
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "config", "cookie.txt")
        if os.path.exists(path):
            return open(path, encoding="utf-8").read().strip()
        return ""

    def auth_headers(self):
        h = {"User-Agent": UA, "Referer": f"{BASE}/create", "Origin": BASE}
        if self.cookie:
            h["Cookie"] = self.cookie
        return h

    # ---- 元信息 ----
    def list_models(self):
        status, data = http_json("GET", BASE + "/api/images/models",
                                 {"User-Agent": UA}, timeout=20)
        if status == 200 and isinstance(data, dict) and data.get("models"):
            return {m.get("id") or m.get("name"): {"ref": 3, "cost": None, "tags": m.get("description", "")}
                    for m in data["models"] if m.get("id") or m.get("name")}
        return IMAGE_MODELS

    def cost_of(self, model, job_overrides):
        return IMAGE_MODELS.get(model, {}).get("cost")

    def supports_image_input(self, model):
        return IMAGE_MODELS.get(model, {}).get("ref", 0) > 0

    # ---- multipart 参考图上传 ----
    _REF_CACHE = {}  # src -> (bytes, filename, mime, w, h)，batch 场景避免重复读盘/下载

    @staticmethod
    def _image_dimensions(data):
        """读 PNG/JPEG/GIF/WEBP 宽高。失败返回 (1024, 1024)（原版行为）。"""
        try:
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", data[16:24])
                return w, h
            if data[:2] == b"\xff\xd8":
                i = 2
                while i < len(data) - 9:
                    if data[i] != 0xFF:
                        i += 1
                        continue
                    marker = data[i + 1]
                    if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                        h, w = struct.unpack(">HH", data[i + 5:i + 9])
                        return w, h
                    seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
                    i += 2 + seg_len
            if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                chunk = data[12:16]
                if chunk == b"VP8 ":
                    w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
                    h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
                    return w, h
                if chunk == b"VP8L":
                    b0, b1, b2, b3 = data[21], data[22], data[23], data[24]
                    w = 1 + ((b1 & 0x3F) << 8 | b0)
                    h = 1 + ((b3 & 0x0F) << 10 | b2 << 2 | (b1 & 0xC0) >> 6)
                    return w, h
                if chunk == b"VP8X":
                    w = 1 + int.from_bytes(data[24:27], "little")
                    h = 1 + int.from_bytes(data[27:30], "little")
                    return w, h
        except Exception:
            pass
        return 1024, 1024

    @classmethod
    def read_image_source(cls, src):
        """返回 (bytes, filename, mime, w, h)。src 为本地路径或 http(s) URL。结果缓存。"""
        if src in cls._REF_CACHE:
            return cls._REF_CACHE[src]
        if src.lower().startswith("http"):
            status, raw = http_request("GET", src, {"User-Agent": UA}, timeout=60)
            if status != 200 or not raw:
                raise ProviderError(f"参考图下载失败 HTTP {status}: {src}")
            name = src.split("/")[-1].split("?")[0] or "reference.jpg"
            mime = mimetypes.guess_type(name)[0] or "image/jpeg"
            result = (raw, name, mime, *cls._image_dimensions(raw))
        elif not os.path.exists(src):
            raise ProviderError(f"参考图不存在: {src}")
        else:
            raw = open(src, "rb").read()
            if len(raw) > MAX_IMAGE_BYTES:
                raise ProviderError(f"参考图过大（{len(raw)} > {MAX_IMAGE_BYTES}）: {src}")
            name = os.path.basename(src)
            mime = mimetypes.guess_type(name)[0] or "image/jpeg"
            result = (raw, name, mime, *cls._image_dimensions(raw))
        cls._REF_CACHE[src] = result
        return result

    @staticmethod
    def build_form(fields, files):
        """multipart/form-data 编码（零依赖手写）。files: [(field, filename, mime, bytes)]"""
        boundary = "----TPS" + uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            if v is None:
                continue
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
        for field, fname, mime, blob in files:
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{fname}\"\r\n"
                f"Content-Type: {mime}\r\n\r\n".encode("utf-8") + blob + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        return boundary, b"".join(parts)

    def _submit_payload(self, url, payload, imgs, timeout=60):
        """统一提交：带参考图走 multipart，否则 JSON；幂等键 header 为 "{sid}:{ridx}"。
        返回解包后的 generation dict。"""
        fields = dict(payload)
        files = []
        if imgs:
            meta = []
            for src in imgs:
                raw, fname, mime, w, h = self.read_image_source(src)
                files.append(("referenceImages", fname, mime, raw))
                meta.append({"width": w, "height": h})
            fields["referenceImageMetadata"] = json.dumps(meta)
            boundary, body = self.build_form(fields, files)
            ctype = f"multipart/form-data; boundary={boundary}"
        else:
            body = json.dumps(fields).encode("utf-8")
            ctype = "application/json"
        headers = self.auth_headers()
        headers.update({
            "Content-Type": ctype,
            "Idempotency-Key": f"{payload['submissionId']}:{payload['requestIndex']}",
        })
        status, data = http_json("POST", url, headers, body, timeout)
        if status not in (200, 202) or not isinstance(data, dict) or not isinstance(data.get("generation"), dict):
            err = data.get("error") if isinstance(data, dict) else None
            raise ProviderError(f"提交失败 HTTP {status}: {err or str(data)[:200]}")
        gen = data["generation"]
        if not gen.get("id"):
            raise ProviderError(f"返回的 generation 缺少 id：{json.dumps(gen, ensure_ascii=False)[:200]}")
        return gen

    # ---- 核心三步 ----
    def submit(self, job, model, ratio, batch_size, style):
        """单次提交一张（batch_size>1 由 main.py 外层循环）。返回 generation dict（已解包）。"""
        ov = job["overrides"]
        prompt = job["prompt"]
        if style:
            prompt = f"{prompt}, {style} style" if "style" not in prompt[:20] else prompt
        tier = (ov.get("tier") or self.config.get("resolution_tier", "1k")).lower()
        tier = "2k" if tier in ("high", "2k") else "1k"
        quality = "high" if tier == "2k" else "standard"
        imgs = ov.get("images") or []
        sid = str(uuid.uuid4())
        payload = {
            "prompt": prompt,
            "model": model,
            "n": 1,
            "size": tier,
            "resolutionTier": tier,
            "quality": quality,
            "ratio": ratio,
            "responseFormat": "b64_json",
            "submissionId": sid,
            "requestIndex": 1,
            "requestCount": max(1, batch_size),
            "batchId": str(uuid.uuid4()),
        }
        steps = ov.get("steps")
        if steps is not None:
            payload["steps"] = int(steps)
        neg = ov.get("neg")
        if neg:
            payload["negativePrompt"] = neg
        gen = self._submit_payload(BASE + "/api/images/generate", payload, imgs,
                                   self.config.get("request_timeout_seconds", 60))
        gen["_cost"] = self.cost_of(model, ov)
        return gen

    def poll(self, handle, job=None, timeout=600, interval=2):
        """轮询直到 success/failed/超时。返回 (status, [asset_url])。

        原版行为：status=success 时 assets[*].url 即下载地址（b64_json 模式下
        平台实际也返回 CDN url 字段；若无 url 则视为失败交由上层重试）。
        """
        gen_id = handle.get("id")
        errors = 0
        start = time.time()
        while time.time() - start < timeout:
            try:
                status, data = http_json("GET", f"{BASE}/api/generations/{gen_id}",
                                         self.auth_headers(), timeout=30)
            except Exception:
                errors += 1
                if errors >= 30:
                    return "failed", []
                time.sleep(interval)
                continue
            if status != 200:
                errors += 1
                if errors >= 30:
                    return "failed", []
                time.sleep(interval)
                continue
            errors = 0
            gen = data.get("generation") if isinstance(data, dict) else None
            if not isinstance(gen, dict):
                gen = {}
            st = gen.get("status")
            if st == "success":
                assets = gen.get("assets", [])
                urls = [a["url"] for a in assets if a.get("url")]
                if urls:
                    return "success", urls
                return "failed", []  # success 但无 url：异常态，交上层失败处理
            if st == "failed":
                return "failed", []
            time.sleep(interval)
        return "timeout", []

    def download(self, asset, dest_dir, prompt, idx, kind="image"):
        headers = {"User-Agent": UA}
        if self.cookie and BASE in (asset or ""):
            headers["Cookie"] = self.cookie
        status, raw = http_request("GET", asset, headers, timeout=120)
        if status != 200 or not raw:
            raise ProviderError(f"下载失败 HTTP {status}")
        err = verify_download(raw, kind)
        if err:
            raise ProviderError(f"下载内容校验失败：{err}")
        import datetime, random
        ext = ext_for(asset, "image/png" if kind == "image" else "video/mp4")
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        rid = random.randint(100, 999)
        fname = f"{ts}_{slugify(prompt)}_{idx}_{rid}{ext}"
        os.makedirs(dest_dir, exist_ok=True)
        with open(os.path.join(dest_dir, fname), "wb") as f:
            f.write(raw)
        return fname

    # ---- 自检 ----
    def check(self):
        if not self.cookie:
            return False, "未配置 Cookie（config/cookie.txt）"
        try:
            status, data = http_json("GET", BASE + "/api/auth/me", self.auth_headers(), timeout=20)
            if status == 200 and isinstance(data, dict):
                u = data.get("user")
                if u:
                    return True, f"登录有效：{u.get('email') or u.get('name') or '(已登录)'}"
        except Exception as e:
            return False, f"网络异常：{e}"
        return False, "Cookie 可能过期（/api/auth/me user 为空）"


def register(registry):
    registry["imagifly"] = ImagiflyProvider
