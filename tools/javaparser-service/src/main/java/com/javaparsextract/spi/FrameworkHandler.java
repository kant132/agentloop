package com.javaparsextract.spi;

import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.body.MethodDeclaration;

import java.util.Optional;
import java.util.Set;

/**
 * 框架路由注解处理器 SPI。
 *
 * 每个框架（Spring MVC、JAX-RS、GraphQL 等）实现此接口，
 * RouteExtractor 通过注册的处理器列表分发注解解析。
 */
public interface FrameworkHandler {

    /** 框架名称（如 "spring-mvc"、"jaxrs"）。 */
    String frameworkName();

    /** 该框架支持的方法级注解短名集合。 */
    Set<String> supportedAnnotations();

    /** 判断类是否属于此框架的控制器/资源类。 */
    boolean isController(ClassOrInterfaceDeclaration cls);

    /** 提取类级基础路径；若类不是此框架的控制器则返回 null。 */
    String extractClassBasePath(ClassOrInterfaceDeclaration cls);

    /** 解析方法级注解，返回路由结果；若注解不属于此框架则返回 empty。 */
    Optional<RouteResult> handleMethodAnnotation(
            String annotationName,
            AnnotationExpr annotation,
            MethodDeclaration method,
            String classBasePath,
            String classFqn);
}
