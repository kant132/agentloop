# -*- coding: utf-8 -*-
"""config_scanner.py — 扫描 web.xml / Spring XML 配置，提取 CXF 端点声明。

扫描内容：
1. web.xml → CXFServlet 注册 (URL pattern)
2. Spring XML → <jaxws:endpoint> / <jaxrs:server> 声明
3. Spring Boot → @Bean CxfServlet / ServletRegistrationBean (grep 源码)

输出：
{
  "cxf_servlet_url_pattern": "/services/*",
  "jaxws_endpoints": [
    {"id": "myService", "implementor": "com.foo.MyServiceImpl", "address": "/myService"},
    ...
  ],
  "jaxrs_servers": [
    {"id": "myRestService", "serviceClass": "com.foo.MyRestService", "address": "/rest"},
    ...
  ]
}
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


# CXF XML 命名空间
_NS_CXF = "http://cxf.apache.org/jaxws"
_NS_CXF_RS = "http://cxf.apache.org/jaxrs"
_NS_SPRING = "http://www.springframework.org/schema/beans"


def scan_config(project_root: Path) -> dict[str, Any]:
    """扫描项目配置文件，提取 CXF 端点声明。

    Args:
        project_root: 项目根目录

    Returns:
        dict with keys: cxf_servlet_url_pattern, jaxws_endpoints, jaxrs_servers
    """
    result: dict[str, Any] = {
        "cxf_servlet_url_pattern": None,
        "jaxws_endpoints": [],
        "jaxrs_servers": [],
    }

    # 1. web.xml → CXFServlet URL pattern
    web_xml = _find_web_xml(project_root)
    if web_xml:
        result["cxf_servlet_url_pattern"] = _parse_cxf_servlet(web_xml)

    # 2. Spring XML → jaxws:endpoint / jaxrs:server
    xml_files = _find_spring_xml_files(project_root)
    for xml_file in xml_files:
        result["jaxws_endpoints"].extend(_parse_jaxws_endpoints(xml_file))
        result["jaxrs_servers"].extend(_parse_jaxrs_servers(xml_file))

    # 3. Spring Boot Java 配置 → grep Endpoint.publish / CxfServlet
    java_files = list((project_root / "src" / "main" / "java").rglob("*.java")) if (project_root / "src" / "main" / "java").exists() else []
    for java_file in java_files:
        text = java_file.read_text(encoding="utf-8", errors="replace")
        # Endpoint.publish("/services/MyService", new MyServiceImpl())
        for m in re.finditer(r'Endpoint\.publish\s*\(\s*"([^"]+)"', text):
            addr = m.group(1)
            result["jaxws_endpoints"].append({
                "id": f"publish:{java_file.name}",
                "implementor": str(java_file.relative_to(project_root)),
                "address": addr,
                "source": "Endpoint.publish()",
            })
        # CxfServlet registration
        if "CxfServlet" in text:
            for m in re.finditer(r'addUrlMappings\s*\(\s*"([^"]+)"', text):
                if not result["cxf_servlet_url_pattern"]:
                    result["cxf_servlet_url_pattern"] = m.group(1)

    return result


def _find_web_xml(project_root: Path) -> Path | None:
    """查找 web.xml。"""
    candidates = [
        project_root / "src" / "main" / "webapp" / "WEB-INF" / "web.xml",
        project_root / "webapp" / "WEB-INF" / "web.xml",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _parse_cxf_servlet(web_xml: Path) -> str | None:
    """从 web.xml 中解析 CXFServlet 的 URL pattern。"""
    try:
        tree = ET.parse(web_xml)
        root = tree.getroot()
        # 找 <servlet-class> 含 CXFServlet 的 servlet
        for servlet_elem in root.iter():
            tag = servlet_elem.tag.split("}")[-1] if "}" in servlet_elem.tag else servlet_elem.tag
            if tag != "servlet":
                continue
            servlet_name = None
            servlet_class = None
            for child in servlet_elem:
                child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if child_tag == "servlet-name":
                    servlet_name = child.text
                elif child_tag == "servlet-class" and child.text and "CXFServlet" in child.text:
                    servlet_class = child.text
            if servlet_class and servlet_name:
                # 找对应的 servlet-mapping
                for mapping in root.iter():
                    mtag = mapping.tag.split("}")[-1] if "}" in mapping.tag else mapping.tag
                    if mtag != "servlet-mapping":
                        continue
                    map_name = None
                    map_pattern = None
                    for child in mapping:
                        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if child_tag == "servlet-name" and child.text == servlet_name:
                            map_name = servlet_name
                        elif child_tag == "url-pattern":
                            map_pattern = child.text
                    if map_name and map_pattern:
                        return map_pattern
    except ET.ParseError:
        pass
    return None


def _find_spring_xml_files(project_root: Path) -> list[Path]:
    """查找 Spring XML 配置文件。"""
    results: list[Path] = []
    search_dirs = [
        project_root / "src" / "main" / "resources",
        project_root / "src" / "main" / "webapp" / "WEB-INF",
    ]
    for d in search_dirs:
        if d.is_dir():
            results.extend(d.rglob("*.xml"))
    return results


def _parse_jaxws_endpoints(xml_file: Path) -> list[dict[str, str]]:
    """从 Spring XML 中解析 <jaxws:endpoint> 声明。"""
    results: list[dict[str, str]] = []
    try:
        tree = ET.parse(xml_file)
        root = tree.getroot()
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag != "endpoint":
                continue
            # 检查是否在 jaxws 命名空间
            ns = elem.tag.split("}")[0] + "}" if "}" in elem.tag else ""
            if _NS_CXF not in ns and "jaxws" not in ns.lower():
                continue
            results.append({
                "id": elem.get("id", ""),
                "implementor": elem.get("implementor", ""),
                "address": elem.get("address", ""),
                "source": f"spring-xml:{xml_file.name}",
            })
    except ET.ParseError:
        pass
    return results


def _parse_jaxrs_servers(xml_file: Path) -> list[dict[str, str]]:
    """从 Spring XML 中解析 <jaxrs:server> 声明。"""
    results: list[dict[str, str]] = []
    try:
        tree = ET.parse(xml_file)
        root = tree.getroot()
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag != "server":
                continue
            ns = elem.tag.split("}")[0] + "}" if "}" in elem.tag else ""
            if _NS_CXF_RS not in ns and "jaxrs" not in ns.lower():
                continue
            results.append({
                "id": elem.get("id", ""),
                "serviceClass": elem.get("serviceClass", ""),
                "address": elem.get("address", ""),
                "source": f"spring-xml:{xml_file.name}",
            })
    except ET.ParseError:
        pass
    return results
