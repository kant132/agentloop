#!/usr/bin/env python3
"""auto_preset.py — 从 JAR 包自动生成 preset.json + route.json。

只需要 JAR 路径，通过 jar-analyzer-engine 构建 jar-analyzer.db 自动提取：
1. groupId: 从 spring_controller_table 的 class_name 包前缀推断
2. route.json: 从 route_table 提取所有路由端点 (Spring MVC + JAX-RS + JAX-WS)
3. preset.json: 组装完整配置

Usage:
    python scripts/auto_preset.py --jar D:/path/to/app.jar
    python scripts/auto_preset.py --jar D:/path/to/app.jar --output-dir projects/myproject
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent  # D:\agentloop
JAR_ANALYZER_JAR = ROOT / "tools" / "jar-analyzer-engine" / "target" / "jar-analyzer-engine-1.2.0-jar-with-dependencies.jar"


def build_jar_analyzer_db(jar_path: Path, output_db: Path) -> bool:
    """用 jar-analyzer 构建 jar-analyzer.db。"""
    if not JAR_ANALYZER_JAR.exists():
        print(f"ERROR: jar-analyzer JAR 不存在: {JAR_ANALYZER_JAR}", file=sys.stderr)
        return False
    print(f"  构建 jar-analyzer.db: {jar_path.name} ...")
    result = subprocess.run(
        ["java", "-jar", str(JAR_ANALYZER_JAR), "--jar", str(jar_path)],
        capture_output=True, text=True, timeout=600,
        cwd=str(output_db.parent),  # 直接在目标目录构建
    )
    if result.returncode != 0:
        print(f"  jar-analyzer 构建失败: {result.stderr[:300]}", file=sys.stderr)
        return False
    # jar-analyzer.db 输出到 CWD (即 output_db.parent)
    cwd_db = output_db.parent / "jar-analyzer.db"
    if cwd_db.exists() and cwd_db.resolve() != output_db.resolve():
        shutil.copy2(str(cwd_db), str(output_db))
        cwd_db.unlink()
    elif cwd_db.exists():
        # 已经在目标位置
        pass
    else:
        # 回退: CWD 是 agentloop 根目录
        root_db = ROOT / "jar-analyzer.db"
        if root_db.exists():
            output_db.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(root_db), str(output_db))
    return True


def infer_group_id(jar_db_path: Path) -> str:
    """从 spring_controller_table 的 class_name 包前缀推断 groupId。"""
    import sqlite3
    conn = sqlite3.connect(str(jar_db_path))
    # 统计 controller class_name 的顶层包
    pkgs: Counter[str] = Counter()
    for row in conn.execute("SELECT class_name FROM spring_controller_table").fetchall():
        parts = row[0].split("/")
        if len(parts) >= 3:
            # 取前 2-3 段作为 groupId
            pkg = ".".join(parts[:3])
            pkgs[pkg] += 1
    conn.close()
    if not pkgs:
        # 没有 spring controller，从 method_table 推断
        conn = sqlite3.connect(str(jar_db_path))
        for row in conn.execute("SELECT DISTINCT class_name FROM method_table LIMIT 100").fetchall():
            parts = row[0].split("/")
            if len(parts) >= 3 and parts[0] != "java" and parts[0] != "javax":
                pkg = ".".join(parts[:3])
                pkgs[pkg] += 1
        conn.close()
    if not pkgs:
        return "unknown"
    return pkgs.most_common(1)[0][0]


def extract_routes(jar_db_path: Path, group_id: str, project_root: Path | None = None) -> list[dict[str, Any]]:
    """从 jar-analyzer.db route_table 提取所有路由端点（Spring MVC + JAX-RS + JAX-WS）。

    route_table 由 jar-analyzer-engine ASM 原生扫描填充，包含所有框架的端点。
    返回 route.json 格式的 items 列表。
    """
    import sqlite3
    conn = sqlite3.connect(str(jar_db_path))
    conn.row_factory = sqlite3.Row

    routes: list[dict[str, Any]] = []
    seen_fqns: set[str] = set()

    for row in conn.execute(
        "SELECT class_name, method_name, method_desc, framework, http_method, "
        "path, base_path, method_path, line_number "
        "FROM route_table ORDER BY class_name, method_name"
    ).fetchall():
        cn = row["class_name"] or ""
        mn = row["method_name"] or ""
        framework = row["framework"] or ""
        http_method_raw = (row["http_method"] or "").strip()

        # URL 路径: 优先 base_path + method_path, 回退 path
        base_path = row["base_path"] or ""
        method_path = row["method_path"] or ""
        path = row["path"] or ""
        if base_path:
            full_url = base_path + method_path if method_path else base_path
        elif path and path != "none":
            full_url = path
        else:
            continue

        class_fqn = cn.replace("/", ".")
        fqn = f"{class_fqn}#{mn}"
        if fqn in seen_fqns:
            continue
        seen_fqns.add(fqn)

        desc = row["method_desc"] or "()V"
        has_params = not desc.startswith("()")
        line_number = row["line_number"] or 0

        # 解析 HTTP methods
        http_methods: list[str] = []
        rt_upper = http_method_raw.upper()
        for m in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            if m in rt_upper:
                http_methods.append(m)
        if not http_methods:
            http_methods = ["ANY"]

        routes.append({
            "fqn": fqn,
            "method_fqn": fqn,
            "class_fqn": class_fqn,
            "method_name": mn,
            "http_method": http_methods[0],
            "http_methods": http_methods,
            "full_url": full_url,
            "has_external_param": has_params,
            "start_line": line_number,
            "file": "",
            "nodes_id": "",
            "source": framework,
        })

    conn.close()
    return routes


# _extract_cxf_routes 和 _extract_cxf_from_anno_table 已删除:
# route_table 由 jar-analyzer-engine ASM 原生扫描，已包含所有框架 (Spring MVC + JAX-RS + JAX-WS)


def generate_preset(
    jar_path: Path,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """从 JAR 生成 preset.json + route.json，返回 preset dict。"""

    # 1. 推断项目信息
    jar_name = jar_path.stem  # e.g. mall-admin-1.0-SNAPSHOT

    # 2. 确定 output_dir
    if output_dir is None:
        # 默认放在 projects/{groupId}/
        # 先构建 jar-analyzer.db 来推断 groupId
        temp_db = ROOT / "jar-analyzer.db"
        if not build_jar_analyzer_db(jar_path, temp_db):
            sys.exit(2)
        group_id = infer_group_id(temp_db)
        output_dir = ROOT / "projects" / group_id.replace(".", "_") / "loop_audit"
        output_db = output_dir / "jar-analyzer.db"
        if temp_db.resolve() != output_db.resolve():
            output_db.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(temp_db), str(output_db))
            temp_db.unlink()
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_db = output_dir / "jar-analyzer.db"
        if not build_jar_analyzer_db(jar_path, output_db):
            sys.exit(2)
        group_id = infer_group_id(output_db)

    print(f"  groupId: {group_id}")
    print(f"  output_dir: {output_dir}")

    # 3. 提取路由
    routes = extract_routes(output_db if output_db.exists() else (ROOT / "jar-analyzer.db"), group_id, Path(jar_path.parent.parent))
    print(f"  routes: {len(routes)}")

    # 4. 写 route.json
    exposure_dir = output_dir / "exposure"
    exposure_dir.mkdir(parents=True, exist_ok=True)
    route_file = exposure_dir / "route.json"
    route_data = {
        "asset_type": "route",
        "source": "jar-analyzer route_table",
        "stats": {
            "total": len(routes),
            "by_http_method": _count_by_method(routes),
        },
        "items": routes,
    }
    route_file.write_text(
        json.dumps(route_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  route.json → {route_file}")

    # 5. 组装 preset.json
    preset = {
        "groupId": group_id,
        "projectName": jar_name,
        "projectRoot": str(jar_path.parent.parent),  # JAR 的上级目录的上级
        "jarAnalyzerDb": str(output_db),
        "targetJarPath": str(jar_path),
        "loopDir": str(output_dir),
        "exposurePipeline": True,
    }

    # 6. 写 preset.json
    preset_file = output_dir.parent / "preset.json"
    preset_file.write_text(
        json.dumps(preset, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  preset.json → {preset_file}")

    return preset


def _count_by_method(routes: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in routes:
        for m in r.get("http_methods", [r.get("http_method", "ANY")]):
            key = m or "ANY"
            counts[key] = counts.get(key, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从 JAR 包自动生成 preset.json + route.json"
    )
    parser.add_argument(
        "--jar", required=True, type=Path,
        help="目标 JAR 包路径",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="输出目录 (默认: projects/{groupId}/loop_audit/)",
    )
    args = parser.parse_args(argv)

    jar_path = args.jar.resolve()
    if not jar_path.is_file():
        print(f"ERROR: JAR 不存在: {jar_path}", file=sys.stderr)
        return 2

    print(f"=== auto_preset: {jar_path.name} ===")
    generate_preset(jar_path, args.output_dir)
    print("=== 完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
