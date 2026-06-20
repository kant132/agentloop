package com.javaparsextract.framework;

import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.javaparsextract.spi.AnnotationUtils;
import com.javaparsextract.spi.FrameworkHandler;
import com.javaparsextract.spi.RouteResult;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * JAX-RS 框架路由处理器。
 *
 * 支持 javax.ws.rs 和 jakarta.ws.rs 双前缀。
 */
public class JaxRsFramework implements FrameworkHandler {

    private static final Set<String> JAXRS_METHOD_ANNOTATIONS = Set.of(
            "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS");

    private final LinkedHashMap<String, Handler> dispatch;

    @FunctionalInterface
    private interface Handler {
        List<String> httpMethods();
    }

    public JaxRsFramework() {
        dispatch = new LinkedHashMap<>();
        dispatch.put("GET", () -> List.of("GET"));
        dispatch.put("POST", () -> List.of("POST"));
        dispatch.put("PUT", () -> List.of("PUT"));
        dispatch.put("DELETE", () -> List.of("DELETE"));
        dispatch.put("PATCH", () -> List.of("PATCH"));
        dispatch.put("HEAD", () -> List.of("HEAD"));
        dispatch.put("OPTIONS", () -> List.of("OPTIONS"));
        dispatch.put("Path", () -> List.of("ANY"));
    }

    @Override public String frameworkName() { return "jaxrs"; }

    @Override public Set<String> supportedAnnotations() {
        return dispatch.keySet();
    }

    @Override
    public boolean isController(ClassOrInterfaceDeclaration cls) {
        for (AnnotationExpr ann : cls.getAnnotations()) {
            String name = AnnotationUtils.getShortAnnotationName(ann);
            if ("Path".equals(name)) return true;
        }
        return false;
    }

    @Override
    public String extractClassBasePath(ClassOrInterfaceDeclaration cls) {
        if (!isController(cls)) return null;
        for (AnnotationExpr ann : cls.getAnnotations()) {
            String name = AnnotationUtils.getShortAnnotationName(ann);
            if ("Path".equals(name)) {
                return AnnotationUtils.extractPathValue(ann);
            }
        }
        return "";
    }

    @Override
    public Optional<RouteResult> handleMethodAnnotation(
            String annotationName, AnnotationExpr annotation,
            MethodDeclaration method, String classBasePath, String classFqn) {
        Handler h = dispatch.get(annotationName);
        if (h == null) return Optional.empty();

        List<String> httpMethods = h.httpMethods();
        String path;
        if ("Path".equals(annotationName)) {
            // 方法级 @Path 是子路径，不是 HTTP 方法
            path = AnnotationUtils.extractPathValue(annotation);
        } else {
            // HTTP 方法注解：路径可能来自同一方法上的 @Path
            path = findMethodLevelPath(method);
        }

        String fullUrl = AnnotationUtils.joinPath(classBasePath, path);
        String methodFqn = classFqn + "#" + method.getNameAsString();
        int startLine = method.getBegin().map(p -> p.line).orElse(0);

        return Optional.of(new RouteResult(
                classFqn, classBasePath, methodFqn, method.getNameAsString(),
                fullUrl, httpMethods, annotationName, "", startLine));
    }

    private String findMethodLevelPath(MethodDeclaration method) {
        for (AnnotationExpr ann : method.getAnnotations()) {
            String name = AnnotationUtils.getShortAnnotationName(ann);
            if ("Path".equals(name)) {
                return AnnotationUtils.extractPathValue(ann);
            }
        }
        return "";
    }
}
