#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP 层：SSL 上下文、请求封装、UA、下载校验。

从 imagifly-batch 的 imagifly.py 抽取，行为保持一致：
- 优先 certifi 校验，证书缺失时回退不校验（macOS 常见问题）
- 自定义 UA（Cloudflare 1010 风控需要完整浏览器 UA，勿用 python-urllib 裸 UA）
- 下载内容魔数校验，防止把 CDN 错误页存成 .png/.mp4
"""
import ssl
import urllib.request
import urllib.error

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")


def _make_ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    ctx = ssl.create_default_context()
    try:
        ctx.load_default_certs()
        return ctx
    except Exception:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


_SSL_CTX = _make_ssl_context()


def _open(req, timeout):
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)
    except TypeError:
        return urllib.request.urlopen(req, timeout=timeout)


def http_request(method, url, headers=None, body=None, timeout=60):
    """返回 (status, raw_bytes)。网络异常抛出。"""
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with _open(req, timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read()
        except Exception:
            return e.code, b""


def http_json(method, url, headers=None, body=None, timeout=60):
    """返回 (status, dict_or_None)。"""
    import json as _json
    status, raw = http_request(method, url, headers, body, timeout)
    if not raw:
        return status, None
    try:
        return status, _json.loads(raw.decode("utf-8"))
    except Exception:
        return status, None


# 下载文件头魔数：写盘前校验，防止把 CDN 错误页/HTML 存成 .png/.mp4
FILE_SIGNATURES = {
    "image": (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF"),
    "video": (b"\x00\x00\x00 ftyp", b"\x1aE\xdf\xa3"),
}


def verify_download(raw, kind):
    """校验下载内容魔数。返回错误描述或 None。"""
    if not raw or len(raw) < 16:
        return f"内容过短（{len(raw) if raw else 0} 字节）"
    head = raw[:16]
    for sig in FILE_SIGNATURES.get(kind, ()):
        if head.startswith(sig) or (sig[4:8] == b"ftyp" and head[4:8] == b"ftyp"):
            return None
    return f"非 {kind} 内容（前 16 字节：{head[:8]!r}）"
