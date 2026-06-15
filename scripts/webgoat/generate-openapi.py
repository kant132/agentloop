"""
generate-openapi.py — 从 WebGoat Java 源码提取端点定义，生成 OpenAPI 3.0 spec

输出：webgoat-openapi.yaml（供 schemathesis fuzzing 使用）

用法：
    python scripts/generate-openapi.py --src D:\code\WebGoat-2025.3\src\main\java --output webgoat-openapi.yaml
"""
import re
import os
import json
import argparse
from pathlib import Path
from typing import Optional

# WebGoat 所有 Controller 端点的 context path
CONTEXT_PATH = "/WebGoat"

# Java -> OpenAPI 类型映射
TYPE_MAP = {
    "String": {"type": "string"},
    "int": {"type": "integer"},
    "Integer": {"type": "integer"},
    "long": {"type": "integer", "format": "int64"},
    "Long": {"type": "integer", "format": "int64"},
    "boolean": {"type": "boolean"},
    "Boolean": {"type": "boolean"},
    "double": {"type": "number"},
    "float": {"type": "number"},
    "byte[]": {"type": "string", "format": "binary"},
    "MultipartFile": {"type": "string", "format": "binary"},
}

# 正则：提取 Mapping 注解
# 支持: @PostMapping, @GetMapping, @PutMapping, @DeleteMapping, @PatchMapping, @RequestMapping
MAPPING_ANNOTATIONS = {
    "PostMapping": "post",
    "GetMapping": "get",
    "PutMapping": "put",
    "DeleteMapping": "delete",
    "PatchMapping": "patch",
}

# 提取类级 @RequestMapping 前缀
CLASS_MAPPING_RE = re.compile(
    r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?(?:\{"?([^"]*?)"?\s*(?:,\s*"[^"]*?")*\}|("?[^",\)]*?"))\s*\)',
    re.DOTALL
)

# 提取方法级 Mapping（path 或 value）
METHOD_MAPPING_RE = re.compile(
    r'@(Post|Get|Put|Delete|Patch|Request)Mapping\s*\(([^)]*)\)',
    re.DOTALL
)

# 提取 path 值（支持 path= 和直接字符串）
PATH_VALUE_RE = re.compile(
    r'(?:(?:path|value)\s*=\s*)?(?:"([^"]*?)"|\{([^}]+)\})',
    re.DOTALL
)

# 多 path 值
MULTI_PATH_RE = re.compile(r'"([^"]*?)"')

# 提取 @RequestParam
REQUEST_PARAM_RE = re.compile(
    r'@RequestParam\s*\(([^)]*)\)\s*([\w<>\[\]]+)\s+(\w+)',
    re.DOTALL
)
# 简化版（无括号）
REQUEST_PARAM_SIMPLE_RE = re.compile(
    r'@RequestParam\s+([\w<>\[\]]+)\s+(\w+)'
)

# 提取 @PathVariable
PATH_VAR_RE = re.compile(
    r'@PathVariable\s*(?:\((?:value\s*=\s*)?(?:"([^"]*?)"|([^\)]+))\))?\s*([\w<>\[\]]+)\s+(\w+)',
    re.DOTALL
)
PATH_VAR_SIMPLE_RE = re.compile(
    r'@PathVariable\s+([\w<>\[\]]+)\s+(\w+)'
)

# 提取 @RequestBody
REQUEST_BODY_RE = re.compile(
    r'@RequestBody\s*(?:\([^)]*\)\s*)?([\w<>\[\].]+)\s+(\w+)'
)

# consumes/produces
CONTENT_TYPE_RE = re.compile(r'(?:consumes|produces)\s*=\s*([^,\)]+)', re.DOTALL)


def normalize_path(prefix: str, path: str) -> str:
    """拼接 prefix + path，确保以 / 开头"""
    prefix = prefix.strip().strip('"').strip("'")
    path = path.strip().strip('"').strip("'")
    
    # 处理 Spring MVC 占位符
    if not path and not prefix:
        return "/"
    if not path:
        path = prefix
    elif prefix and not path.startswith("/") and not prefix.endswith("/"):
        path = prefix + "/" + path
    elif prefix:
        path = prefix + path
    
    if not path.startswith("/"):
        path = "/" + path
    
    # 转换 Spring {var} 为 OpenAPI {var}（已经是同格式）
    return path


def extract_class_prefix(java_content: str) -> str:
    """提取类级 @RequestMapping 前缀"""
    m = CLASS_MAPPING_RE.search(java_content)
    if m:
        val = m.group(1) or m.group(2) or ""
        val = val.strip()
        # 去除引号
        val = val.replace('"', '').replace("'", "")
        return val
    return ""


def extract_endpoints(java_content: str, file_path: str) -> list:
    """从单个 Java 文件提取所有端点定义"""
    endpoints = []
    class_prefix = extract_class_prefix(java_content)
    
    # 找到所有方法级 Mapping
    for m in METHOD_MAPPING_RE.finditer(java_content):
        annotation_type = m.group(1)  # Post/Get/Put/Delete/Patch/Request
        params_str = m.group(2).strip()
        
        # 确定 HTTP 方法
        if annotation_type == "Request":
            # 从 params 中提取 method=
            method_match = re.search(r'method\s*=\s*(?:RequestMethod\.)?(\w+)', params_str)
            http_method = method_match.group(1).lower() if method_match else "get"
        else:
            http_method = annotation_type.lower()
        
        # 提取 path
        paths = []
        if params_str:
            # 提取 path= 或 value= 的值
            path_match = re.search(r'path\s*=\s*"([^"]*?)"', params_str)
            if path_match:
                paths.append(path_match.group(1))
            else:
                value_match = re.search(r'value\s*=\s*"([^"]*?)"', params_str)
                if value_match:
                    paths.append(value_match.group(1))
                elif params_str.startswith('"'):
                    # 直接字符串
                    for p in MULTI_PATH_RE.finditer(params_str):
                        val = p.group(1)
                        if not any(kw in val for kw in ["MediaType", "APPLICATION", "TEXT", "ALL"]):
                            paths.append(val)
                        if len(paths) >= 3:
                            break
        
        if not paths:
            paths = [""]
        
        for path in paths:
            full_path = normalize_path(class_prefix, path)
            endpoints.append({
                "http_method": http_method,
                "path": full_path,
                "annotation": f"@{annotation_type}Mapping",
                "file": file_path,
                "params_str": params_str,
            })
    
    return endpoints


def extract_params_from_method(method_block: str, endpoint: dict) -> dict:
    """从方法块中提取参数定义"""
    params = []
    path_params = []
    body = None
    
    # @RequestParam
    for m in REQUEST_PARAM_RE.finditer(method_block):
        param_attrs = m.group(1)
        java_type = m.group(2)
        var_name = m.group(3)
        
        # 提取 param name（value="xxx"）
        name_match = re.search(r'(?:value|name)\s*=\s*"([^"]*?)"', param_attrs)
        param_name = name_match.group(1) if name_match else var_name
        
        # required
        req_match = re.search(r'required\s*=\s*(true|false)', param_attrs)
        required = req_match.group(1) != "false" if req_match else True
        
        # 默认值
        default_match = re.search(r'defaultValue\s*=\s*"([^"]*?)"', param_attrs)
        
        schema = TYPE_MAP.get(java_type, {"type": "string"})
        schema = dict(schema)
        
        param = {
            "name": param_name,
            "in": "query",
            "required": required,
            "schema": schema,
        }
        params.append(param)
    
    # 简化版 @RequestParam Type name
    for m in REQUEST_PARAM_SIMPLE_RE.finditer(method_block):
        java_type = m.group(1)
        var_name = m.group(2)
        schema = TYPE_MAP.get(java_type, {"type": "string"})
        params.append({
            "name": var_name,
            "in": "query",
            "required": True,
            "schema": dict(schema),
        })
    
    # @PathVariable
    for m in PATH_VAR_RE.finditer(method_block):
        path_name = m.group(1) or m.group(2)
        java_type = m.group(3)
        var_name = m.group(4)
        name = path_name if path_name and not path_name.startswith("@") else var_name
        schema = TYPE_MAP.get(java_type, {"type": "string"})
        path_params.append({
            "name": name,
            "in": "path",
            "required": True,
            "schema": dict(schema),
        })
    
    # 简化 @PathVariable
    for m in PATH_VAR_SIMPLE_RE.finditer(method_block):
        java_type = m.group(1)
        var_name = m.group(2)
        schema = TYPE_MAP.get(java_type, {"type": "string"})
        path_params.append({
            "name": var_name,
            "in": "path",
            "required": True,
            "schema": dict(schema),
        })
    
    # @RequestBody
    body_match = REQUEST_BODY_RE.search(method_block)
    if body_match:
        java_type = body_match.group(1)
        body = {
            "java_type": java_type,
            "description": f"Request body of type {java_type}",
        }
    
    # 从 path 推断 path params（如 /foo/{id}）
    path_var_names = re.findall(r'\{(\w+)\}', endpoint["path"])
    existing_path_names = {p["name"] for p in path_params}
    for var_name in path_var_names:
        if var_name not in existing_path_names:
            path_params.append({
                "name": var_name,
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            })
    
    return {"params": params, "path_params": path_params, "body": body}


def find_method_block(java_content: str, mapping_match) -> str:
    """根据 mapping 匹配位置，找到对应方法体"""
    # 从 mapping 匹配位置向后找到方法签名开始
    start = mapping_match.end()
    # 向后查找 } 来定位方法体（简化处理：取 200 个字符）
    block = java_content[start:start + 500]
    return block


def scan_java_source(src_dir: str) -> dict:
    """扫描整个 Java 源码目录，提取所有端点"""
    all_endpoints = []
    src_path = Path(src_dir)
    
    for java_file in src_path.rglob("*.java"):
        content = java_file.read_text(encoding="utf-8", errors="ignore")
        
        # 跳过非 Controller 类
        if "@RestController" not in content and "@Controller" not in content:
            continue
        
        endpoints = extract_endpoints(content, str(java_file))
        
        # 对每个端点，找到对应方法块提取参数
        for endpoint in endpoints:
            # 提取方法签名中的参数
            method_block = None
            # 用简单的正则找到方法声明
            path_escaped = re.escape(endpoint["path"].lstrip("/"))
            method_re = re.compile(
                rf'(?:public|private|protected)\s+\S+\s+\w+\s*\(([^)]*)\)',
                re.DOTALL
            )
            # 在 mapping 之后找最近的 public 方法
            mapping_idx = content.find(f'"{endpoint["path"].lstrip("/")}"')
            if mapping_idx == -1:
                mapping_idx = content.find(endpoint["path"].lstrip("/"))
            
            if mapping_idx > 0:
                method_text = content[mapping_idx:mapping_idx + 600]
                pm = method_re.search(method_text)
                if pm:
                    method_block = pm.group(0)
            
            if method_block:
                param_info = extract_params_from_method(method_block, endpoint)
            else:
                param_info = {"params": [], "path_params": [], "body": None}
            
            endpoint.update(param_info)
            all_endpoints.append(endpoint)
    
    return all_endpoints


def build_openapi_spec(endpoints: list, base_url: str) -> dict:
    """生成 OpenAPI 3.0 spec"""
    paths = {}
    
    for ep in endpoints:
        path = ep["path"]
        method = ep["http_method"]
        
        if path not in paths:
            paths[path] = {}
        
        # 构建 parameters 列表
        parameters = []
        for p in ep.get("path_params", []):
            parameters.append(p)
        for p in ep.get("params", []):
            parameters.append(p)
        
        operation = {
            "operationId": ep["file"].replace("\\", "/").split("/")[-1].replace(".java", "") + "_" + method,
            "summary": f"{ep['annotation']} {path}",
            "description": f"Source: {ep['file']}",
        }
        
        if parameters:
            operation["parameters"] = parameters
        
        # Request body
        if ep.get("body"):
            body_type = ep["body"]["java_type"]
            if body_type in ["String", "string"]:
                content_type = "text/plain"
                schema = {"type": "string"}
            elif body_type == "MultipartFile":
                content_type = "multipart/form-data"
                schema = {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}}
            else:
                content_type = "application/json"
                schema = {"type": "object"}
            
            operation["requestBody"] = {
                "content": {
                    content_type: {"schema": schema}
                }
            }
        elif method in ["post", "put", "patch"] and not ep.get("params"):
            # POST/PUT 通常需要 body
            operation["requestBody"] = {
                "content": {
                    "application/x-www-form-urlencoded": {
                        "schema": {"type": "object"}
                    }
                }
            }
        
        # Responses
        operation["responses"] = {
            "200": {"description": "successful operation"},
            "401": {"description": "unauthorized"},
            "403": {"description": "forbidden"},
            "404": {"description": "not found"},
            "500": {"description": "internal server error"},
        }
        
        paths[path][method] = operation
    
    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "WebGoat 2025.3 API (auto-generated for schemathesis fuzzing)",
            "version": "2025.3",
            "description": "Auto-generated OpenAPI spec from WebGoat source for dynamic security fuzzing",
        },
        "servers": [{"url": base_url}],
        "paths": paths,
    }
    
    return spec


def categorize_risk(endpoint: dict) -> str:
    """根据端点特征标记风险等级"""
    path = endpoint["path"].lower()
    
    high_risk_keywords = [
        "sqlinjection", "sqli", "xxe", "ssrf", "pathtraversal",
        "deserializ", "rce", "exec", "command", "upload",
        "register", "login", "auth", "access-control", "missingac",
        "jwt", "crypto", "spoofcookie", "hijack",
    ]
    
    for kw in high_risk_keywords:
        if kw in path:
            return "HIGH"
    
    if "xss" in path:
        return "MEDIUM"  # XSS 不是 fuzzing 主要目标
    
    return "LOW"


def main():
    parser = argparse.ArgumentParser(description="Generate OpenAPI spec from WebGoat Java source")
    parser.add_argument("--src", default=r"D:\code\WebGoat-2025.3\src\main\java",
                        help="Java source root directory")
    parser.add_argument("--output", default=r"D:\wiki\good-skill\agentloop\scripts\webgoat-openapi.yaml",
                        help="Output YAML file path")
    parser.add_argument("--base-url", default="http://localhost:8081/WebGoat",
                        help="WebGoat base URL")
    parser.add_argument("--json", action="store_true", help="Output as JSON instead of YAML")
    
    args = parser.parse_args()
    
    print(f"[+] Scanning Java source: {args.src}")
    endpoints = scan_java_source(args.src)
    print(f"[+] Found {len(endpoints)} endpoints")
    
    # 风险分类
    for ep in endpoints:
        ep["risk"] = categorize_risk(ep)
    
    high = [e for e in endpoints if e["risk"] == "HIGH"]
    med = [e for e in endpoints if e["risk"] == "MEDIUM"]
    low = [e for e in endpoints if e["risk"] == "LOW"]
    print(f"[+] Risk breakdown: HIGH={len(high)}, MEDIUM={len(med)}, LOW={len(low)}")
    
    # 生成 OpenAPI spec
    spec = build_openapi_spec(endpoints, args.base_url)
    
    print(f"[+] Generated spec with {len(spec['paths'])} paths")
    
    # 输出
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    if args.json:
        output_path = output_path.with_suffix(".json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)
    else:
        try:
            import yaml
            with open(output_path, "w", encoding="utf-8") as f:
                yaml.dump(spec, f, default_flow_style=False, allow_unicode=True)
        except ImportError:
            # fallback to JSON
            output_path = output_path.with_suffix(".json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(spec, f, indent=2, ensure_ascii=False)
    
    print(f"[+] Written to: {output_path}")
    
    # 同时输出一份端点清单（供审计参考）
    manifest_path = output_path.parent / "webgoat-endpoints.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        manifest = []
        for ep in endpoints:
            manifest.append({
                "method": ep["http_method"].upper(),
                "path": ep["path"],
                "risk": ep["risk"],
                "file": ep["file"],
                "params": [p["name"] for p in ep.get("params", [])],
                "path_params": [p["name"] for p in ep.get("path_params", [])],
            })
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"[+] Endpoint manifest: {manifest_path}")
    
    # 打印高风险端点
    print(f"\n[!] HIGH-risk endpoints for fuzzing priority:")
    for ep in high:
        params_str = ", ".join([p["name"] for p in ep.get("params", [])] + 
                                [f"{{{p['name']}}}" for p in ep.get("path_params", [])])
        print(f"    {ep['http_method'].upper():6} {ep['path']:<55} [{params_str}]")


if __name__ == "__main__":
    main()
