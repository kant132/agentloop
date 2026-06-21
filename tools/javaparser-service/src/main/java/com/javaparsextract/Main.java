package com.javaparsextract;

import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.ParserConfiguration.LanguageLevel;
import com.github.javaparser.JavaParser;
import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.resolution.declarations.ResolvedMethodDeclaration;
import com.github.javaparser.symbolsolver.JavaSymbolSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.CombinedTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.JavaParserTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.ReflectionTypeSolver;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.stream.Collectors;

/**
 * Java 方法调用提取器
 * <p>
 * 输入若干 .java 文件，提取每个方法内调用的所有其他方法的信息：
 * - startLine: 外层方法声明的起始行号（从 0 算）
 * - methodSignature: 外层方法签名
 * - calledFQN: 被调用方法的 FQN + 实际调用参数
 * - isSink: 是否为外部 sink（groupId 内部调用 / 短名解析失败 → false）
 * - file: 调用所在文件路径
 * <p>
 * 输出 JSON 数组：
 * [{"startLine":5,"methodSignature":"public void foo()","calledFQN":"java.util.List.add(\"x\")","isSink":true,"file":"..."}]
 * <p>
 * 用法：
 * <pre>
 * // 新方式：properties 配置文件（多文件 + 多 groupId + 多线程）
 * java -jar extractor.jar --config config.properties
 *
 * // 旧方式 1：方法调用提取（位置参数）
 * java -jar extractor.jar file1.java [file2.java ...] [--group-id GID] [--source-root PATH]
 * java -jar extractor.jar file.java [sourceRoot]
 *
 * // 旧方式 2：路由注解解析
 * java -jar extractor.jar --routes file.java [sourceRoot]
 * </pre>
 */
public class Main {

    public static void main(String[] args) throws Exception {
        // 新模式：--config <properties 文件>
        if (args.length >= 2 && "--config".equals(args[0])) {
            Path configPath = Path.of(args[1]);
            if (!Files.exists(configPath)) {
                System.err.println("Config file not found: " + configPath.toAbsolutePath());
                System.exit(1);
            }
            Config config = loadConfig(configPath);
            if (config.files.isEmpty()) {
                System.err.println("Config error: no files specified (file.N=... entries missing)");
                System.exit(1);
            }
            if ("routes".equalsIgnoreCase(config.mode)) {
                System.out.println(runRoutes(config));
            } else {
                System.out.println(runCalls(config));
            }
            return;
        }

        // 旧模式 1：路由注解解析（--routes file.java [sourceRoot]）
        if (args.length > 0 && "--routes".equals(args[0])) {
            if (args.length < 2) {
                System.err.println("Usage: java -jar java-method-call-extractor.jar --routes <file.java> [sourceRoot]");
                System.exit(1);
            }
            Path routeFile = Path.of(args[1]);
            if (!Files.exists(routeFile)) {
                System.err.println("File not found: " + routeFile.toAbsolutePath());
                System.exit(1);
            }
            Config config = new Config();
            config.mode = "routes";
            config.files.add(routeFile.toString());
            if (args.length >= 3) {
                Path sr = Path.of(args[2]);
                if (Files.isDirectory(sr)) config.sourceRoot = sr.toString();
            }
            System.out.println(runRoutes(config));
            return;
        }

        // 旧模式 2：方法调用提取（位置参数）
        List<Path> javaFiles = new ArrayList<>();
        String groupId = null;
        Path sourceRoot = null;

        for (int i = 0; i < args.length; i++) {
            String a = args[i];
            if ("--group-id".equals(a) && i + 1 < args.length) {
                groupId = args[++i];
            } else if ("--source-root".equals(a) && i + 1 < args.length) {
                sourceRoot = Path.of(args[++i]);
            } else if (!a.startsWith("-")) {
                Path f = Path.of(a);
                if (Files.isRegularFile(f) && a.endsWith(".java")) {
                    javaFiles.add(f);
                } else if (Files.isDirectory(f) && sourceRoot == null) {
                    sourceRoot = f;
                } else if (Files.exists(f)) {
                    javaFiles.add(f);
                }
            }
        }

        if (javaFiles.isEmpty()) {
            System.err.println("Usage: java -jar java-method-call-extractor.jar <file1.java> [file2.java ...] [--group-id GID] [--source-root PATH]");
            System.err.println("  --group-id GID    : 项目 groupId，用于 sink 判定");
            System.err.println("  --source-root PATH: 项目源码根目录，用于跨文件 FQN 解析");
            System.err.println("  --config <file>   : properties 配置文件（多文件 + 多 groupId + 多线程）");
            System.err.println("  --routes <file>   : Spring/JAX-RS 路由提取");
            System.exit(1);
        }

        Config config = new Config();
        config.mode = "calls";
        for (Path f : javaFiles) config.files.add(f.toString());
        if (groupId != null) config.groupIds.add(groupId);
        if (sourceRoot != null) config.sourceRoot = sourceRoot.toString();
        System.out.println(runCalls(config));
    }

    // ==================== properties 配置加载 ====================

    /**
     * 从 properties 文件加载配置。
     * <p>
     * 支持的键：
     * <pre>
     * mode=calls | routes
     * workers=4
     * sourceRoot=D:/code/...
     * groupId=org.owasp.webgoat                  # 单个 groupId（可选）
     * groupId.1=org.owasp.webgoat                # 多个 groupId
     * groupId.2=org.owasp.webgoat.container
     * file.1=D:/path/to/File1.java
     * file.2=D:/path/to/File2.java
     * </pre>
     * <p>
     * groupId 推导优先级：
     * <ol>
     *   <li>显式配置（groupId 或 groupId.N）</li>
     *   <li>未配置时从第一个文件的 package 声明自动推导：取前 3 段（如
     *       org.owasp.webgoat.lessons.idor → org.owasp.webgoat）</li>
     * </ol>
     */
    static Config loadConfig(Path configPath) throws IOException {
        Properties props = new Properties();
        try (Reader r = Files.newBufferedReader(configPath, StandardCharsets.UTF_8)) {
            props.load(r);
        }
        Config c = new Config();
        c.mode = props.getProperty("mode", "calls").trim();
        String w = props.getProperty("workers");
        if (w != null && !w.isBlank()) {
            try { c.workers = Math.max(1, Integer.parseInt(w.trim())); }
            catch (NumberFormatException ignored) { /* keep default 4 */ }
        }
        c.sourceRoot = props.getProperty("sourceRoot");
        if (c.sourceRoot != null) c.sourceRoot = c.sourceRoot.trim();
        if (c.sourceRoot != null && c.sourceRoot.isEmpty()) c.sourceRoot = null;

        // 收集 file.N（按数字顺序）
        List<Map.Entry<String, String>> fileEntries = new ArrayList<>();
        for (String key : props.stringPropertyNames()) {
            String val = props.getProperty(key);
            if (val == null) continue;
            if (key.startsWith("file.")) fileEntries.add(Map.entry(key, val.trim()));
        }
        fileEntries.sort((a, b) -> compareIndexedKey(a.getKey(), b.getKey(), "file."));
        for (Map.Entry<String, String> e : fileEntries) c.files.add(e.getValue());

        // groupId：1) 单个 groupId 键；2) 多个 groupId.N 键；3) 都未配置则从首文件 package 自动推导（取前 3 段）
        String singleGid = props.getProperty("groupId");
        if (singleGid != null && !singleGid.trim().isEmpty()) {
            c.groupIds.add(singleGid.trim());
        }
        List<Map.Entry<String, String>> gidEntries = new ArrayList<>();
        for (String key : props.stringPropertyNames()) {
            String val = props.getProperty(key);
            if (val == null) continue;
            if (key.startsWith("groupId.")) gidEntries.add(Map.entry(key, val.trim()));
        }
        gidEntries.sort((a, b) -> compareIndexedKey(a.getKey(), b.getKey(), "groupId."));
        for (Map.Entry<String, String> e : gidEntries) c.groupIds.add(e.getValue());

        if (c.groupIds.isEmpty() && !c.files.isEmpty()) {
            String pkg = extractPackage(Path.of(c.files.get(0)));
            if (pkg != null && !pkg.isEmpty()) {
                String[] parts = pkg.split("\\.");
                if (parts.length >= 3) {
                    c.groupIds.add(parts[0] + "." + parts[1] + "." + parts[2]);
                } else {
                    c.groupIds.add(pkg);
                }
            }
        }
        return c;
    }

    /**
     * 从 Java 文件中提取 package 声明。仅解析语法树，不依赖符号解析。
     * 失败时返回 null（不抛异常，调用方降级处理）。
     */
    static String extractPackage(Path javaFile) {
        try {
            String content = Files.readString(javaFile);
            CompilationUnit cu = StaticJavaParser.parse(content);
            return cu.getPackageDeclaration().map(p -> p.getNameAsString()).orElse(null);
        } catch (Exception e) {
            return null;
        }
    }

    private static int compareIndexedKey(String a, String b, String prefix) {
        try {
            int ia = Integer.parseInt(a.substring(prefix.length()));
            int ib = Integer.parseInt(b.substring(prefix.length()));
            return Integer.compare(ia, ib);
        } catch (NumberFormatException e) {
            return a.compareTo(b);
        }
    }

    // ==================== calls 模式 ====================

    /**
     * 方法调用提取（多线程）。
     * <p>
     * 流程：
     * 1. 配置 StaticJavaParser 符号解析器（Reflection + JavaParser source root）
     * 2. 串行解析所有文件（填充 type solver 缓存，保证跨文件 FQN 解析可靠）
     * 3. 并行提取方法调用（call.resolve() 是性能瓶颈，可并行）
     */
    static String runCalls(Config config) throws Exception {
        Path sourceRoot = config.sourceRoot != null ? Path.of(config.sourceRoot) : locateProjectRoot(Path.of(config.files.get(0)));
        List<Path> files = config.files.stream().map(Path::of).toList();

        // 1. 串行解析所有文件（用全局 StaticJavaParser，只做语法解析，不涉及符号解析）
        StaticJavaParser.getConfiguration().setLanguageLevel(LanguageLevel.JAVA_21);
        Map<Path, String> fileSources = new LinkedHashMap<>();
        for (Path f : files) {
            try {
                fileSources.put(f, Files.readString(f));
            } catch (IOException e) {
                System.err.println("Skip (read error): " + f);
            }
        }
        if (fileSources.isEmpty()) return "[]";

        // 2. 并行提取方法调用
        //    每个线程创建独立的 JavaParser + TypeSolver，避免 JavaParserTypeSolver 的 Guava 缓存竞争
        List<CallRecord> allRecords = Collections.synchronizedList(new ArrayList<>());
        int nWorkers = Math.min(config.workers, fileSources.size());
        ExecutorService pool = Executors.newFixedThreadPool(nWorkers);
        List<Future<?>> futures = new ArrayList<>();

        for (Map.Entry<Path, String> entry : fileSources.entrySet()) {
            Path f = entry.getKey();
            String source = entry.getValue();
            String filePath = f.toString().replace("\\", "/");
            futures.add(pool.submit(() -> {
                try {
                    // 线程级 JavaParser + TypeSolver
                    CombinedTypeSolver ts = new CombinedTypeSolver();
                    ts.add(new ReflectionTypeSolver());
                    if (sourceRoot != null && Files.isDirectory(sourceRoot)) {
                        ts.add(new JavaParserTypeSolver(sourceRoot));
                    }
                    JavaSymbolSolver ss = new JavaSymbolSolver(ts);
                    JavaParser parser = new JavaParser(
                        new ParserConfiguration()
                            .setLanguageLevel(LanguageLevel.JAVA_21)
                            .setSymbolResolver(ss)
                    );
                    var parseResult = parser.parse(source);
                    if (!parseResult.isSuccessful()) return;
                    CompilationUnit cu = parseResult.getResult().orElse(null);
                    if (cu == null) return;

                    // 从 package 推导 groupId
                    List<String> fileGroupIds = new ArrayList<>(config.groupIds);
                    String pkg = cu.getPackageDeclaration().map(p -> p.getNameAsString()).orElse(null);
                    if (pkg != null) {
                        String[] parts = pkg.split("\\.");
                        if (parts.length >= 3) {
                            String autoGid = parts[0] + "." + parts[1] + "." + parts[2];
                            if (!fileGroupIds.contains(autoGid)) fileGroupIds.add(autoGid);
                        } else if (!pkg.isEmpty()) {
                            if (!fileGroupIds.contains(pkg)) fileGroupIds.add(pkg);
                        }
                    }

                    List<CallRecord> recs = extractFromCU(cu, filePath, fileGroupIds);
                    allRecords.addAll(recs);
                } catch (Throwable t) {
                    System.err.println("Extract error: " + f + " : " + t.getMessage());
                }
            }));
        }
        for (Future<?> fut : futures) {
            try { fut.get(); } catch (Exception ex) {
                System.err.println("Worker failed: " + ex.getMessage());
            }
        }
        pool.shutdown();

        return toJson(allRecords);
    }

    /**
     * 从单个 CU 提取方法调用记录。
     */
    private static List<CallRecord> extractFromCU(CompilationUnit cu, String filePath, List<String> groupIds) {
        List<CallRecord> records = new ArrayList<>();
        for (MethodDeclaration method : cu.findAll(MethodDeclaration.class)) {
            int startLine = method.getBegin().map(p -> p.line - 1).orElse(-1);
            String signature = method.getDeclarationAsString(true, true, true);
            for (MethodCallExpr call : method.findAll(MethodCallExpr.class)) {
                String calledFQN = resolveCall(call);
                boolean isSink = determineSink(calledFQN, groupIds);
                records.add(new CallRecord(startLine, signature, calledFQN, isSink, filePath));
            }
        }
        return records;
    }

    /**
     * 解析方法调用，返回 FQN.method(实参) 格式；无法解析时降级为原始文本。
     */
    private static String resolveCall(MethodCallExpr call) {
        String argsStr = call.getArguments().stream()
                .map(Node::toString)
                .collect(Collectors.joining(", "));

        try {
            ResolvedMethodDeclaration resolved = call.resolve();
            return resolved.getQualifiedName() + "(" + argsStr + ")";
        } catch (RuntimeException e) {
            // 降级：尝试从 this.xxx 或 xxx 解析字段类型
            String scope = call.getScope().map(Object::toString).orElse("");
            String name = call.getNameAsString();

            // this.field.method(args) → 找 field 的声明类型
            if (scope.startsWith("this.")) {
                String fieldName = scope.substring(5); // 去掉 "this."
                String fieldType = resolveFieldType(call, fieldName);
                if (fieldType != null) {
                    return fieldType + "." + name + "(" + argsStr + ")";
                }
            }

            // 降级：用调用点可见的名字
            String prefix = scope.isEmpty() ? name : scope + "." + name;
            return prefix + "(" + argsStr + ")";
        }
    }

    /**
     * 从当前类中查找字段声明的类型名，并通过 import 解析为完整 FQN。
     * 遍历 AST 找到包含 call 的类，在该类中查找 fieldName 的声明，
     * 然后从 CompilationUnit 的 import 列表中解析短名为完整 FQN。
     */
    private static String resolveFieldType(MethodCallExpr call, String fieldName) {
        // 向上遍历找到包含此调用的 ClassOrInterfaceDeclaration
        com.github.javaparser.ast.body.ClassOrInterfaceDeclaration cls = null;
        Node parent = call.getParentNode().orElse(null);
        while (parent != null) {
            if (parent instanceof com.github.javaparser.ast.body.ClassOrInterfaceDeclaration) {
                cls = (com.github.javaparser.ast.body.ClassOrInterfaceDeclaration) parent;
                break;
            }
            parent = parent.getParentNode().orElse(null);
        }
        if (cls == null) return null;

        // 在类中查找同名字段
        String shortTypeName = null;
        for (com.github.javaparser.ast.body.FieldDeclaration fd : cls.getFields()) {
            for (com.github.javaparser.ast.body.VariableDeclarator vd : fd.getVariables()) {
                if (vd.getNameAsString().equals(fieldName)) {
                    shortTypeName = vd.getType().asString();
                    break;
                }
            }
            if (shortTypeName != null) break;
        }
        if (shortTypeName == null) return null;

        // 如果已经是完整 FQN（含 . 且不以小写开头），直接返回
        if (shortTypeName.contains(".") && !shortTypeName.matches("^[a-z].*")) {
            return shortTypeName;
        }

        // 从 CompilationUnit 的 import 列表解析短名
        com.github.javaparser.ast.CompilationUnit cu = null;
        Node p = call;
        while (p != null) {
            if (p instanceof com.github.javaparser.ast.CompilationUnit) {
                cu = (com.github.javaparser.ast.CompilationUnit) p;
                break;
            }
            p = p.getParentNode().orElse(null);
        }
        if (cu == null) return shortTypeName;

        // 精确匹配 import：import xxx.yyy.User; → shortTypeName="User" → 返回 "xxx.yyy.User"
        for (com.github.javaparser.ast.ImportDeclaration imp : cu.getImports()) {
            String impFqn = imp.getNameAsString();
            String impShort = impFqn.substring(impFqn.lastIndexOf('.') + 1);
            if (impShort.equals(shortTypeName)) {
                return impFqn;
            }
        }

        // 通配符 import：import xxx.yyy.*; → 返回 "xxx.yyy.User"
        for (com.github.javaparser.ast.ImportDeclaration imp : cu.getImports()) {
            if (imp.isAsterisk()) {
                String pkg = imp.getNameAsString();
                return pkg + "." + shortTypeName;
            }
        }

        // java.lang 自动导入
        return shortTypeName;
    }

    /**
     * 判定是否为外部 sink（支持多个 groupId）。
     * <p>
     * - 无 groupId 列表 → 不判定，返回 false（保持兼容，由调用方自行处理）。
     * - 空 calledFQN → 保守标 true（JAR 偶发输出，安全起见按 sink 处理）。
     * - 成功解析为完整 FQN（≥3 段）且不以任何 groupId 开头 → 真 sink。
     * - 成功解析为完整 FQN 但属于某个 groupId 命名空间 → 内部调用，非 sink。
     * - 短名（符号解析失败，降级输出）→ 大概率同文件/同包内部调用，保守标 false 避免误判。
     */
    static boolean determineSink(String calledFQN, List<String> groupIds) {
        if (groupIds == null || groupIds.isEmpty()) {
            return false;
        }
        if (calledFQN == null || calledFQN.isEmpty()) {
            return true;
        }
        // 检查是否属于任一 groupId
        for (String gid : groupIds) {
            if (gid != null && !gid.isEmpty() && calledFQN.startsWith(gid + ".")) {
                return false;
            }
        }
        // 看起来是完整 FQN（至少 3 段且不以任何 groupId 开头）→ 真正的外部 sink
        if (calledFQN.contains(".") && calledFQN.split("\\.").length >= 3) {
            return true;
        }
        // 短名（符号解析失败）：大概率是同文件/同包内部调用，保守不标
        return false;
    }

    // ==================== routes 模式 ====================

    /**
     * 路由注解解析（多线程）。
     * <p>
     * 多个文件可并行解析（路由提取不依赖符号解析，每个文件独立）。
     */
    static String runRoutes(Config config) throws Exception {
        List<Path> files = config.files.stream().map(Path::of).toList();
        if (files.isEmpty()) return "[]";

        List<Map<String, Object>> allRoutes = Collections.synchronizedList(new ArrayList<>());
        int nWorkers = Math.min(config.workers, files.size());
        ExecutorService pool = Executors.newFixedThreadPool(nWorkers);
        List<Future<?>> futures = new ArrayList<>();

        for (Path f : files) {
            futures.add(pool.submit(() -> {
                try {
                    List<Map<String, Object>> routes = RouteExtractor.extractFromFile(f);
                    synchronized (allRoutes) {
                        allRoutes.addAll(routes);
                    }
                } catch (Exception e) {
                    System.err.println("Route extract error: " + f + " : " + e.getMessage());
                }
            }));
        }
        for (Future<?> fut : futures) {
            try { fut.get(); } catch (Exception ex) {
                System.err.println("Worker failed: " + ex.getMessage());
            }
        }
        pool.shutdown();

        return routeListToJson(allRoutes);
    }

    // ==================== 辅助 ====================

    /**
     * 向上查找 src/main/java 或 src/test/java 作为项目源码根。
     */
    private static Path locateProjectRoot(Path javaFile) {
        Path current = javaFile.toAbsolutePath().getParent();
        while (current != null) {
            for (String sub : List.of("src/main/java", "src/test/java")) {
                Path candidate = current.resolve(sub);
                if (Files.isDirectory(candidate) &&
                        javaFile.toAbsolutePath().startsWith(candidate)) {
                    return candidate;
                }
            }
            current = current.getParent();
        }
        return null;
    }

    private record CallRecord(int startLine, String methodSignature, String calledFQN, boolean isSink, String file) {}

    // ==================== Config 内部类 ====================

    static class Config {
        String mode = "calls";
        List<String> files = new ArrayList<>();
        List<String> groupIds = new ArrayList<>();
        String sourceRoot = null;
        int workers = 4;
    }

    // ==================== 极简 JSON 序列化（避免引入 Jackson/Gson 依赖）====================

    private static String toJson(List<CallRecord> records) {
        if (records.isEmpty()) return "[]";
        StringBuilder sb = new StringBuilder("[\n");
        for (int i = 0; i < records.size(); i++) {
            CallRecord r = records.get(i);
            Map<String, Object> obj = new LinkedHashMap<>();
            obj.put("startLine", r.startLine());
            obj.put("methodSignature", r.methodSignature());
            obj.put("calledFQN", r.calledFQN());
            obj.put("isSink", r.isSink());
            obj.put("file", r.file());
            sb.append("  ").append(toJsonObj(obj));
            if (i < records.size() - 1) sb.append(",");
            sb.append("\n");
        }
        sb.append("]");
        return sb.toString();
    }

    private static String routeListToJson(List<Map<String, Object>> routes) {
        if (routes.isEmpty()) return "[]";
        StringBuilder sb = new StringBuilder("[\n");
        for (int i = 0; i < routes.size(); i++) {
            sb.append("  ").append(toJsonObj(routes.get(i)));
            if (i < routes.size() - 1) sb.append(",");
            sb.append("\n");
        }
        sb.append("]");
        return sb.toString();
    }

    private static String toJsonObj(Map<String, Object> obj) {
        StringBuilder sb = new StringBuilder("{");
        int i = 0;
        for (Map.Entry<String, Object> entry : obj.entrySet()) {
            if (i++ > 0) sb.append(", ");
            sb.append("\"").append(escapeJson(entry.getKey())).append("\": ");
            Object v = entry.getValue();
            if (v instanceof Number) {
                sb.append(v);
            } else if (v instanceof Boolean) {
                sb.append(v);
            } else if (v instanceof List<?> list) {
                sb.append("[");
                for (int j = 0; j < list.size(); j++) {
                    if (j > 0) sb.append(", ");
                    sb.append("\"").append(escapeJson(String.valueOf(list.get(j)))).append("\"");
                }
                sb.append("]");
            } else {
                sb.append("\"").append(escapeJson(String.valueOf(v))).append("\"");
            }
        }
        sb.append("}");
        return sb.toString();
    }

    private static String escapeJson(String s) {
        return s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }
}
