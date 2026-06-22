#!/usr/bin/env python3
"""auto_preset.py — 从 JAR 包自动生成 preset.json + route.json。

只需要 JAR 路径，通过 jar-analyzer.db 自动提取：
1. groupId: 从 spring_controller_table 的 class_name 包前缀推断
2. route.json: 从 spring_method_table 提取所有路由端点
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
    """从 jar-analyzer.db 提取路由端点（Spring MVC + CXF/JAX-RS/JAX-WS）。

    返回 route.json 格式的 items 列表。

    数据源：
    1. spring_method_table — jar-analyzer 自动识别的 Spring MVC 端点
    2. javaparser-service — 有源码时精确提取 @Path 路径值 (含 JaxRsFramework + JaxWsFramework)
    3. anno_table — 无源码时回退方案，查 JAX-RS/JAX-WS 注解 (路径不精确)
    """
    import sqlite3
    conn = sqlite3.connect(str(jar_db_path))
    conn.row_factory = sqlite3.Row

    routes: list[dict[str, Any]] = []
    seen_fqns: set[str] = set()  # 去重

    # ── 1. Spring MVC (spring_method_table) ──────────────────────
    for row in conn.execute(
        "SELECT class_name, method_name, method_desc, restful_type, path "
        "FROM spring_method_table ORDER BY class_name, method_name"
    ).fetchall():
        cn = row["class_name"] or ""
        mn = row["method_name"] or ""
        rt = (row["restful_type"] or "").strip()
        path = row["path"] or ""

        if path == "none" or not path:
            continue

        class_fqn = cn.replace("/", ".")
        fqn = f"{class_fqn}#{mn}"
        if fqn in seen_fqns:
            continue
        seen_fqns.add(fqn)

        desc = row["method_desc"] or "()V"
        has_params = not desc.startswith("()")

        http_methods: list[str] = []
        rt_upper = rt.upper()
        if "GET" in rt_upper:
            http_methods.append("GET")
        if "POST" in rt_upper:
            http_methods.append("POST")
        if "PUT" in rt_upper:
            http_methods.append("PUT")
        if "DELETE" in rt_upper:
            http_methods.append("DELETE")
        if "PATCH" in rt_upper:
            http_methods.append("PATCH")
        if not http_methods:
            if "REQUEST" in rt_upper or "RESPONSE" in rt_upper:
                http_methods = ["ANY"]

        routes.append({
            "fqn": fqn,
            "method_fqn": fqn,
            "class_fqn": class_fqn,
            "method_name": mn,
            "http_method": http_methods[0] if http_methods else "ANY",
            "http_methods": http_methods if http_methods else ["ANY"],
            "full_url": path,
            "has_external_param": has_params,
            "start_line": 0,
            "file": "",
            "nodes_id": "",
            "source": "spring_mvc",
        })

    # ── 2. CXF/JAX-RS/JAX-WS (有源码→javaparser-service, 无源码→anno_table) ──
    cxf_routes = _extract_cxf_routes(jar_db_path, project_root, group_id)
    for r in cxf_routes:
        fqn = r.get("method_fqn", "")
        if fqn and fqn not in seen_fqns:
            seen_fqns.add(fqn)
            routes.append(r)
    conn.close()
    return routes


# JAX-RS 注解名 → HTTP method
_JAXRS_HTTP_ANNOTATIONS: dict[str, str] = {
    "jakarta/ws/rs/GET;": "GET",
    "javax/ws/rs/GET;": "GET",
    "jakarta/ws/rs/POST;": "POST",
    "javax/ws/rs/POST;": "POST",
    "jakarta/ws/rs/PUT;": "PUT",
    "javax/ws/rs/PUT;": "PUT",
    "jakarta/ws/rs/DELETE;": "DELETE",
    "javax/ws/rs/DELETE;": "DELETE",
    "jakarta/ws/rs/PATCH;": "PATCH",
    "javax/ws/rs/PATCH;": "PATCH",
}


def _extract_cxf_routes(
    jar_db_path: Path,
    project_root: Path | None,
    group_id: str,
) -> list[dict[str, Any]]:
    """提取 CXF/JAX-RS/JAX-WS 端点。

    优先级:
    1. 有源码 → javaparser-service RouteExtractor --routes (精确路径, 含 JaxWsFramework)
    2. 无源码 → jar-analyzer.db anno_table (路径不精确)
    """
    # 1. config_scanner: 扫描配置文件
    cxf_url_prefix = "/services"
    if project_root and project_root.is_dir():
        try:
            from scripts.exposure.collectors.cxf.config_scanner import scan_config
            cfg = scan_config(project_root)
            pattern = cfg.get("cxf_servlet_url_pattern", "")
            if pattern:
                cxf_url_prefix = pattern.replace("/*", "").rstrip("/") or "/services"
        except Exception:
            pass

    # 2. 有源码 → javaparser-service
    source_root = None
    if project_root:
        for candidate in [project_root / "src" / "main" / "java", project_root / "sources"]:
            if candidate.is_dir():
                source_root = candidate
                break

    javaparser_jar = ROOT / "tools" / "javaparser" / "java-method-call-extractor-1.0.0.jar"

    if source_root and javaparser_jar.exists():
        import subprocess as _sp
        try:
            result = _sp.run(
                ["java", "-jar", str(javaparser_jar), "--routes", str(source_root), "--group-id", group_id],
                capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace",
            )
            if result.returncode == 0 and result.stdout:
                import json as _json
                data = _json.loads(result.stdout)
                if isinstance(data, list):
                    return data
        except Exception:
            pass

    # 3. 无源码 → jar-analyzer.db anno_table 回退
    return _extract_cxf_from_anno_table(jar_db_path, group_id, cxf_url_prefix)


def _extract_cxf_from_anno_table(
    jar_db_path: Path,
    group_id: str,
    cxf_url_prefix: str,
) -> list[dict[str, Any]]:
    """从 jar-analyzer.db anno_table 提取 CXF 端点 (无源码回退方案)。

    anno_table 不存注解参数值, 路径只能从类名/方法名推断。
    """
    import sqlite3
    conn = sqlite3.connect(str(jar_db_path))
    conn.row_factory = sqlite3.Row
    routes: list[dict[str, Any]] = []

    # JAX-RS: @Path 类 + @GET/@POST 方法
    path_classes: set[str] = set()
    for row in conn.execute(
        "SELECT DISTINCT class_name FROM anno_table "
        "WHERE anno_name LIKE '%ws/rs/Path%' AND method_name IS NULL"
    ).fetchall():
        path_classes.add(row["class_name"])

    for cls_name in path_classes:
        method_annos: dict[str, list[str]] = {}
        for row in conn.execute(
            "SELECT method_name, anno_name FROM anno_table "
            "WHERE class_name = ? AND method_name IS NOT NULL "
            "AND (anno_name LIKE '%ws/rs/GET%' OR anno_name LIKE '%ws/rs/POST%' "
            "  OR anno_name LIKE '%ws/rs/PUT%' OR anno_name LIKE '%ws/rs/DELETE%' "
            "  OR anno_name LIKE '%ws/rs/PATCH%')",
            (cls_name,)
        ).fetchall():
            method_annos.setdefault(row["method_name"], []).append(row["anno_name"])

        if not method_annos:
            for row in conn.execute(
                "SELECT method_name FROM method_table "
                "WHERE class_name = ? AND method_name NOT IN ('<init>', '<clinit>')",
                (cls_name,)
            ).fetchall():
                method_annos[row["method_name"]] = ["REQUEST"]

        class_fqn = cls_name.replace("/", ".")
        class_short = cls_name.split("/")[-1] if "/" in cls_name else cls_name

        for mn, annos in method_annos.items():
            fqn = f"{class_fqn}#{mn}"
            http_methods: list[str] = []
            for anno in annos:
                for anno_key, http_method in _JAXRS_HTTP_ANNOTATIONS.items():
                    if anno_key in anno:
                        if http_method not in http_methods:
                            http_methods.append(http_method)
            if not http_methods:
                http_methods = ["ANY"]

            mt_row = conn.execute(
                "SELECT method_desc, line_number FROM method_table "
                "WHERE class_name = ? AND method_name = ? LIMIT 1",
                (cls_name, mn)
            ).fetchone()
            desc = mt_row["method_desc"] if mt_row else "()V"
            has_params = not desc.startswith("()")
            line_number = mt_row["line_number"] if mt_row else 0

            routes.append({
                "fqn": fqn, "method_fqn": fqn, "class_fqn": class_fqn,
                "method_name": mn,
                "http_method": http_methods[0],
                "http_methods": http_methods,
                "full_url": f"/{class_short}/{mn}",
                "has_external_param": has_params,
                "start_line": line_number or 0,
                "file": "", "nodes_id": "",
                "source": "jax-rs",
            })

    # JAX-WS: @WebService 类 + @WebMethod 方法
    ws_classes: set[str] = set()
    for row in conn.execute(
        "SELECT DISTINCT class_name FROM anno_table "
        "WHERE anno_name LIKE '%WebService%'"
    ).fetchall():
        ws_classes.add(row["class_name"])

    for cls_name in ws_classes:
        web_methods: set[str] = set()
        for row in conn.execute(
            "SELECT DISTINCT method_name FROM anno_table "
            "WHERE class_name = ? AND anno_name LIKE '%WebMethod%' "
            "AND method_name IS NOT NULL",
            (cls_name,)
        ).fetchall():
            web_methods.add(row["method_name"])

        if not web_methods:
            for row in conn.execute(
                "SELECT method_name FROM method_table "
                "WHERE class_name = ? AND method_name NOT IN ('<init>', '<clinit>')",
                (cls_name,)
            ).fetchall():
                web_methods.add(row["method_name"])

        if not web_methods:
            continue

        class_fqn = cls_name.replace("/", ".")
        class_short = cls_name.split("/")[-1] if "/" in cls_name else cls_name

        for mn in sorted(web_methods):
            fqn = f"{class_fqn}#{mn}"
            mt_row = conn.execute(
                "SELECT method_desc, line_number FROM method_table "
                "WHERE class_name = ? AND method_name = ? LIMIT 1",
                (cls_name, mn)
            ).fetchone()
            desc = mt_row["method_desc"] if mt_row else "()V"
            has_params = not desc.startswith("()")
            line_number = mt_row["line_number"] if mt_row else 0

            routes.append({
                "fqn": fqn, "method_fqn": fqn, "class_fqn": class_fqn,
                "method_name": mn,
                "http_method": "POST",
                "http_methods": ["POST"],
                "full_url": f"{cxf_url_prefix}/{class_short}",
                "has_external_param": has_params,
                "start_line": line_number or 0,
                "file": "", "nodes_id": "",
                "source": "jax-ws",
            })

    conn.close()
    return routes


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
        "source": "jar-analyzer spring_method_table",
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
