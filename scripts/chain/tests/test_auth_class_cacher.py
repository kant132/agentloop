"""
test_auth_class_cacher.py — AuthClassCacher 单元测试 (7 个)

用 MockMemurai 客户端, 不真连 Memurai。

覆盖:
  - cache_auth_classes: 写 N 条, 返回数量, key 格式正确
  - get_cached_auth_class: 命中返回 dict, 未命中返回 None
  - list_all_auth_classes: scan 出 fqn 列表
  - 缺 fqn 条目跳过
"""
import json

import pytest

from auth_class_cacher import AuthClassCacher


class MockMemurai:
    """模拟 memurai_client.Memurai, 实现 set_json/get_json/scan 三个鸭子方法。"""

    def __init__(self):
        self.store = {}        # key -> json str

    def set_json(self, key, obj, ex=None):
        self.store[key] = json.dumps(obj, ensure_ascii=False)
        return True

    def get_json(self, key):
        raw = self.store.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    def scan(self, pattern):
        # 简化: 支持 * 通配前缀匹配
        import fnmatch
        return [k for k in self.store.keys() if fnmatch.fnmatch(k, pattern)]


# =========================================================================
# cache_auth_classes
# =========================================================================

def test_cache_writes_all_items_and_returns_count():
    """3 条有效 item → 写入 3 条, 返回 3"""
    cli = MockMemurai()
    cacher = AuthClassCacher(cli)
    items = [
        {"fqn": "com.a.LoginFilter", "type": "filter"},
        {"fqn": "com.a.AuthInterceptor", "type": "interceptor"},
        {"fqn": "com.a.JwtProvider", "type": "provider"},
    ]
    n = cacher.cache_auth_classes(items, "com.a")
    assert n == 3
    assert len(cli.store) == 3


def test_cache_skips_items_without_fqn():
    """缺 fqn 的条目跳过, 不计入返回值"""
    cli = MockMemurai()
    cacher = AuthClassCacher(cli)
    items = [
        {"fqn": "com.a.X", "type": "filter"},
        {"type": "no-fqn"},          # 缺 fqn
        {"fqn": "", "type": "x"},    # 空 fqn
        {"fqn": "com.a.Y"},
    ]
    n = cacher.cache_auth_classes(items, "com.a")
    assert n == 2


def test_cache_key_format():
    """key 格式 = {group_id}:auth:class:{fqn}"""
    cli = MockMemurai()
    cacher = AuthClassCacher(cli)
    cacher.cache_auth_classes([{"fqn": "com.a.Filter#init"}], "org.test")
    assert "org.test:auth:class:com.a.Filter#init" in cli.store


# =========================================================================
# get_cached_auth_class
# =========================================================================

def test_get_returns_dict_when_present():
    """命中 → 返回原 dict"""
    cli = MockMemurai()
    cacher = AuthClassCacher(cli)
    item = {"fqn": "com.a.LoginFilter", "type": "filter", "order": 1}
    cacher.cache_auth_classes([item], "com.a")
    got = cacher.get_cached_auth_class("com.a.LoginFilter", "com.a")
    assert got == item


def test_get_returns_none_when_missing():
    """未命中 → None"""
    cli = MockMemurai()
    cacher = AuthClassCacher(cli)
    assert cacher.get_cached_auth_class("com.a.NoSuch", "com.a") is None


# =========================================================================
# list_all_auth_classes
# =========================================================================

def test_list_returns_fqns_from_scan():
    """scan 出所有 auth class 的 fqn 列表"""
    cli = MockMemurai()
    cacher = AuthClassCacher(cli)
    cacher.cache_auth_classes(
        [{"fqn": "com.a.X"}, {"fqn": "com.a.Y#m"}, {"fqn": "com.a.Z"}],
        "org.test",
    )
    fqns = cacher.list_all_auth_classes("org.test")
    assert set(fqns) == {"com.a.X", "com.a.Y#m", "com.a.Z"}


def test_list_returns_empty_when_none():
    """无缓存 → 空列表"""
    cli = MockMemurai()
    cacher = AuthClassCacher(cli)
    assert cacher.list_all_auth_classes("org.empty") == []
