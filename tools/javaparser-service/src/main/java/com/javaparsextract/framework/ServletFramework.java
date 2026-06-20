package com.javaparsextract.framework;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.expr.MemberValuePair;
import com.github.javaparser.ast.expr.NormalAnnotationExpr;
import com.github.javaparser.ast.expr.SingleMemberAnnotationExpr;
import com.javaparsextract.spi.AnnotationUtils;
import com.javaparsextract.spi.FrameworkHandler;
import com.javaparsextract.spi.RouteResult;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Servlet 框架路由处理器。
 *
 * @WebServlet(urlPatterns) 和 @WebFilter(urlPatterns) 是类级路由，
 * 方法级不产生路由端点。
 */
public class ServletFramework implements FrameworkHandler {

    private static final Set<String> SERVLET_MARKERS = Set.of("WebServlet", "WebFilter");

    @Override public String frameworkName() { return "servlet"; }

    @Override public Set<String> supportedAnnotations() { return Set.of(); }

    @Override
    public boolean isController(ClassOrInterfaceDeclaration cls) {
        for (AnnotationExpr ann : cls.getAnnotations()) {
            String name = AnnotationUtils.getShortAnnotationName(ann);
            if (SERVLET_MARKERS.contains(name)) return true;
        }
        return false;
    }

    @Override
    public String extractClassBasePath(ClassOrInterfaceDeclaration cls) {
        if (!isController(cls)) return null;
        return extractUrlPatterns(cls);
    }

    @Override
    public Optional<RouteResult> handleMethodAnnotation(
            String annotationName, AnnotationExpr annotation,
            MethodDeclaration method, String classBasePath, String classFqn) {
        // Servlet 路由是类级 only
        return Optional.empty();
    }

    /**
     * 从类级注解提取路由（Servlet 特有：类级路由才是端点）。
     * 由 RouteExtractor orchestrator 在发现 Servlet 类时调用。
     */
    public List<RouteResult> extractClassLevelRoutes(
            ClassOrInterfaceDeclaration cls, String classFqn, String filePath) {
        List<RouteResult> results = new ArrayList<>();
        for (AnnotationExpr ann : cls.getAnnotations()) {
            String name = AnnotationUtils.getShortAnnotationName(ann);
            if ("WebServlet".equals(name)) {
                String path = extractUrlPatternsFromAnnotation(ann);
                results.add(new RouteResult(classFqn, path, classFqn, cls.getNameAsString(),
                        path, List.of("ANY"), name, filePath,
                        cls.getBegin().map(p -> p.line).orElse(0)));
            } else if ("WebFilter".equals(name)) {
                String path = extractUrlPatternsFromAnnotation(ann);
                results.add(new RouteResult(classFqn, path, classFqn, cls.getNameAsString(),
                        path, List.of("ANY"), name, filePath,
                        cls.getBegin().map(p -> p.line).orElse(0)));
            }
        }
        return results;
    }

    private String extractUrlPatterns(ClassOrInterfaceDeclaration cls) {
        for (AnnotationExpr ann : cls.getAnnotations()) {
            String name = AnnotationUtils.getShortAnnotationName(ann);
            if (SERVLET_MARKERS.contains(name)) {
                return extractUrlPatternsFromAnnotation(ann);
            }
        }
        return "";
    }

    private String extractUrlPatternsFromAnnotation(AnnotationExpr ann) {
        if (ann instanceof SingleMemberAnnotationExpr sma) {
            return AnnotationUtils.stripQuotes(sma.getMemberValue().toString());
        }
        if (ann instanceof NormalAnnotationExpr na) {
            for (MemberValuePair pair : na.getPairs()) {
                String key = pair.getNameAsString();
                if ("urlPatterns".equals(key) || "value".equals(key)) {
                    return AnnotationUtils.stripQuotes(pair.getValue().toString());
                }
            }
        }
        return "";
    }
}
