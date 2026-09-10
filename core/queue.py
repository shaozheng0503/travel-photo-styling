#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务队列：prompts.txt 逐行解析、内联参数、断点续跑、失败重入队。

从 imagifly-batch 的 imagifly.py 抽取。队列格式（每行一条）：

    <prompt 文本> | model=xxx | 16:9 | x2 | img=ref.png | provider=imagifly

`|` 分隔的内联参数可任意组合，每行独立覆盖默认配置。
"""
import json
import os
import re

# 可被 providers 注册表扩展：模型名 -> provider 名
MODEL_PROVIDER_MAP = {}

ASPECT_RATIOS = ["16:9", "21:9", "4:3", "3:2", "5:4", "1:1", "4:5", "2:3", "3:4", "9:16", "9:21"]


def log(msg):
    print(msg, flush=True)


def parse_prompt_line(line):
    """解析一行队列：<prompt> | k=v / 比例 / xN。返回 job dict。"""
    parts = [p.strip() for p in line.split("|")]
    prompt = parts[0]
    overrides = {}
    for p in parts[1:]:
        if not p:
            continue
        m = re.match(r"^model\s*=\s*(.+)$", p, re.I)
        if m:
            overrides["model"] = m.group(1).strip()
            continue
        m = re.match(r"^provider\s*=\s*(.+)$", p, re.I)
        if m:
            overrides["provider"] = m.group(1).strip()
            continue
        m = re.match(r"^style\s*=\s*(.+)$", p, re.I)
        if m:
            overrides["style"] = m.group(1).strip()
            continue
        m = re.match(r"^neg\s*=\s*(.+)$", p, re.I)
        if m:
            overrides["neg"] = m.group(1).strip()
            continue
        m = re.match(r"^img\s*=\s*(.+)$", p, re.I)
        if m:
            overrides["images"] = [x.strip() for x in m.group(1).split(",") if x.strip()]
            continue
        m = re.match(r"^steps\s*=\s*(\d+)$", p)
        if m:
            overrides["steps"] = int(m.group(1))
            continue
        m = re.match(r"^tier\s*=\s*(.+)$", p, re.I)
        if m:
            overrides["tier"] = m.group(1).strip()
            continue
        m = re.match(r"^x(\d+)$", p, re.I)
        if m:
            overrides["batch"] = max(1, min(4, int(m.group(1))))
            continue
        m = re.match(r"^secs\s*=\s*(\d+)$", p)
        if m:
            overrides["seconds"] = int(m.group(1))
            continue
        m = re.match(r"^res\s*=\s*(\w+)$", p, re.I)
        if m:
            overrides["video_resolution"] = m.group(1).upper()
            continue
        m = re.match(r"^fps\s*=\s*(\d+)$", p)
        if m:
            overrides["fps"] = int(m.group(1))
            continue
        if p in ASPECT_RATIOS:
            overrides["ratio"] = p
            continue
    return {"raw": line, "prompt": prompt, "overrides": overrides}


def load_queue(path):
    """读取队列文件，跳过注释/空行。"""
    jobs = []
    if not os.path.exists(path):
        return jobs
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            jobs.append(parse_prompt_line(ln))
    return jobs


def load_config(path):
    import json
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _atomic_write(path, lines):
    """临时文件 + os.replace 原子替换：避免读写间隙崩溃导致队列文件丢失。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln)
    os.replace(tmp, path)


def record_success(paths, job, model, kind, files, cost):
    import datetime
    with open(paths["done"], "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}\t{job['raw']}\t{','.join(files)}\n")
    with open(paths["results"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"status": "success", "type": kind, "model": model,
                            "prompt": job["prompt"], "files": files,
                            "cost_estimate": cost}, ensure_ascii=False) + "\n")
    # 从 prompts.txt 原子移除已完成的行（非原子写崩溃会丢整个队列）
    try:
        with open(paths["prompts"], encoding="utf-8") as f:
            lines = [ln for ln in f.readlines() if ln.strip() != job["raw"]]
        _atomic_write(paths["prompts"], lines)
    except FileNotFoundError:
        pass
    except Exception as e:
        log(f"  ⚠️ 更新队列文件失败：{e}")


def record_failure(paths, job, model, err):
    with open(paths["failed"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"status": "failed", "model": model, "prompt": job["prompt"],
                            "raw": job["raw"], "error": err}, ensure_ascii=False) + "\n")


def load_failed(paths):
    """读取 failed.jsonl。返回 (records, raw_lines)。"""
    if not os.path.exists(paths["failed"]):
        return [], []
    raw_lines = open(paths["failed"], encoding="utf-8").readlines()
    records = []
    for ln in raw_lines:
        try:
            records.append(json.loads(ln))
        except Exception:
            pass
    return records, raw_lines
