# -*- coding: utf-8 -*-
"""registry.py — collector 注册表与发现机制。

支持两种注册方式：
1. 装饰器：@register_collector 自动登记
2. 显式：register(Cls) 手动登记

主流程通过 get_all_collectors() 拿到全部 collector，按可用性筛选后串行/并行执行。
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .contracts import Collector

# 全局注册表：asset_type -> Collector 类
_REGISTRY: dict[str, type["Collector"]] = {}


def register_collector(asset_type: str):
    """类装饰器：注册一个 Collector 实现。

    用法::

        @register_collector("route")
        class RouteCollector:
            ...
    """
    def decorator(cls: type) -> type:
        if asset_type in _REGISTRY:
            raise ValueError(
                f"asset_type '{asset_type}' 已被 "
                f"{_REGISTRY[asset_type].__name__} 占用"
            )
        _REGISTRY[asset_type] = cls
        return cls
    return decorator


def register(cls: type) -> type:
    """显式注册：用 cls.asset_type 作为 key。"""
    asset_type = getattr(cls, "asset_type", None)
    if not asset_type:
        raise ValueError(f"{cls.__name__} 缺少 asset_type 类属性")
    if asset_type in _REGISTRY:
        raise ValueError(
            f"asset_type '{asset_type}' 已被 "
            f"{_REGISTRY[asset_type].__name__} 占用"
        )
    _REGISTRY[asset_type] = cls
    return cls


def get_collector(asset_type: str) -> type["Collector"] | None:
    """按 asset_type 取一个 collector 类，不存在返回 None。"""
    return _REGISTRY.get(asset_type)


def get_all_collectors() -> dict[str, type["Collector"]]:
    """返回全部已注册 collector 的浅拷贝。"""
    return dict(_REGISTRY)


def autodiscover() -> None:
    """自动导入 collectors 子包，触发 @register_collector 装饰器。

    必须在主流程启动时调用一次。
    """
    from . import collectors as pkg
    for _finder, modname, _ispkg in pkgutil.iter_modules(pkg.__path__):
        if modname.startswith("_"):
            continue
        importlib.import_module(f"{pkg.__name__}.{modname}")
