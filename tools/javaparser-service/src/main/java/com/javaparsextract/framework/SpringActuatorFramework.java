package com.javaparsextract.framework;

import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.expr.MemberValuePair;
import com.github.javaparser.ast.expr.NormalAnnotationExpr;
import com.github.javaparser.ast.expr.SingleMemberAnnotationExpr;
import com.javaparsextract.spi.AnnotationUtils;
import com.javaparsextract.spi.FrameworkHandler;
import com.javaparsextract.spi.RouteResult;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * Spring Boot Actuator 自定义端点处理器。
 *
 * @Endpoint(id="custom") → /actuator/custom
 * @ReadOperation → GET, @WriteOperation → POST, @DeleteOperation → DELETE
 */
public class SpringActuatorFramework implements FrameworkHandler {

    private static final Set<String> ENDPOINT_MARKERS = Set.of("Endpoint", "WebEndpoint");

    private final LinkedHashMap<String, List<String>> dispatch;

    public SpringActuatorFramework() {
        dispatch = new LinkedHashMap<>();
        dispatch.put("ReadOperation", List.of("GET"));
        dispatch.put("WriteOperation", List.of("POST"));
        dispatch.put("DeleteOperation", List.of("DELETE"));
    }

    @Override public String frameworkName() { return "spring-actuator"; }

    @Override public Set<String> supportedAnnotations() {
        return dispatch.keySet();
    }

    @Override
    public boolean isController(ClassOrInterfaceDeclaration cls) {
        for (AnnotationExpr ann : cls.getAnnotations()) {
            String name = AnnotationUtils.getShortAnnotationName(ann);
            if (ENDPOINT_MARKERS.contains(name)) return true;
        }
        return false;
    }

    @Override
    public String extractClassBasePath(ClassOrInterfaceDeclaration cls) {
        if (!isController(cls)) return null;
        String endpointId = extractEndpointId(cls);
        return "/actuator/" + endpointId;
    }

    @Override
    public Optional<RouteResult> handleMethodAnnotation(
            String annotationName, AnnotationExpr annotation,
            MethodDeclaration method, String classBasePath, String classFqn) {
        List<String> httpMethods = dispatch.get(annotationName);
        if (httpMethods == null) return Optional.empty();

        String methodFqn = classFqn + "#" + method.getNameAsString();
        int startLine = method.getBegin().map(p -> p.line).orElse(0);

        return Optional.of(new RouteResult(
                classFqn, classBasePath, methodFqn, method.getNameAsString(),
                classBasePath, httpMethods, annotationName, "", startLine));
    }

    private String extractEndpointId(ClassOrInterfaceDeclaration cls) {
        for (AnnotationExpr ann : cls.getAnnotations()) {
            String name = AnnotationUtils.getShortAnnotationName(ann);
            if ("Endpoint".equals(name) || "WebEndpoint".equals(name)) {
                return extractIdParam(ann);
            }
        }
        return "";
    }

    private String extractIdParam(AnnotationExpr ann) {
        if (ann instanceof SingleMemberAnnotationExpr sma) {
            return AnnotationUtils.stripQuotes(sma.getMemberValue().toString());
        }
        if (ann instanceof NormalAnnotationExpr na) {
            for (MemberValuePair pair : na.getPairs()) {
                if ("id".equals(pair.getNameAsString())) {
                    return AnnotationUtils.stripQuotes(pair.getValue().toString());
                }
            }
        }
        return "";
    }
}
