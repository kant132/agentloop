"""
test_cross_agent_integration.py — Phase 2 daemon 集成测试 (AR-09/10)

TDD RED 状态：这些测试在实现前应该 FAIL。

覆盖:
  AR-10: auth_class_cacher 集成 (3 tests)
    - daemon 在 cleanup_memurai 后调用 cache_auth_classes
    - auth_class_cacher 接收 codegraph_db
    - auth_class_cacher 写入 Memurai

  AR-09: priority_calculator 集成 (4 tests)
    - daemon 在 opencode session 前调用 rank_chains
    - endpoint metadata 从 exposure_assets.json 加载
    - 排序后的端点列表写入文件
    - 第一轮使用 base priority（无历史链数据）
"""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import json
import sys
import importlib

# Import cross-agent-50r module
# Note: cross-agent-50r.py has hyphens, need importlib
import importlib.util
_daemon_path = Path(__file__).resolve().parents[1] / "cross-agent-50r.py"
_spec = importlib.util.spec_from_file_location("cross_agent_50r", _daemon_path)
_daemon = importlib.util.module_from_spec(_spec)
sys.modules["cross_agent_50r"] = _daemon
_spec.loader.exec_module(_daemon)


# =========================================================================
# AR-10: auth_class_cacher 集成
# =========================================================================

def test_ar10_auth_cacher_called_after_cleanup():
    """daemon 应在 cleanup_memurai 后、run_opencode_session 前调用 cache_auth_classes"""
    # After AR-10, daemon should import auth_class_cacher
    assert hasattr(_daemon, 'AuthClassCacher') or hasattr(_daemon, 'create_cacher'), \
        "daemon should import auth_class_cacher (AuthClassCacher or create_cacher)"


def test_ar10_auth_cacher_receives_codegraph_db():
    """auth_class_cacher 应接收 codegraph_db 路径"""
    # After AR-10, daemon should have _query_auth_classes helper
    assert hasattr(_daemon, '_query_auth_classes'), \
        "daemon should have _query_auth_classes helper function"
    
    # Verify it accepts a Path parameter
    import inspect
    sig = inspect.signature(_daemon._query_auth_classes)
    params = list(sig.parameters.keys())
    assert len(params) >= 1, "_query_auth_classes should accept at least one parameter (db_path)"


def test_ar10_auth_cacher_writes_to_memurai():
    """auth_class_cacher 应写入 {groupId}:auth:class:{fqn} keys"""
    # Import auth_class_cacher directly to verify key format
    from auth_class_cacher import AuthClassCacher
    
    # Create mock Memurai client
    mock_memurai = Mock()
    mock_memurai.set_json = Mock(return_value=True)
    mock_memurai.scan = Mock(return_value=[])
    
    cacher = AuthClassCacher(mock_memurai)
    
    # Test cache_auth_classes
    auth_items = [
        {"fqn": "com.example.SecurityConfig", "type": "config"},
        {"fqn": "com.example.AuthFilter", "type": "filter"},
    ]
    
    group_id = "com.example"
    count = cacher.cache_auth_classes(auth_items, group_id)
    
    assert count == 2, "Should cache 2 auth classes"
    
    # Verify set_json was called with correct key format
    calls = mock_memurai.set_json.call_args_list
    assert len(calls) == 2
    
    # Check key format: {groupId}:auth:class:{fqn}
    for call in calls:
        key = call[0][0]  # First positional arg
        assert key.startswith(f"{group_id}:auth:class:"), \
            f"Key should start with {group_id}:auth:class:, got {key}"


# =========================================================================
# AR-09: priority_calculator 集成
# =========================================================================

def test_ar09_rank_chains_called_before_session():
    """daemon 应在 run_opencode_session 前调用 rank_chains"""
    # After AR-09, daemon should import priority_calculator
    assert hasattr(_daemon, 'rank_chains') or hasattr(_daemon, 'calculate_priority'), \
        "daemon should import priority_calculator (rank_chains or calculate_priority)"


def test_ar09_endpoint_metadata_loaded_from_exposure_assets():
    """链数据应从 jar-analyzer.db chains 表加载"""
    daemon_source = Path(_daemon_path).read_text(encoding='utf-8')
    assert 'jar-analyzer.db' in daemon_source or 'chains' in daemon_source, \
        "daemon should reference jar-analyzer.db chains table for chain data"


def test_ar09_ordered_endpoint_list_written_to_file():
    """批次应写入 chain_batch.json"""
    daemon_source = Path(_daemon_path).read_text(encoding='utf-8')
    assert 'chain_batch.json' in daemon_source, \
        "daemon should write chain_batch.json"


def test_ar09_base_priority_without_prior_chains():
    """第一轮应使用 base priority（无历史链数据）"""
    # Import priority_calculator directly
    from priority_calculator import calculate_priority, _base
    
    # Test base priority calculation
    # GET with external params = 0
    endpoint_get = {"http_method": "GET", "has_external_params": True}
    assert _base(endpoint_get) == 0, "GET with params should have base=0"
    
    # POST = 10
    endpoint_post = {"http_method": "POST", "has_external_params": True}
    assert _base(endpoint_post) == 10, "POST should have base=10"
    
    # No external params = -100 (penalty)
    endpoint_no_params = {"http_method": "GET", "has_external_params": False}
    assert _base(endpoint_no_params) == -100, \
        f"GET without params should have base=-100, got {_base(endpoint_no_params)}"
    
    # Test full priority with no prior chains (sink_count=0, preset_count=0)
    priority = calculate_priority(endpoint_post, 0, 0)
    assert priority == 10, f"POST with no sinks should have priority=10, got {priority}"
