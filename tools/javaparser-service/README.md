# Java Method Call Extractor

基于 [JavaParser](https://github.com/javaparser/javaparser) 的方法调用提取工具。输入一个 Java 文件，提取每个方法内调用的所有其他方法，输出 JSON 格式结果。

## 输出格式

```json
[
  {
    "startLine": 10,
    "methodSignature": "public void addItem(String item)",
    "calledFQN": "java.util.List.add(item)"
  }
]
```

| 字段 | 说明 |
|---|---|
| `startLine` | 外层方法声明的起始行号（从 0 算） |
| `methodSignature` | 外层方法签名（含修饰符、返回类型、形参） |
| `calledFQN` | 被调用方法的 Fully Qualified Name + 调用点实参 |

## 构建

```bash
mvn clean package
```

## 使用

```bash
java -jar target/java-method-call-extractor-1.0.0.jar <file.java> [sourceRoot]
```

- `file.java`: 要分析的 Java 文件
- `sourceRoot`（可选）: 项目源码根目录，用于跨文件 FQN 解析（例如同项目内多个类的相互调用解析）

### 示例

```bash
java -jar target/java-method-call-extractor-1.0.0.jar test-data/SampleService.java test-data
```

## 特性

- **JDK 21** + **JavaParser 3.26.3**
- **符号解析**: `ReflectionTypeSolver`（JDK 类）+ `JavaParserTypeSolver`（项目源码）
- **降级策略**: 无法解析时返回原始调用文本，不丢数据
- **Fat JAR**: `maven-shade-plugin` 打包为单文件，无需额外依赖
- **自动探测项目根**: 如未指定 sourceRoot，自动向上查找 `src/main/java`

## 目录结构

```
├── pom.xml
├── src/main/java/com/javaparsextract/Main.java
├── target/java-method-call-extractor-1.0.0.jar  # 构建产物
└── test-data/SampleService.java                 # 测试样例
```
