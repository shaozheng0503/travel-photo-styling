#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""旅行照片配图（travel-photo-styling）

把一批旅行照片 + 提示词模板交给任意生图 API，自动校验、估算成本、
逐条生成并下载。核心特性：

- 零依赖（Python 3.8+ 标准库）
- Provider 可扩展：imagifly 内置，openai_compat 一次接入所有 OpenAI 兼容 API，
  generic 纯配置接入任意「提交 → 轮询 → 下载」模式的生图服务
- 提示词模板库：prompts/ 内置 9 套旅行照片风格化模板（贴纸/票根/徽章/亚克力/几何/字体…）
- 成本阻断：高成本任务必须 --confirm
- 断点续跑：成功移入 done.txt，失败留 failed.jsonl 可一键重入队

用法：
    python main.py --list-providers        # 看可用生图服务
    python main.py --check                 # 配置/登录态自检
    python main.py --list-models           # 当前 provider 的模型清单
    python main.py --validate              # 校验队列（不调 API）
    python main.py --estimate              # 估算成本（不调 API）
    python main.py --dry-run               # 解析预演（不调 API）
    python main.py                         # 跑全部（高成本需 --confirm）
    python main.py 3                       # 只跑前 3 条
    python main.py --retry-failed          # 失败任务重入队
"""
import argparse
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.queue import (ASPECT_RATIOS, load_config, load_failed, load_queue,
                        log, parse_prompt_line, record_failure, record_success)
from providers import (available_names, build_provider, default_provider_name,
                       load_providers_config)

ROOT = os.path.dirname(os.path.abspath(__file__))
PATHS = {
    "prompts": os.path.join(ROOT, "prompts.txt"),
    "done": os.path.join(ROOT, "done.txt"),
    "results": os.path.join(ROOT, "results.jsonl"),
    "failed": os.path.join(ROOT, "failed.jsonl"),
    "images": os.path.join(ROOT, "images"),
}
HIGH_COST_THRESHOLD = 5


# ---------- provider 解析 ----------
def resolve_provider(job, cli, cfg):
    """provider 解析优先级：行内 provider= > 行内 model 前缀路由 > CLI --provider > config 默认。"""
    ov = job["overrides"]
    if ov.get("provider"):
        return ov["provider"]
    pname = cli.get("provider") or cfg.get("provider") or default_provider_name()
    # 模型名路由：自定义 provider 段里声明的 model 命中即用该段
    if ov.get("model"):
        pconf = load_providers_config()
        for section, pc in pconf.items():
            if isinstance(pc, dict) and ov["model"] in (pc.get("models") or {}):
                return section
    return pname


def resolve_model(job, cli, cfg):
    ov = job["overrides"]
    return ov.get("model") or cli.get("model") or cfg.get("model") or "gpt-image-2"


def resolve(job, cli, cfg):
    model = resolve_model(job, cli, cfg)
    pname = resolve_provider(job, cli, cfg)
    ratio = ov_ratio(job) or cli.get("ratio") or cfg.get("ratio") or "1:1"
    batch = job["overrides"].get("batch", cfg.get("batch_size", 1))
    style = job["overrides"].get("style")
    return pname, model, ratio, batch, style


def ov_ratio(job):
    return job["overrides"].get("ratio")


def job_cost(provider, model, job):
    c = provider.cost_of(model, job["overrides"])
    return c if c is not None else 0


# ---------- 任务执行 ----------
def run_job(provider, job, idx, cfg, resolved):
    pname, model, ratio, batch, style = resolved
    log(f"[{idx}] provider={pname} model={model} ratio={ratio} batch={batch}"
        f"{' style=' + style if style else ''} | {job['prompt'][:60]}")
    try:
        imgs = job["overrides"].get("images") or []
        if imgs and not provider.supports_image_input(model):
            log(f"  ⚠️ {model} 不支持参考图，img= 将被忽略或失败")
        handle = provider.submit(job, model, ratio, batch, style)
        gen_id = handle.get("id")
        log(f"  → 任务 {gen_id} 轮询中 ...")
        status, urls = provider.poll(handle, job=job,
                                     timeout=cfg.get("image_poll_timeout_seconds", 600),
                                     interval=cfg.get("poll_interval_seconds", 2))
        if status != "success" or not urls:
            raise RuntimeError(f"生成未成功：{status}")
        files = []
        for i, url in enumerate(urls[:max(1, batch)]):
            files.append(provider.download(url, PATHS["images"], job["prompt"], i))
            log(f"  ✅ {files[-1]}")
        record_success(PATHS, job, model, "image", files, provider.cost_of(model, job["overrides"]))
        return True
    except Exception as e:
        log(f"  ❌ 失败：{e}")
        record_failure(PATHS, job, model, str(e))
        return False


# ---------- 预检命令 ----------
def cmd_validate(cfg, cli):
    jobs = load_queue(PATHS["prompts"])
    if not jobs:
        print("队列为空。把提示词写进 prompts.txt（每行一个），模板见 prompts/ 目录。")
        return 1
    print(f"共 {len(jobs)} 条任务：")
    ok = True
    providers_cache = {}
    for i, job in enumerate(jobs, 1):
        try:
            pname, model, ratio, batch, style = resolve(job, cli, cfg)
            if pname not in providers_cache:
                providers_cache[pname] = build_provider(pname)
            p = providers_cache[pname]
            models = p.list_models()
            known = model in models if isinstance(models, dict) else True
            warn = ""
            if not known:
                warn = f" ⚠️ 模型 {model} 不在 {pname} 注册表（将透传给 API）"
            imgs = job["overrides"].get("images") or []
            ref_note = ""
            if imgs and not p.supports_image_input(model):
                ref_note = " ⚠️ 该模型不支持参考图"
                ok = False
            print(f"  [{i}] ✓ {pname}/{model} ratio={ratio} batch={batch}"
                  f"{' style=' + style if style else ''}{warn}{ref_note} | {job['prompt'][:40]}")
        except Exception as e:
            print(f"  [{i}] ❌ {e}")
            ok = False
    print("\n✅ 校验通过" if ok else "\n❌ 存在问题，请修正后再跑")
    return 0 if ok else 1


def cmd_estimate(cfg, cli):
    jobs = load_queue(PATHS["prompts"])
    if not jobs:
        print("队列为空。")
        return 1
    providers_cache = {}
    total = 0
    unknown = 0
    print(f"{'#':>3}  {'provider/model':40} {'cost':>6}  prompt")
    for i, job in enumerate(jobs, 1):
        pname, model, ratio, batch, style = resolve(job, cli, cfg)
        if pname not in providers_cache:
            providers_cache[pname] = build_provider(pname)
        p = providers_cache[pname]
        c = p.cost_of(model, job["overrides"])
        if c is None:
            unknown += 1
            print(f"{i:>3}  {pname + '/' + model:40} {'?':>6}  {job['prompt'][:40]}")
            continue
        line_cost = c * batch
        total += line_cost
        flag = "  ⚠️高成本" if c >= HIGH_COST_THRESHOLD else ""
        print(f"{i:>3}  {pname + '/' + model:40} {line_cost:>6}  {job['prompt'][:40]}{flag}")
    print(f"\n合计约 {total}（口径取决于平台，最终以实际扣费为准）")
    if unknown:
        print(f"⚠️ {unknown} 条成本未知（provider 未提供 cost 信息）")
    return 0


def cmd_dry_run(cfg, cli):
    jobs = load_queue(PATHS["prompts"])
    if not jobs:
        print("队列为空。")
        return 1
    for i, job in enumerate(jobs, 1):
        pname, model, ratio, batch, style = resolve(job, cli, cfg)
        print(f"[{i}] {pname}/{model} ratio={ratio} batch={batch}"
              f"{' style=' + style if style else ''}")
        print(f"    prompt: {job['prompt'][:80]}")
        if job["overrides"].get("images"):
            print(f"    refs: {job['overrides']['images']}")
    print(f"\n共 {len(jobs)} 条，不会调用 API。")
    return 0


def cmd_check(cfg, cli):
    pname = cli.get("provider") or cfg.get("provider") or default_provider_name()
    print(f"可用 providers：{', '.join(available_names())}")
    pconf = load_providers_config()
    for name in available_names():
        if name in pconf or name == "imagifly":
            try:
                p = build_provider(name)
                ok, msg = p.check()
                mark = "✅" if ok else "❌"
                print(f"  {mark} {name}: {msg}")
            except Exception as e:
                print(f"  ❌ {name}: {e}")
    return 0


def cmd_list_models(cfg, cli):
    pname = cli.get("provider") or cfg.get("provider") or default_provider_name()
    p = build_provider(pname)
    models = p.list_models()
    print(f"provider={pname} 模型清单：")
    if isinstance(models, dict):
        for name, info in models.items():
            if isinstance(info, dict):
                print(f"  {name:36} ref={info.get('ref', '?')} cost={info.get('cost', '?')} {info.get('tags', '')}")
            else:
                print(f"  {name}")
    else:
        for m in models:
            print(f"  {m}")
    return 0


def cmd_summary():
    if not os.path.exists(PATHS["results"]):
        print("还没有 results.jsonl，先跑一次任务。")
        return 0
    succ = fail = 0
    files = []
    with open(PATHS["results"], encoding="utf-8") as f:
        for ln in f:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r["status"] == "success":
                succ += 1
                files.extend(r.get("files", []))
            else:
                fail += 1
    print(f"成功 {succ} 条，失败 {fail} 条，产物 {len(files)} 个文件。")
    if os.path.exists(PATHS["failed"]) and os.path.getsize(PATHS["failed"]) > 0:
        print("失败项见 failed.jsonl，可用 --retry-failed 重新入队。")
    return 0


def cmd_retry_failed():
    records, _ = load_failed(PATHS)
    if not records:
        print("没有 failed.jsonl 或为空。")
        return 0
    with open(PATHS["prompts"], "a", encoding="utf-8") as f:
        for r in records:
            if r.get("raw"):
                f.write(r["raw"] + "\n")
    os.remove(PATHS["failed"])
    print(f"已把 {len(records)} 条失败任务重新写入 prompts.txt。运行 python main.py 即可重试。")
    return 0


def cmd_render(cfg, args):
    """把 prompts/ 下的模板文件渲染成单行队列条目。"""
    pfile = args.render
    if not os.path.exists(pfile):
        # 相对 prompts/ 目录找
        alt = os.path.join(ROOT, "prompts", pfile)
        if os.path.exists(alt):
            pfile = alt
        else:
            print(f"❌ 模板不存在: {pfile}")
            return 1
    text = open(pfile, encoding="utf-8").read()
    from core.utils import single_line_prompt
    inline = single_line_prompt(text)
    model = args.model or "gpt-image-2"
    ratio = args.ratio or "3:4"
    parts = [inline, f"model={model}", ratio]
    if args.provider:
        parts.append(f"provider={args.provider}")
    if args.img:
        parts.append(f"img={os.path.abspath(args.img)}")
    line = " | ".join(parts)
    print(line)
    if args.add:
        with open(PATHS["prompts"], "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(f"已加入 prompts.txt（当前 {len(load_queue(PATHS['prompts']))} 条）")
    return 0


# ---------- 主入口 ----------
def main():
    ap = argparse.ArgumentParser(description="旅行照片配图：任意生图 API 的批量照片风格化")
    ap.add_argument("n", nargs="?", type=int, help="只跑前 N 条")
    ap.add_argument("--provider", help="指定生图服务（imagifly/openai_compat/自定义段名）")
    ap.add_argument("--model", help="覆盖默认模型")
    ap.add_argument("--ratio", help="覆盖默认宽高比")
    ap.add_argument("--render", metavar="TEMPLATE", help="把模板文件渲染成单行队列条目（打印或 --add 入队）")
    ap.add_argument("--img", help="配合 --render：参考图路径")
    ap.add_argument("--add", action="store_true", help="配合 --render：渲染后直接追加进 prompts.txt")
    ap.add_argument("--validate", action="store_true", help="校验队列（不调 API）")
    ap.add_argument("--estimate", action="store_true", help="估算成本（不调 API）")
    ap.add_argument("--dry-run", action="store_true", help="解析预演（不调 API）")
    ap.add_argument("--check", action="store_true", help="预检：providers / 登录态")
    ap.add_argument("--list-models", action="store_true", help="列出当前 provider 的模型")
    ap.add_argument("--list-providers", action="store_true", help="列出可用 providers")
    ap.add_argument("--summary", action="store_true", help="汇总 results.jsonl")
    ap.add_argument("--retry-failed", action="store_true", help="把 failed.jsonl 重新入队")
    ap.add_argument("--confirm", action="store_true", help="确认运行高成本任务")
    args = ap.parse_args()

    cfg = load_config(os.path.join(ROOT, "config", "config.json"))

    if args.list_providers:
        print(f"内置：imagifly, openai_compat, generic")
        custom = load_providers_config()
        if custom:
            print(f"自定义（config/providers.json）：{', '.join(custom.keys())}")
        print(f"默认 provider：{default_provider_name()}")
        return 0

    cli = {"provider": args.provider, "model": args.model, "ratio": args.ratio}

    if args.render:
        return cmd_render(cfg, args)
    if args.validate:
        return cmd_validate(cfg, cli)
    if args.estimate:
        return cmd_estimate(cfg, cli)
    if args.dry_run:
        return cmd_dry_run(cfg, cli)
    if args.check:
        return cmd_check(cfg, cli)
    if args.list_models:
        return cmd_list_models(cfg, cli)
    if args.summary:
        return cmd_summary()
    if args.retry_failed:
        return cmd_retry_failed()

    # 真实运行
    jobs = load_queue(PATHS["prompts"])
    if not jobs:
        print("队列为空。把提示词写进 prompts.txt（每行一个），或用 --render 快速入队。")
        return 1
    if args.n:
        jobs = jobs[:args.n]
    log(f"==== 开始运行 {len(jobs)} 条任务 ====")
    ok = fail = 0
    providers_cache = {}
    for i, job in enumerate(jobs, 1):
        resolved = resolve(job, cli, cfg)
        pname, model, ratio, batch, style = resolved
        if pname not in providers_cache:
            providers_cache[pname] = build_provider(pname)
        p = providers_cache[pname]
        c = job_cost(p, model, job)
        if c >= HIGH_COST_THRESHOLD and not args.confirm:
            log(f"  ⚠️ [{i}] {model} 单次约 {c}（≥{HIGH_COST_THRESHOLD}），需 --confirm 确认。跳过本条。")
            fail += 1
            continue
        if run_job(p, job, i, cfg, resolved):
            ok += 1
        else:
            fail += 1
        time.sleep(float(cfg.get("delay_between_seconds", 2)))
    log(f"==== 完成：成功 {ok}，失败 {fail} ====")
    cmd_summary()
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
