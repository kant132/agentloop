# XXE（XML 外部实体注入）

> **类型 ID**: `AP-INJ-XXE`
> **轨道**: FWD-A
> **业务域**: 9 类必查之一（仅在项目处理 XML 时加载）

## 一、定义

XML 解析器开启了外部实体加载，攻击者通过恶意 XML 读取本地文件、SSRF、甚至 RCE。

## 二、典型场景

```java
// ❌ 危险
DocumentBuilder db = DocumentBuilderFactory.newInstance().newDocumentBuilder();
Document doc = db.parse(userInput);  // 可能含 <!ENTITY xxe SYSTEM "file:///etc/passwd">

// ❌ SAX
SAXParser parser = SAXParserFactory.newInstance().newSAXParser();
parser.parse(userInput, handler);
```

## 三、检测启发式

```yaml
pattern: DocumentBuilderFactory.newInstance().newDocumentBuilder().parse($VAR)
pattern: SAXParserFactory.newInstance().newSAXParser().parse($VAR, $H)
```

## 四、消毒器

```java
// ✅ 安全配置
DocumentBuilderFactory dbf = DocumentBuilderFactory.newInstance();
dbf.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
dbf.setFeature("http://xml.org/sax/features/external-general-entities", false);
dbf.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
dbf.setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false);
dbf.setXIncludeAware(false);
dbf.setExpandEntityReferences(false);
```

## 五、误报模式

- 上述安全配置已启用
- 解析的 XML 完全硬编码
- 输入是 base64 解码的固定 schema
