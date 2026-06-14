---
name: playwright-skill
description: 浏览器自动化子 skill。Phase 5 PoC 前端验证用。触发 JavaScript、模拟点击、捕获网络请求、截屏。
---

# Playwright 浏览器自动化（playwright-skill）

> Phase 5 PoC 前端验证。

## 一、核心用法

### 1.1 加载页面
```typescript
await page.goto('https://target.com/login');
```

### 1.2 填写表单
```typescript
await page.fill('input[name="username"]', 'admin');
await page.fill('input[name="password"]', "' OR '1'='1");
```

### 1.3 提交 + 等待
```typescript
await Promise.all([
  page.waitForNavigation(),
  page.click('button[type="submit"]')
]);
```

### 1.4 截屏
```typescript
await page.screenshot({ path: 'poc-evidence.png' });
```

### 1.5 捕获网络
```typescript
const requests = [];
page.on('request', req => requests.push(req.url()));
page.on('response', res => requests.push(`${res.status()} ${res.url()}`));
```

## 二、与 FWD-X 的衔接

- FWD-X 给出 `poc_payload`
- playwright 加载到浏览器 → 提交 → 观察响应
- 返回：触发成功 / 失败 + 截屏 + 网络日志

## 三、必读

- `conduct/必读/02-环境感知.md`
- PoC subagent 必读

## 四、依赖

- 浏览器：Chromium / Firefox / WebKit
- Node.js
- MCP `playwright` 服务

## 五、安全注意

- 不要在生产环境执行破坏性 PoC
- 重要操作前先备份 / 截图存档
- cookie / session 隔离（不同 PoC 用不同 profile）
