# Java 原生反序列化

> **类型 ID**: `AP-DESER-JAVA`
> **轨道**: FWD-A（有明确 sink）
> **业务域**: 9 类必查之一（最高危）

## 一、定义

用户输入流被传入 `ObjectInputStream.readObject()`，攻击者可通过构造恶意序列化对象执行任意代码（gadget chain）。

## 二、典型场景

```java
// ❌ 直接反序列化用户输入
ObjectInputStream ois = new ObjectInputStream(request.getInputStream());
Object obj = ois.readObject();  // gadget chain 攻击
```

## 三、检测启发式

### 3.1 AST 模式
```yaml
pattern: |
  ObjectInputStream $OIS = new ObjectInputStream($STREAM);
  $OBJ = $OIS.readObject();
```

### 3.2 SQLite 特征
```sql
SELECT m.fqn FROM method m
WHERE m.body LIKE '%ObjectInputStream%'
  AND m.body LIKE '%readObject%'
```

## 四、消毒器

| 消毒器 | 检测特征 |
|--------|---------|
| **`ObjectInputFilter`** | `ois.setObjectInputFilter(filter)` + 白名单 |
| **白名单类** | 仅允许业务类反序列化 |
| **完全避免** | 用 JSON / Protobuf 替代 |
| **JEP 290** | `serialFilter` 全局配置 |
| **SnakeYaml safeLoad** | `Yaml.load` → `Yaml.safeLoad` |
