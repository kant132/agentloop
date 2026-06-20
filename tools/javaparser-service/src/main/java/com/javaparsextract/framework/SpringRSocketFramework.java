package com.javaparsextract.framework;

import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.javaparsextract.spi.AnnotationUtils;
import com.javaparsextract.spi.FrameworkHandler;
import com.javaparsextract.spi.RouteResult;

import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * Spring RSocket 框架路由处理器。
 *
 * @MessageMapping → RSocket（通过 import 检查区分 WebSocket @MessageMapping）
 */
public class SpringRSocketFramework implements FrameworkHandler {

    private static final Set<String> CONTROLLER_MARKERS = Set.of("Controller", "RestController");

    @Override public String frameworkName() { return "spring-rsocket"; }

    @Override public Set<String> supportedAnnotations() { return Set.of("MessageMapping"); }

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
        if (!"MessageMapping".equals(annotationName)) return Optional.empty();

        String path = AnnotationUtils.extractPathValue(annotation);
        String fullUrl = AnnotationUtils.joinPath(classBasePath, path);
        String methodFqn = classFqn + "#" + method.getNameAsString();
        int startLine = method.getBegin().map(p -> p.line).orElse(0);

        return Optional.of(new RouteResult(
                classFqn, classBasePath, methodFqn, method.getNameAsString(),
                fullUrl, List.of("RSOCKET"), annotationName, "", startLine));
    }

    /** 检查 CU 中是否有 RSocket import（区分 WebSocket @MessageMapping）。 */
    public static boolean hasRSocketImports(CompilationUnit cu) {
        for (com.github.javaparser.ast.ImportDeclaration imp : cu.getImports()) {
            String name = imp.getNameAsString();
            if (name.startsWith("org.springframework.messaging.rsocket")) {
                return true;
            }
        }
        return false;
    }
}
