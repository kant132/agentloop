# 端点: org.owasp.webgoat.webwolf.mailbox.MailboxController#deleteAllMail

**chain_id**: cf9c07b1dc6c  
**priority**: 1  **total_sinks**: 1  **last_sinks**: 1

## 调用链（共1层）
```
  depth=0: org.owasp.webgoat.webwolf.mailbox::MailboxController::deleteAllMail(sink num: 1) ← [sink]
```

## 污点传播分析
- depth=0: org.owasp.webgoat.webwolf.mailbox::MailboxController::deleteAllMail(sink num: 1) — [触发漏洞]

## 审计结论

### injection
- **verdict**: inconclusive
- 原因: 
- 需要: 

### file
- **verdict**: N/A

### auth
- **verdict**: N/A

### biz
- **verdict**: N/A

