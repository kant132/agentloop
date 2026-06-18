"""
sink_registry.py
================

预置 sink 点库 + 动态 sink 识别。

依据:
- ``projects/_template/07-Sink表.json`` (通用 Java Sink 清单)
- ``types/注入类/SQL注入.md`` §3.1 危险 sink 函数

核心规则 (与 ``chain_builder`` 一致): **所有非 groupId 命名空间的方法视为 sink**
(设计文档 §"关键设计决策")。

API
---
- ``PRESET_SINKS``: ``dict[category, list[fqn]]`` 预置库
- ``is_preset_sink(method_signature) -> bool``
- ``identify_dynamic_sinks(group_id, all_methods) -> list[dict]``
- ``count_sinks_in_chain(chain_nodes) -> int``
- ``match_preset_sinks(chain_nodes) -> int``
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence


# ============================================================== 预置 sink 库
#
# 类别 → 方法 FQN 列表。来源: 07-Sink表.json + SQL注入.md §3.1。
# 仅收录高危/严重 sink; 低危 (log_injection 等) 省略以控制规模。
PRESET_SINKS: Dict[str, List[str]] = {
    "injection.sql": [
        "java.sql.Statement.executeQuery",
        "java.sql.Statement.execute",
        "java.sql.Statement.executeUpdate",
        "java.sql.Statement.executeBatch",
        "java.sql.Connection.prepareStatement",
        "org.springframework.jdbc.core.JdbcTemplate.query",
        "org.springframework.jdbc.core.JdbcTemplate.update",
        "org.springframework.jdbc.core.JdbcTemplate.queryForObject",
        "org.springframework.jdbc.core.JdbcTemplate.queryForList",
        "org.springframework.jdbc.core.NamedParameterJdbcTemplate.query",
        "javax.persistence.EntityManager.createNativeQuery",
        "jakarta.persistence.EntityManager.createNativeQuery",
        "org.hibernate.Session.createNativeQuery",
        "org.hibernate.query.Query.list",
    ],
    "injection.cmd": [
        "java.lang.Runtime.exec",
        "java.lang.ProcessBuilder.start",
        "java.lang.ProcessBuilder.<init>",
        "javax.script.ScriptEngine.eval",
        "groovy.lang.GroovyShell.evaluate",
    ],
    "injection.ldap": [
        "javax.naming.directory.DirContext.search",
        "javax.naming.directory.InitialDirContext.search",
        "org.springframework.ldap.core.LdapTemplate.search",
    ],
    "injection.expression": [
        "org.springframework.expression.spel.standard.SpelExpressionParser.parseExpression",
        "org.springframework.expression.Expression.getValue",
        "org.springframework.expression.Expression.setValue",
        "ognl.Ognl.getValue",
        "ognl.Ognl.setValue",
        "org.mvel2.MVEL.eval",
        "org.mvel2.MVEL.executeExpression",
        "javax.el.ELProcessor.eval",
    ],
    "injection.nosql": [
        "com.mongodb.client.MongoCollection.find",
        "com.mongodb.client.MongoCollection.updateOne",
        "com.mongodb.client.MongoCollection.deleteOne",
        "org.springframework.data.mongodb.core.MongoTemplate.find",
    ],
    "injection.xpath": [
        "javax.xml.xpath.XPath.evaluate",
        "javax.xml.xpath.XPathFactory.newInstance",
    ],
    "injection.xxe": [
        "javax.xml.parsers.DocumentBuilderFactory.newInstance",
        "javax.xml.parsers.SAXParserFactory.newInstance",
        "javax.xml.stream.XMLInputFactory.newInstance",
        "javax.xml.transform.TransformerFactory.newInstance",
    ],
    "deserialization": [
        "java.io.ObjectInputStream.readObject",
        "java.io.ObjectInputStream.readUnshared",
        "java.beans.XMLDecoder.readObject",
        "com.thoughtworks.xstream.XStream.fromXML",
        "org.yaml.snakeyaml.Yaml.load",
        "org.yaml.snakeyaml.Yaml.loadAs",
        "com.fasterxml.jackson.databind.ObjectMapper.readValue",
        "com.fasterxml.jackson.databind.ObjectMapper.readTree",
        "com.alibaba.fastjson.JSON.parseObject",
        "com.alibaba.fastjson.JSON.parse",
    ],
    "ssrf": [
        "java.net.URL.openConnection",
        "java.net.URL.openStream",
        "java.net.HttpURLConnection.connect",
        "org.springframework.web.client.RestTemplate.getForObject",
        "org.springframework.web.client.RestTemplate.postForObject",
        "org.springframework.web.client.RestTemplate.exchange",
        "org.springframework.web.reactive.function.client.WebClient.get",
        "org.springframework.web.reactive.function.client.WebClient.post",
        "org.apache.hc.client5.http.classic.HttpClient.execute",
        "okhttp3.Request$Builder.url",
    ],
    "path_traversal": [
        "java.io.File.<init>",
        "java.io.FileInputStream.<init>",
        "java.io.FileOutputStream.<init>",
        "java.io.RandomAccessFile.<init>",
        "java.nio.file.Paths.get",
        "java.nio.file.Path.resolve",
        "java.nio.file.Files.write",
        "java.nio.file.Files.readAllBytes",
        "java.nio.file.Files.newInputStream",
    ],
    "xss": [
        "java.io.PrintWriter.write",
        "java.io.PrintWriter.println",
    ],
    "open_redirect": [
        "javax.servlet.http.HttpServletResponse.sendRedirect",
        "org.springframework.web.servlet.view.RedirectView.setUrl",
    ],
    "crypto_weak": [
        "java.security.MessageDigest.getInstance",
        "javax.crypto.Cipher.getInstance",
        "java.util.Random.nextInt",
        "java.util.Random.nextLong",
    ],
    "file_upload": [
        "org.springframework.web.multipart.MultipartFile.transferTo",
        "org.springframework.web.multipart.MultipartFile.getBytes",
    ],
    "jndi": [
        "javax.naming.InitialContext.lookup",
    ],
}


# 扁平化: 所有 preset fqn → category 的反查表 (import 期一次性构建)
_FQN_TO_CATEGORY: Dict[str, str] = {
    fqn: cat
    for cat, fqns in PRESET_SINKS.items()
    for fqn in fqns
}


# ============================================================== 工具

def _normalize_signature(sig: str) -> str:
    """剥离实参: ``Class.method(args)`` → ``Class.method``。

    chain_builder 的 JAR 输出形如
    ``"java.sql.Statement.executeQuery(query + userInput)"``, 需归一化后才能与 preset 匹配。
    """
    if not sig:
        return ""
    i = sig.find("(")
    return sig[:i] if i >= 0 else sig


def is_preset_sink(method_signature: str) -> bool:
    """方法签名是否命中预置 sink 库。

    自动剥离 ``(args)`` 后缀, 支持带实参的 called_fqn 文本。
    """
    norm = _normalize_signature(method_signature).strip()
    if not norm:
        return False
    # 精确匹配
    if norm in _FQN_TO_CATEGORY:
        return True
    # 内部类 $ 写法兼容: Request$Builder.url ≈ Request.Builder.url
    alt = norm.replace("$", ".")
    return alt in _FQN_TO_CATEGORY


# ============================================================== 动态 sink

@dataclass
class DynamicSink:
    """identify_dynamic_sinks 返回的单条记录。"""
    fqn: str
    category: str            # preset 类别名; 未命中 = "dynamic"
    is_preset: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"fqn": self.fqn, "category": self.category, "is_preset": self.is_preset}


def identify_dynamic_sinks(
    group_id: str,
    all_methods: Sequence[str],
) -> List[Dict[str, Any]]:
    """识别所有动态 sink。

    规则 (设计文档): **所有非 groupId 命名空间的方法 = 动态 sink**。

    Parameters
    ----------
    group_id:
        项目 groupId, 例如 ``"org.owasp.webgoat"``。
    all_methods:
        待判定的方法 FQN / 签名列表。

    Returns
    -------
    list[dict]: 每条 ``{fqn, category, is_preset}``
    """
    gid = (group_id or "").strip()
    out: List[DynamicSink] = []
    for m in all_methods:
        if not m:
            continue
        norm = _normalize_signature(m)
        # groupId 命名空间 → 内部方法, 不是 sink
        if gid and (norm.startswith(gid + ".") or norm.startswith(gid + "#")):
            continue
        cat = _FQN_TO_CATEGORY.get(norm) or _FQN_TO_CATEGORY.get(
            norm.replace("$", ".")
        )
        out.append(DynamicSink(
            fqn=m,
            category=cat or "dynamic",
            is_preset=cat is not None,
        ))
    return [d.to_dict() for d in out]


# ============================================================== 链上 sink 计数

def _iter_chain_sinks(chain_nodes: Sequence[Mapping[str, Any]]):
    """遍历链节点的所有 sink FQN (展平)。"""
    for n in chain_nodes:
        for s in n.get("sinks", []) or []:
            yield s


def count_sinks_in_chain(chain_nodes: Sequence[Mapping[str, Any]]) -> int:
    """链上 sink 总数 (所有节点 sinks 列表长度之和)。"""
    return sum(1 for _ in _iter_chain_sinks(chain_nodes))


def match_preset_sinks(chain_nodes: Sequence[Mapping[str, Any]]) -> int:
    """链上命中预置 sink 库的数量 (用于 priority 公式的 preset_match 项)。"""
    return sum(1 for s in _iter_chain_sinks(chain_nodes) if is_preset_sink(s))
