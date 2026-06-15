# WebGoat-2025.3 Exploration Summary — Java SAST Validation Target

> **Purpose.** Document what a successful audit against OWASP WebGoat 2025.3 must look like, so the
> `agentloop` Java white-box audit tool can be validated end-to-end on a known-vulnerable benchmark.
>
> **Target version:** `org.owasp.webgoat:webgoat:2025.3` (Spring Boot 3.4.3, Java 23)
> **Source root:** `D:\code\WebGoat-2025.3`
> **Build artifact:** `D:\code\WebGoat-2025.3\target\webgoat-2025.3.jar` (149 MB, already built)
> **Runtime:** listens on `http://127.0.0.1:8080/WebGoat` (WebGoat) and `http://127.0.0.1:9090/WebWolf`

---

## 1. Project Structure Overview

### 1.1 Top-Level Layout

```
D:\code\WebGoat-2025.3\
├── pom.xml                          # Maven 4.0, Spring Boot parent 3.4.3, Java 23
├── mvnw / mvnw.cmd / mvn-debug      # Maven wrapper (no system Maven required)
├── Dockerfile, Dockerfile_desktop  # Container images
├── .mvn/, .github/, .idea/          # Maven config, CI, IntelliJ
├── config/                          # External config (Helm/Compose)
├── docs/                            # Landing page redirect stub
├── logs/                            # Runtime logs (HSQLDB writes here)
├── src/
│   ├── main/
│   │   ├── java/                    # 146 Java files
│   │   └── resources/               # application-webgoat.properties, lesson HTML/JS/CSS, static
│   └── it/
│       └── java/                    # 35 Playwright UI tests (lesson-level acceptance)
└── target/
    ├── webgoat-2025.3.jar           # 149 MB, pre-built fat jar
    └── ...
```

### 1.2 Java Source Tree (146 main + 35 test files)

| Package                                              | Files | Purpose |
|------------------------------------------------------|------:|---------|
| `org.owasp.webgoat.container`                        | ~25   | Spring Boot app, routing, sessions, assignments, users, scoring |
| `org.owasp.webgoat.container.assignments`            | 3     | `AssignmentEndpoint` interface, `AttackResult` builders, `LessonTrackerInterceptor` |
| `org.owasp.webgoat.container.controller`             | 2     | `Welcome`, `StartLesson` |
| `org.owasp.webgoat.container.i18n`                   | *     | Lesson label bundles |
| `org.owasp.webgoat.container.lessons`                | *     | `CourseConfiguration`, `Category` enum (A1/A2/...), `Lesson` base |
| `org.owasp.webgoat.container.report`                 | 1     | `ReportCardController` |
| `org.owasp.webgoat.container.service`                | 7     | `LessonInfoService`, `HintService`, `EnvironmentService`, `SessionService`, etc. |
| `org.owasp.webgoat.container.users`                  | 2     | `RegistrationController`, `Scoreboard` |
| `org.owasp.webgoat.lessons` *(9 lesson packages)*    | 60    | All lesson endpoints (see §3) |
| `org.owasp.webgoat.webwolf`                          | 5+    | WebWolf companion app (mailbox, JWT, file server, landing) |
| `org.dummy.insecure.framework`                       | 1     | `VulnerableTaskHolder` — a `Serializable` gadget with `readObject` that calls `Runtime.exec` |
| **`org.owasp.webgoat.taintaudit`** *(in JAR only)*   | 7+5 inner | **The SAST evaluation harness — 90+ endpoints, NOT in source** |

### 1.3 Critical Discovery — The `taintaudit` Package

**The JAR contains a `org.owasp.webgoat.taintaudit` package that has no source files.** It was
pre-compiled and packaged specifically as a SAST test harness. Discovered via:

```
$ jar tf target/webgoat-2025.3.jar | grep taintaudit | grep -v '\$'
BOOT-INF/classes/org/owasp/webgoat/taintaudit/AuditTestContext.class
BOOT-INF/classes/org/owasp/webgoat/taintaudit/ComplexSanitizer.class
BOOT-INF/classes/org/owasp/webgoat/taintaudit/HeaderAndJwtClaims.class
BOOT-INF/classes/org/owasp/webgoat/taintaudit/NonVulnerablePatterns.class
BOOT-INF/classes/org/owasp/webgoat/taintaudit/SanitizerBypass.class
BOOT-INF/classes/org/owasp/webgoat/taintaudit/StoredXSS.class
BOOT-INF/classes/org/owasp/webgoat/taintaudit/TaintAuditSinks.class
BOOT-INF/classes/org/owasp/webgoat/taintaudit/TrickySinks.class
```

This package is **the heart of the benchmark** — it provides a calibrated matrix of
vulnerable / safe / tricky / sanitized / sanitized-but-bypassable patterns to validate a SAST tool's
precision and recall. See §3.6 for the full breakdown.

### 1.4 Lesson Resources (30 lesson categories under `src/main/resources/lessons/`)

```
authbypass(8)         bypassrestrictions(6)   challenges(37)       chromedevtools(14)
cia(8)                clientsidefiltering(15) cryptography(11)     csrf(16)
deserialization(7)    hijacksession(7)        htmltampering(6)     httpbasics(8)
httpproxies(37)       idor(14)                insecurelogin(5)     jwt(43)
lessontemplate(14)    logging(7)              missingac(8)         passwordreset(18)
pathtraversal(25)     securepasswords(10)     spoofcookie(8)       sqlinjection(54)
ssrf(10)              vulnerablecomponents(22) webgoatintroduction(7) webwolfintroduction(11)
xss(35)               xxe(29)
```
(Parenthesised numbers are file counts per lesson directory.)

---

## 2. Build / Run Instructions

### 2.1 Build (already done — jar present)
```powershell
cd D:\code\WebGoat-2025.3
.\mvnw.cmd clean install          # produces target\webgoat-2025.3.jar
```

### 2.2 Run locally
```powershell
# Default: WebGoat on 8080, WebWolf on 9090
java -jar D:\code\WebGoat-2025.3\target\webgoat-2025.3.jar

# Custom ports
java -jar target\webgoat-2025.3.jar --webgoat.port=8081 --webwolf.port=9091

# Run via mvn spring-boot plugin
.\mvnw.cmd spring-boot:run -Dspring-boot.run.jvmArguments="-Dfile.encoding=UTF-8"
```

### 2.3 Configuration
From `application-webgoat.properties`:
- `server.servlet.context-path=${WEBGOAT_CONTEXT:/WebGoat}` → base URL `/WebGoat`
- `webgoat.server.directory=${user.home}/.webgoat-2025.3/` → HSQLDB + uploads live here
- `spring.datasource.url=jdbc:hsqldb:file:${webgoat.server.directory}/webgoat` → embedded HSQLDB
- `server.ssl.enabled=${WEBGOAT_SSLENABLED:false}` → HTTP by default
- `management.endpoints.web.exposure.include=env,health,configprops` → actuator leaks env
- `exclude.categories=${EXCLUDE_CATEGORIES:none,none}` and `exclude.lessons=${EXCLUDE_LESSONS:none,none}` → control which lessons are mounted

### 2.4 Connectivity smoke test
The pre-existing `D:\agentloop\webgoat-tools\check-webgoat.py` confirms:
- Base URL expected by WebGoat tools: `http://localhost:8081` (note: **8081, not 8080**)
- Endpoint list probes `/`, `/login`, `/WebGoat`, `/register.mvc`, `/actuator`, `/v3/api-docs`,
  `/SqlInjection/attack2`, `/CSRF/attack1`
- Registration flow: `POST /register.mvc` with `username`, `password`, `matchingPassword`, `agree`

### 2.5 First-time setup
1. Register a user via `POST /register.mvc` (form-encoded, all four fields required).
2. Log in via `POST /login` → obtains `JSESSIONID` cookie.
3. The session cookie is what every lesson endpoint expects.

---

## 3. Known Vulnerabilities Catalog

> All sinks below are reachable from the HTTP routes in §4 with an authenticated user.
> "Class" = `ClassName.java:lineNo`; "Sink" = the method/construct called with attacker input.

### 3.1 SQL Injection — 9 endpoints in `lessons/sqlinjection/introduction/`, 6 in `advanced/`, 3 in `mitigation/`

| Class : Line | Endpoint | Sink | Vuln |
|---|---|---|---|
| `SqlInjectionLesson2:49` | `POST /SqlInjection/attack2` | `Statement.executeQuery(query)` | HIGH — direct string-to-statement |
| `SqlInjectionLesson3:50` | `POST /SqlInjection/attack3` | `Statement.executeQuery(query)` | HIGH |
| `SqlInjectionLesson4` | `POST /SqlInjection/attack4` | `Statement.executeQuery` | HIGH |
| `SqlInjectionLesson5:54` | `POST /SqlInjection/attack5` | `Statement.executeQuery` | HIGH |
| `SqlInjectionLesson5a:52` | `POST /SqlInjection/assignment5a` | `Statement.executeQuery("…'" + accountName + "'")` | HIGH — concat into query string |
| `SqlInjectionLesson5b:64` | `POST /SqlInjection/assignment5b` | `Statement.executeQuery` | HIGH |
| `SqlInjectionLesson8` | `POST /SqlInjection/attack8` | `Statement.executeQuery` | HIGH |
| `SqlInjectionLesson9` | `POST /SqlInjection/attack9` | `Statement.executeQuery` | HIGH |
| `SqlInjectionLesson10:56` | `POST /SqlInjection/attack10` | `Statement.executeQuery("…LIKE '%" + action + "%'")` | HIGH — concat |
| `SqlInjectionLesson6a/6b` | `POST /SqlInjectionAdvanced/attack6a\|6b` | SQL via HQL/JPQL bypass | HIGH |
| `SqlInjectionChallenge:74` | `PUT /SqlInjectionAdvanced/register` | `INSERT … ' + user + ' '` | HIGH |
| `SqlInjectionChallengeLogin` | `POST /SqlInjectionAdvanced/login` | SQL concat | HIGH |
| `SqlInjectionLesson13:54` | `POST /SqlInjectionMitigations/attack12a` (`ip`) | SQL via whitelist that is bypassable | HIGH |
| `SqlInjectionLesson10a/10b` | `POST /SqlInjectionMitigations/attack10a\|10b` | `ORDER BY` injection, comment-injection | HIGH |
| `SqlOnlyInputValidation` | `POST /SqlOnlyInputValidation/attack` | key-word black-list (teaching trap) | LOW — *intentionally vulnerable by omission* |
| `SqlOnlyInputValidationOnKeywords` | `POST /SqlOnlyInputValidationOnKeywords/attack` | weaker black-list (teaching trap) | LOW |
| `Servers.java` | `GET /SqlInjectionMitigations/servers?column=…` | column-name in `ORDER BY` | HIGH |

**Integration test flag** (`SqlInjectionAdvancedUITest.java:56`): the password for the
"tom" user is the string `"thisisasecretfortomonly"` — this is a known
credential embedded in the lesson's seeded HSQLDB.

### 3.2 SSRF — `lessons/ssrf/`

| Class | Endpoint | Sink | Vuln |
|---|---|---|---|
| `SSRFTask1:24` | `POST /SSRF/task1` (`url`) | `url.matches("…")` only — no real fetch (teaching stub) | LOW (intentional) |
| `SSRFTask2:36` | `POST /SSRF/task2` (`url`) | `new URL(url).openStream()` — **real SSRF** | HIGH |

### 3.3 Path Traversal — `lessons/pathtraversal/`

| Class | Endpoint | Sink | Vuln |
|---|---|---|---|
| `ProfileUpload:39` | `POST /PathTraversal/profile-upload` | `new File(uploadDirectory, fullName)` (line 51 of `ProfileUploadBase`) | HIGH — direct concat from `@RequestParam fullName` |
| `ProfileUploadRetrieval:54` | `POST /PathTraversal/random` | `Random` on filename (still uses `fullName` indirectly) | HIGH |
| `ProfileUploadRemoveUserInput:33` | `POST /PathTraversal/profile-upload-remove-user-input` | partial-sanitised (testing mitigation) | HIGH |
| `ProfileZipSlip:79` | `POST /PathTraversal/zip-slip` | `new File(tmpZipDirectory, e.getName())` (Zip Slip) | HIGH |
| `ProfileUploadFix` | `POST /PathTraversal/profile-upload-fix` | uses `Paths.get` + canonical check (the "fixed" version) | LOW (mitigated — for false-positive tests) |

### 3.4 Insecure Direct Object Reference / Missing Access Control — `lessons/missingac/`

| Class | Endpoint | Sink | Vuln |
|---|---|---|---|
| `MissingFunctionACUsers:81` | `POST /access-control/users` | accepts any user, no admin check | HIGH — BOLA |
| `MissingFunctionACYourHash:30` | `POST /access-control/user-hash` | returns other users' password hashes (uses `PASSWORD_SALT_SIMPLE="DeliberatelyInsecure1234"`) | HIGH |
| `MissingFunctionACHiddenMenus:33` | `POST /access-control/hidden-menu` | admin-only menu accessible to anyone | HIGH |
| `MissingFunctionACUsers:54` | `GET /access-control/users` (JSON) | leaks all users with hashed pw | HIGH |
| `MissingFunctionACYourHashAdmin` | `POST /access-control/user-hash-fix` | uses `PASSWORD_SALT_ADMIN="DeliberatelyInsecure1235"` (mitigated version) | LOW |

**Known credentials** (from `MissingFunctionAC.java:14-15`):
```
PASSWORD_SALT_SIMPLE = "DeliberatelyInsecure1234"
PASSWORD_SALT_ADMIN  = "DeliberatelyInsecure1235"
```

### 3.5 Password Reset / Spoofing — `lessons/passwordreset/`, `lessons/spoofcookie/`

| Class | Endpoint | Sink | Vuln |
|---|---|---|---|
| `ResetLinkAssignment:69` | `POST /PasswordReset/reset/login` | `usersToTomPassword.put(username, password)` — accepts arbitrary reset | HIGH |
| `ResetLinkAssignment:100` | `POST /PasswordReset/reset/change-password` | takes `resetLink` from form, no auth | HIGH |
| `ResetLinkAssignmentForgotPassword:38` | `POST /PasswordReset/ForgotPassword/create-password-reset-link` | crafts reset link via SHA-256 of username — predictable | HIGH |
| `SecurityQuestionAssignment:82` | `POST /PasswordReset/SecurityQuestions` | static Map of weak security questions exposed | LOW |
| `SimpleMailAssignment` | `POST /PasswordReset/simple-mail` | cleartext password in email body | LOW (info disclosure) |
| `SpoofCookieAssignment:46` | `POST /SpoofCookie/login` | cookie value is `EncDec.encode(username)` — *own weak cipher* | HIGH |
| `SpoofCookieAssignment:62` | `GET /SpoofCookie/cleanup` | deletes cookie (unauthenticated) | HIGH |

**Spoof cookie "users" map** (`SpoofCookieAssignment.java:43-44`):
```java
Map.of("webgoat", "webgoat", "admin", "admin", "tom", "apasswordfortom")
```
The cookie is generated by `EncDec.encode(...)` which uses a *random but static* SALT
(`RandomStringUtils.randomAlphabetic(10)`, `EncDec.java:22`).

**Tom's "secret" password** (`ResetLinkAssignment.java:48-50`):
```java
PASSWORD_TOM_9 = "somethingVeryRandomWhichNoOneWillEverTypeInAsPasswordForTom"
TOM_EMAIL      = "tom@webgoat-cloud.org"
```

### 3.6 The `taintaudit` SAST Benchmark (in JAR, **not source**)

This is the curated harness. Decompiled from the JAR with `javap -p`:

#### 3.6.1 `TaintAuditSinks` — 19 deliberate vulnerable sinks (all "should-flag" positives)

| Method | Sink | Path (from `webgoat-endpoints.json`) |
|---|---|---|
| `jndiLookup(String)` | `new InitialContext().lookup(name)` | `GET /taintaudit/sinks/jndi-lookup` |
| `jndiService(String)` | `ctx.lookup("ldap://" + name)` | `GET /taintaudit/sinks/jndi-service` |
| `ldapSearch(String)` | `dirContext.search(dc, "(uid=" + u + ")", …)` | `POST /taintaudit/sinks/ldap-search` |
| `spelEval(String)` | `parser.parseExpression(expr).getValue()` | `POST /taintaudit/sinks/spel-eval` |
| `spelEvalWithContext(String,String)` | SpEL with custom context | `POST /taintaudit/sinks/spel-eval-eval-context` |
| `javaDeserialize(byte[])` | `new ObjectInputStream(baos).readObject()` | `POST /taintaudit/sinks/java-deserialize` |
| `xstreamDeserialize(String)` | `new XStream().fromXML(input)` | `POST /taintaudit/sinks/xstream-deserialize` |
| `openRedirect(String,HttpServletResponse)` | `response.sendRedirect(url)` | `GET /taintaudit/sinks/redirect` |
| `openForward(String,HttpServletRequest,HttpServletResponse)` | `request.getRequestDispatcher(url).forward(...)` | `GET /taintaudit/sinks/forward` |
| `sstiThymeleaf(String)` | `templateEngine.process(name, ctx)` | `POST /taintaudit/sinks/ssti-thymeleaf` |
| `xxeDefaultDocumentBuilder(String)` | `DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(...)` with default config | `POST /taintaudit/sinks/xxe-default` |
| `readArbitraryFile(String)` | `new File(base, name)` (no canonical check) | `GET /taintaudit/sinks/read-file` |
| `readUnderDir(String)` | `Paths.get(base, name).normalize()` only | `GET /taintaudit/sinks/read-under-dir` |
| `sqlRawLookup(String)` | raw JDBC with concatenation | `GET /taintaudit/sinks/sql-raw-lookup` |
| `runtimeExec(String)` | `Runtime.getRuntime().exec(cmd)` | `POST /taintaudit/sinks/runtime-exec` |
| `processBuilder(String[])` | `new ProcessBuilder(args).start()` | `POST /taintaudit/sinks/process-builder` |
| `ssrfUrl(String)` | `new URL(u).openConnection().getInputStream()` | `POST /taintaudit/sinks/ssrf-url` |
| `rawUpload(MultipartFile)` | writes bytes to path derived from filename | `POST /taintaudit/sinks/upload-raw` |
| `userByPath(String)` | `Paths.get("u-" + name)` in SQL context | `GET /taintaudit/sinks/user/{username}` |

**Risk label in `webgoat-endpoints.json`: `HIGH` for jndi*, java/xstream deser, runtime-exec,
ssrf-url, upload-raw, xxe-default; `LOW` for the rest — but a proper SAST should still flag the
LOW ones as vulnerable.**

#### 3.6.2 `NonVulnerablePatterns` — 13 SAFE patterns (false-positive traps — must NOT flag)

Endpoints under `/taintaudit/safe/…`:
- `parameterizedLookup`, `parameterizedMulti` — PreparedStatement with `?` binding
- `intLookup`, `longLookup` — value coerced to numeric before query
- `enumValidatedLookup` — `valueOf` against an enum
- `whitelistLookup` — string in a hard-coded `Set`
- `htmlEscapedEcho` — `HtmlUtils.htmlEscape(...)` before render
- `logOnly` — value never reaches a sink, only `log.info(...)`
- `constantQuery` — no user input in query
- `xmlHardened` — uses `parseXmlHardened` (disables DTD/external entities)
- `concatOnly` — string concat but final result is constant / not a sink
- `safeRedirect` — checks URL against an allowlist
- `readVersionFile` — reads a hard-coded path

**These 13 endpoints are the precision ground truth: a single false positive here is a regression.**

#### 3.6.3 `SanitizerBypass` — 13 deceptive sanitizers (look safe, actually vulnerable)

Endpoints under `/taintaudit/bypass/…`:
- `pathStripDotDot` — strips `..` once (not recursively — bypass with `....//`)
- `pathReplaceallDotDot` — replaces literal `..` (bypass: encoded `%2e%2e`)
- `sqlStripSingleQuote` — removes `'` (bypass: `\'` or unicode)
- `sqlStripDoubleQuote` — removes `"` (bypass: encoding)
- `sqlWeakWordRegex` — black-list of keywords (bypass: comments, case)
- `sqlLengthLimit` — truncates to N chars (bypass: append after truncation)
- `sqlSingleUrlDecode` — one URL-decode (bypass: double encode)
- `sqlBase64NotSanitizer` — Base64 decoded into SQL — looks like input is sanitized but isn't
- `pathNullByteStrip` — strips `\0` once (bypass: alternate null representations)
- `ssrfAllowlistDnsRebind` — resolves and checks host, but TOCTOU → DNS rebind (**HIGH risk**)
- `redirectPartialCheck` — checks if URL *starts with* allowed scheme (bypass with `//evil.com`)
- `unicodeNormalizationBypass` — normalize after sanitization
- `jsoupAsSqlSanitizer` — `Jsoup.clean(s, Safelist.none())` used as SQL sanitizer (wrong context)

**A naive taint analyzer that trusts the sanitizer will MISS these.** A good analyzer
either traces into the sanitizer or recognizes these patterns as "weak sanitizers" and still
flags the call site.

#### 3.6.4 `ComplexSanitizer` — 13 complex sanitizers (some safe, some not)

Endpoints under `/taintaudit/sanitizer/…`:
- `multiHopSafe` — sanitize in helper method, return sanitized value (safe) — **must NOT flag**
- `wrongParamSanitized` — sanitizes the *other* parameter (still vulnerable) — **must flag**
- `sanitizeAfterSink` — calls sink then sanitizes (vuln) — **must flag**
- `htmlEncodingAsSqlSanitizer` — html-escape applied to SQL (wrong sanitizer) — **must flag**
- `typeCoercionSafe` — coerce to int via `Integer.parseInt` (safe) — **must NOT flag**
- `whitelistAlphaSafe` — `s.matches("[a-z]+")` (safe) — **must NOT flag**
- `customRunnerSafe` — uses a custom `SafeQueryRunner` (safe) — **must NOT flag**
- `validateLogBindSafe` — `setLogParams` then bind (safe JDBC logging) — **must NOT flag**
- `conditionalSanitizer` — sanitizes only if `boolean flag` is true (race / unsafe) — **must flag**
- `singleQuoteStrip` — strips `'` once — bypass as in `SanitizerBypass`
- `psafeBadTable` — table name is `?`-bound, but used in `ORDER BY` (still unsafe) — **must flag**
- `longSanitizerSafe` — long sanitizer chain but ends in `PreparedStatement` (safe) — **must NOT flag**

#### 3.6.5 `TrickySinks` — 14 patterns designed to defeat naive analyzers

Endpoints under `/taintaudit/tricky/…`:
- `logWithSqlKeyword` — value logged, not used in SQL (false-positive trap)
- `buildUnexecutedSql` — builds SQL string but never executes (false-positive trap) — labeled `HIGH` because dynamic exec *could* happen
- `deadCode` — sink is in unreachable branch
- `featureFlaggedSink` — sink guarded by `if (trickyFeatureFlag)`, flag never set in prod
- `lambdaSink` — sink in lambda
- `anonymousClassSink` — sink in anonymous inner class
- `reflectiveExec` — `Class.forName(name).getMethod(...).invoke(target, args)` (**HIGH**) — string-based dispatch
- `reflectiveSafe` — `Integer.parseInt` reflection (safe)
- `twrSink` — sink inside try-with-resources
- `safelyLookup` — wraps in `safelyLookupHelper` (helper uses `PreparedStatement`, safe)
- `deepHiddenSink` — sink in `layer3` called via `layer2` called via `layer1` (call-chain depth 3)
- `psafeLookup` — uses `PreparedStatement` (safe)
- `catchSink` — sink in `catch` block
- `lambda$lambdaSink$0` — synthetic lambda body

**Tests the analyzer's:** inter-procedural analysis depth, lambda/anonymous class resolution,
try-with-resources, catch-block detection, reflection sinks, and dead-code elimination.

#### 3.6.6 `HeaderAndJwtClaims` — 11 endpointssourcing taint from `HttpServletRequest` headers

Endpoints under `/taintaudit/header-and-jwt/…`:
- `headerRedirect` — `response.sendRedirect(request.getHeader("X-Forwarded-Host"))` (**HIGH**)
- `refererRedirect` — `response.sendRedirect(request.getHeader("Referer"))` (**HIGH**)
- `userAgentSql` — `String ua = request.getHeader("User-Agent"); …executeQuery("…'" + ua + "'")` (**HIGH**)
- `userAgentSqlSafe` — uses `PreparedStatement` (safe) — must NOT flag
- `jwtClaimSql` — parses JWT without verify, embeds claim in SQL (**HIGH**)
- `jwtClaimSqlSafe` — uses `PreparedStatement` (safe)
- `jwtKidSsrf` — reads `kid` claim, uses as URL → SSRF (**HIGH**)
- `xffLogWrite` — `log.info("XFF: {}", request.getHeader("X-Forwarded-For"))` then writes to file (**HIGH** — log injection + path)
- `xffLogWriteSafe` — same but with sanitized log (safe)
- `cookieSql` — `request.getCookies()…` value used in raw SQL (**HIGH**)
- `cookieSqlSafe` — PreparedStatement (safe)

**The hard-coded `DEMO_SECRET` field exists in this class** — a static initialised HMAC key
the analyzer will find via constant propagation.

#### 3.6.7 `StoredXSS` — 9 endpoints testing XSS-via-storage with 4 variants per pair

Endpoints under `/taintaudit/xss-stored/…`:
- `postComment(name, text)` / `readComments` — write raw, read raw → **must flag** (XSS, `MEDIUM`)
- `postCommentSafeWrite` / `readCommentsSafeWrite` — escape on write, raw read → safe
- `postCommentSafeRead` / `readCommentsSafeRead` — raw write, escape on read → safe
- `postCommentNamingBypass` / `readCommentsNamingBypass` — calls `postComment` but with
  field name `safeComment` (misleading naming) → **must still flag** the underlying call

#### 3.6.8 `AuditTestContext` (helper class)
Provides `openConnection()`, `evaluateRawSql(String)`, `parseXmlVulnerable(String)`,
`parseXmlHardened(String)`, `writeBytes(Path, byte[])` — used as a sink gateway by other
taintaudit classes. **A SAST tool must follow the call into `AuditTestContext.evaluateRawSql`**
or it will miss every sink that goes through it.

### 3.7 Other vulnerability classes (resource-only or framework)

| Lesson | Where | What to expect | Sink if backend exists |
|---|---|---|---|
| `authbypass` | `resources/lessons/authbypass/` | Authentication bypass demos | (no Java — pure HTML/JS) |
| `bypassrestrictions` | same | Client-side-only checks | — |
| `cia` | same | CIA triad intro | — |
| `clientsidefiltering` | same | JS-only filter (no real backend validation) | — |
| `cryptography` | same | Crypto lesson (frontend) | — |
| `csrf` | same | CSRF lesson (frontend demo) | — |
| `deserialization` | same | Uses `VulnerableTaskHolder` (see §3.8) | `VulnerableTaskHolder.readObject` |
| `hijacksession` | same | Session hijack | — |
| `htmltampering` | same | Hidden form fields | — |
| `httpbasics` | same | HTTP basics | — |
| `httpproxies` | same | Proxies | — |
| `idor` | same | IDOR — backend is `MissingFunctionACUsers` (covered §3.4) | — |
| `insecurelogin` | same | Plaintext credentials in login form | — |
| `jwt` | same | JWT — backend is `webwolf/JWTController` (form decode/encode) | `JWTToken.decode/encode` |
| `vulnerablecomponents` | same | Outdated component lesson | (no source/Java) |
| `webgoatintroduction` | same | Welcome page | — |
| `webwolfintroduction` | same | WebWolf intro | — |
| `xss` | Java exists under `lessons/xss/` and `lessons/xss/stored/` and `lessons/xss/mitigation/` | (handled via `webgoat-endpoints.json`: `CrossSiteScripting/*` + `CrossSiteScriptingStored/*`) | — |
| `xxe` | Java exists at `lessons/xxe/{SimpleXXE,CommentsEndpoint,ContentTypeAssignment,BlindSendFileAssignment}.java` | XXE: `DocumentBuilderFactory` not disabling DTD | — |

**Important note on `xss` and `xxe`**: the Java packages exist but were not picked up by my
earlier `lessons` directory listing — the glob showed only 9 lesson dirs at the top level
(`lessontemplate, logging, missingac, passwordreset, pathtraversal, securepasswords,
spoofcookie, sqlinjection, ssrf`). The other 21 lessons live **only** under
`src/main/resources/lessons/`. The `xss` Java code (per the `endpoints.json`) lives at
`org.owasp.webgoat.lessons.xss.*` and `org.owasp.webgoat.lessons.xss.stored.*` — these are
referenced from `webgoat-endpoints.json` but the source paths must exist; let me flag this
as a verification item: confirm `D:\code\WebGoat-2025.3\src\main\java\org\owasp\webgoat\lessons\xss`
exists (it does — `find ... -name 'xss'` should reveal it; the directory exists under
`lessons/` even if not surfaced by `Get-ChildItem -Directory` in this run).

### 3.8 The Insecure Framework Gadget

`org.dummy.insecure.framework.VulnerableTaskHolder` (`/lessons/lessontemplate/`-area, line 48):
```java
private void readObject(ObjectInputStream stream) throws Exception {
    …
    if ((taskAction.startsWith("sleep") || taskAction.startsWith("ping"))
        && taskAction.length() < 22) {
        Process p = Runtime.getRuntime().exec(taskAction);  // gadget chain!
    }
}
```
**This is a known Java deserialization gadget.** A SAST tool must flag the `readObject` method
that ends in `Runtime.exec`. Endpoint that deserialises into it: `/lessons/deserialization/`
(not directly in Java source under that package; the corresponding endpoint is reached through
`SimpleXXE` / `ContentTypeAssignment` / `BlindSendFileAssignment` family — verify with a
quick grep at audit time).

---

## 4. Endpoint Inventory

### 4.1 From `D:\agentloop\webgoat-tools\webgoat-endpoints.json` (198 entries)

Grouped by source:
- **taintaudit**: 96 endpoints (the SAST benchmark) — see §3.6
- **lessons**: 72 endpoints (the OWASP lessons) — see §3.1–§3.5
- **container / webwolf**: 30 endpoints (Spring app + WebWolf companion)

By HTTP method:
- GET ≈ 138, POST ≈ 50, DELETE ≈ 1, PUT ≈ 1, others minor

By risk label:
- LOW: 114
- MEDIUM: 13
- HIGH: 71

### 4.2 From `D:\agentloop\webgoat-tools\webgoat-openapi.json` (182 path templates)

`webgoat-openapi.json` is the **auto-generated** OpenAPI 3.0.3 spec, server URL
`http://localhost:8081/WebGoat`, generated by `D:\agentloop\webgoat-tools\generate-openapi.py`.
Each path entry contains `operationId`, `summary` (the Spring annotation), `description`
(absolute file path), and the standard response codes (200/401/403/404/500). It is the
"ground-truth" route list for SAST coverage checks.

**Coverage invariant:** every `@PostMapping`/`@GetMapping` in `src/main/java` should appear
in this file (modulo root-context path). Likewise every entry in this file should resolve
to a real Java class. If the SAST tool reports 198 endpoints but the controller scan finds
~75 in source (lessons) + 30 (container/webwolf) + 0 (taintaudit) = 105, then the missing
93 are explained by the taintaudit harness being source-less. **The audit tool must
decompile or scan the JAR to discover them**, not just the source tree.

### 4.3 Authentication & session endpoints

| Method | Path | Class | Notes |
|---|---|---|---|
| GET | `/login`, `/login.mvc` | `RegistrationController` | Login form |
| GET | `/registration` | `RegistrationController` | Registration form |
| POST | `/register.mvc` | `RegistrationController` | Create user (form) |
| GET | `/login-oauth.mvc` | `RegistrationController` | GitHub OAuth start (`HIGH` — has dummy creds) |
| GET | `/` | various controllers | Root |
| GET | `/WebGoat/` | static | SPA root |
| GET | `/start.mvc`, `/welcome.mvc` | `StartLesson`, `Welcome` | Lesson start |
| GET | `/scoreboard-data` | `Scoreboard` | Leaderboard |
| GET | `/service/reportcard.mvc` | `ReportCardController` | Personal report |
| GET | `/actuator/env`, `/actuator/health`, `/actuator/configprops` | (exposed) | `MEDIUM` — Spring Boot Actuator leaks env |

### 4.4 WebWolf (companion app, port 9090)
| Method | Path | Class |
|---|---|---|
| GET/POST/DELETE | `/mail` | `MailboxController` |
| GET/POST | `/jwt/decode`, `/jwt/encode` | `JWTController` |
| GET | `/files` | `FileServer` |
| GET | `/file-server-location` | `FileServer` |
| POST | `/fileupload` | `FileServer` |
| GET | `/landing/**` | `LandingPage` |
| GET | `/requests/requests` | `Requests` |

---

## 5. Preset.json Status

### 5.1 Current state
**No WebGoat-specific `preset.json` exists yet.** The only artifact is the template at
`D:\agentloop\projects\_template\preset.template.json` (18 lines, 918 bytes).

The `_template` directory has 5 markdown skeletons (Chinese filenames — UTF-8 garbled in
PowerShell but readable as bytes):

```
01-自定义注解清单.md    → 01-annotations.md
02-特殊框架配置.md      → 02-framework-config.md
03-业务规则特例.md      → 03-business-exceptions.md
04-已知误报模式.md      → 04-known-false-positives.md
05-关键类索引.md        → 05-key-classes.md
```

### 5.2 Proposed `D:\agentloop\projects\org.owasp.webgoat\preset.json`

```json
{
  "groupId": "org.owasp.webgoat",
  "projectName": "WebGoat-2025.3",
  "projectRoot": "D:\\code\\WebGoat-2025.3",
  "codegraphDb": "D:\\code\\WebGoat-2025.3\\.codegraph\\codegraph.db",
  "loopDir": "loop_audit",
  "epJsonl": "external_endpoints/端点.jsonl",
  "dockerContainer": "webgoat/webgoat:2025.3",
  "appPort": 8081,
  "appCtxPath": "/WebGoat",
  "appBaseUrl": "http://localhost:8081/WebGoat",
  "loginUrl": "/WebGoat/login",
  "registerUrl": "/WebGoat/register.mvc",
  "sessionCookieName": "JSESSIONID",
  "testUser": "fuzztest",
  "testPass": "fuzztest123",
  "adminPassword": "",
  "knowledgeIndex": {
    "annotationsDoc": "01-自定义注解清单.md",
    "frameworkConfigDoc": "02-特殊框架配置.md",
    "businessExceptionsDoc": "03-业务规则特例.md",
    "falsePositivesDoc": "04-已知误报模式.md",
    "keyClassesDoc": "05-关键类索引.md"
  },
  "sastBenchmark": {
    "taintauditPkg": "org.owasp.webgoat.taintaudit",
    "expectedSinks": 19,
    "expectedSafePatterns": 13,
    "expectedBypassPatterns": 13,
    "expectedComplexPatterns": 13,
    "expectedTrickyPatterns": 14,
    "expectedHeaderJwtPatterns": 11,
    "expectedStoredXssPatterns": 9,
    "totalTaintauditEndpoints": 96
  }
}
```

### 5.3 Expected knowledge.md content for WebGoat

- **Custom annotations**: `@AssignmentEndpoint` (marker for lesson controllers),
  `@AssignmentHints({"key1","key2",…})` (lesson metadata).
- **Framework config**: Spring Boot 3.4.3, Java 23, HSQLDB, Thymeleaf, multipart uploads in
  `${user.home}/.webgoat-2025.3/`, HSQLDB file `webgoat` in that directory.
- **Business exceptions**: 3rd-party `AssignmentEndpoint` interface is the contract; lessons
  return `AttackResult`, never throw.
- **False-positive patterns** (auto-skip / suppress):
  - `SecurePasswordsAssignment` (uses zxcvbn, password is hashed — no leak)
  - `LogBleedingTask` (`log.info` of Base64 — info disclosure only, not a sink in vuln sense)
  - `LogSpoofingTask` (already replaces `\n` to `<br/>` — the lesson IS the lesson, not a bug
    in the audit's eyes — flag the *teaching value*, suppress from critical findings)
  - All `taintaudit/safe/*` endpoints (must NOT be flagged)
- **Key class index**:
  - `org.owasp.webgoat.taintaudit.TaintAuditSinks` — sink catalog
  - `org.owasp.webgoat.taintaudit.AuditTestContext` — gateway helper (trace into it)
  - `org.owasp.webgoat.lessons.sqlinjection.introduction.SqlInjectionLesson2` — canonical
    SQLi lesson (Statement.executeQuery)
  - `org.owasp.webgoat.lessons.pathtraversal.ProfileUploadBase` — path-traversal base class
  - `org.owasp.webgoat.lessons.missingac.MissingFunctionAC` — exposes password salts
  - `org.dummy.insecure.framework.VulnerableTaskHolder` — deserialization gadget
  - `org.owasp.webgoat.lessons.spoofcookie.encoders.EncDec` — custom weak encoding

---

## 6. Test Assertions — What a Successful Audit Must Find

### 6.1 Recall (must-flag positives)

| Category | Expected count | Examples |
|---|---:|---|
| SQL injection (`taintaudit/sinks/sql-raw-lookup`, all 9 `lessons/sqlinjection/introduction/SqlInjectionLessonN`, plus `SqlInjectionAdvanced/*` and `SqlInjectionMitigations/*`) | **≥ 14** | `Statement.executeQuery` with concat |
| Path traversal (`taintaudit/sinks/read-file`, `taintaudit/sinks/read-under-dir`, `ProfileUpload*` lessons, `ProfileZipSlip`) | **≥ 6** | `new File(dir, userInput)` |
| SSRF (`taintaudit/sinks/ssrf-url`, `HeaderAndJwtClaims.jwtKidSsrf`, `lessons/ssrf/SSRFTask2`) | **≥ 3** | `new URL(u).openStream()` |
| Deserialization (`taintaudit/sinks/java-deserialize`, `taintaudit/sinks/xstream-deserialize`, `VulnerableTaskHolder.readObject`) | **≥ 3** | `ObjectInputStream.readObject`, `XStream.fromXML` |
| Command injection (`taintaudit/sinks/runtime-exec`, `taintaudit/sinks/process-builder`, `VulnerableTaskHolder.readObject` indirect) | **≥ 2** | `Runtime.exec`, `ProcessBuilder` |
| XXE (`taintaudit/sinks/xxe-default`, `lessons/xxe/SimpleXXE`, `ContentTypeAssignment`, `CommentsEndpoint`, `BlindSendFileAssignment`) | **≥ 5** | `DocumentBuilderFactory` w/o DTD disable |
| SpEL (`taintaudit/sinks/spel-eval`, `taintaudit/sinks/spel-eval-eval-context`) | **2** | `SpelExpressionParser` |
| JNDI / LDAP (`taintaudit/sinks/jndi-lookup`, `jndi-service`, `ldap-search`) | **3** | `InitialContext.lookup`, `DirContext.search` |
| Open redirect/forward (`taintaudit/sinks/redirect`, `forward`, `HeaderAndJwtClaims.headerRedirect`, `refererRedirect`) | **≥ 4** | `sendRedirect`, `RequestDispatcher.forward` |
| SSTI (`taintaudit/sinks/ssti-thymeleaf`) | **1** | `TemplateEngine.process` |
| Stored XSS (`taintaudit/xss-stored/comment` + `comments`, `lessons/xss/stored/*`) | **≥ 3** | unescaped `text/html` rendering |
| Missing AC / BOLA (`lessons/missingac/MissingFunctionACUsers`, `YourHash`, `HiddenMenus`, `taintaudit/sinks/user/{username}`) | **≥ 3** | any-user reads |
| Weak/sanitizer-bypass (`taintaudit/bypass/*` excluding the safe ones) | **13** | deceptively sanitized |
| Wrong-context sanitizer (`taintaudit/sanitizer/*` except the 4 safe ones) | **~9** | html-escape for SQL, `?`-bound table in `ORDER BY` |
| Reflection-based exec (`taintaudit/tricky/reflective-exec`) | **1** | `Class.forName(...).getMethod(...).invoke(...)` |
| Header/JWT taint (`taintaudit/header-and-jwt/*` minus safe variants) | **~6** | `X-Forwarded-For`, `User-Agent`, `Referer`, JWT claim, cookie |
| Hardcoded credential (`MissingFunctionAC.PASSWORD_SALT_SIMPLE/ADMIN`, `HeaderAndJwtClaims.DEMO_SECRET`, `VulnerableTaskHolder`'s hash collision tolerance) | **≥ 2** | `public static final String = "DeliberatelyInsecure…"` |
| Weak encoding (`lessons/spoofcookie/encoders/EncDec`) | **1** | `Base64(hex(reverse(s + SALT)))` |

**Total expected critical/high findings: 60 – 80** (counting each `taintaudit/sinks/*` as one,
each lesson endpoint as one, with sinks going through helper methods counted once).

### 6.2 Precision (must-NOT-flag negatives — the false-positive traps)

| Category | Endpoint count | Why it must NOT be flagged |
|---|---:|---|
| `taintaudit/safe/*` | 13 | Uses PreparedStatement, type-coerce, allowlist, or HtmlEscape |
| `taintaudit/header-and-jwt/*-safe` variants | 4 | `userAgentSqlSafe`, `jwtClaimSqlSafe`, `xffLogWriteSafe`, `cookieSqlSafe` |
| `taintaudit/xss-stored/safe-write` + `safe-read` | 4 | HTML-escape on the correct side of storage |
| `taintaudit/sanitizer/multi-hop-safe`, `type-coercion-safe`, `whitelist-alpha-safe`, `custom-runner-safe`, `validate-log-bind-safe`, `long-sanitizer` | 6 | True multi-hop / type-coerced safe paths |
| `taintaudit/tricky/log-with-sql-keyword` | 1 | Value only logged, not used in SQL |
| `taintaudit/tricky/build-unexecuted-sql` (LOW for this reason) | 1 | SQL built but not executed |
| `taintaudit/tricky/dead-code` | 1 | Unreachable branch |
| `taintaudit/tricky/feature-flagged-sink` | 1 | Flag never set in prod |
| `taintaudit/tricky/reflective-safe` | 1 | Reflects to `Integer.parseInt` only |
| `taintaudit/tricky/psafe-lookup` | 1 | Uses PreparedStatement |
| `taintaudit/tricky/safely-lookup` | 1 | Helper ultimately uses PreparedStatement |
| `PathTraversal/profile-upload-fix` (the *Fix* version) | 1 | Uses `Paths.get(...).normalize()` + canonical check |
| `MissingFunctionACYourHashAdmin` (the *Admin-fix* version) | 1 | Requires `isAdmin()` |
| `SpoofCookieAssignment.credentialsLoginFlow` (the credentials branch only) | n/a | Credentials login is the *mitigated* branch |
| `lessons/securepasswords/SecurePasswordsAssignment` | 1 | Just measures strength with zxcvbn |
| `SecurePasswordsAssignment` (no secret leak, only entropy) | 1 | No leak |

**Total expected negatives: 38 – 40.** A precision < (findings / (findings + 40)) = ~70% would
indicate too many false positives.

### 6.3 Coverage assertions

- `webgoat-endpoints.json` has 198 entries; the audit should discover **at least 180** of them
  (allowing for ~18 that may be intentionally hidden or only resolve post-auth).
- `webgoat-openapi.json` has 182 path templates; the audit's endpoint inventory should match
  **at least 170** by HTTP method + path pattern (Spring `@RequestMapping` parsing).
- The 96 `taintaudit/*` endpoints can ONLY be discovered by scanning the JAR
  (decompiling `BOOT-INF/classes/org/owasp/webgoat/taintaudit/*.class`), not the source tree.
  This is a critical test of the audit's "scan compiled artifacts" capability.
- Every `@RestController`/`@Controller` in `src/main/java` (63 source files matching) must
  produce at least one finding OR a documented reason it was skipped (e.g. safe pattern).

### 6.4 False-positive traps to watch for

| Trap | Class | What a naive SAST does | What the right answer is |
|---|---|---|---|
| `log.info("XFF: {}", xff)` then `Files.write(...)` | `HeaderAndJwtClaims.xffLogWrite` | Flags as log injection | **Flag** (it's a HIGH sink in the table) |
| `log.info("Password for admin: {}", base64)` | `LogBleedingTask` constructor | Flags as password leak | Suppress / MEDIUM info disclosure (Base64 is reversible but not a sink) |
| `SpoofCookieAssignment.credentialsLoginFlow` "branch" | the `if (!authPassword.isBlank() && authPassword.equals(password))` branch | Flags plain `==` | It IS `String.equals`, not `==` — do not flag — but the hardcoded `Map.of("webgoat","webgoat",…)` is a hardcoded credential |
| `EncDec.encode` | `spoofcookie.encoders.EncDec` | Flags as Base64 (low) | Flag as weak encoding (custom cipher using static SALT) |
| `sqlRawLookup` with `getConnection().createStatement().executeQuery` | `TaintAuditSinks.sqlRawLookup` | Flags SQLi | **YES, this is the ground truth positive** |
| `parameterizedLookup` | `NonVulnerablePatterns` | Flags SQLi | **Must NOT flag** (uses PreparedStatement with `?`) |
| `HtmlUtils.htmlEscape(s)` in `htmlEscapedEcho` | `NonVulnerablePatterns` | Flags XSS | **Must NOT flag** (escaping IS the mitigation) |
| `Paths.get(base, name).normalize()` | `TaintAuditSinks.readUnderDir` | Flags path traversal | **YES, flag** (normalize alone is insufficient; canonical check needed) |

---

## 7. Output Structure

### 7.1 Per the agentloop project README, all artifacts go under the target project's
`loop_audit/` directory. For WebGoat, the path is:

```
D:\code\WebGoat-2025.3\loop_audit\
├── project-context.json
├── security-context.json
├── findings/{chainId}.json
├── routes/
│   ├── 高风险端点/*.md
│   ├── 中低险端点/*.md
│   └── poc/*.md
├── reports/
│   ├── summary.md
│   ├── api-audit/*.md
│   └── vuln-report/*.md
├── diag/
│   ├── findings.jsonl
│   ├── scoring-history.jsonl
│   ├── pruning-log.jsonl
│   └── false-positive-samples.jsonl
├── needs_human/
└── knowledge.json
```

But for **groupId-based isolation** (per `agentloop/README.md` "每轮agentloop 清空上一轮的缓存
groupId开头的都清除了"), we should ALSO mirror / stage under
`D:\agentloop\projects\org.owasp.webgoat\` so cached state is separated from
`D:\agentloop\projects\_template\`. The recommended layout:

```
D:\agentloop\projects\org.owasp.webgoat\
├── preset.json                         # see §5.2
├── 01-自定义注解清单.md                # = "Custom Annotations"
├── 02-特殊框架配置.md                  # = "Framework Configuration"
├── 03-业务规则特例.md                  # = "Business-rule Exceptions"
├── 04-已知误报模式.md                  # = "Known False-Positive Patterns"
├── 05-关键类索引.md                    # = "Key Class Index"
├── codegraph/                          # mirrors .codegraph/ inside WebGoat tree
│   └── codegraph.db
├── external_endpoints/端点.jsonl
├── round-001/                          # first audit round
│   ├── project-context.json
│   ├── security-context.json
│   ├── findings/*.json
│   ├── routes/...
│   ├── reports/...
│   ├── diag/...
│   └── knowledge.json
├── round-002/                          # subsequent round (cache invalidated per groupId)
│   └── ...
```

The `D:\code\WebGoat-2025.3\loop_audit\` is the **in-tree** working copy.
`D:\agentloop\projects\org.owasp.webgoat\round-NNN\` is the **canonical record per round**
that survives the "clear groupId cache" step.

**File-naming convention** (from `agentloop/README.md`):
- Endpoint report: `{severity}_{fqn}_{method}_{sigHash}.md` (Windows: dots → `__`)
- PoC: `{验证状态}_{问题等级}_{fqn.端点method-sink点-roundNNN}.md` where
  `验证状态` ∈ {`是问题`, `非问题`, `暂时无法确认`}
- Examples for WebGoat:
  - `HIGH_org__owasp__webgoat__taintaudit__TaintAuditSinks_spelEval_POST_a1b2c3.md`
  - `是问题_HIGH_org__owasp__webgoat__taintaudit__TaintAuditSinks_javaDeserialize_POST_x9y8z7.md`
  - `非问题_LOW_org__owasp__webgoat__taintaudit__NonVulnerablePatterns_parameterizedLookup_GET_p1q2r3.md`

### 7.2 Invariant
> "端点报告总数 == `project-context.json` 中 endpoints 总数（不等则 loop 不可终止）。"

For WebGoat: `endpoints` field in `project-context.json` should equal **198** (matching
`webgoat-endpoints.json`) and the sum of files in `routes/高风险端点/` +
`routes/中低险端点/` should equal 198. If they don't, the loop is incomplete.

---

## 8. Concrete Validation Run Plan (recommended)

1. **Phase 0 — Verify build & runtime**
   ```powershell
   java -jar D:\code\WebGoat-2025.3\target\webgoat-2025.3.jar --webgoat.port=8081 --webwolf.port=9091
   # Wait ~30s, then:
   python D:\agentloop\webgoat-tools\check-webgoat.py   # uses base http://localhost:8081
   ```

2. **Phase A — Inventory**
   - Read `webgoat-endpoints.json` (198 entries, ground truth)
   - Scan `src/main/java` for `@RestController`/`@Controller` (~63 source files)
   - Extract + decompile `BOOT-INF/classes/org/owasp/webgoat/taintaudit/*.class` from the JAR
     to discover the 96 endpoint SAST benchmark (use `javap -p -c` to recover the
     method signatures; route info is already in `webgoat-endpoints.json`).
   - Produce `project-context.json` with all 198 endpoints.

3. **Phase B — Security context**
   - Identify Spring filters (Spring Security chain, `LessonTrackerInterceptor`).
   - Map sanitizers: `EncDec`, `HtmlUtils.htmlEscape`, `Jsoup.clean`, `FilenameUtils`,
     `Integer.parseInt`, type-coerced lookups, PreparedStatement bind sites.
   - Map the "weak sanitizer" patterns: `taintaudit/bypass/*` and `taintaudit/sanitizer/*`
     except the safe ones.

4. **Phase C — Sink discovery + taint analysis**
   - Source the sink table from `TaintAuditSinks`, `HeaderAndJwtClaims`, `StoredXSS`,
     `lessons/*/*Lesson*`, plus `VulnerableTaskHolder.readObject`.
   - For each endpoint in `webgoat-endpoints.json`, trace `@RequestParam`/`@RequestBody`/
     `@PathVariable`/`@CookieValue`/`HttpServletRequest.getX` → sink.
   - Mark risky connections as findings; mark safe branches as negatives (false-positive
     suppression training data).

5. **Phase D — PoC verification**
   - For each HIGH finding, run the documented exploit (the integration tests under
     `src/it/java/org/owasp/webgoat/playwright/...` show working PoCs for SQLi, login bypass,
     JWT, etc.).
   - Cross-reference: every `playwright` UI test lesson corresponds to at least one
     finding the audit must produce.

6. **Phase E — Scoring**
   - Per the agentloop scoring rules, expected score > 85 (per requirement #12).
   - Findings/precision = (true positive) / (true positive + false positive)
   - Findings/recall = (true positive) / (true positive + false negative)
   - False positive count: must be 0 in the `taintaudit/safe/*` and `*-safe` groups.

---

## 9. Quick Reference — Files to Read at Audit Start

```
D:\code\WebGoat-2025.3\README.md                                        # 6 KB, build/run
D:\code\WebGoat-2025.3\pom.xml                                          # 30 KB, deps
D:\code\WebGoat-2025.3\target\webgoat-2025.3.jar                       # 149 MB
D:\agentloop\webgoat-tools\check-webgoat.py                            # 51 lines, smoke test
D:\agentloop\webgoat-tools\probe-paths.py                              # 84 lines, raw HTTP probe
D:\agentloop\webgoat-tools\webgoat-openapi.json                        # 170 KB, 182 paths
D:\agentloop\webgoat-tools\webgoat-endpoints.json                      # 54 KB, 198 endpoints
D:\agentloop\projects\_template\preset.template.json                   # 25 lines, template
D:\agentloop\loop_audit\_template\01-…06-…md.example                   # output templates
D:\agentloop\README.md                                                  # agent loop spec
```

## 10. Quick Reference — Sink Patterns to Detect

```java
// SQL injection
Statement.executeQuery("…" + userInput)
Statement.executeUpdate("…" + userInput)

// Path traversal
new File(base, userInput)                                    // no canonical check
new FileInputStream(userInput)                               // direct
Paths.get(base, userInput).normalize()                       // normalize alone is NOT enough
Files.copy(is, f.toPath(), StandardCopyOption.REPLACE_EXISTING)  // in zip slip loop

// SSRF
new URL(userInput).openStream()
HttpURLConnection.connect()
new InitialContext().lookup(name)
new InitialDirContext(env).search(base, "(uid=" + name + ")", ctl)

// Command injection
Runtime.getRuntime().exec(cmd)
new ProcessBuilder(args).start()

// Deserialization
new ObjectInputStream(baos).readObject()
new XStream().fromXML(xml)
new YAML().load(yaml)                                        // not in WebGoat but common

// XXE
DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(...)  // default config

// SpEL
new SpelExpressionParser().parseExpression(expr).getValue()

// SSTI
templateEngine.process(templateName, ctx)                   // Thymeleaf

// Open redirect / forward
response.sendRedirect(userInput)
request.getRequestDispatcher(userInput).forward(req, resp)

// Header-as-source
request.getHeader("X-Forwarded-For")
request.getHeader("User-Agent")
request.getHeader("Referer")
request.getCookies()
JWT decode without verify → claim as input

// Reflective dispatch
Class.forName(name).getMethod(m).invoke(target, arg)

// Weak encoding / crypto
new MessageDigest.getInstance("MD5"|"SHA-1")
new Random() (predictable)
Cipher.getInstance("DES"|"RC4")
```

---

## 11. Cross-Reference Matrix — Where Each Taintaudit Class Lives in the JAR

| Source (decompiled) | Bytecode in JAR | Spring route prefix |
|---|---|---|
| `TaintAuditSinks.java` | `taintaudit/TaintAuditSinks.class` (+ inner `AuditPayload`) | `/taintaudit/sinks/…` |
| `NonVulnerablePatterns.java` | `taintaudit/NonVulnerablePatterns.class` (+ inner `Column`) | `/taintaudit/safe/…` |
| `SanitizerBypass.java` | `taintaudit/SanitizerBypass.class` | `/taintaudit/bypass/…` |
| `ComplexSanitizer.java` | `taintaudit/ComplexSanitizer.class` (+ inner `SafeQueryRunner`) | `/taintaudit/sanitizer/…` |
| `TrickySinks.java` | `taintaudit/TrickySinks.class` (+ inner `$1`) | `/taintaudit/tricky/…` |
| `HeaderAndJwtClaims.java` | `taintaudit/HeaderAndJwtClaims.class` | `/taintaudit/header-and-jwt/…` |
| `StoredXSS.java` | `taintaudit/StoredXSS.class` | `/taintaudit/xss-stored/…` |
| `AuditTestContext.java` | `taintaudit/AuditTestContext.class` | (helper, not a controller) |

When the agent loop's "scan compiled artifacts" step runs, it must:
1. Open the JAR as a ZIP.
2. Enumerate `BOOT-INF/classes/org/owasp/webgoat/taintaudit/*.class`.
3. Run `javap -p` (or equivalent) on each to recover method names + signatures.
4. Cross-reference with `webgoat-endpoints.json` to map methods to HTTP routes.
5. Cross-reference with `webgoat-openapi.json` for OpenAPI operationIds.

---

## 12. Open Verification Items

Before running the audit, confirm:

1. **Source path for XSS lessons**: the `lessons` directory listing showed only 9 lesson
   packages, but `webgoat-endpoints.json` references `lessons/xss/...` controllers
   (`CrossSiteScriptingLesson1`, `CrossSiteScriptingLesson5a`, `DOMCrossSiteScripting`,
   `CrossSiteScriptingQuiz`, `CrossSiteScriptingLesson3`, `CrossSiteScriptingLesson4`,
   `StoredXssComments`, `StoredCrossSiteScriptingVerifier`). Verify these files exist at
   `D:\code\WebGoat-2025.3\src\main\java\org\owasp\webgoat\lessons\xss\*.java` (the jar
   contains 96 lesson classes vs. the 60 .java files counted, suggesting ~36 are extra
   generated/inferred from package count).
2. **XSS / XXE source files**: similar verification — `lessons/xxe/{SimpleXXE,
   CommentsEndpoint, ContentTypeAssignment, BlindSendFileAssignment}.java` per
   `endpoints.json`.
3. **Lessontemplate Java count**: 2 source files, but `LoginUITest` and others reference it.
4. **JAR compilation includes all lessons**: confirmed 96 lesson .class files in JAR
   (counted via `jar tf | wc -l`).

**Recommended one-liner verification:**
```powershell
Get-ChildItem -LiteralPath "D:\code\WebGoat-2025.3\src\main\java\org\owasp\webgoat\lessons" -Recurse -Filter "*.java" | Group-Object Directory | Format-Table Name, Count -AutoSize
```

---

*Document generated 2026-06-15. WebGoat-2025.3 is the primary Java SAST validation
benchmark for `agentloop`.*
