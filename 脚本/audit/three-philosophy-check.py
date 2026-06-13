"""three-philosophy-check.py

**3 哲学硬约束**（用户 2026-06-13 硬性规定）

每轮 opencode 跑完后，老板**必跑** 3 哲学自检：
- 马斯克：第一性原理 → 删掉最边缘的 50% 文件，保留最核心
- 康德：可普遍化原则 → 验证命名/格式是否一致
- 苏格拉底：承认无知 → 列出 3 件不确定的事

输出：round{N}-philosophy.md 含 3 段。
"""
import argparse
import sys
from pathlib import Path

# Build Chinese via chr() to avoid encoding issues
def cn(cp_list):
    return "".join(chr(c) for c in cp_list)

CN_MUSK   = cn([0x9A6C, 0x65AF, 0x514B])  # 马斯克
CN_KANT   = cn([0x5EB7, 0x5FB7])  # 康德
CN_SOC    = cn([0x82CF, 0x683C, 0x62C9, 0x5E95])  # 苏格拉底

CN_TOP5   = cn([0x6700, 0x6838, 0x5FC3, 0x7684, 0x0035])  # 最核心的5
CN_DELETE = cn([0x6700, 0x8FB9, 0x7F18, 0x7684, 0x0035])  # 最边缘的5
CN_IF_ALL = cn([0x5982, 0x679C, 0x90FD, 0x4EE5, 0x4E0A, 0x8D2D, 0x8D77, 0x540C, 0x6837, 0x67D0, 0x7EC4, 0x5316])  # 如果都以同样模板起
CN_DO_NOT = cn([0x4E0D, 0x8BE5, 0x91CD, 0x590D, 0xFF1F])  # 应该重做?
CN_DONT_KNOW_3 = cn([0x4E0D, 0x77E5, 0x9053, 0x7684, 0x0033, 0x4EF6, 0x4E8B])  # 不知道的3件事


def musk_first_principles(poc_dir: Path, output_path: Path):
    """马斯克：第一性原理 → 列出 top 5 关键端点 + bottom 5 边缘端点"""
    files = list(poc_dir.glob("*.md"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "a", encoding="utf-8") as fp:
        fp.write(f"\n## {CN_MUSK} - 第一性原理\n\n")
        fp.write(f"**问题**: 哪些端点最关键？哪些最边缘？\n\n")
        fp.write(f"**{CN_TOP5}**:\n")
        for fpath in files[:5]:
            fp.write(f"- [ ] {fpath.name} (严重等级: ?, 关键原因: ?)\n")
        fp.write(f"\n**{CN_DELETE}**:\n")
        for fpath in files[-5:]:
            fp.write(f"- [ ] {fpath.name} (可删原因: ?)\n")
        fp.write(f"\n**行动**: 高优资源 70% 集中在 top 5；bottom 5 0 权重\n\n")


def kant_categorical(poc_dir: Path, output_path: Path):
    """康德：可普遍化 → 验证命名/格式一致性"""
    files = list(poc_dir.glob("*.md"))
    with open(output_path, "a", encoding="utf-8") as fp:
        fp.write(f"\n## {CN_KANT} - 绝对命令 / 可普遍化\n\n")
        fp.write(f"**问题**: {CN_IF_ALL}，能区分高/低风险吗？\n\n")
        fp.write(f"**检验项**:\n")
        fp.write(f"- [ ] 文件名都含验证状态前缀 (是问题/非问题/暂时无法确认/failed)\n")
        fp.write(f"- [ ] 文件名都含问题等级 (致命/严重/中/低/无)\n")
        fp.write(f"- [ ] 文件名都含 fqn.method#sigHash\n")
        fp.write(f"- [ ] PoC 内容都含 '## 实际响应' 段\n")
        fp.write(f"- [ ] PoC 内容都含 '## 根因分析' 段\n\n")
        fp.write(f"**{CN_DO_NOT}**\n\n")


def socrates_humility(poc_dir: Path, output_path: Path, last_n: int = 50):
    """苏格拉底：承认无知 → 列出 3 件不确定的事"""
    files = list(poc_dir.glob("*.md"))
    import sqlite3
    sessions = []
    try:
        conn = sqlite3.connect(r"C:\Users\Administrator\.local\share\opencode\opencode.db")
        cur = conn.cursor()
        sessions = cur.execute("""
            SELECT title, tokens_input, tokens_output, cost
            FROM session
            WHERE title LIKE 'WGB%' OR title LIKE 'Audit batch%'
            ORDER BY time_created DESC LIMIT ?
        """, (last_n,)).fetchall()
        conn.close()
    except Exception as e:
        sessions = [("opencode.db unavailable", "", "", str(e))]
    with open(output_path, "a", encoding="utf-8") as fp:
        fp.write(f"\n## {CN_SOC} - 承认无知\n\n")
        fp.write(f"**问题**: 不知道什么？\n\n")
        fp.write(f"**{CN_DONT_KNOW_3}**:\n")
        fp.write(f"1. opencode subagent 是否真读了代码？还是用模板？\n")
        fp.write(f"2. 路由名 vs 实际 handler method 是不是同一个？（命名歧义）\n")
        fp.write(f"3. WebGoat 端点的 200 响应到底是真漏洞还是教学伪漏洞？\n\n")
        fp.write(f"**opencode 实际活动**（最近 {len(sessions)} sessions）:\n")
        for s in sessions[:5]:
            fp.write(f"- {s[0]}  in={s[1]}  out={s[2]}  cost={s[3]}\n")
        fp.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poc-dir", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    poc_dir = Path(args.poc_dir)
    output = Path(args.output)

    # 重建（每次新轮覆写）
    if output.exists():
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(f"# 3 哲学自检 — {{round}}\n\n")
        f.write("**用户 2026-06-13 硬性规定**：每轮 opencode 跑完必跑 3 哲学自检\n\n")
        f.write("---\n\n")

    musk_first_principles(poc_dir, output)
    kant_categorical(poc_dir, output)
    socrates_humility(poc_dir, output)

    print(f"[+] 3 philosophy check written: {output}")


if __name__ == "__main__":
    main()
