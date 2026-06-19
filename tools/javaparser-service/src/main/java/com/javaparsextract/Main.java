package com.javaparsextract;

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
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * Java 方法调用提取器
 * <p>
 * 输入一个 .java 文件，提取每个方法内调用的所有其他方法的信息：
 * - startLine: 外层方法声明的起始行号（从 0 算）
 * - methodSignature: 外层方法签名
 * - calledFQN: 被调用方法的 FQN + 实际调用参数
 * <p>
 * 输出 JSON 数组：
 * [{"startLine":5,"methodSignature":"public void foo()","calledFQN":"java.util.List.add(\"x\")"}]
 */
public class Main {

    public static void main(String[] args) throws IOException {
        // 模式 1：路由注解解析（--routes file.java [sourceRoot]）
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
            System.out.println(RouteExtractor.extract(routeFile));
            return;
        }

        // 模式 2：方法调用提取（默认，原有逻辑）
        if (args.length < 1) {
            System.err.println("Usage: java -jar java-method-call-extractor.jar <file.java> [sourceRoot]");
            System.err.println("  sourceRoot: optional - project source root for cross-file FQN resolution");
            System.err.println("  Use --routes <file.java> for Spring/JAX-RS route extraction");
            System.exit(1);
        }

        Path javaFile = Path.of(args[0]);
        if (!Files.exists(javaFile)) {
            System.err.println("File not found: " + javaFile.toAbsolutePath());
            System.exit(1);
        }

        // 1. 配置符号解析器
        CombinedTypeSolver typeSolver = new CombinedTypeSolver();
        typeSolver.add(new ReflectionTypeSolver());

        Path sourceRoot = (args.length > 1)
                ? Path.of(args[1])
                : locateProjectRoot(javaFile);
        if (sourceRoot != null) {
            typeSolver.add(new JavaParserTypeSolver(sourceRoot));
        }

        JavaSymbolSolver symbolSolver = new JavaSymbolSolver(typeSolver);
        StaticJavaParser.getConfiguration().setSymbolResolver(symbolSolver);

        // 2. 解析源码
        CompilationUnit cu = StaticJavaParser.parse(Files.readString(javaFile));

        // 3. 遍历所有方法声明，收集其内部的方法调用
        List<CallRecord> records = new ArrayList<>();
        for (MethodDeclaration method : cu.findAll(MethodDeclaration.class)) {
            // 起始行号，从 0 算（JavaParser 是 1-based）
            int startLine = method.getBegin().map(p -> p.line - 1).orElse(-1);

            // 方法签名：含访问修饰符、返回类型、方法名、形参列表
            String signature = method.getDeclarationAsString(true, true, true);

            // 收集该方法体内所有方法调用（包含嵌套调用）
            for (MethodCallExpr call : method.findAll(MethodCallExpr.class)) {
                String calledFQN = resolveCall(call);
                records.add(new CallRecord(startLine, signature, calledFQN));
            }
        }

        // 4. 输出 JSON
        System.out.println(toJson(records));
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
            // 降级：用调用点可见的名字
            String scope = call.getScope().map(Object::toString).orElse("");
            String name = call.getNameAsString();
            String prefix = scope.isEmpty() ? name : scope + "." + name;
            return prefix + "(" + argsStr + ")";
        }
    }

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

    private record CallRecord(int startLine, String methodSignature, String calledFQN) {}

    // ==================== 极简 JSON 序列化（避免引入 Jackson/Gson 依赖）====================

    private static String toJson(List<CallRecord> records) {
        if (records.isEmpty()) return "[]";
        StringBuilder sb = new StringBuilder("[\n");
        for (int i = 0; i < records.size(); i++) {
            CallRecord r = records.get(i);
            Map<String, Object> obj = new LinkedHashMap<>();
            obj.put("startLine", r.startLine);
            obj.put("methodSignature", r.methodSignature);
            obj.put("calledFQN", r.calledFQN);
            sb.append("  ").append(toJsonObj(obj));
            if (i < records.size() - 1) sb.append(",");
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
