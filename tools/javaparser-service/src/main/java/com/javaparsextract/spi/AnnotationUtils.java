package com.javaparsextract.spi;

import com.github.javaparser.ast.expr.AnnotationExpr;
import com.github.javaparser.ast.expr.ArrayInitializerExpr;
import com.github.javaparser.ast.expr.Expression;
import com.github.javaparser.ast.expr.FieldAccessExpr;
import com.github.javaparser.ast.expr.MemberValuePair;
import com.github.javaparser.ast.expr.NameExpr;
import com.github.javaparser.ast.expr.NormalAnnotationExpr;
import com.github.javaparser.ast.expr.SingleMemberAnnotationExpr;

import java.util.ArrayList;
import java.util.List;

/**
 * 注解解析共享工具方法。
 *
 * 从 RouteExtractor 提取的 7 个纯函数 + 3 个包前缀常量。
 * 所有方法 public static，供 FrameworkHandler 实现类复用。
 */
public final class AnnotationUtils {

    public static final String SPRING_PREFIX = "org.springframework.web.bind.annotation.";
    public static final String JAXRS_PREFIX = "javax.ws.rs.";
    public static final String JAKARTA_JAXRS_PREFIX = "jakarta.ws.rs.";

    public static final List<String> ALL_METHODS = List.of(
            "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS");

    private AnnotationUtils() {}

    /**
     * 从注解提取 path/value 参数。
     * - SingleMemberAnnotationExpr: @GetMapping("/users") → "/users"
     * - NormalAnnotationExpr: 找 value 或 path 键
     * - MarkerAnnotationExpr: @GetMapping → ""
     */
    public static String extractPathValue(AnnotationExpr ann) {
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
     * 解析 method= 参数值。
     * 支持：RequestMethod.GET / GET / {RequestMethod.GET, RequestMethod.POST}
     */
    public static List<String> parseMethodParam(Expression value) {
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

    /** 获取注解短名（去掉 Spring/JAX-RS 包前缀）。 */
    public static String getShortAnnotationName(AnnotationExpr ann) {
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

    /** 路径拼接：normalize(base) + "/" + normalize(method)。空路径 → 根路径 "/"。 */
    public static String joinPath(String base, String method) {
        String b = stripSlash(base);
        String m = stripSlash(method);
        if (b.isEmpty() && m.isEmpty()) return "/";
        if (b.isEmpty()) return "/" + m;
        if (m.isEmpty()) return "/" + b;
        return "/" + b + "/" + m;
    }

    public static String stripSlash(String s) {
        if (s == null || s.isEmpty()) return "";
        return s.replaceAll("^/+|/+$", "");
    }

    public static String stripQuotes(String s) {
        if (s == null) return "";
        s = s.trim();
        if (s.startsWith("\"") && s.endsWith("\"") && s.length() >= 2) {
            return s.substring(1, s.length() - 1);
        }
        return s;
    }

    public static String normalizeMethod(String raw) {
        return raw.toUpperCase();
    }
}
