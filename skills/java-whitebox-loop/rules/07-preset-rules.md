# 预置规则（Preset Rules）

> 启动时加载，**避免重复发现** + **加速扫描**。

## 一、为何需要预置规则

| 场景 | 无预置 | 有预置 |
|------|--------|--------|
| 项目 groupId | 需扫描全 pom.xml 推断 | 启动时填入，直接用 |
| 框架识别 | 需扫依赖、注解、配置 | 启动时填入 `spring-boot`，跳过识别 |
| 自定义注解 | 需扫所有 @Xxx 找内部注解 | 启动时填入 `@Inner, @OpenApi` |
| 业务规则特例 | 需人工总结后填入 | 启动时从 `项目/{groupId}/` 加载 |
| 包前缀 | 需统计 import 找主包 | 启动时填入 `com.example` |

**加速效果**：每端点节省 5-10 秒 = 200 端点 = 节省 15-30 分钟。

## 二、预置规则 Schema

存到 `项目/{groupId}/preset.json`：

```json
{
  "version": 1,
  "project": {
    "groupId": "com.example.x",
    "artifactId": "core-biz",
    "version": "1.0.0",
    "git_url": "https://git.example.com/x/core-biz.git"
  },
  "framework": {
    "primary": "spring-boot",
    "secondary": ["spring-data-jpa", "spring-security", "dubbo", "kafka"],
    "java_version": "1.8",
    "build_tool": "maven"
  },
  "package": {
    "main": "com.example",
    "modules": [
      {"name": "user", "package": "com.example.user", "size_hint": "medium"},
      {"name": "order", "package": "com.example.order", "size_hint": "large"}
    ],
    "internal_packages": ["com.example.internal", "com.example.admin"],
    "third_party_packages": ["com.example.thirdparty"]
  },
  "endpoints": {
    "known_dispatcher_servlets": ["DispatcherServlet"],
    "known_filter_patterns": [
      "com.example.security.*Filter",
      "com.example.auth.*Interceptor"
    ],
    "known_controllers": [
      "com.example.user.UserController",
      "com.example.order.OrderController"
    ]
  },
  "annotations": {
    "custom": [
      {
        "name": "@Inner",
        "fqn": "com.example.common.annotation.Inner",
        "meaning": "internal_endpoint",
        "auth_equivalent": "internal_network_only",
        "first_seen": "2026-06-12",
        "sha256": "a1b2c3..."
      },
      {
        "name": "@OpenApi",
        "fqn": "com.example.common.annotation.OpenApi",
        "meaning": "openapi_gateway_exposed",
        "auth_equivalent": "gateway_layer"
      }
    ],
    "framework_loaded": [
      "@PreAuthorize", "@Secured", "@RolesAllowed", "@RequiresPermissions"
    ]
  },
  "framework_skip_packages": [
    "java.", "javax.", "jdk.",
    "org.springframework.",
    "org.apache.dubbo.",
    "org.apache.kafka.",
    "org.hibernate.",
    "com.fasterxml.jackson."
  ],
  "tech_stack_vuln_whitelist": {
    "spring-boot": ["类型/注入类/SQL注入.md", "类型/鉴权类/缺失鉴权.md"],
    "spring-data-jpa": ["类型/注入类/SQL注入.md"],
    "dubbo": ["类型/反序列化/Java原生反序列化.md"],
    "kafka": ["类型/反序列化/Java原生反序列化.md"]
  }
}
```

## 三、加载时机

```python
def load_preset(group_id):
    preset_path = f"项目/{group_id}/preset.json"
    if not exists(preset_path):
        return None  # 启动扫描识别模式
    
    with open(preset_path) as f:
        preset = json.load(f)
    
    return preset
```

## 四、使用方式

### 4.1 groupId 加速
- 无需：扫 pom.xml 找 `<groupId>`
- 直接：`preset.project.groupId` → 用于 Memurai key 前缀

### 4.2 框架识别加速
- 无需：扫 `import org.springframework...` 推断框架
- 直接：`preset.framework.primary` → 加载对应注解清单

### 4.3 自定义注解加速
- 无需：扫所有 `@Xxx` 找项目内部注解
- 直接：`preset.annotations.custom` → 已知注解 + 鉴权等价物

### 4.4 包白名单/黑名单加速
- 无需：每次剪枝判定都查代码
- 直接：`preset.framework_skip_packages` → 不进入这些包的链

### 4.5 漏洞类型白名单加速
- 无需：加载全部 `类型/` 24 个文件
- 直接：`preset.tech_stack_vuln_whitelist[primary]` → 只加载相关子集

## 五、缺失时的回退

`preset.json` 不存在 → 启动**预置识别模式**：

```python
def auto_detect_preset(project_root):
    """扫描项目自动生成 preset.json 草案"""
    # 1. 读 pom.xml / build.gradle 找 groupId / artifactId / dependencies
    # 2. 扫 src/main/java 找主包前缀
    # 3. 扫 import 找主流框架
    # 4. 扫 @Xxx 找候选自定义注解
    # 5. 写项目/{groupId}/preset.json.draft（待人工确认）
```

## 六、跨项目共享

- 全局预置（不带 groupId）→ 通用模板
- 项目预置（带 groupId）→ 项目级

预置文件应在第一次扫描后生成，第二次扫描复用。**不要每次扫描都重新识别**。

## 七、预置 vs 知识沉淀

| 维度 | 预置规则 | 知识沉淀 |
|------|---------|---------|
| 写入时机 | 启动前 + 第一次扫描后 | 每轮 Loop 结束 |
| 内容 | 项目元信息（静态） | 经验教训（动态） |
| 路径 | `项目/{groupId}/preset.json` | `loop_audit/知识沉淀-round{N}.md` |
| 加载 | orchestrator 启动时 | FWD subagent 启动时 |
| 误用 | 当作"事实"使用 | 当作"参考"使用 |

## 八、初始化脚本

```bash
# 第一次扫描前生成草案
python 脚本/audit/preset-init.py --project-root ../target-project --group-id com.example.x
# 输出：项目/com.example.x/preset.json.draft
# 人工 review 后改名为 preset.json
```
