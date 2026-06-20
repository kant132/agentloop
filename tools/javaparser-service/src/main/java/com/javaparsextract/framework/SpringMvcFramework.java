package com.javaparsextract.framework;

import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.expr.NormalAnnotationExpr;
import com.javaparsextract.spi.AnnotationUtils;
import com.javaparsextract.spi.FrameworkHandler;
import com.javaparsextract.spi.RouteResult;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * Spring MVC 框架路由处理器。
 *
 * 方法引用分发表：6 个注解 → 6 个处理方法。
 */
public class SpringMvcFramework implements FrameworkHandler {

    private static final Set<String> CONTROLLER_MARKERS = Set.of(
            "RestController", "Controller", "ControllerAdvice", "RestControllerAdvice");

    private final LinkedHashMap<String, Handler> dispatch;

    @FunctionalInterface
    private interface Handler {
        Optional<RouteResult> handle(String annotationName, AnnotationExpr ann,
                                      MethodDeclaration method, String classBasePath, String classFqn);
    }

    public SpringMvcFramework() {
        dispatch = new LinkedHashMap<>();
        dispatch.put("GetMapping", this::handleGetMapping);
        dispatch.put("PostMapping", this::handlePostMapping);
        dispatch.put("PutMapping", this::handlePutMapping);
        dispatch.put("DeleteMapping", this::handleDeleteMapping);
        dispatch.put("PatchMapping", this::handlePatchMapping);
        dispatch.put("RequestMapping", this::handleRequestMapping);
    }

    @Override public String frameworkName() { return "spring-mvc"; }

    @Override public Set<String> supportedAnnotations() {
        return dispatch.keySet();
    }

    @Override
    public boolean isController(ClassOrInterfaceDeclaration cls) {
        for (AnnotationExpr ann : cls.getAnnotations()) {
            String name = AnnotationUtils.getShortAnnotationName(ann);
            if (CONTROLLER_MARKERS.contains(name)) return true;
        }
        return false;
    }

    @Override
    public String extractClassBasePath(ClassOrInterfaceDeclaration cls) {
        if (!isController(cls)) return null;
        for (AnnotationExpr ann : cls.getAnnotations()) {
            String name = AnnotationUtils.getShortAnnotationName(ann);
            if ("RequestMapping".equals(name)) {
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
        return h.handle(annotationName, annotation, method, classBasePath, classFqn);
    }

    // ==================== 分发表处理方法 ====================

    private Optional<RouteResult> handleGetMapping(String name, AnnotationExpr ann,
            MethodDeclaration method, String basePath, String fqn) {
        return buildResult(name, ann, method, basePath, fqn, List.of("GET"));
    }

    private Optional<RouteResult> handlePostMapping(String name, AnnotationExpr ann,
            MethodDeclaration method, String basePath, String fqn) {
        return buildResult(name, ann, method, basePath, fqn, List.of("POST"));
    }

    private Optional<RouteResult> handlePutMapping(String name, AnnotationExpr ann,
            MethodDeclaration method, String basePath, String fqn) {
        return buildResult(name, ann, method, basePath, fqn, List.of("PUT"));
    }

    private Optional<RouteResult> handleDeleteMapping(String name, AnnotationExpr ann,
            MethodDeclaration method, String basePath, String fqn) {
        return buildResult(name, ann, method, basePath, fqn, List.of("DELETE"));
    }

    private Optional<RouteResult> handlePatchMapping(String name, AnnotationExpr ann,
            MethodDeclaration method, String basePath, String fqn) {
        return buildResult(name, ann, method, basePath, fqn, List.of("PATCH"));
    }

    private Optional<RouteResult> handleRequestMapping(String name, AnnotationExpr ann,
            MethodDeclaration method, String basePath, String fqn) {
        List<String> methods = resolveRequestMappingMethods(ann);
        return buildResult(name, ann, method, basePath, fqn, methods);
    }

    // ==================== 内部辅助 ====================

    private List<String> resolveRequestMappingMethods(AnnotationExpr ann) {
        if (ann instanceof NormalAnnotationExpr na) {
            for (var pair : na.getPairs()) {
                if ("method".equals(pair.getNameAsString())) {
                    List<String> parsed = AnnotationUtils.parseMethodParam(pair.getValue());
                    if (!parsed.isEmpty()) return parsed;
                }
            }
        }
        return new ArrayList<>(AnnotationUtils.ALL_METHODS);
    }

    private Optional<RouteResult> buildResult(String annotationName, AnnotationExpr ann,
            MethodDeclaration method, String classBasePath, String classFqn, List<String> httpMethods) {
        String path = AnnotationUtils.extractPathValue(ann);
        String fullUrl = AnnotationUtils.joinPath(classBasePath, path);
        String methodFqn = classFqn + "#" + method.getNameAsString();
        int startLine = method.getBegin().map(p -> p.line).orElse(0);
        return Optional.of(new RouteResult(
                classFqn, classBasePath, methodFqn, method.getNameAsString(),
                fullUrl, httpMethods, annotationName, "", startLine));
    }
}
