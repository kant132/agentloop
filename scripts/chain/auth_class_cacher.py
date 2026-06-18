"""
auth_class_cacher.py
====================

缓存所有认证/鉴权相关类到 Memurai。

复用 ``scripts/redis/memurai_client.py`` 的 ``Memurai`` 客户端
(``set_json`` / ``get_json`` / ``scan`` 三个鸭子方法)。

key 格式::

    {group_id}:auth:class:{fqn}

API
---
- ``AuthClassCacher(memurai_client)`` — 构造, 接受任意实现上述三方法的 client
- ``cache_auth_classes(auth_items, group_id) -> int``
- ``get_cached_auth_class(fqn, group_id) -> dict | None``
- ``list_all_auth_classes(group_id) -> list[str]``
- ``create_cacher(host, port, ...) -> AuthClassCacher`` — 用真实 Memurai 构造
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

# 让 memurai_client 可被 import (与 chain_builder 同款 sys.path 注入)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REDIS_DIR = _REPO_ROOT / "scripts" / "redis"
if str(_REDIS_DIR) not in sys.path:
    sys.path.insert(0, str(_REDIS_DIR))


def _cache_key(group_id: str, fqn: str) -> str:
    """``{group_id}:auth:class:{fqn}``"""
    return f"{group_id}:auth:class:{fqn}"


def _key_prefix(group_id: str) -> str:
    """scan 前缀: ``{group_id}:auth:class:*``"""
    return f"{group_id}:auth:class:*"


class AuthClassCacher:
    """认证鉴权类缓存器。

    client 鸭子类型需实现:
        - ``set_json(key, obj, ex=None) -> bool``
        - ``get_json(key) -> Any | None``
        - ``scan(pattern) -> list[str]``
    """

    def __init__(self, memurai_client: Any):
        self.client = memurai_client

    def cache_auth_classes(
        self,
        auth_items: Sequence[Mapping[str, Any]],
        group_id: str,
    ) -> int:
        """批量写入 auth class。

        Parameters
        ----------
        auth_items:
            每条至少含 ``fqn`` 字段 (其余作为 payload 一并存)。
        group_id:
            项目 groupId。

        Returns
        -------
        int: 成功写入数量 (缺 fqn 的条目跳过)。
        """
        ok = 0
        for item in auth_items:
            fqn = item.get("fqn")
            if not fqn:
                continue
            key = _cache_key(group_id, str(fqn))
            if self.client.set_json(key, dict(item)):
                ok += 1
        return ok

    def get_cached_auth_class(
        self,
        fqn: str,
        group_id: str,
    ) -> Optional[Dict[str, Any]]:
        """按 fqn 取单个 auth class。

        Returns
        -------
        dict | None: 命中返回原 dict; 未命中或值非 dict 返回 None。
        """
        key = _cache_key(group_id, fqn)
        val = self.client.get_json(key)
        return val if isinstance(val, dict) else None

    def list_all_auth_classes(self, group_id: str) -> List[str]:
        """列出该 group 下所有缓存的 auth class fqn。

        Returns
        -------
        list[str]: fqn 列表 (从 key 剥离前缀得到, 保持 scan 顺序)。
        """
        prefix = _cache_key(group_id, "")   # "{gid}:auth:class:"
        keys = self.client.scan(_key_prefix(group_id))
        out: List[str] = []
        for k in keys:
            if k.startswith(prefix):
                out.append(k[len(prefix):])
        return out


def create_cacher(
    host: str = "localhost",
    port: int = 6379,
    password: Optional[str] = None,
    db: int = 0,
    timeout: float = 300.0,
) -> AuthClassCacher:
    """用真实 ``memurai_client.Memurai`` 构造 ``AuthClassCacher``。

    懒导入: 仅在实际需要连接时才 import, 避免无 memurai-cli 环境下模块加载失败。
    """
    from memurai_client import Memurai  # type: ignore
    cli = Memurai(
        host=host, port=port, password=password,
        db=db, timeout=timeout,
    )
    return AuthClassCacher(cli)
