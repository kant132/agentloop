---
name: jadx-python-decompile
description: jar 反编译子 skill。当项目仅含 jar 包无源码时调用。反编译后需将 .class 转 .java，再走 codegraph 索引。
---

# jadx 反编译（jadx-python-decompile）

> Phase 1 / Phase 5 按需调用。**仅在项目是 jar-only 时**触发。

## 一、调用时机

- Phase 1：发现 `lib/*.jar` 或 `BOOT-INF/lib/*.jar` 且无源码
- Phase 5：某个端点实现类在 jar 中（非项目自身代码）

## 二、工作流

```bash
# 1. 找 jar
find . -name "*.jar" -not -path "*/.m2/*"

# 2. 反编译到 src-decompiled/
jadx -d src-decompiled/ path/to/target.jar

# 3. codegraph 索引反编译后的源码
codegraph init
codegraph index src-decompiled/
```

## 三、注意事项

- 反编译后的代码可能含 `synthetic` 字段（AOP 织入的中间方法）→ 在 FWD 中保留
- 内部类名格式：`OuterClass$InnerClass`
- 泛型信息丢失（jadx 默认）→ 部分判定器可能受影响
- 内部 lambda 名称可能不准确

## 四、codegraph FQN 解析

反编译后 FQN 仍为原始 `com.example.X`，但 file 路径含 `src-decompiled/`。

agent 在 FWD 时需注意：
- key 中加 `view:source` / `view:decompiled` 区分
- 优先用 source（项目源码）
- 仅当 source 缺失时用 decompiled
