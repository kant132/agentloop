"""
test_chain_file_writer.py — ChainFileWriter 单元测试 (7 个)

覆盖:
  - write(): 生成单端点链文件, 路径格式 method:nodeid->method:nodeid
  - write_all(): 多端点/多变体分组写入
  - 文件名安全化 (特殊字符)
  - 空链处理
"""
from pathlib import Path

import pytest

from chain_file_writer import ChainFileWriter, safe_endpoint_name


def _node(fqn: str, node_id: str, **kw):
    base = {"fqn": fqn, "node_id": node_id}
    base.update(kw)
    return base


def _chain(entry: str, nodes):
    return {"entry_fqn": entry, "chain": list(nodes)}


# =========================================================================
# safe_endpoint_name
# =========================================================================

def test_safe_endpoint_name_replaces_unsafe_chars():
    """# 和 . 等特殊字符替换为 _ 产生 Windows 安全文件名"""
    name = safe_endpoint_name("com.example.Foo#bar")
    assert "/" not in name
    assert "\\" not in name
    assert "#" not in name
    assert ":" not in name
    assert len(name) > 0


def test_safe_endpoint_name_stable():
    """同一 fqn 两次调用返回相同结果"""
    a = safe_endpoint_name("org.owasp.X#m1")
    b = safe_endpoint_name("org.owasp.X#m1")
    assert a == b


# =========================================================================
# write()
# =========================================================================

def test_write_creates_chains_dir_and_file(tmp_path):
    """write 在 output_dir 下创建 chains/ 子目录及单个 .txt"""
    data = _chain("com.example.Foo#bar", [
        _node("com.example.Foo#bar", "n1"),
        _node("com.lib.Util#get", "n2"),
    ])
    out = ChainFileWriter.write(data, str(tmp_path))
    assert out.exists()
    assert out.parent.name == "chains"
    assert out.suffix == ".txt"


def test_write_line_format_fqn_colon_nodeid_arrow_joined(tmp_path):
    """文件内容 = fqn:nodeid->fqn:nodeid 一行"""
    data = _chain("com.example.Foo#bar", [
        _node("com.example.Foo#bar", "n1"),
        _node("com.lib.Util#get", "n2"),
    ])
    out = ChainFileWriter.write(data, str(tmp_path))
    text = out.read_text(encoding="utf-8")
    assert "com.example.Foo#bar:n1" in text
    assert "com.lib.Util#get:n2" in text
    assert "->" in text
    assert text.endswith("\n")


def test_write_returns_path(tmp_path):
    """write 返回写入的 Path 对象"""
    data = _chain("com.example.Foo#bar", [_node("com.example.Foo#bar", "n1")])
    out = ChainFileWriter.write(data, str(tmp_path))
    assert isinstance(out, Path)
    assert out.is_file()


def test_write_empty_chain_produces_file_with_no_path(tmp_path):
    """空链仍生成文件, 但内容为空行 (或空文件)"""
    data = _chain("com.example.Empty#m", [])
    out = ChainFileWriter.write(data, str(tmp_path))
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    # 空链: 不应包含 ->
    assert "->" not in text


# =========================================================================
# write_all()
# =========================================================================

def test_write_all_groups_by_endpoint(tmp_path):
    """多变体同端点 → 同一文件多行; 不同端点 → 多文件"""
    chains = [
        _chain("com.A#m", [_node("com.A#m", "a1"), _node("com.X#sink", "a2")]),
        _chain("com.A#m", [_node("com.A#m", "b1"), _node("com.Y#sink", "b2")]),
        _chain("com.B#m", [_node("com.B#m", "c1")]),
    ]
    outs = ChainFileWriter.write_all(chains, str(tmp_path))
    # 两个端点 → 两个文件
    assert len(outs) == 2
    # com.A 文件含两行 (两条链路)
    a_file = next(p for p in outs if "com_A" in p.name or "A_m" in p.name)
    a_lines = [l for l in a_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(a_lines) == 2


def test_write_all_returns_list_of_paths(tmp_path):
    """write_all 返回 list[Path], 全部存在"""
    chains = [_chain("com.A#m", [_node("com.A#m", "a1")])]
    outs = ChainFileWriter.write_all(chains, str(tmp_path))
    assert isinstance(outs, list)
    assert all(isinstance(p, Path) for p in outs)
    assert all(p.is_file() for p in outs)
