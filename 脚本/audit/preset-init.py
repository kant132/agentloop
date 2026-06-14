"""
preset-init.py

扫描项目自动生成 preset.json 草案（首次扫描前用）。

检测：
1. pom.xml / build.gradle → groupId, artifactId, dependencies
2. src/main/java 主包前缀
3. import 主流框架
4. @Xxx 自定义注解候选

输出：
  项目/{groupId}/preset.json.draft（待人工 review 后改名为 preset.json）
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path


def detect_from_pom(pom_path: str) -> dict:
    """从 pom.xml 提取 groupId / artifactId / dependencies"""
    if not os.path.exists(pom_path):
        return {}
    try:
        content = open(pom_path, "r", encoding="utf-8").read()
    except Exception:
        return {}

    result = {"groupId": None, "artifactId": None, "dependencies": []}

    # 简单正则（不解析 XML）
    m = re.search(r"<groupId>([^<]+)</groupId>", content)
    if m:
        result["groupId"] = m.group(1).strip()
    m = re.search(r"<artifactId>([^<]+)</artifactId>", content)
    if m:
        result["artifactId"] = m.group(1).strip()
    # dependencies
    for m in re.finditer(r"<artifactId>([^<]+)</artifactId>", content):
        dep = m.group(1).strip()
        if dep not in result["dependencies"]:
            result["dependencies"].append(dep)

    return result


def detect_main_package(src_root: str) -> str:
    """找 src/main/java 下的主包前缀（出现次数最多的顶层包）"""
    if not os.path.exists(src_root):
        return ""
    counter = {}
    for root, dirs, files in os.walk(src_root):
        for f in files:
            if f.endswith(".java"):
                rel = os.path.relpath(os.path.join(root, f), src_root)
                # rel = com/example/x/Foo.java → com.example.x
                parts = rel.split(os.sep)
                if len(parts) >= 2:
                    top = parts[0]  # com
                    counter[top] = counter.get(top, 0) + 1
    if not counter:
        return ""
    # 取最多 + 第二多拼起来
    sorted_top = sorted(counter.items(), key=lambda x: -x[1])
    return sorted_top[0][0] if sorted_top else ""


def detect_frameworks(src_root: str) -> list:
    """扫 import 找主流框架"""
    frameworks = {
        "spring-web": ["org.springframework.web", "org.springframework.web.bind.annotation"],
        "spring-boot": ["org.springframework.boot"],
        "spring-data-jpa": ["org.springframework.data.jpa"],
        "spring-data-redis": ["org.springframework.data.redis"],
        "spring-security": ["org.springframework.security"],
        "dubbo": ["org.apache.dubbo"],
        "kafka": ["org.apache.kafka"],
        "rabbitmq": ["org.springframework.amqp", "com.rabbitmq"],
        "mybatis": ["org.apache.ibatis"],
        "hibernate": ["org.hibernate"],
        "jackson": ["com.fasterxml.jackson"],
        "fastjson": ["com.alibaba.fastjson"],
    }
    found = set()
    if not os.path.exists(src_root):
        return []

    for root, dirs, files in os.walk(src_root):
        # 限制深度避免过慢
        if root.count(os.sep) - src_root.count(os.sep) > 5:
            dirs.clear()
            continue
        for f in files:
            if not f.endswith(".java"):
                continue
            try:
                content = open(os.path.join(root, f), "r", encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for fw, imports in frameworks.items():
                for imp in imports:
                    if imp in content:
                        found.add(fw)
                        break
    return sorted(found)


def detect_custom_annotations(src_root: str, main_pkg: str) -> list:
    """找 @Xxx 自定义注解候选（main_pkg 之外的简单类名注解）"""
    if not os.path.exists(src_root) or not main_pkg:
        return []

    candidates = {}  # name -> {fqn, count}
    for root, dirs, files in os.walk(src_root):
        if root.count(os.sep) - src_root.count(os.sep) > 5:
            dirs.clear()
            continue
        for f in files:
            if not f.endswith(".java"):
                continue
            try:
                content = open(os.path.join(root, f), "r", encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            # 找 import main_pkg.X.X.X
            for m in re.finditer(r"import\s+(" + re.escape(main_pkg) + r"[\w.]+)", content):
                fqn = m.group(1)
                # 只关注带 Annotation 的类
                if fqn.endswith("Annotation") or "annotation" in fqn.lower():
                    short = fqn.split(".")[-1]
                    if short not in candidates:
                        candidates[short] = {"fqn": fqn, "count": 0}
                    candidates[short]["count"] += 1

    # 简化：只输出 ≥ 3 处引用的候选
    return [
        {"name": "@" + name, "fqn": info["fqn"], "usage_count": info["count"]}
        for name, info in candidates.items()
        if info["count"] >= 3
    ]


def main():
    parser = argparse.ArgumentParser(description="生成项目 preset.json 草案")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--group-id", help="强制指定 groupId（不指定则从 pom.xml 读）")
    args = parser.parse_args()

    project_root = args.project_root
    pom_path = os.path.join(project_root, "pom.xml")
    gradle_path = os.path.join(project_root, "build.gradle")
    src_root = os.path.join(project_root, "src", "main", "java")

    # 1. 从 pom.xml 读
    pom_info = detect_from_pom(pom_path) if os.path.exists(pom_path) else {}
    group_id = args.group_id or pom_info.get("groupId")
    artifact_id = pom_info.get("artifactId")

    if not group_id:
        print("ERROR: 无法识别 groupId，请用 --group-id 手动指定", file=sys.stderr)
        sys.exit(1)

    # 2. 主包
    main_pkg = detect_main_package(src_root)
    if not main_pkg:
        # 退化用 group_id 前缀
        main_pkg = ".".join(group_id.split(".")[:2])

    # 3. 框架
    frameworks = detect_frameworks(src_root)
    primary = "spring-boot" if "spring-boot" in frameworks else (frameworks[0] if frameworks else "unknown")

    # 4. 自定义注解
    custom_annos = detect_custom_annotations(src_root, main_pkg)

    # 5. 组装
    preset = {
        "version": 1,
        "project": {
            "groupId": group_id,
            "artifactId": artifact_id or "unknown",
            "version": "0.0.0-SNAPSHOT",
        },
        "framework": {
            "primary": primary,
            "secondary": [f for f in frameworks if f != primary],
            "build_tool": "maven" if os.path.exists(pom_path) else "gradle",
        },
        "package": {
            "main": main_pkg,
            "modules": [],
        },
        "annotations": {
            "custom": [
                {**a, "meaning": "TODO_人工识别", "auth_equivalent": "TODO"}
                for a in custom_annos
            ],
            "framework_loaded": [
                "@PreAuthorize", "@Secured", "@RolesAllowed", "@RequiresPermissions"
            ],
        },
        "framework_skip_packages": [
            "java.", "javax.", "jdk.",
            "org.springframework.",
            "org.apache.dubbo.",
            "org.apache.kafka.",
            "org.hibernate.",
            "com.fasterxml.jackson.",
        ],
        "tech_stack_vuln_whitelist": {},
        "_note": "本文件由 preset-init.py 自动生成，TODO 项需人工 review 后改名为 preset.json",
    }

    # 6. 输出
    output_dir = Path("项目") / group_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "preset.json.draft"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(preset, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "output": str(output_path),
        "detected": {
            "groupId": group_id,
            "artifactId": artifact_id,
            "main_package": main_pkg,
            "frameworks": frameworks,
            "custom_annotations_count": len(custom_annos),
        },
        "next_step": f"人工 review {output_path} 后改名为 preset.json",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
