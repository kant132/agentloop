package com.javaparsextract;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.expr.ArrayInitializerExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.MemberValuePair;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.NormalAnnotationExpr;
import com.github.javaparser.ast.expr.SingleMemberAnnotationExpr;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 路由注解解析器。
 *
 * 精确解析 Spring/JAX-RS 路由注解：
 * - 类级 @RequestMapping/@Path → 基础路径
 * - 方法级 @GetMapping/@PostMapping/.../方法级 @RequestMapping → 方法路径 + HTTP 方法
 * - 路径拼接：类路径 + 方法路径
 * - 多 method 展开
 *
 * 输出 JSON 数组，每条记录描述一个路由端点。
 */
public class RouteExtractor {

    /** 注解短名 → 默认 HTTP 方法（Spring 组合注解）。 */
    private static final Map<String, String> ANNOTATION_METHOD_MAP = Map.of(
            "GetMapping", "GET",
            "PostMapping", "POST",
            "PutMapping", "PUT",
            "DeleteMapping", "DELETE",
            "PatchMapping", "PATCH"
    );

    /** @RequestMapping 无 method 参数时的全展开。 */
    private static final List<String> ALL_METHODS = List.of(
            "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"
    );

    /** JAX-RS 方法注解。 */
    private static final Set<String> JAXRS_METHOD_ANNOTATIONS = Set.of(
            "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"
    );

    /** 类级控制器标记注解。 */
    private static final Set<String> CONTROLLER_MARKERS = Set.of(
            "RestController", "Controller", "ControllerAdvice", "RestControllerAdvice"
    );

    private static final String SPRING_PREFIX = "org.springframework.web.bind.annotation.";
    private static final String JAXRS_PREFIX = "javax.ws.rs.";
    private static final String JAKARTA_JAXRS_PREFIX = "jakarta.ws.rs.";

    /**
     * 解析单个 Java 文件，提取所有路由端点，返回 JSON 字符串。
     */
    public static String extract(Path javaFile) throws IOException {
        CompilationUnit cu = StaticJavaParser.parse(Files.readString(javaFile));
        List<Map<String, Object>> routes = new ArrayList<>();

        String packageName = cu.getPackageDeclaration()
                .map(p -> p.getNameAsString()).orElse("");

        for (ClassOrInterfaceDeclaration cls : cu.findAll(ClassOrInterfaceDeclaration.class)) {
            if (cls.isInterface()) continue;

            String className = cls.getNameAsString();
            String classFqn = packageName.isEmpty() ? className : packageName + "." + className;

            String classBasePath = extractClassBasePath(cls);
            if (classBasePath == null) continue; // 不是控制器

            for (MethodDeclaration method : cls.getMethods()) {
                Map<String, Object> route = extractMethodRoute(
                        method, classFqn, classBasePath, javaFile);
                if (route != null) {
                    routes.add(route);
                }
            }
        }

        return toJson(routes);
    }

    // ==================== 类级基础路径 ====================

    /**
     * 解析类级基础路径。
     *
     * @return 路径字符串（可能为空 ""）；若类不是控制器则返回 null。
     */
    private static String extractClassBasePath(ClassOrInterfaceDeclaration cls) {
        boolean isController = false;
        String requestMappingPath = null;
        String jaxrsPath = null;

        for (AnnotationExpr ann : cls.getAnnotations()) {
            String name = getShortAnnotationName(ann);
            if (CONTROLLER_MARKERS.contains(name)) {
                isController = true;
            } else if ("RequestMapping".equals(name)) {
                requestMappingPath = extractPathFromAnnotation(ann);
            } else if ("Path".equals(name)) {
                jaxrsPath = extractPathFromAnnotation(ann);
            }
        }

        if (jaxrsPath != null) return jaxrsPath;     // JAX-RS @Path 优先
        if (isController) {
            return requestMappingPath != null ? requestMappingPath : "";
        }
        return null;
    }

    // ==================== 方法级路由 ====================

    /**
     * 解析方法级路由。一个方法只取第一个识别到的路由注解。
     */
    private static Map<String, Object> extractMethodRoute(
            MethodDeclaration method, String classFqn,
            String classBasePath, Path file) {

        for (AnnotationExpr ann : method.getAnnotations()) {
            String name = getShortAnnotationName(ann);
            List<String> httpMethods = resolveHttpMethods(name, ann);
            if (httpMethods == null || httpMethods.isEmpty()) continue;

            String path = extractPathFromAnnotation(ann);
            String fullUrl = joinPath(classBasePath, path);
            String methodFqn = classFqn + "#" + method.getNameAsString();
            int startLine = method.getBegin().map(p -> p.line).orElse(0);

            Map<String, Object> route = new LinkedHashMap<>();
            route.put("class_fqn", classFqn);
            route.put("class_base_path", classBasePath);
            route.put("method_fqn", methodFqn);
            route.put("method_name", method.getNameAsString());
            route.put("full_url", fullUrl);
            route.put("http_methods", httpMethods);
            route.put("annotation", name);
            route.put("file", file.toString().replace('\\', '/'));
            route.put("start_line", startLine);
            return route;
        }
        return null;
    }

    // ==================== 注解参数提取 ====================

    /**
     * 从注解提取 path/value 参数。
     * - SingleMemberAnnotationExpr: `@GetMapping("/users")` → "/users"
     * - NormalAnnotationExpr: 找 value 或 path 键
     * - MarkerAnnotationExpr: `@GetMapping` → ""
     */
    private static String extractPathFromAnnotation(AnnotationExpr ann) {
        if (ann instanceof SingleMemberAnnotationExpr sma) {
            return stripQuotes(sma.getMemberValue().toString());
        }
        if (ann instanceof NormalAnnotationExpr na) {
            for (MemberValuePair pair : na.getPairs()) {
                String key = pair.getNameAsString();
                if ("value".equals(key) || "path".equals(key)) {
                    return stripQuotes(pair.getValue().toString());
                }
            }
        }
        return "";
    }

    /**
     * 解析 HTTP 方法列表。
     *
     * @return 方法列表；若注解不是路由注解则返回 null。
     */
    private static List<String> resolveHttpMethods(String annotationName, AnnotationExpr ann) {
        // Spring 组合注解：GetMapping→GET
        if (ANNOTATION_METHOD_MAP.containsKey(annotationName)) {
            return List.of(ANNOTATION_METHOD_MAP.get(annotationName));
        }

        // JAX-RS 方法注解：@GET / @POST / ...
        if (JAXRS_METHOD_ANNOTATIONS.contains(annotationName)) {
            return List.of(annotationName);
        }

        // @RequestMapping：解析 method 参数
        if ("RequestMapping".equals(annotationName)) {
            if (ann instanceof NormalAnnotationExpr na) {
                for (MemberValuePair pair : na.getPairs()) {
                    if ("method".equals(pair.getNameAsString())) {
                        List<String> parsed = parseMethodParam(pair.getValue());
                        if (!parsed.isEmpty()) return parsed;
                    }
                }
            }
            // 无 method 参数（或解析失败）→ 全展开
            return new ArrayList<>(ALL_METHODS);
        }

        // WebSocket
        if ("MessageMapping".equals(annotationName)) {
            return List.of("WS");
        }

        // 异常处理端点
        if ("ExceptionHandler".equals(annotationName)) {
            return List.of("ANY");
        }

        return null;
    }

    /**
     * 解析 method= 参数值。
     * 支持形式：
     * - RequestMethod.GET（FieldAccessExpr）
     * - GET（NameExpr）
     * - {RequestMethod.GET, RequestMethod.POST}（ArrayInitializerExpr）
     */
    private static List<String> parseMethodParam(Expression value) {
        List<String> methods = new ArrayList<>();
        if (value instanceof FieldAccessExpr fae) {
            methods.add(normalizeMethod(fae.getNameAsString()));
        } else if (value instanceof NameExpr ne) {
            methods.add(normalizeMethod(ne.getNameAsString()));
        } else if (value instanceof ArrayInitializerExpr arr) {
            for (Expression elem : arr.getValues()) {
                if (elem instanceof FieldAccessExpr fae2) {
                    methods.add(normalizeMethod(fae2.getNameAsString()));
                } else if (elem instanceof NameExpr ne2) {
                    methods.add(normalizeMethod(ne2.getNameAsString()));
                }
            }
        }
        return methods;
    }

    private static String normalizeMethod(String raw) {
        return raw.toUpperCase();
    }

    /**
     * 获取注解短名（去掉 Spring/JAX-RS 包前缀）。
     */
    private static String getShortAnnotationName(AnnotationExpr ann) {
        String name = ann.getNameAsString();
        if (name.startsWith(SPRING_PREFIX)) {
            return name.substring(SPRING_PREFIX.length());
        }
        if (name.startsWith(JAXRS_PREFIX)) {
            return name.substring(JAXRS_PREFIX.length());
        }
        if (name.startsWith(JAKARTA_JAXRS_PREFIX)) {
            return name.substring(JAKARTA_JAXRS_PREFIX.length());
        }
        return name;
    }

    // ==================== 路径拼接 ====================

    /**
     * 路径拼接：normalize(base) + "/" + normalize(method)。
     * 空路径 → 根路径 "/"。
     */
    private static String joinPath(String base, String method) {
        String b = stripSlash(base);
        String m = stripSlash(method);
        if (b.isEmpty() && m.isEmpty()) return "/";
        if (b.isEmpty()) return "/" + m;
        if (m.isEmpty()) return "/" + b;
        return "/" + b + "/" + m;
    }

    private static String stripSlash(String s) {
        if (s == null || s.isEmpty()) return "";
        return s.replaceAll("^/+|/+$", "");
    }

    private static String stripQuotes(String s) {
        if (s == null) return "";
        s = s.trim();
        if (s.startsWith("\"") && s.endsWith("\"") && s.length() >= 2) {
            return s.substring(1, s.length() - 1);
        }
        return s;
    }

    // ==================== 极简 JSON 序列化 ====================

    private static String toJson(List<Map<String, Object>> routes) {
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
