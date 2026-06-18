"""
chain_file_writer.py
====================

将调用链按端点写入独立 .txt 文件, 每行一条链路。

格式: ``fqn:nodeid->fqn:nodeid->...`` (一链一行)

输出位置: ``{output_dir}/chains/{endpoint_safe_name}.txt``

API
---
- ``ChainFileWriter.write(chain_data, output_dir) -> Path``
      单端点单链 → 一个文件一行。
- ``ChainFileWriter.write_all(chains, output_dir) -> list[Path]``
      多端点/多变体 → 按端点分组, 同端点写多行, 不同端点各一文件。

chain_data 结构 (与 ``chain_builder.build_chain`` 输出一致)::

    {
      "entry_fqn": "com.example.Foo#bar",
      "chain": [{"fqn": "...", "node_id": "...", ...}, ...],
    }
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

# Windows + POSIX 文件名都不允许的字符
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_\-]")


def safe_endpoint_name(fqn: str) -> str:
    """把 entry_fqn 转成 Windows 安全的文件名 (无 / \\ : . # 空格等)。

    >>> safe_endpoint_name("com.example.Foo#bar")
    'com_example_Foo_bar'
    """
    if not fqn:
        return "unnamed_endpoint"
    name = _UNSAFE_CHARS.sub("_", fqn)
    # 压缩连续下划线, 去首尾
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "unnamed_endpoint"


def _format_path(nodes: Sequence[Mapping[str, Any]]) -> str:
    """nodes → ``fqn:nodeid->fqn:nodeid`` 字符串。空链返回空串。"""
    if not nodes:
        return ""
    return "->".join(
        f"{n.get('fqn', '')}:{n.get('node_id', '')}" for n in nodes
    )


class ChainFileWriter:
    """调用链文件写入器。无状态, 所有方法为静态。"""

    @staticmethod
    def write(chain_data: Mapping[str, Any], output_dir: str | Path) -> Path:
        """写单端点链文件 (覆盖模式)。

        Parameters
        ----------
        chain_data:
            ``{entry_fqn, chain: [nodes]}``
        output_dir:
            输出根目录; 实际文件落在 ``{output_dir}/chains/`` 下。

        Returns
        -------
        Path: 写入的 .txt 文件路径
        """
        out_root = Path(output_dir)
        chains_dir = out_root / "chains"
        chains_dir.mkdir(parents=True, exist_ok=True)

        entry = chain_data.get("entry_fqn", "unnamed_endpoint")
        fname = safe_endpoint_name(entry) + ".txt"
        target = chains_dir / fname

        nodes = chain_data.get("chain", [])
        line = _format_path(nodes)
        # 空链 → 写空行 (保持文件存在但无路径内容)
        target.write_text(line + "\n" if line else "", encoding="utf-8")
        return target

    @staticmethod
    def write_all(
        chains: Sequence[Mapping[str, Any]],
        output_dir: str | Path,
    ) -> List[Path]:
        """多端点/多变体批量写入, 按端点分组。

        - 同 entry_fqn 的多条 chain → 同一文件多行 (每行一条链路)
        - 不同 entry_fqn → 各自独立文件

        Returns
        -------
        list[Path]: 每个端点的 .txt 文件路径 (按首次出现顺序)
        """
        out_root = Path(output_dir)
        chains_dir = out_root / "chains"
        chains_dir.mkdir(parents=True, exist_ok=True)

        # group: safe_name -> list of path-lines (保持端点首次出现顺序)
        grouped: Dict[str, List[str]] = {}
        order: List[str] = []
        for cd in chains:
            entry = cd.get("entry_fqn", "unnamed_endpoint")
            safe = safe_endpoint_name(entry)
            if safe not in grouped:
                grouped[safe] = []
                order.append(safe)
            grouped[safe].append(_format_path(cd.get("chain", [])))

        results: List[Path] = []
        for safe in order:
            lines = grouped[safe]
            content = "\n".join(l for l in lines if l is not None)
            # 确保以换行结尾
            if content and not content.endswith("\n"):
                content += "\n"
            target = chains_dir / f"{safe}.txt"
            target.write_text(content, encoding="utf-8")
            results.append(target)
        return results
