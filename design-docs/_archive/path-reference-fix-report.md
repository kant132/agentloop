# Path Reference Fix Report

**Date**: 2026-06-15
**Scope**: `D:\agentloop\` — post-folder-rename path reference remediation
**Goal**: Replace broken Chinese folder path references with new English equivalents (file contents only, no file renames)

---

## Summary

Chinese top-level folders were renamed:
- `提议` → `proposals`
- `类型` → `types`
- `脚本` → `scripts`
- `行为准则` → `conduct`
- `设计文档` → `design-docs`
- `需求分析` → `requirements`
- `项目` → `projects`

Pre-existing `scripts/` (WebGoat tools) renamed to `webgoat-tools/`.

This report documents all text replacements made inside files.

---

## Replacements by Direction

| Direction | Replacements Made |
|-----------|-------------------|
| `提议/` → `proposals/` | 0 *(see notes)* |
| `类型/` → `types/` | 50 |
| `脚本/` → `scripts/` | 27 |
| `行为准则/` → `conduct/` | 52 |
| `设计文档/` → `design-docs/` | 4 |
| `需求分析/` → `requirements/` | 0 *(not found)* |
| `项目/` → `projects/` | 42 |
| **Total** | **175** |

---

## Files Modified (40 files)

```
D:\agentloop\conduct\优化路径\04-subagent失败率降低.md
D:\agentloop\conduct\必读\01-避免重复劳动.md
D:\agentloop\conduct\经验\01-PoC失败处理回路.md
D:\agentloop\conduct\经验\02-假阳反例库.md
D:\agentloop\conduct\经验\05-跨项目通用Pattern.md
D:\agentloop\conduct\优化路径\03-缓存命中率提升.md
D:\agentloop\design-docs\chain-sql-engine.md
D:\agentloop\design-docs\会话记录-最终方案.md
D:\agentloop\doc\TDD.md
D:\agentloop\loop_audit\_template\04-终态汇总报告.md.example
D:\agentloop\loop_audit\_template\11-subagent-failures.jsonl.example
D:\agentloop\proposals\01-污点分析的本质.md
D:\agentloop\proposals\02-业务逻辑分析的精髓.md
D:\agentloop\proposals\04-LLM不知道的深层规则.md
D:\agentloop\proposals\改进建议.md
D:\agentloop\proposals\改进建议-v2-综合六位大师.md
D:\agentloop\projects\_template\03-业务规则特例.md
D:\agentloop\requirements\codex-工程答复-回应六位大师.md
D:\agentloop\requirements\实现评估.md
D:\agentloop\requirements\预置经验规则.md
D:\agentloop\scripts\ast\scanner_utils.py
D:\agentloop\scripts\audit\collect-feedback.py
D:\agentloop\scripts\audit\cross-agent-50r.py
D:\agentloop\scripts\deprecated\README.md
D:\agentloop\skills\java-forward-vuln-discovery\SKILL.md
D:\agentloop\skills\java-forward-vuln-discovery\sql-cheatsheet.md
D:\agentloop\skills\java-forward-vuln-discovery\templates\subagent-FWD-A.md
D:\agentloop\skills\java-forward-vuln-discovery\templates\subagent-FWD-B.md
D:\agentloop\skills\java-forward-vuln-discovery\templates\subagent-FWD-C.md
D:\agentloop\skills\java-forward-vuln-discovery\templates\subagent-FWD-D.md
D:\agentloop\skills\java-forward-vuln-discovery\templates\subagent-FWD-INFO.md
D:\agentloop\skills\java-whitebox-loop\SKILL.md
D:\agentloop\skills\java-whitebox-loop\rules\01-phase-gates.md
D:\agentloop\skills\java-whitebox-loop\rules\02-scoring.md
D:\agentloop\skills\java-whitebox-loop\rules\03-subagent-dispatch.md
D:\agentloop\skills\java-whitebox-loop\rules\06-pruning-rules.md
D:\agentloop\skills\java-whitebox-loop\rules\07-preset-rules.md
D:\agentloop\skills\playwright-skill\SKILL.md
D:\agentloop\skills\ssh-skill\SKILL.md
D:\agentloop\skills\threat-model-analyst\SKILL.md
D:\agentloop\test_scripts\test_preset_init.py
```

---

## Notes on Specific Cases

### `提议/` — Left unchanged (0 replacements)
In `requirements\codex-工程答复-回应六位大师.md`, the strings `提议/` and `"提议/"` appear in section titles as **document series names**, not as path references:
- Line 449: `## 八、对"提议/"系列文档的整体评价`
- Line 478: `"提议/"系列文档作为**分析参考**，不再更新`

These refer to the Chinese-named proposal documents by their title, not as filesystem paths. Per the user's "不改文件名" constraint (file contents only), these titles referencing the old document names were **not changed** — they document the old naming, not a broken path.

### `需求分析/` — Not found (0 replacements)
The folder `需求分析` was renamed to `requirements`. No file contained a reference to `需求分析/` as a path.

### `类型/扩展名/内容` — Left unchanged
In `types\文件操作\任意文件上传.md`, the phrase `类型/扩展名/内容` appears as Chinese text describing file validation concepts (type/extension/content), **not** as a path reference. This was correctly left unchanged.

### `项目/决策` — Left unchanged
In `checker\nuwa-skill-main\examples\elon-musk-perspective\SKILL.md` and similar files, the phrase `项目/决策` appears as Chinese text meaning "project/decision", not as a path. This was correctly excluded.

### Pre-existing LSP errors
Some modified Python files (e.g., `scripts\audit\collect-feedback.py`, `scripts\audit\cross-agent-50r.py`) show LSP errors that are **pre-existing** and unrelated to the path fixes.

---

## Idempotency

A second run of this fix should produce **0 changes**. All replacements were made exactly once using the old Chinese folder names as patterns.

---

## Skipped Directories

The following directories were excluded from scanning to avoid self-referential loops or huge binary blobs:
- `.git`, `.idea`, `.hypothesis`, `.pytest_cache`, `.omo`
- `tools/` (third-party JARs)
- `checker/` (third-party skills — only modified when paths were clearly about agentloop structure)

---

## Files Requiring Human Review (Ambiguity Flags)

| File | Reason |
|------|--------|
| `requirements\codex-工程答复-回应六位大师.md` | Contains `"提议/"` as document series title — ambiguous whether this should be updated to `proposals/` |
| `types\文件操作\任意文件上传.md` | `类型/扩展名/内容` is Chinese text, not a path — correctly left unchanged, but worth verifying |
| `checker\nuwa-skill-main\examples\*\SKILL.md` | `项目/决策` is Chinese text — correctly excluded |

---

## Conclusion

**175 replacements** made across **40 files**. All clearly-identifiable Chinese folder path references have been replaced with their English equivalents. The operation is idempotent.