# TDD 测试矩阵

> 基于现存测试模块。旧版 Doc/archive/TDD.md 已归档（引用不存在的脚本）。

## 运行测试

```powershell
# 全量测试
$env:PYTHONIOENCODING="utf-8"
python -m pytest scripts/exposure/collectors/tests/ scripts/exposure/tests/ scripts/chain/tests/ scripts/analysis/tests/ scripts/redis/tests/ -q

# 单独运行某个模块
python -m pytest scripts/redis/tests/ -v

# 覆盖率
python -m pytest scripts/redis/tests/ --cov=scripts/redis/memurai_client --cov-report=term-missing
```

> scripts/tests/ 和 test_scripts/ 因 conftest 路径冲突不能同时运行，分两批跑。

## 测试模块清单

| 目录 | 测试文件数 | 测试数 | 覆盖模块 |
|------|-----------|--------|---------|
| `scripts/exposure/collectors/tests/` | 8 | 77 | 8 个 collector |
| `scripts/exposure/tests/` | 2 | 18 | synthesizer + hotspot_ranker |
| `scripts/chain/tests/` | 4 | 30 | chain_file_writer + sink_registry + priority + auth_cacher |
| `scripts/analysis/tests/` | 4 | 54 | method_body_loader + load_counter + report_gen + metric_simplifier |
| `scripts/redis/tests/` | 1 | 71 | memurai_client（覆盖率 87%） |
| `scripts/tests/` | 2 | 25 | scanner_utils + self_evolution_merge |

**总计：21 个测试文件，275 个测试用例**

## conftest 路径说明

P0-3 已修复：conftest.py 中的中文路径 "脚本" 已改为 "scripts"。

## 添加新测试

1. 在对应模块的 tests/ 子目录创建 test_*.py
2. 用 unittest.mock.patch mock 外部依赖
3. 不真连外部服务
4. 跑 python -m pytest <新测试路径> -v 验证
