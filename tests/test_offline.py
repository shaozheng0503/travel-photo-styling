#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线单测：不调任何真实 API、不花积分。

跑法：python tests/test_offline.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Windows 控制台默认 GBK/cp1252，打印中文/✓ 符号会 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from core.queue import parse_prompt_line, load_queue
from core.utils import single_line_prompt
from providers import REGISTRY, build_provider, load_providers_config
from providers.imagifly import ImagiflyProvider
from providers.generic import GenericProvider

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def test_queue_parse():
    print("[queue] 行解析")
    job = parse_prompt_line("一只猫 | model=gpt-image-2 | 3:4 | x2 | img=a.png,b.png | provider=imagifly | style=anime | neg=blurry | steps=20 | tier=1k")
    ov = job["overrides"]
    check("model", ov["model"] == "gpt-image-2")
    check("ratio", ov["ratio"] == "3:4")
    check("batch", ov["batch"] == 2)
    check("images", ov["images"] == ["a.png", "b.png"])
    check("provider", ov["provider"] == "imagifly")
    check("style", ov["style"] == "anime")
    check("neg", ov["neg"] == "blurry")
    check("steps", ov["steps"] == 20)
    check("tier", ov["tier"] == "1k")
    check("prompt 保留", job["prompt"] == "一只猫")

    job2 = parse_prompt_line("纯提示词没有参数")
    check("无参数行", job2["overrides"] == {})


def test_provider_registry():
    print("[providers] 注册表")
    check("内置三个", set(["imagifly", "openai_compat", "generic"]) <= set(REGISTRY.keys()))
    conf = load_providers_config()
    check("_readme 被忽略", "_readme" not in conf)
    check("myapi 段存在", "myapi" in conf)
    p = build_provider("myapi")
    check("myapi → generic 实例", isinstance(p, GenericProvider))


def test_generic_sync():
    print("[generic] sync_url 模式")
    cfg = {
        "type": "generic", "api_key": "sk-test",
        "models": {"m1": {"ref": 1, "cost": 2}},
        "templates": {"submit": {
            "method": "POST", "url": "https://x.example.com/gen?p={prompt}&k={api_key}",
            "headers": {"Authorization": "Bearer {api_key}"},
            "body_json": {"model": "{model}", "prompt": "{prompt}", "n": "{batch}"},
        }},
        "response": {"mode": "sync_url", "url_key": "data.0.url"},
    }
    p = GenericProvider(cfg)
    check("cost", p.cost_of("m1", {}) == 2)
    check("supports ref", p.supports_image_input("m1"))
    # mock http_json
    import providers.generic as g
    orig = g.http_json
    captured = {}
    def mock_hj(method, url, headers, body, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = body
        return 200, {"data": [{"url": "https://cdn.example.com/abc.png"}]}
    g.http_json = mock_hj
    try:
        job = {"prompt": "a cat", "overrides": {}}
        handle = p.submit(job, "m1", "3:4", 1, None)
        check("模板渲染 URL", "p=a%20cat" in captured["url"] or "a cat" in captured["url"], captured["url"])
        check("header 渲染", captured["headers"]["Authorization"] == "Bearer sk-test")
        body = json.loads(captured["body"])
        check("body_json 渲染", body["model"] == "m1" and body["n"] == "1")
        st, urls = p.poll(handle, job=job)
        check("sync 提取 url", st == "success" and urls == ["https://cdn.example.com/abc.png"])
    finally:
        g.http_json = orig


def test_generic_poll_task():
    print("[generic] poll_task 模式")
    cfg = {
        "type": "generic",
        "templates": {
            "submit": {"method": "POST", "url": "https://x.example.com/submit",
                       "body_json": {"prompt": "{prompt}"}},
            "poll": {"method": "GET", "url": "https://x.example.com/task/{id}"},
        },
        "response": {"mode": "poll_task", "task_id_key": "data.taskId",
                     "status_key": "data.state", "done_value": "done",
                     "url_key": "data.resultUrl"},
    }
    p = GenericProvider(cfg)
    import providers.generic as g
    orig = g.http_json
    calls = {"n": 0}
    def mock_hj(method, url, headers, body, timeout):
        if "/submit" in url:
            return 200, {"data": {"taskId": "T123"}}
        calls["n"] += 1
        if calls["n"] < 3:
            return 200, {"data": {"state": "running"}}
        return 200, {"data": {"state": "done", "resultUrl": "https://cdn.example.com/x.png"}}
    g.http_json = mock_hj
    orig_sleep = g.time.sleep
    g.time.sleep = lambda s: None
    try:
        job = {"prompt": "test", "overrides": {}}
        handle = p.submit(job, "m1", "1:1", 1, None)
        check("task_id 提取", handle["id"] == "T123")
        st, urls = p.poll(handle, job=job, timeout=10, interval=0)
        check("轮询到成功", st == "success" and urls == ["https://cdn.example.com/x.png"])
        check("轮询了 3 次", calls["n"] == 3)
    finally:
        g.http_json = orig
        g.time.sleep = orig_sleep


def test_imagifly_multipart():
    print("[imagifly] multipart 与幂等键")
    boundary, body = ImagiflyProvider.build_form(
        {"prompt": "一只猫", "model": "gpt-image-2"},
        [("referenceImages", "ref.png", "image/png", b"\x89PNG\r\n\x1a\nFAKE")])
    check("boundary 出现", boundary.encode() in body)
    check("字段编码", "一只猫".encode("utf-8") in body)
    check("文件名编码", b"ref.png" in body)
    check("mime 编码", b"Content-Type: image/png" in body)
    check("None 字段跳过", b"name=\"skip\"" not in
          ImagiflyProvider.build_form({"skip": None}, [])[1])
    # 幂等键格式
    import re
    m = re.search(rb'Content-Disposition: form-data; name="prompt"', body)
    check("form-data 头", m is not None)

    # 图片尺寸读取
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (400).to_bytes(4, "big") + (300).to_bytes(4, "big")
    w, h = ImagiflyProvider._image_dimensions(png)
    check("PNG 尺寸", (w, h) == (400, 300))
    w, h = ImagiflyProvider._image_dimensions(b"\xff\xd8\xff\xe0garbage")
    check("JPEG 垃圾回退 1024", (w, h) == (1024, 1024))

    # WEBP 尺寸（VP8X）
    webp = b"RIFF" + b"\x00" * 4 + b"WEBPVP8X" + b"\x00" * 8 + (1023).to_bytes(3, "little") + (767).to_bytes(3, "little")
    w, h = ImagiflyProvider._image_dimensions(webp)
    check("WEBP VP8X 尺寸", (w, h) == (1024, 768))

    # 魔数校验
    from core.http import verify_download
    check("PNG 魔数过", verify_download(b"\x89PNG\r\n\x1a\n" + b"x" * 20, "image") is None)
    check("HTML 拦截", verify_download(b"<html>error page</html>", "image") is not None)
    check("短内容拦截", verify_download(b"abc", "image") is not None)


def test_imagifly_submit_payload():
    print("[imagifly] 提交 payload 与响应解包")
    import providers.imagifly as im
    p = ImagiflyProvider.__new__(ImagiflyProvider)  # 不触发 cookie 加载
    p.config = {}
    p.cookie = ""
    captured = {}

    def mock_hj(method, url, headers, body, timeout):
        captured["headers"] = headers
        import json as j
        captured["body"] = j.loads(body.decode("utf-8")) if headers["Content-Type"] == "application/json" else None
        return 202, {"generation": {"id": "GEN123", "status": "pending"}}

    orig_hj = im.http_json
    im.http_json = mock_hj
    try:
        job = {"prompt": "a cat", "overrides": {"tier": "2k", "steps": 30, "neg": "blurry"}}
        gen = p.submit(job, "gpt-image-2", "3:4", 2, None)
        check("generation 解包", gen["id"] == "GEN123")
        body = captured["body"]
        check("size 传档位", body["size"] == "2k", str(body.get("size")))
        check("resolutionTier", body["resolutionTier"] == "2k")
        check("quality 映射", body["quality"] == "high")
        check("responseFormat b64", body["responseFormat"] == "b64_json")
        check("requestCount=batch", body["requestCount"] == 2)
        check("steps 透传", body["steps"] == 30)
        check("negativePrompt", body["negativePrompt"] == "blurry")
        sid = body["submissionId"]
        check("幂等键 = sid:idx", captured["headers"]["Idempotency-Key"] == f"{sid}:1")
        check("Referer 存在", "Referer" in captured["headers"])
    finally:
        im.http_json = orig_hj


def test_single_line():
    print("[utils] 模板单行化")
    out = single_line_prompt("第一行\n第二行  有空格\n第三行｜含全角竖线")
    check("换行压空格", "\n" not in out)
    check("竖线移除", "|" not in out)


def test_placeholders():
    print("[utils] 占位符替换")
    from core.utils import apply_placeholders, extract_placeholders
    tpl = "为[COUNTRY]制作海报，地标是[LANDMARK]，城市[COUNTRY]很大"
    check("提取占位符", extract_placeholders(tpl) == ["COUNTRY", "LANDMARK"])
    out, missing = apply_placeholders(tpl, {"COUNTRY": "日本", "LANDMARK": "东京塔"})
    check("全替换", out == "为日本制作海报，地标是东京塔，城市日本很大" and not missing)
    out, missing = apply_placeholders(tpl, {"country": "日本"})
    check("大小写不敏感", out.startswith("为日本"))
    check("缺失收集", missing == ["LANDMARK"])
    out, missing = apply_placeholders("没有占位符", {})
    check("无占位符原样", out == "没有占位符" and not missing)


def test_build_queue():
    print("[tools] 批量计划 build_queue")
    import importlib.util
    import tempfile
    spec = importlib.util.spec_from_file_location(
        "build_queue", os.path.join(ROOT, "tools", "build_queue.py"))
    bq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bq)

    # 真实模板（仓库内）+ 临时照片文件
    tmpdir = tempfile.mkdtemp()
    photo = os.path.join(tmpdir, "gate.jpg")
    open(photo, "wb").write(b"\xff\xd8\xff\xe0fake")

    plan = {
        "defaults": {"model": "gpt-image-2", "ratio": "3:4"},
        "images": {"gate": photo},
        "tasks": [
            {"tag": "贴纸", "template": "i2i/03a_记忆贴纸_单层_I2I.txt", "image": "gate"},
            {"tag": "高清", "template": "i2i/03c_珐琅徽章_单层_I2I.txt",
             "image": "gate", "tier": "2k", "model": "nano-banana-2"},
            {"tag": "自由", "prompt": "做成明信片", "image": "gate", "ratio": "1:1", "n": 2},
        ],
    }
    lines, warnings, errors = bq.build_lines(plan)
    check("无阻断错误", not errors, str(errors))
    check("展开 3 条", len(lines) == 3, str(len(lines)))
    check("defaults 生效", "model=gpt-image-2" in lines[0] and "| 3:4" in lines[0])
    if len(lines) >= 2:
        check("task 覆盖 defaults",
              "model=nano-banana-2" in lines[1] and "tier=2k" in lines[1])
        check("模板正文入行", "海报" in lines[0] or "贴纸" in lines[0])
    if len(lines) >= 3:
        check("自由 prompt", lines[2].startswith("做成明信片"))
        check("ratio 覆盖", "| 1:1" in lines[2])
        check("x2 批次", "x2" in lines[2])
    check("img 路径正向斜杠",
          all("img=" in ln and "\\" not in ln.split("img=")[1] for ln in lines))

    # 占位符未填 → 警告不阻断
    _, w2, e2 = bq.build_lines({"tasks": [{"template": "t2i/01_手绘旅行海报_T2I.txt"}]})
    check("占位符缺失仅警告", not e2 and any("COUNTRY" in x for x in w2), str((w2, e2)))

    # 模板不存在 → 阻断
    _, _, e3 = bq.build_lines({"tasks": [{"template": "i2i/不存在_I2I.txt"}]})
    check("模板缺失阻断", bool(e3))

    # 照片不存在 → 警告但仍入队
    l4, w4, e4 = bq.build_lines({"tasks": [{"prompt": "x", "image": "C:/nope/zzz.jpg"}]})
    check("照片缺失仅警告", not e4 and len(l4) == 1 and any("不存在" in x for x in w4))

    # 空计划 → 阻断
    _, _, e5 = bq.build_lines({})
    check("空计划报错", bool(e5))


def main():
    test_queue_parse()
    test_provider_registry()
    test_generic_sync()
    test_generic_poll_task()
    test_imagifly_multipart()
    test_imagifly_submit_payload()
    test_single_line()
    test_placeholders()
    test_build_queue()
    print(f"\n结果：{PASS} 通过，{FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
