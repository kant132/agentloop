# -*- coding: utf-8 -*-
"""cxf_route_merger.py — 合并 javaparser-service 路由 + config_scanner 结果 + jar-analyzer.db。

编排流程:
1. config_scanner 扫描 web.xml / Spring XML → CXFServlet URL 前缀 + jaxws:endpoint 声明
2. javaparser-service RouteExtractor --routes → 精确路由 (含 @Path 路径值)
3. jar-analyzer.db anno_table → 无源码时的回退方案
4. 合并去重，输出 route.json 格式

调用方式:
    from scripts.exposure.collectors.cxf.cxf_route_merger import extract_all_cxf_routes
    routes = extract_all_cxf_routes(jar_db_path, project_root, group_id)
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

# javaparser-service JAR 路径
_JAVAPARSER_SERVICE_JAR = Path(__file__).resolve().parents[4] / "tools" / "javaparser" / "java-method-call-extractor-1.0.0.jar"


def extract_all_cxf_routes(
    jar_db_path: Path,
    project_root: Path | None,
    group_id: str,
) -> list[dict[str, Any]]:
    """提取所有 CXF/JAX-RS/JAX-WS 端点。

    优先级:
    1. 有源码 → javaparser-service RouteExtractor --routes (精确路径)
    2. 无源码 → jar-analyzer.db anno_table (路径不精确)
    3. config_scanner → CXFServlet URL 前缀 + jaxws:endpoint 声明 (补充)

    Args:
        jar_db_path: jar-analyzer.db 路径
        project_root: 项目根目录 (可为 None, 无源码时)
        group_id: 项目 groupId

    Returns:
        route.json 格式的 items 列表
    """
    routes: list[dict[str, Any]] = []
    seen_fqns: set[str] = set()

    # 1. config_scanner: 扫描配置文件
    cxf_config: dict[str, Any] = {}
    if project_root and project_root.is_dir():
        from .config_scanner import scan_config
        cxf_config = scan_config(project_root)

    cxf_url_prefix = cxf_config.get("cxf_servlet_url_pattern", "/services/*")
    # 去掉通配符
    if cxf_url_prefix:
        cxf_url_prefix = cxf_url_prefix.replace("/*", "").rstrip("/")

    # 2. 有源码 → javaparser-service RouteExtractor --routes
    source_root = None
    if project_root:
        for candidate in [
            project_root / "src" / "main" / "java",
            project_root / "sources",
        ]:
            if candidate.is_dir():
                source_root = candidate
                break

    if source_root and _JAVAPARSER_SERVICE_JAR.exists():
        # javaparser-service 已包含 JaxRsFramework + JaxWsFramework
        # --routes 模式会自动识别所有框架的路由
        jp_routes = _run_javaparser_routes(source_root, group_id)
        for r in jp_routes:
            fqn = r.get("method_fqn", "")
            if fqn and fqn not in seen_fqns:
                seen_fqns.add(fqn)
                routes.append(r)
    else:
        # 3. 无源码 → jar-analyzer.db anno_table 回退
        db_routes = _extract_from_anno_table(jar_db_path, group_id, cxf_url_prefix)
        for r in db_routes:
            fqn = r.get("method_fqn", "")
            if fqn and fqn not in seen_fqns:
                seen_fqns.add(fqn)
                routes.append(r)

    # 4. 合并 config_scanner 的 jaxws:endpoint 声明
    for ep in cxf_config.get("jaxws_endpoints", []):
        implementor = ep.get("implementor", "")
        if implementor:
            # implementor 是实现类 FQN, 所有 public 方法都是端点
            # 这里只记录端点存在, 具体方法在 javaparser-service 或 anno_table 中
            pass  # 已在步骤 2/3 中覆盖

    return routes


def _run_javaparser_routes(source_root: Path, group_id: str) -> list[dict[str, Any]]:
    """调 javaparser-service JAR 的 --routes 模式提取路由。

    RouteExtractor 会自动加载所有 FrameworkHandler:
    - SpringMvcFramework (Spring MVC)
    - JaxRsFramework (JAX-RS @Path + @GET/@POST)
    - JaxWsFramework (JAX-WS @WebService + @WebMethod) ← 需新增
    - ServletFramework (@WebServlet)
    - SpringGraphQLFramework, SpringActuatorFramework, ...
    """
    try:
        result = subprocess.run(
            [
                "java", "-jar", str(_JAVAPARSER_SERVICE_JAR),
                "--routes", str(source_root),
                "--group-id", group_id,
            ],
            capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0 or not result.stdout:
            return []
        import json
        data = json.loads(result.stdout)
        if not isinstance(data, list):
            return []
        return data
    except Exception:
        return []


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


def _extract_from_anno_table(
    jar_db_path: Path,
    group_id: str,
    cxf_url_prefix: str,
) -> list[dict[str, Any]]:
    """从 jar-analyzer.db anno_table 提取 CXF 端点 (无源码回退方案)。

    anno_table 不存注解参数值, 路径只能从类名/方法名推断。
    """
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
        class_short = cls_name.split("/")[-1]

        for mn, annos in method_annos.items():
            fqn = f"{class_fqn}#{mn}"
            http_methods: list[str] = []
            for anno in annos:
                for anno_key, http_method in _JAXRS_HTTP_ANNOTATIONS.items():
                    if anno_key in anno and http_method not in http_methods:
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
                "source": "jax-rs (anno_table fallback)",
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
        class_short = cls_name.split("/")[-1]
        url_prefix = cxf_url_prefix or "/services"

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
                "full_url": f"{url_prefix}/{class_short}",
                "has_external_param": has_params,
                "start_line": line_number or 0,
                "file": "", "nodes_id": "",
                "source": "jax-ws (anno_table fallback)",
            })

    conn.close()
    return routes
