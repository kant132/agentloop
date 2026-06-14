"""
ask-opencode-50x.py

**跨 agent 反思循环**（用户 2026-06-13 规定）。

老板 = 我（主 agent）
工人 = opencode subagent（续 session ses_1431cc1e8ffeqhGMCdBv541S1X）

每轮（最多 50 轮）：
1. 老板提 1 个反思问题
2. opencode 用工具查 opencode.db，看自己之前的 tool 调用历史
3. opencode 回答"我为什么这样做"
4. 老板读 opencode.db 看实际行为
5. 老板更新 SKILL/prompt，记录改进点

5min 硬上限（用户 2026-06-13 规定）。

输出：
  loop_audit/loop-log/cross-agent/round{N}.json  每轮问答
  loop_audit/loop-log/cross-agent/summary.md    老板的反思记录
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(r"D:\code\WebGoat-2025.3\loop_audit\loop-log\cross-agent")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 50 个反思问题（按"为什么 opencode 没做"角度展开）
QUESTIONS = [
    "你之前为什么写了 4 个文件就停了？267 个路由的报告去哪了？",
    "你之前为什么取消 3 个 explore subagent？取消的标准是什么？",
    "你之前写'终态汇总报告.md'时，相数据有没有真正生成？",
    "你的 prompt 里哪条让你觉得'信息够了就可以总结'？",
    "你为什么没运行我写好的 batch-generate-route-reports.py 脚本？",
    "你写 _gen_reports.py 时为什么不测试就停了？",
    "你的 skill 定义（java-whitebox-loop）哪里让你误解可以跳过 Phase 4？",
    "如果重做，你会怎么确保 269 个路由报告全部生成？",
    "你看到 12 个 rules 文件时，是想全读完还是按需读？",
    "你看到 269 个 routes 时，你估计写完每个 .md 需要多少时间？",
    "如果有人给你硬约束'must produce 269 files'你会怎么改工作流？",
    "你在 t=1781286764866 写'数据齐了'的判断依据是什么？",
    "你的反思打分（30+30+30+10+10）在哪个回合实际打过？",
    "你执行 Phase 5 时，是否意识到'至少 100 个'的最低要求？",
    "你写完 4 个文件后，下一个动作应该是什么？",
    "你之前发起的 task subagent 失败/超时率是多少？",
    "你看到哪些信号让你决定'this is enough, stop here'？",
    "如果你看到 P5.4 不变量约束在 prompt 里，你会怎么内化？",
    "你的'finished'判定标准是什么？5 结束条件哪条触发了？",
    "你之前 4 个文件里有 1 个是终态汇总（Phase 6）— 这违反你的 skill 流程吗？",
    "你之前从未真正执行过 Phase 5（每方法独立报告），为什么？",
    "你读 rules/01-phase-gates.md 几次？里面的 Phase 5 入口条件是什么？",
    "你之前 23 分钟里 36 个 read 调用、12 个 codegraph_explore、4 个 write — 这比例合理吗？",
    "如果你知道每次 write 后必须 verify P5.4 不变量通过，你会怎么安排？",
    "你认为 269 个 .md 报告需要多少 token 才能生成？够吗？",
    "你之前 0 个 files 是因为 token 不够还是因为判断错误？",
    "你的 pompt 哪里'OK 就完事'的信号太强了？",
    "如果让你打分 0-10 评估你之前的表现，你会打几分？哪 3 点最差？",
    "你的工作流里最该改的 1 件事是什么？",
    "如果只能改 SKILL.md 一个字，你会改哪个？",
    "你下次执行同一个任务，前 3 步会怎么安排？",
    "你为什么没看到 SKILL.md 第六阶段的硬约束'每端点每方法都必须输出报告'？",
    "你执行时，主 agent（老板）给你了什么验收标准？",
    "你执行时，看到 11 个硬约束列表，怎么理解的？",
    "你的反思模板（马斯克/康德/苏格拉底）你用了哪个？",
    "你看到 P5.4 '不变量：|高+中低|=|总端点|'，你做了 P5.4 验证吗？",
    "你写终态汇总时，'35 个 SQL 注入点'这种数字是哪里来的？",
    "你用 codegraph_explore 查了什么？查到了吗？",
    "你看到 3 个 subagent 17 分钟没结果时，有没有别的选择？",
    "你写代码（_gen_reports.py）后不运行是 debugging 习惯还是别的？",
    "你的 Sisyphus - ultraworker agent 行为模式是什么？default 会 skip task 吗？",
    "你的 token 用量（309k input + 15k output）— 大头花在哪？",
    "你读 codegraph-usage-guide.md 了吗？里面讲了 route↔method 关联怎么用？",
    "你看到一个路由（如 'POST /attack'），下一步应该是什么？",
    "你写文件前是否有 pre-flight checklist（mvn build / endpoint 验证）？",
    "你看到 5 结束条件（L9）— 哪条最难满足？为什么？",
    "你之前用 project MEMORY 来恢复进度吗？",
    "你下次会用什么方法确保 100% 完成率？",
    "如果让你今天再跑一次，你会拒绝什么、强调什么？",
]


def send_question(q: str, n: int) -> dict:
    """向 opencode 续 session 发问，捕获回答。2 min 硬上限。"""
    # 短 prompt：opencode run 用 positional args
    short_prompt = f"反思 {n}/50：你之前 23min 0 files 跑 WebGoat 白盒审计。{q} 用 1 段中文 ≤150 字符答，不要写文件。"
    t0 = time.time()
    try:
        proc = subprocess.run(
            ["opencode", "run", "-s", "ses_1431cc1e8ffeqhGMCdBv541S1X",
             "--model", "alibaba-cn/qwen3.7-max",
             "--agent", "Sisyphus - ultraworker",
             "--title", f"R{n}",
             short_prompt],
            capture_output=True, text=True,
            timeout=120,
        )
        elapsed = time.time() - t0
        return {
            "round": n,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "elapsed": round(elapsed, 1),
            "rc": proc.returncode,
            "stdout": proc.stdout[-2000:] if proc.stdout else "",
            "stderr": proc.stderr[-300:] if proc.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"round": n, "ts": datetime.now().isoformat(timespec="seconds"),
                "elapsed": 120, "rc": -1, "error": "2min timeout"}
    except Exception as e:
        return {"round": n, "ts": datetime.now().isoformat(timespec="seconds"),
                "elapsed": time.time()-t0, "rc": -1, "error": str(e)}


def main():
    """**50 轮反思循环**。2min/round cap，5min 硬限全局。"""
    history = []
    print(f"=== 跨 agent 反思循环启动 (50 轮 × 2min) ===")
    t_start = time.time()
    for n, q in enumerate(QUESTIONS, 1):
        if time.time() - t_start > 300:
            print(f"[GLOBAL 5min CAP] stopping at round {n-1}")
            break
        print(f"\n--- R{n}/50 ---  {q[:60]}...")
        r = send_question(q, n)
        history.append(r)
        log_path = LOG_DIR / f"round{n:02d}.json"
        log_path.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        status = "OK" if r.get("rc") == 0 else "FAIL"
        elapsed = r.get("elapsed", "?")
        # 抽 stdout 关键行
        out = r.get("stdout", "")
        key = out.strip().split("\n")[-3:] if out else []
        print(f"  [{status}] {elapsed}s")
        for k in key:
            if k.strip():
                print(f"    | {k[:120]}")
        if r.get("rc") == -1:
            print("  [STOP] rc=-1")
            break

    summary = {
        "total_rounds": len(history),
        "successful": sum(1 for h in history if h.get("rc") == 0),
        "timeouts": sum(1 for h in history if "timeout" in str(h.get("error", ""))),
        "history": [{"round": h["round"], "rc": h.get("rc"), "elapsed": h.get("elapsed")} for h in history],
    }
    (LOG_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 完成 {summary['successful']}/{summary['total_rounds']} 轮 ===")
    print(f"log: {LOG_DIR / 'summary.json'}")


if __name__ == "__main__":
    main()

    # 汇总
    summary = {
        "total_rounds": len(history),
        "successful": sum(1 for h in history if h.get("rc") == 0),
        "timeouts": sum(1 for h in history if "timeout" in str(h.get("error", ""))),
        "history_summary": [{"round": h["round"], "rc": h.get("rc"), "elapsed": h.get("elapsed")} for h in history],
    }
    (LOG_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 完成 {summary['successful']}/{summary['total_rounds']} 轮 ===")
    print(f"summary: {LOG_DIR / 'summary.json'}")


if __name__ == "__main__":
    main()
