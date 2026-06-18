# AGENTS.md — scripts/

审计流水线核心执行引擎。6 个功能子目录 + 1 归档 + 1 测试。

## 结构

```
scripts/
├── ast/            # 攻击面扫描（1269行主入口 + scanner_utils + JPype桥接 + ast-grep/sanitizer finder）
├── audit/          # 守护进程编排（cross-agent-50r.py 332行主入口 + PoC监控 + 自进化 + 覆盖率验证）
├── chain/          # 调用链引擎（chain_builder 902行 + CTE递归提取 + 20 LEFT JOIN多跳 + 方法调用抽取）
├── redis/          # Memurai缓存层（memurai_client 482行 CLI封装 + 批预取 + 状态追踪 + 自检）
├── tests/          # pytest单元测试（scanner_utils 14个测试）
├── webgoat/        # WebGoat专用辅助脚本（OpenAPI生成 + 连通检查 + 路径探测）
└── deprecated/     # 仅README.md，废弃脚本归档
```

## 入口脚本

| 脚本 | 行数 | 调用方式 | 角色 |
|------|------|---------|------|
| `audit/cross-agent-50r.py` | 332 | `python scripts/audit/cross-agent-50r.py --preset {preset.json}` | **主守护进程** — 50轮循环编排 |
| `ast/attack_surface_scanner.py` | 1269 | `python scripts/ast/attack_surface_scanner.py --preset {preset.json}` | Phase 1 攻击面扫描 |
| `chain/chain_builder.py` | 902 | `python scripts/chain/chain_builder.py --preset {preset.json} --endpoint {ep}` | Phase 2 调用链构建 |
| `redis/redis-batch-prefetch.py` | 157 | 被chain_builder调用 | 方法体批量预取到Memurai |

## 依赖图

```
cross-agent-50r.py
  ├── check_core_tools.py      (启动守卫)
  ├── self_evolution.py         (收敛+评分+知识合并)
  ├── poc-monitor.py            (subprocess.Popen 后台)
  ├── verify-endpoint-coverage.py (subprocess.run)
  └── scripts.redis.memurai_client

chain_builder.py
  ├── sqlite-extract-chain.py   (importlib, 连字符模块名)
  ├── method_calls_extractor.py (importlib)
  └── scanner_utils.py          (sys.path回退)

attack_surface_scanner.py
  └── scanner_utils.py          (同目录import)
```

## 非标准模式

1. **Memurai CLI替代pip redis** — 全程subprocess调`memurai-cli.exe`，`--pipe`发RESP协议流
2. **importlib加载连字符模块** — chain_builder用`importlib.util.spec_from_file_location()`
3. **chr()构造中文字符串** — audit-poc-quality.py/sample-poc-for-boss.py规避Windows编码冲突
4. **多路径sys.path.insert** — 无统一包管理，各脚本自行注入
5. **conftest.py引用已废弃中文路径** — `"脚本"` → 实际应为`"scripts"`

## 注意

- `memurai_client.py` 是缓存单点，修改需回归测试
- `webgoat/` 是项目专用脚本，违反"不过拟合"原则，建议迁移到`projects/org.owasp.webgoat/`
- `scripts/tests/conftest.py` 的sys.path指向`"脚本"`（中文），需修复为`"scripts"`
