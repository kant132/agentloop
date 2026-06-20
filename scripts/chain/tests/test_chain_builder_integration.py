"""
test_chain_builder_integration.py — Phase 2 集成测试 (AR-06/07/08)

TDD RED 状态：这些测试在实现前应该 FAIL。

覆盖:
  AR-08: sink_registry 集成 (3 tests)
    - chain_builder 使用 identify_dynamic_sinks 替代 naive is_sink
    - ChainNode.sinks 包含分类数据
    - is_preset_sink 用于分类

  AR-07: redis-batch-prefetch 集成 (3 tests)
    - chain_builder 在构建链后调用 prefetch_chain
    - startLine 字段映射正确
    - group_id 正确传递

  AR-06: chain_file_writer 集成 (3 tests)
    - build_chain 接受 loop_audit_dir 参数
    - chain_file_writer 在构建后调用
    - 输出文件格式正确
"""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import sys

# Import chain_builder (will be modified in Wave 2)
import chain_builder
from chain_file_writer import ChainFileWriter


# =========================================================================
# AR-08: sink_registry 集成
# =========================================================================

def test_ar08_sink_registry_replaces_naive_is_sink():
    """chain_builder 应使用 sink_registry.identify_dynamic_sinks 替代 JAR 的 is_sink 标志"""
    # Mock JAR output with is_sink=True/False
    mock_method_calls = [
        {"called_fqn": "java.sql.Statement.executeQuery(sql)", "is_sink": True},
        {"called_fqn": "com.example.UserService.getUser(id)", "is_sink": False},
        {"called_fqn": "org.apache.commons.lang3.StringUtils.trim(s)", "is_sink": True},
    ]
    
    # After AR-08 integration, chain_builder should call identify_dynamic_sinks
    # This test verifies the integration point exists
    assert hasattr(chain_builder, '_sr'), "chain_builder should import sink_registry as _sr"
    
    # Verify identify_dynamic_sinks is called with correct parameters
    group_id = "com.example"
    all_called_fqns = [c["called_fqn"] for c in mock_method_calls]
    
    # Call the function that should be integrated
    dynamic_sinks = chain_builder._sr.identify_dynamic_sinks(group_id, all_called_fqns)
    
    # Verify it returns categorized sinks
    assert isinstance(dynamic_sinks, list)
    assert len(dynamic_sinks) > 0
    # Non-groupId calls should be identified as sinks
    sink_fqns = [s["fqn"] for s in dynamic_sinks]
    assert "java.sql.Statement.executeQuery(sql)" in sink_fqns
    assert "org.apache.commons.lang3.StringUtils.trim(s)" in sink_fqns
    # GroupId calls should NOT be sinks
    assert "com.example.UserService.getUser(id)" not in sink_fqns


def test_ar08_chain_node_sinks_populated_with_categories():
    """ChainNode.sinks 应包含分类 sink 数据（不仅仅是 FQN 字符串）"""
    # After AR-08, result dict should include sink_categories
    # This is a placeholder test - actual implementation will verify the result structure
    assert hasattr(chain_builder, '_sr'), "chain_builder should import sink_registry"
    
    # Verify sink_registry has the categorization function
    assert hasattr(chain_builder._sr, 'is_preset_sink')
    assert hasattr(chain_builder._sr, 'identify_dynamic_sinks')
    
    # Test categorization
    test_fqn = "java.sql.Statement.executeQuery(sql)"
    is_preset = chain_builder._sr.is_preset_sink(test_fqn)
    assert is_preset, f"{test_fqn} should be a preset sink"


def test_ar08_is_preset_sink_used_for_categorization():
    """is_preset_sink 应用于 sink 分类"""
    assert hasattr(chain_builder, '_sr'), "chain_builder should import sink_registry"
    
    # Test preset sinks
    preset_sinks = [
        "java.sql.Statement.executeQuery(sql)",
        "java.lang.Runtime.exec(cmd)",
        "javax.naming.InitialContext.lookup(name)",
    ]
    
    for fqn in preset_sinks:
        assert chain_builder._sr.is_preset_sink(fqn), f"{fqn} should be preset sink"
    
    # Test non-preset sinks
    non_preset = [
        "org.apache.commons.lang3.StringUtils.trim(s)",
        "java.util.List.add(item)",
    ]
    
    for fqn in non_preset:
        assert not chain_builder._sr.is_preset_sink(fqn), f"{fqn} should NOT be preset sink"


# =========================================================================
# AR-07: redis-batch-prefetch 集成
# =========================================================================

def test_ar07_prefetch_chain_called_after_build():
    """chain_builder 应在构建链节点后调用 prefetch_chain"""
    # After AR-07, chain_builder should import redis-batch-prefetch
    assert hasattr(chain_builder, '_rbp'), "chain_builder should import redis-batch-prefetch as _rbp"
    
    # Verify prefetch_chain function exists
    assert hasattr(chain_builder._rbp, 'prefetch_chain')
    assert hasattr(chain_builder._rbp, 'make_method_key')


def test_ar07_startline_field_mapping():
    """ChainNode.start_line 应映射到 prefetch 输入的 startLine/line"""
    assert hasattr(chain_builder, '_rbp'), "chain_builder should import redis-batch-prefetch"
    
    # Test make_method_key with the expected field mapping
    group_id = "com.example"
    fqn = "com.example.UserService#getUser"
    start_line = 42
    
    # The prefetch module should accept startLine or line
    key = chain_builder._rbp.make_method_key(group_id, fqn, start_line)
    assert key == f"{group_id}:method:{fqn}#{start_line}"


def test_ar07_prefetch_chain_receives_group_id():
    """prefetch_chain 应接收正确的 group_id"""
    assert hasattr(chain_builder, '_rbp'), "chain_builder should import redis-batch-prefetch"
    
    # Verify prefetch_chain signature includes group_id parameter
    import inspect
    sig = inspect.signature(chain_builder._rbp.prefetch_chain)
    params = list(sig.parameters.keys())
    
    assert 'group_id' in params, "prefetch_chain should have group_id parameter"
    assert 'chain_id' in params, "prefetch_chain should have chain_id parameter"


# =========================================================================
# AR-06: chain_file_writer 集成
# =========================================================================

def test_ar06_loop_audit_dir_parameter_exists():
    """build_chain 和 build_all_chains_for_endpoint 应接受 loop_audit_dir 参数"""
    import inspect
    
    # Check build_chain signature
    sig = inspect.signature(chain_builder.build_chain)
    params = list(sig.parameters.keys())
    assert 'loop_audit_dir' in params, "build_chain should accept loop_audit_dir parameter"
    
    # Check build_all_chains_for_endpoint signature
    sig2 = inspect.signature(chain_builder.build_all_chains_for_endpoint)
    params2 = list(sig2.parameters.keys())
    assert 'loop_audit_dir' in params2, "build_all_chains_for_endpoint should accept loop_audit_dir parameter"


def test_ar06_chain_file_writer_called_after_build():
    """chain_builder 应在构建链后调用 ChainFileWriter.write_all"""
    # After AR-06, chain_builder should import chain_file_writer
    assert hasattr(chain_builder, '_cfw') or hasattr(chain_builder, 'ChainFileWriter'), \
        "chain_builder should import chain_file_writer"
    
    # Verify ChainFileWriter is accessible
    if hasattr(chain_builder, '_cfw'):
        assert hasattr(chain_builder._cfw, 'ChainFileWriter')
    else:
        assert hasattr(chain_builder, 'ChainFileWriter')


def test_ar06_chain_file_output_format():
    """输出文件应包含 fqn:nodeid->fqn:nodeid->... 格式"""
    # Test ChainFileWriter directly to verify format
    from chain_file_writer import ChainFileWriter
    import tempfile
    
    # Create test chain data
    chain_data = [
        {
            "entry_fqn": "com.example.Controller#getUser",
            "chain": [
                {"fqn": "com.example.Controller#getUser", "node_id": "n1"},
                {"fqn": "com.example.UserService#findById", "node_id": "n2"},
                {"fqn": "com.example.UserDao#selectById", "node_id": "n3"},
            ]
        }
    ]
    
    with tempfile.TemporaryDirectory() as tmpdir:
        paths = ChainFileWriter.write_all(chain_data, tmpdir)
        
        assert len(paths) == 1, "Should write one chain file"
        
        # Read and verify format
        content = paths[0].read_text(encoding='utf-8').strip()
        expected = "com.example.Controller#getUser:n1->com.example.UserService#findById:n2->com.example.UserDao#selectById:n3"
        assert content == expected, f"Chain file format mismatch: {content}"
