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
