#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量计划 → prompts.txt 队列。

一次跑几十上百条任务时，手写 N 遍 --render 太累。把「照片 × 模板」的编排
写成一份 JSON 计划，本脚本一次展开成多行队列条目，再交给 main.py 跑。

计划文件示例见 examples/batch-plan.example.json。

用法：
    python tools/build_queue.py plan.json               # 打印生成的队列行
    python tools/build_queue.py plan.json --add         # 追加进 prompts.txt
    python tools/build_queue.py plan.json --out q.txt   # 写到指定文件
    python tools/build_queue.py plan.json --check       # 只校验计划，不输出
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.utils import apply_placeholders, single_line_prompt  # noqa: E402

PROMPTS_DIR = os.path.join(ROOT, "prompts")
QUEUE = os.path.join(ROOT, "prompts.txt")

# 除 model/ratio/provider/img 外，可直接透传进内联参数的任务字段
PASSTHROUGH = ("tier", "steps", "neg", "style", "seed")


def resolve_template(name):
    """模板名 → 绝对路径。支持 prompts/ 相对路径与绝对路径。找不到返回 None。"""
    for cand in (name, os.path.join(PROMPTS_DIR, name)):
        if os.path.exists(cand):
            return os.path.abspath(cand)
    return None


def resolve_image(value, images):
    """照片值 → 绝对路径。value 可以是 images 字典的 key，也可以是直接路径。"""
    path = images.get(value, value)
    return os.path.abspath(os.path.expanduser(str(path)))


def build_lines(plan):
    """展开计划为队列行。

    返回 (lines, warnings, errors)。
    warnings 不阻断（如占位符未填、照片不存在）；errors 阻断（模板缺失）。
    """
    defaults = plan.get("defaults") or {}
    images = plan.get("images") or {}
    tasks = plan.get("tasks") or []

    lines, warnings, errors = [], [], []
    if not tasks:
        errors.append("计划里没有 tasks")

    for i, task in enumerate(tasks, 1):
        tag = task.get("tag") or f"#{i}"
        template, prompt = task.get("template"), task.get("prompt")

        # ---- 提示词来源：模板文件 或 直接写 ----
        if template:
            path = resolve_template(template)
            if not path:
                errors.append(f"[{tag}] 模板不存在: {template}")
                continue
            with open(path, encoding="utf-8") as f:
                text = f.read()
            text, missing = apply_placeholders(text, task.get("vars") or {})
            if missing:
                warnings.append(f"[{tag}] 占位符未替换: {missing}（计划里补 vars）")
            prompt_text = single_line_prompt(text)
        elif prompt:
            prompt_text = single_line_prompt(prompt)
        else:
            errors.append(f"[{tag}] 既没有 template 也没有 prompt")
            continue

        if not prompt_text:
            errors.append(f"[{tag}] 提示词为空")
            continue

        # ---- 内联参数：defaults <- task 覆盖 ----
        def pick(key):
            return task.get(key, defaults.get(key))

        parts = [prompt_text]
        model = pick("model")
        if model:
            parts.append(f"model={model}")
        ratio = pick("ratio")
        if ratio:
            parts.append(ratio)
        provider = pick("provider")
        if provider:
            parts.append(f"provider={provider}")
        for key in PASSTHROUGH:
            val = pick(key)
            if val not in (None, ""):
                parts.append(f"{key}={val}")
        n = pick("n")
        if n:
            parts.append(f"x{n}")

        # ---- 参考图 ----
        img = task.get("image")
        if img:
            path = resolve_image(img, images)
            if os.path.exists(path):
                parts.append(f"img={path.replace(os.sep, '/')}")
            else:
                warnings.append(f"[{tag}] 照片不存在: {path}（仍写入队列，跑之前补上）")
                parts.append(f"img={path.replace(os.sep, '/')}")

        lines.append(" | ".join(parts))

    return lines, warnings, errors


def main():
    ap = argparse.ArgumentParser(description="批量计划 → prompts.txt 队列")
    ap.add_argument("plan", help="JSON 计划文件路径")
    ap.add_argument("--add", action="store_true", help="追加进 prompts.txt")
    ap.add_argument("--out", metavar="FILE", help="写到指定文件（覆盖）")
    ap.add_argument("--check", action="store_true", help="只校验计划，不输出队列行")
    args = ap.parse_args()

    if not os.path.exists(args.plan):
        print(f"❌ 计划文件不存在: {args.plan}")
        return 1
    try:
        with open(args.plan, encoding="utf-8") as f:
            plan = json.load(f)
    except Exception as e:
        print(f"❌ 计划文件不是合法 JSON: {e}")
        return 1
    if not isinstance(plan, dict):
        print("❌ 计划文件顶层必须是对象（含 tasks 数组）")
        return 1

    lines, warnings, errors = build_lines(plan)

    for w in warnings:
        print(f"⚠️  {w}")
    if errors:
        for e in errors:
            print(f"❌ {e}")
        return 1

    if args.check:
        print(f"✅ 计划校验通过：{len(lines)} 条任务"
              + (f"，{len(warnings)} 条提醒" if warnings else ""))
        return 0

    for ln in lines:
        print(ln)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\n✅ 已写入 {args.out}（{len(lines)} 条）")
    if args.add:
        with open(QUEUE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\n✅ 已追加进 prompts.txt（{len(lines)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
