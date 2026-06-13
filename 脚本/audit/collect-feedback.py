"""collect-feedback.py

**项目结束反馈收集** (用户 2026-06-13 修订: 不是每轮反馈, 是项目结束一次性反馈)

审计项目 run 完后, opencode worker 一次性分析所有 round 数据, 写反馈到:
  项目/{groupId}/审计反馈报告（通过分析本次任务agentloop执行过程和结结果，提出不足之处）.md

流程:
1. 读 preset.json 拿 groupId / projectRoot / log_dir
2. 读所有 round{N}.json (LOG_DIR)
3. 派发 opencode session, 短消息: "Analyze all rounds, write feedback"
4. opencode 读 round json + 路由报告 + diag artifacts, 输出 ## Project Run 段
5. 沉底追加到 feedback.md (如无则从 _template 复制)

用法:
  # 独立调用
  python 脚本/audit/collect-feedback.py

  # 让 daemon 主循环结束自动调
  AGENTLOOP_FINAL_FEEDBACK=1 python 脚本/audit/cross-agent-50r.py
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# 复用 cross-agent-50r 的 preset 加载 + 常量
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PRESETS_DIR = _REPO_ROOT / "项目"
_TEMPLATES_DIR = _PRESETS_DIR / "_template"

OPENCODE_CMD = r"C:\Users\Administrator\AppData\Roaming\npm\opencode.cmd"
FEEDBACK_TIMEOUT = 900  # 15 min 硬上限 (用户 2026-06-13 规定)


def load_preset() -> dict:
    """同 cross-agent-50r.py 的 load_preset, 独立可调用。"""
    p = os.environ.get("AGENTLOOP_PRESET")
    if not p:
        default = _PRESETS_DIR / "org.owasp.webgoat" / "preset.json"
        if default.exists():
            p = str(default)
        else:
            raise FileNotFoundError(
                f"未指定 preset.json。请:\n"
                f"  1. 设 env AGENTLOOP_PRESET=项目/<groupId>/preset.json\n"
                f"  或 2. cp 项目/_template/preset.template.json 项目/<groupId>/preset.json"
            )
    pf = Path(p)
    if not pf.exists():
        raise FileNotFoundError(f"preset.json 不存在: {pf}")
    return json.loads(pf.read_text(encoding="utf-8"))


def collect_rounds(log_dir: Path) -> list:
    """**读所有 round{N}.json** (用户 2026-06-13 反馈分析的数据源)。"""
    rounds = []
    for f in sorted(log_dir.glob("round*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            rounds.append(d)
        except Exception as e:
            print(f"  [WARN] 读 {f.name} 失败: {e}")
    return rounds


def get_feedback_path(preset: dict) -> Path:
    """反馈文件路径: 项目/{groupId}/审计反馈报告...md"""
    group = preset["groupId"]
    return _PRESETS_DIR / group / "审计反馈报告（通过分析本次任务agentloop执行过程和结结果，提出不足之处）.md"


def ensure_feedback_md(preset: dict) -> Path:
    """**首次时从 _template 复制**, 后续直接用。"""
    fb_path = get_feedback_path(preset)
    if fb_path.exists():
        return fb_path
    # 从 _template 复制 + 替换 groupId
    tpl = _TEMPLATES_DIR / "审计反馈报告（通过分析本次任务agentloop执行过程和结结果，提出不足之处）.md"
    if not tpl.exists():
        # 模板缺失, 创建一个最小骨架
        fb_path.parent.mkdir(parents=True, exist_ok=True)
        fb_path.write_text(
            f"# {preset['groupId']} 审计反馈报告\n\n## 元信息\n\n- groupId: `{preset['groupId']}`\n\n## Project Run 1 - {datetime.now().date()} - TBD\n",
            encoding="utf-8",
        )
        return fb_path
    content = tpl.read_text(encoding="utf-8")
    content = content.replace("{{groupId}}", preset["groupId"])
    content = content.replace("{{date}}", str(datetime.now().date()))
    content = content.replace("{{firstRunDate}}", str(datetime.now().date()))
    content = content.replace("{{latestRunDate}}", str(datetime.now().date()))
    content = content.replace("{{totalRuns}}", "1")
    content = content.replace("{{nRounds}}", "TBD")
    content = content.replace("{{status}}", "TBD")
    fb_path.parent.mkdir(parents=True, exist_ok=True)
    fb_path.write_text(content, encoding="utf-8")
    return fb_path


def build_feedback_prompt(preset: dict, rounds: list, log_dir: Path) -> str:
    """**构造反馈 prompt** (发给 opencode)。

    4 段必含:
    1. 跑通了什么
    2. 卡在哪里 (3 个最大瓶颈, 按 round 顺序)
    3. 改进建议 (P0/P1/P2)
    4. 跨 run 趋势
    """
    rounds_summary = []
    for r in rounds:
        rounds_summary.append({
            "round": r.get("round"),
            "elapsed": r.get("elapsed"),
            "p54_pass": r.get("p54_pass"),
            "n_hi": r.get("n_hi"),
            "n_lo": r.get("n_lo"),
            "n_poc": r.get("n_poc"),
            "n_redis_audit_keys": r.get("n_redis_audit_keys"),
            "rc": r.get("rc"),
        })
    rounds_json = json.dumps(rounds_summary, ensure_ascii=False, indent=2)
    fb_path = get_feedback_path(preset)

    return f"""你是 agentloop 工具的审计执行 worker, 刚跑完 {preset['groupId']} 的稳定性实验。

## 数据源

**所有 round 的总结** (read-only, 不要改这些):
```json
{rounds_json}
```

**round 详细 json** 路径: `{log_dir}/round{{N:02d}}.json` (N = 1..{len(rounds)})

**路由报告** 路径: `{preset['projectRoot']}/loop_audit/routes/{{高风险端点,中低险端点,poc}}/`
**诊断 artifact** 路径: `{preset['projectRoot']}/loop_audit/diag/`

## 任务

分析所有 round 数据, 写**项目级反馈**到:
  `{fb_path}`

**必含 4 段** (用中文, 简明扼要, 每个 ≤ 200 字):

### 1. 跑通了什么 (工具优点)
- 列出 3-5 个具体优点, 每条 ≤ 1 句, 附 file:line 证据

### 2. 卡在哪里 (3 个最大瓶颈, 按 round 顺序)
- 格式: `Round N: 卡在 <哪一步>, 原因: <为什么>, 后果: <导致什么>, 证据: <file:line>`
- 选 3 个最严重的, 不要列所有

### 3. 改进建议 (P0/P1/P2 排序)
- P0 (必修, 阻断性): 1-3 条
- P1 (关注, 严重影响准确率): 1-3 条
- P2 (待观察): 1-3 条
- 每条建议必具体到**代码改动位置** (e.g. "cross-agent-50r.py 第 N 行 COMMAND_TEMPLATE 加占位符")

### 4. 跨 run 趋势 (与上一次 run 比)
- 如无上次 run 数据, 写"首次 run, 无对比"
- 改善 / 退化 / 待观察 各列 1-2 条

## 输出格式

写到 `{fb_path}`, **沉底追加** 1 个新段, 严格用这个模板 (替换 {{}}):

```markdown

---

## Project Run {datetime.now().date()} - {len(rounds)} rounds - {{总状态}}

### 1. 跑通了什么
- ...

### 2. 卡在哪里
- Round {{N}}: 卡在 <...>, 原因: <...>, 后果: <...>, 证据: <file:line>
- ...

### 3. 改进建议
- P0: ...
- P1: ...
- P2: ...

### 4. 跨 run 趋势
- 改善: ...
- 退化: ...
- 待观察: ...
```

**重要**:
- 不要删/改历史 Project Run 段
- 不要写"终态汇总"等其他段
- 不要写代码修复建议**实现** (这是 agentloop 主 agent 的事, 不是 worker 的事)
- 写完**必读自己写的段**, 确认 4 段都齐, 证据真实, 然后回报 1 行: "feedback 写入 {fb_path}, {N} 段"
"""


def run_feedback_session(preset: dict, rounds: list, log_dir: Path) -> dict:
    """**派发 opencode 反馈 session** (1 次, 非每轮)。"""
    fb_path = ensure_feedback_md(preset)
    prompt = build_feedback_prompt(preset, rounds, log_dir)

    prompt_file = Path(rf"C:\Users\ADMINI~1\AppData\Local\Temp\wgb-feedback-{int(time.time())}.txt")
    prompt_file.write_text(prompt, encoding="utf-8")

    short_msg = f"Project-end feedback for {preset['groupId']}. Read attached file and write to {fb_path}."
    cmd = [
        OPENCODE_CMD, "run", short_msg,
        "--model", "alibaba-cn/qwen3.7-max",
        "--agent", "Sisyphus - ultraworker",
        "--title", f"WGB-FEEDBACK-{preset['groupId']}",
        "--file", str(prompt_file),
    ]
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace",
            timeout=FEEDBACK_TIMEOUT,
        )
        elapsed = time.time() - t0
        return {
            "rc": proc.returncode,
            "elapsed": round(elapsed, 1),
            "stdout_tail": proc.stdout[-500:] if proc.stdout else "",
            "stderr_tail": proc.stderr[-300:] if proc.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"rc": -1, "elapsed": FEEDBACK_TIMEOUT, "error": "timeout"}
    except Exception as e:
        return {"rc": -1, "elapsed": time.time() - t0, "error": str(e)}


def main():
    print(f"=== 项目结束反馈收集 ===")
    preset = load_preset()
    print(f"groupId: {preset['groupId']}")
    print(f"projectRoot: {preset['projectRoot']}")

    log_dir = Path(preset['projectRoot']) / preset.get('loopDir', 'loop_audit') / 'loop-log' / 'cross-50r'
    if not log_dir.exists():
        print(f"[FAIL] log 目录不存在: {log_dir}")
        print(f"  → 跑过 cross-agent-50r.py 吗?")
        return 1

    rounds = collect_rounds(log_dir)
    if not rounds:
        print(f"[FAIL] 无 round 数据 ({log_dir}/round*.json)")
        return 1
    print(f"读到 {len(rounds)} 个 round")

    print(f"\n派发 opencode 反馈 session (上限 {FEEDBACK_TIMEOUT}s)...")
    result = run_feedback_session(preset, rounds, log_dir)
    print(f"\n=== 反馈 session 结果 ===")
    print(f"rc: {result.get('rc')}")
    print(f"elapsed: {result.get('elapsed')}s")
    if result.get("error"):
        print(f"error: {result['error']}")
    if result.get("stdout_tail"):
        print(f"stdout_tail: {result['stdout_tail'][-300:]}")

    fb_path = get_feedback_path(preset)
    if fb_path.exists():
        size = fb_path.stat().st_size
        n_sections = fb_path.read_text(encoding="utf-8").count("## Project Run")
        print(f"\n反馈文件: {fb_path}")
        print(f"  size: {size} chars, Project Run 段数: {n_sections}")

    return 0 if result.get("rc") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
