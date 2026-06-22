package com.javaparsextract.framework;

import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.expr.BooleanLiteralExpr;
import com.github.javaparser.ast.expr.MemberValuePair;
import com.github.javaparser.ast.expr.NormalAnnotationExpr;
import com.github.javaparser.ast.expr.SingleMemberAnnotationExpr;
import com.javaparsextract.spi.AnnotationUtils;
import com.javaparsextract.spi.FrameworkHandler;
import com.javaparsextract.spi.RouteResult;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * JAX-WS (SOAP Web Service) 框架路由处理器。
 *
 * 支持 javax.jws.WebService 和 jakarta.jws.WebService。
 *
 * 识别逻辑:
 * 1. 类级 @WebService → 类是 SOAP 端点
 * 2. @WebService(endpointInterface="com.foo.Bar") → 实际方法在 SEI 接口中
 * 3. 方法级 @WebMethod(exclude=true) → 排除
 * 4. 有 @WebMethod 标注的方法 → SOAP 操作
 * 5. 无 @WebMethod → 所有 public 方法都是 SOAP 操作
 * 6. @WebMethod(action="urn:doSomething") → SOAP action
 *
 * 路由路径: CXF 默认 /services/{serviceName}，serviceName 从 @WebService(serviceName="...") 提取。
 */
public class JaxWsFramework implements FrameworkHandler {

    @Override
    public String frameworkName() {
        return "jaxws";
    }

    @Override
    public Set<String> supportedAnnotations() {
        return Set.of("WebService", "WebMethod");
    }

    @Override
    public boolean isController(ClassOrInterfaceDeclaration cls) {
        for (AnnotationExpr ann : cls.getAnnotations()) {
            String name = AnnotationUtils.getShortAnnotationName(ann);
            if ("WebService".equals(name)) {
                return true;
            }
        }
        return false;
    }

    @Override
    public String extractClassBasePath(ClassOrInterfaceDeclaration cls) {
        if (!isController(cls)) {
            return null;
        }

        // 默认 serviceName = 类简单名
        String serviceName = cls.getNameAsString();

        for (AnnotationExpr ann : cls.getAnnotations()) {
            String name = AnnotationUtils.getShortAnnotationName(ann);
            if ("WebService".equals(name)) {
                // 提取 serviceName 参数
                String sn = extractAnnotationParam(ann, "serviceName");
                if (sn != null && !sn.isEmpty()) {
                    serviceName = sn;
                }
            }
        }

        // CXF 默认 URL 前缀: /services/{serviceName}
        return "/services/" + serviceName;
    }

    @Override
    public Optional<RouteResult> handleMethodAnnotation(
            String annotationName, AnnotationExpr annotation,
            MethodDeclaration method, String classBasePath, String classFqn) {

        if ("WebMethod".equals(annotationName)) {
            // 检查 exclude=true
            if (isExcludeTrue(annotation)) {
                return Optional.empty();
            }

            // 提取 action
            String action = extractAnnotationParam(annotation, "action");

            String methodFqn = classFqn + "#" + method.getNameAsString();
            int startLine = method.getBegin().map(p -> p.line).orElse(0);

            // SOAP 默认 POST
            String fullUrl = classBasePath != null ? classBasePath : "/services";

            return Optional.of(new RouteResult(
                    classFqn, classBasePath, methodFqn, method.getNameAsString(),
                    fullUrl, List.of("POST"), annotationName, "", startLine));
        }

        // 如果类有 @WebService 但方法没有 @WebMethod
        // 且类中没有任何 @WebMethod → 所有 public 方法都是端点
        // 这个逻辑在 RouteExtractor 层处理（如果没有 @WebMethod 注解命中，该方法不会被传入）
        return Optional.empty();
    }

    /**
     * 判断 @WebMethod(exclude=true)。
     */
    private boolean isExcludeTrue(AnnotationExpr ann) {
        if (ann instanceof NormalAnnotationExpr na) {
            for (MemberValuePair pair : na.getPairs()) {
                if ("exclude".equals(pair.getNameAsString())) {
                    if (pair.getValue() instanceof BooleanLiteralExpr ble) {
                        return ble.getValue();
                    }
                }
            }
        }
        return false;
    }

    /**
     * 从注解中提取指定参数的 String 值。
     */
    private String extractAnnotationParam(AnnotationExpr ann, String paramName) {
        if (ann instanceof NormalAnnotationExpr na) {
            for (MemberValuePair pair : na.getPairs()) {
                if (pair.getNameAsString().equals(paramName)) {
                    return AnnotationUtils.stripQuotes(pair.getValue().toString());
                }
            }
        }
        if (ann instanceof SingleMemberAnnotationExpr sma) {
            // @WebService("MyService") → value 参数
            if ("value".equals(paramName)) {
                return AnnotationUtils.stripQuotes(sma.getMemberValue().toString());
            }
        }
        return null;
    }
}
