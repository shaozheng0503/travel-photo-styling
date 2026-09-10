#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider 注册表：按名加载、按模型路由。

用法：
    from providers import build_provider, REGISTRY
    p = build_provider("imagifly", provider_config)
    p = build_provider("myapi", provider_config)   # config 里 type=generic
"""
import importlib
import os

BUILTIN_PROVIDERS = ["imagifly", "openai_compat", "generic"]

REGISTRY = {}


def _register_builtins():
    for name in BUILTIN_PROVIDERS:
        mod = importlib.import_module(f"providers.{name}")
        mod.register(REGISTRY)


_register_builtins()


def provider_names():
    return sorted(REGISTRY.keys())


def create_provider(name, config=None):
    """按配置实例化 provider。配置里可用 type 字段覆盖（generic 模式）。

    name 解析优先级：
    1. 内置 provider 名（imagifly / openai_compat / generic）
    2. config/providers.json 里自定义段名（type=generic / openai_compat）
    """
    if name in REGISTRY:
        return REGISTRY[name](config)
    # 自定义段：从 providers.json 找同名段
    pconf = load_providers_config()
    if name in pconf:
        ptype = pconf[name].get("type", "generic")
        if ptype not in REGISTRY:
            raise ValueError(f"未知 provider type: {ptype}")
        return REGISTRY[ptype](pconf[name])
    raise ValueError(f"未知 provider: {name}（可用：{', '.join(available_names())}）")


def build_provider(name, config=None):
    return create_provider(name, config)


def load_providers_config():
    """读 config/providers.json。不存在返回 {}。_ 开头的键视为注释段忽略。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "config", "providers.json")
    if os.path.exists(path):
        import json
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items()
                if not k.startswith("_") and isinstance(v, dict)}
    return {}


def available_names():
    """内置 + providers.json 自定义段名。"""
    names = set(REGISTRY.keys())
    names.update(load_providers_config().keys())
    return sorted(names)


def default_provider_name():
    """config/config.json 的 provider 字段，缺省 imagifly。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "config", "config.json")
    if os.path.exists(path):
        import json
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("provider", "imagifly")
    return "imagifly"
