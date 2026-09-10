#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 抽象基类：任意生图服务的统一接入协议。

接入一个新的生图 API 只需两选一：
A. 纯配置：编辑 config/providers.json，走 GenericProvider（见 providers/generic.py）
B. 写代码：继承 ImageProvider 并实现 submit() / poll() / download()，然后注册

约定：
- submit(job) 返回 provider 侧的任务句柄（dict），至少含 "id" 字段
- poll(handle) 返回 (status, assets)；status ∈ pending / success / failed
- download(asset, dest_dir) 把资产落盘，返回本地文件名
- 成本估算：models 注册表声明 cost；未声明返回 None（不算积分阻断）
"""
import os


class ProviderError(RuntimeError):
    pass


class ImageProvider:
    name = "base"

    def __init__(self, config=None):
        self.config = config or {}

    # ---- 元信息 ----
    def list_models(self):
        """返回该 provider 的可用模型清单（dict 或 list）。默认离线注册表。"""
        return getattr(self, "models", {})

    def model_info(self, model):
        models = self.list_models()
        if isinstance(models, dict) and model in models:
            return models[model]
        return {}

    def cost_of(self, model, job_overrides):
        """估算单次成本（积分/元/次数，取决于平台口径）。无信息返回 None。"""
        info = self.model_info(model)
        return info.get("cost")

    def supports_image_input(self, model):
        info = self.model_info(model)
        return bool(info.get("ref"))

    # ---- 核心三步 ----
    def submit(self, job, model, ratio, batch_size, style):
        """提交任务。返回 handle dict（含 id）。"""
        raise NotImplementedError

    def poll(self, handle, job=None):
        """轮询任务。返回 (status, [asset_url_or_obj, ...])。"""
        raise NotImplementedError

    def download(self, asset, dest_dir, prompt, idx, kind="image"):
        """下载资产到 dest_dir。返回本地文件名。"""
        raise NotImplementedError

    # ---- 便捷 ----
    def auth_headers(self):
        return {}

    def check(self):
        """连通性/登录态自检。返回 (ok, message)。"""
        return True, "not implemented"
