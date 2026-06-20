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
 * Spring GraphQL 框架路由处理器。
 *
 * GraphQL 注解不映射到 URL 路径，而是映射到 GraphQL 操作类型。
 */
public class SpringGraphQLFramework implements FrameworkHandler {

    private static final Set<String> CONTROLLER_MARKERS = Set.of("Controller", "RestController");

    private final LinkedHashMap<String, List<String>> dispatch;

    public SpringGraphQLFramework() {
        dispatch = new LinkedHashMap<>();
        dispatch.put("QueryMapping", List.of("QUERY"));
        dispatch.put("MutationMapping", List.of("MUTATION"));
        dispatch.put("SubscriptionMapping", List.of("SUBSCRIPTION"));
        dispatch.put("SchemaMapping", List.of("ANY"));
    }

    @Override public String frameworkName() { return "spring-graphql"; }

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
        // GraphQL 无 URL 路径概念
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
}
