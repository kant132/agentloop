package com.javaparsextract;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.AnnotationExpr;
import com.javaparsextract.framework.*;
import com.javaparsextract.spi.AnnotationUtils;
import com.javaparsextract.spi.FrameworkHandler;
import com.javaparsextract.spi.JsonUtils;
import com.javaparsextract.spi.RouteResult;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * 路由注解解析器 — 编排层。
 *
 * 加载 FrameworkHandler 列表，遍历文件中的类和方法，
 * 委托匹配的 handler 提取路由。公共 API 不变。
 */
public class RouteExtractor {

    private final List<FrameworkHandler> handlers;
    private final ServletFramework servletFramework;
    private final SpringWebSocketFramework wsFramework;
    private final SpringRSocketFramework rsocketFramework;

    public RouteExtractor() {
        this.servletFramework = new ServletFramework();
        this.wsFramework = new SpringWebSocketFramework();
        this.rsocketFramework = new SpringRSocketFramework();
        this.handlers = List.of(
                new SpringMvcFramework(), new JaxRsFramework(),
                new SpringGraphQLFramework(), new SpringActuatorFramework(),
                servletFramework, wsFramework, rsocketFramework);
    }

    public RouteExtractor(List<FrameworkHandler> handlers) {
        this.handlers = handlers;
        this.servletFramework = findHandler(ServletFramework.class);
        this.wsFramework = findHandler(SpringWebSocketFramework.class);
        this.rsocketFramework = findHandler(SpringRSocketFramework.class);
    }

    /** 向后兼容：解析 Java 文件返回 List<Map>。 */
    public static List<Map<String, Object>> extractFromFile(Path javaFile) throws IOException {
        return new RouteExtractor().doExtractFromFile(javaFile);
    }

    /** 向后兼容：解析 Java 文件返回 JSON 字符串。 */
    public static String extract(Path javaFile) throws IOException {
        return JsonUtils.toJson(extractFromFile(javaFile));
    }

    public List<Map<String, Object>> doExtractFromFile(Path javaFile) throws IOException {
        CompilationUnit cu = StaticJavaParser.parse(Files.readString(javaFile));
        List<Map<String, Object>> routes = new ArrayList<>();
        String pkg = cu.getPackageDeclaration().map(p -> p.getNameAsString()).orElse("");
        String filePath = javaFile.toString().replace('\\', '/');

        for (ClassOrInterfaceDeclaration cls : cu.findAll(ClassOrInterfaceDeclaration.class)) {
            if (cls.isInterface()) continue;
            String fqn = pkg.isEmpty() ? cls.getNameAsString() : pkg + "." + cls.getNameAsString();

            if (servletFramework != null && servletFramework.isController(cls)) {
                servletFramework.extractClassLevelRoutes(cls, fqn, filePath)
                        .forEach(r -> routes.add(r.withFile(filePath).toMap()));
                continue;
            }

            FrameworkHandler h = findMatchingHandler(cls, cu);
            String basePath = h != null ? h.extractClassBasePath(cls) : null;
            if (basePath == null) continue;

            for (MethodDeclaration m : cls.getMethods()) {
                for (AnnotationExpr ann : m.getAnnotations()) {
                    String name = AnnotationUtils.getShortAnnotationName(ann);
                    Optional<RouteResult> result = h.handleMethodAnnotation(name, ann, m, basePath, fqn);
                    if (result.isPresent()) {
                        routes.add(result.get().withFile(filePath).toMap());
                        break;
                    }
                }
            }
        }
        return routes;
    }

    private FrameworkHandler findMatchingHandler(ClassOrInterfaceDeclaration cls, CompilationUnit cu) {
        for (FrameworkHandler h : handlers) {
            if (h.isController(cls)) {
                if (h == wsFramework && rsocketFramework != null
                        && SpringRSocketFramework.hasRSocketImports(cu)) return rsocketFramework;
                if (h == rsocketFramework && wsFramework != null
                        && !SpringRSocketFramework.hasRSocketImports(cu)
                        && SpringWebSocketFramework.hasWebSocketImports(cu)) return wsFramework;
                return h;
            }
        }
        return null;
    }

    @SuppressWarnings("unchecked")
    private <T extends FrameworkHandler> T findHandler(Class<T> clazz) {
        for (FrameworkHandler h : handlers) if (clazz.isInstance(h)) return (T) h;
        return null;
    }
}
