"""method_body_loader.py — 调用链方法体分批加载器。

职责：
- 前 5 层（``depth < 5``）一次性预加载完整方法体
- 后续层（``depth >= 5``）只返回 node_id，AI 按需调用 ``load_by_node_id``
- ``can_skip_deferred``：前 5 层若无 sink 调用且无外部参数传递，可跳过延迟层
- 复用 ``scripts.redis.memurai_client.Memurai``（或任何鸭子类型对象，只要实现
  ``get(key) -> str | None``）；外部依赖通过构造函数注入，便于单测 mock
- 与 ``LoadCounter`` 集成：每次加载调用 ``record_load``

设计要点：
- **不直接 import Memurai**，避免测试启动 CLI 子进程；通过构造函数注入
- ``load_first_5_layers`` 返回结构化字典，便于 AI 上下文序列化
- ``can_skip_deferred`` 是纯静态方法，不读外部状态，便于独立测试
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Protocol


# ------------------------------------------------------------------ 类型契约

class _CacheLike(Protocol):
    """Memurai 客户端的鸭子接口，便于单测注入 mock。"""

    def get(self, key: str) -> Optional[str]: ...


class _CounterLike(Protocol):
    """LoadCounter 的鸭子接口。"""

    def record_load(
        self,
        node_id: str,
        fqn: str,
        chain_id: str,
        source: str = "ai_request",
    ) -> None: ...


# ------------------------------------------------------------------ 节点

@dataclass
class ChainNode:
    """调用链节点（最小字段，兼容上游 chain JSON）。"""
    node_id: str
    fqn: str
    depth: int


# 延迟层判定阈值：depth < 5 → 前 5 层（depth 0..4）
FIRST_N_LAYERS_DEPTH = 5


# ------------------------------------------------------------------ 加载结果

@dataclass
class LoadedBody:
    """单条已加载方法体的结构。"""
    node_id: str
    fqn: str
    body: Optional[str]
    depth: int
    source: str  # cache_hit / first_5_layer / fallback_miss


# ------------------------------------------------------------------ 加载器

class MethodBodyLoader:
    """加载器实例是无状态的（除注入的依赖外），可跨链复用。"""

    def __init__(
        self,
        cache_client: _CacheLike,
        load_counter: Optional[_CounterLike] = None,
        chain_id: str = "default-chain",
    ):
        self.cache = cache_client
        self.counter = load_counter
        self.default_chain_id = chain_id

    # -------------------------------------------------- 内部

    def _fetch_and_record(
        self,
        node_id: str,
        fqn: str,
        chain_id: str,
        source: str,
    ) -> Optional[str]:
        body = self.cache.get(node_id)
        if self.counter is not None:
            # 命中缓存也算一次加载事件，用于评估 ratio
            self.counter.record_load(
                node_id, fqn, chain_id,
                source="cache_hit" if body is not None else source,
            )
        return body

    # -------------------------------------------------- 公开 API

    def load_first_5_layers(
        self,
        chain: Iterable[Any],
        chain_id: Optional[str] = None,
    ) -> dict:
        """一次性加载前 5 层；后续层只返回 node_id。

        Args:
            chain: 节点列表，每个节点至少含 ``node_id`` / ``fqn`` / ``depth``。
                   接受 dict 或 ChainNode。
            chain_id: 覆盖默认 chain_id（用于 record_load）

        Returns:
            ``{"full_bodies": [LoadedBody.as_dict, ...],
               "deferred_node_ids": [str, ...]}``
        """
        cid = chain_id or self.default_chain_id
        full_bodies: List[dict] = []
        deferred: List[str] = []

        for raw in chain:
            node = _coerce_node(raw)
            if node.depth < FIRST_N_LAYERS_DEPTH:
                body = self._fetch_and_record(
                    node.node_id, node.fqn, cid, "first_5_layer"
                )
                full_bodies.append({
                    "node_id": node.node_id,
                    "fqn": node.fqn,
                    "body": body,
                    "depth": node.depth,
                    "source": "cache_hit" if body is not None else "miss",
                })
            else:
                deferred.append(node.node_id)

        return {"full_bodies": full_bodies, "deferred_node_ids": deferred}

    def load_by_node_id(
        self,
        node_id: str,
        fqn: str = "",
        chain_id: Optional[str] = None,
    ) -> Optional[str]:
        """AI 按需加载单条延迟方法体。"""
        cid = chain_id or self.default_chain_id
        return self._fetch_and_record(node_id, fqn, cid, "ai_request")

    # -------------------------------------------------- 跳过判定

    @staticmethod
    def can_skip_deferred(full_bodies: List[dict], taint_analysis: List[dict]) -> bool:
        """检查前 5 层是否已能确定污点消除。

        Args:
            full_bodies: ``load_first_5_layers`` 返回的 ``full_bodies`` 字段
            taint_analysis: 与 full_bodies 同序/同 node_id 集合的分析结果，
                            每条至少含 ``node_id``；可选 ``has_sink``、
                            ``passes_external_param`` 两个 bool

        规则：
            无 sink 调用 且 无外部参数传递 → True（可跳过延迟层）

        Returns:
            True 表示前 5 层已收口，无需继续加载延迟层
        """
        if not full_bodies:
            # 没有前 5 层则不能下结论（保持保守）
            return False

        # 以 node_id 为键建索引，容忍 taint_analysis 多/少条目
        ta_by_node = {ta.get("node_id"): ta for ta in taint_analysis}

        for body in full_bodies:
            ta = ta_by_node.get(body.get("node_id"), {})
            if ta.get("has_sink"):
                return False
            if ta.get("passes_external_param"):
                return False
        return True


# ------------------------------------------------------------------ 辅助

def _coerce_node(raw: Any) -> ChainNode:
    """容忍 dict / ChainNode / 任意带属性的对象。"""
    if isinstance(raw, ChainNode):
        return raw
    if isinstance(raw, dict):
        return ChainNode(
            node_id=str(raw["node_id"]),
            fqn=str(raw.get("fqn", "")),
            depth=int(raw.get("depth", 0)),
        )
    # 兜底：对象属性访问
    return ChainNode(
        node_id=str(getattr(raw, "node_id")),
        fqn=str(getattr(raw, "fqn", "")),
        depth=int(getattr(raw, "depth", 0)),
    )
