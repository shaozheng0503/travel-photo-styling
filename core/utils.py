#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""杂项工具：文件名 slug、扩展名推断、prompt 单行化。"""
import re


def slugify(s, n=40):
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "_", s).strip("_")
    return (s[:n] or "image").lower()


def ext_for(url, content_type):
    ct = (content_type or "").lower()
    if "png" in ct:
        return ".png"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "webp" in ct:
        return ".webp"
    if "mp4" in ct:
        return ".mp4"
    if "webm" in ct:
        return ".webm"
    m = re.search(r"\.(png|jpe?g|webp|gif|mp4|webm|mov)(\?|$)", url or "", re.I)
    if m:
        return "." + m.group(1).lower()
    return ".png"


def single_line_prompt(text):
    """把多行提示词模板压成一行（队列文件每行一条任务）。"""
    return re.sub(r"\s+", " ", text.replace("|", " ").strip())


PLACEHOLDER_RE = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)\]")


def extract_placeholders(text):
    """提取模板中的 [NAME] 占位符（去重保序）。"""
    seen, out = set(), []
    for m in PLACEHOLDER_RE.finditer(text):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


def apply_placeholders(text, variables):
    """把 [NAME] 占位符替换为 variables[NAME] 的值。

    variables: dict，键大小写不敏感（模板里 [COUNTRY]，命令行给 country=日本 也行）。
    返回 (替换后文本, 未替换的占位符列表)。
    """
    norm = {k.upper(): v for k, v in (variables or {}).items()}
    missing = []

    def _sub(m):
        name = m.group(1)
        if name.upper() in norm:
            return str(norm[name.upper()])
        if name not in missing:
            missing.append(name)
        return m.group(0)

    return PLACEHOLDER_RE.sub(_sub, text), missing
