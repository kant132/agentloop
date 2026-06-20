package com.javaparsextract.framework;

import com.github.javaparser.ast.CompilationUnit;
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
 * Spring WebSocket 框架路由处理器。
 *
 * @MessageMapping → WS, @SubscribeMapping → WS_SUB
 */
public class SpringWebSocketFramework implements FrameworkHandler {

    private static final Set<String> CONTROLLER_MARKERS = Set.of("Controller", "RestController");

    private final LinkedHashMap<String, List<String>> dispatch;

    public SpringWebSocketFramework() {
        dispatch = new LinkedHashMap<>();
        dispatch.put("MessageMapping", List.of("WS"));
        dispatch.put("SubscribeMapping", List.of("WS_SUB"));
    }

    @Override public String frameworkName() { return "spring-websocket"; }

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
        return "";
    }

    @Override
    public Optional<RouteResult> handleMethodAnnotation(
            String annotationName, AnnotationExpr annotation,
            MethodDeclaration method, String classBasePath, String classFqn) {
        List<String> httpMethods = dispatch.get(annotationName);
        if (httpMethods == null) return Optional.empty();

        String path = AnnotationUtils.extractPathValue(annotation);
        String fullUrl = AnnotationUtils.joinPath(classBasePath, path);
        String methodFqn = classFqn + "#" + method.getNameAsString();
        int startLine = method.getBegin().map(p -> p.line).orElse(0);

        return Optional.of(new RouteResult(
                classFqn, classBasePath, methodFqn, method.getNameAsString(),
                fullUrl, httpMethods, annotationName, "", startLine));
    }

    /** 检查 CU 中是否有 WebSocket 相关 import（排除 RSocket）。 */
    public static boolean hasWebSocketImports(CompilationUnit cu) {
        for (com.github.javaparser.ast.ImportDeclaration imp : cu.getImports()) {
            String name = imp.getNameAsString();
            if (name.startsWith("org.springframework.messaging.handler")
                    && !name.contains("rsocket")) {
                return true;
            }
        }
        return false;
    }
}
