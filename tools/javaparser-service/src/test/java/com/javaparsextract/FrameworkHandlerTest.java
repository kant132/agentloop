package com.javaparsextract;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.javaparsextract.framework.*;
import com.javaparsextract.spi.AnnotationUtils;
import com.javaparsextract.spi.RouteResult;
import org.junit.jupiter.api.*;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;

import static org.assertj.core.api.Assertions.assertThat;

/** All framework handler tests + integration test in one class. */
class FrameworkHandlerTest {

    // ==================== Spring MVC ====================

    @Test
    void springMvc_frameworkName_and_annotations() {
        var h = new SpringMvcFramework();
        assertThat(h.frameworkName()).isEqualTo("spring-mvc");
        assertThat(h.supportedAnnotations()).hasSize(6);
        assertThat(h.supportedAnnotations()).contains("GetMapping");
    }

    @Test
    void springMvc_isController_and_basePath() throws IOException {
        var h = new SpringMvcFramework();
        var cu = parse("src/test/resources/testdata/SpringMvcController.java");
        var cls = firstClass(cu);
        assertThat(h.isController(cls)).isTrue();
        assertThat(h.extractClassBasePath(cls)).isEqualTo("/api");
    }

    @Test
    void springMvc_methodAnnotations() throws IOException {
        var h = new SpringMvcFramework();
        var cu = parse("src/test/resources/testdata/SpringMvcController.java");
        var cls = firstClass(cu);
        String basePath = h.extractClassBasePath(cls);
        String fqn = "testdata.SpringMvcController";

        List<RouteResult> results = new ArrayList<>();
        for (var method : cls.getMethods()) {
            for (var ann : method.getAnnotations()) {
                String name = AnnotationUtils.getShortAnnotationName(ann);
                h.handleMethodAnnotation(name, ann, method, basePath, fqn)
                        .ifPresent(results::add);
            }
        }
        assertThat(results).isNotEmpty();
        assertThat(results.stream().anyMatch(r -> "GET".equals(r.httpMethods().getFirst()))).isTrue();
        assertThat(results.stream().anyMatch(r -> "POST".equals(r.httpMethods().getFirst()))).isTrue();
    }

    // ==================== JAX-RS ====================

    @Test
    void jaxRs_frameworkName_and_controller() throws IOException {
        var h = new JaxRsFramework();
        assertThat(h.frameworkName()).isEqualTo("jaxrs");
        var cu = parse("src/test/resources/testdata/JaxRsResource.java");
        var cls = firstClass(cu);
        assertThat(h.isController(cls)).isTrue();
        assertThat(h.extractClassBasePath(cls)).isEqualTo("/api");
    }

    // ==================== GraphQL ====================

    @Test
    void graphql_frameworkName_and_annotations() throws IOException {
        var h = new SpringGraphQLFramework();
        assertThat(h.frameworkName()).isEqualTo("spring-graphql");
        assertThat(h.supportedAnnotations()).contains("QueryMapping", "MutationMapping", "SubscriptionMapping");
        var cu = parse("src/test/resources/testdata/GraphQLController.java");
        var cls = firstClass(cu);
        assertThat(h.isController(cls)).isTrue();
    }

    // ==================== Actuator ====================

    @Test
    void actuator_frameworkName_and_endpointId() throws IOException {
        var h = new SpringActuatorFramework();
        assertThat(h.frameworkName()).isEqualTo("spring-actuator");
        var cu = parse("src/test/resources/testdata/ActuatorEndpoint.java");
        var cls = firstClass(cu);
        assertThat(h.isController(cls)).isTrue();
        assertThat(h.extractClassBasePath(cls)).isEqualTo("/actuator/custom");
    }

    // ==================== Servlet ====================

    @Test
    void servlet_detectsWebServlet() throws IOException {
        var h = new ServletFramework();
        assertThat(h.frameworkName()).isEqualTo("servlet");
        var cu = parse("src/test/resources/testdata/ServletExample.java");
        var cls = firstClass(cu);
        assertThat(h.isController(cls)).isTrue();
        assertThat(h.extractClassBasePath(cls)).contains("/api");
    }

    // ==================== WebSocket ====================

    @Test
    void websocket_detectsController() throws IOException {
        var h = new SpringWebSocketFramework();
        assertThat(h.frameworkName()).isEqualTo("spring-websocket");
        assertThat(h.supportedAnnotations()).contains("MessageMapping", "SubscribeMapping");
        var cu = parse("src/test/resources/testdata/WebSocketController.java");
        var cls = firstClass(cu);
        assertThat(h.isController(cls)).isTrue();
    }

    // ==================== RSocket ====================

    @Test
    void rSocket_frameworkName_and_importDetection() throws IOException {
        var h = new SpringRSocketFramework();
        assertThat(h.frameworkName()).isEqualTo("spring-rsocket");
        var wsCu = parse("src/test/resources/testdata/WebSocketController.java");
        var rsCu = parse("src/test/resources/testdata/RSocketController.java");
        assertThat(SpringRSocketFramework.hasRSocketImports(rsCu)).isTrue();
        assertThat(SpringRSocketFramework.hasRSocketImports(wsCu)).isFalse();
    }

    // ==================== Integration ====================

    @Test
    void routeExtractor_springMvc_integration() throws IOException {
        List<Map<String, Object>> routes = RouteExtractor.extractFromFile(
                Path.of("test-data/RouteTestController.java"));
        assertThat(routes).hasSize(4);
        assertThat(routes.get(0).get("annotation")).isEqualTo("GetMapping");
        @SuppressWarnings("unchecked")
        List<String> methods0 = (List<String>) routes.get(0).get("http_methods");
        assertThat(methods0).containsExactly("GET");
    }

    @Test
    void routeExtractor_returnsJson() throws IOException {
        String json = RouteExtractor.extract(Path.of("test-data/RouteTestController.java"));
        assertThat(json).startsWith("[");
        assertThat(json).contains("GetMapping");
    }

    @Test
    void routeExtractor_allTestFiles() throws IOException {
        for (String file : List.of(
                "src/test/resources/testdata/SpringMvcController.java",
                "src/test/resources/testdata/JaxRsResource.java",
                "src/test/resources/testdata/GraphQLController.java",
                "src/test/resources/testdata/ActuatorEndpoint.java",
                "src/test/resources/testdata/ServletExample.java",
                "src/test/resources/testdata/WebSocketController.java",
                "src/test/resources/testdata/RSocketController.java")) {
            List<Map<String, Object>> routes = RouteExtractor.extractFromFile(Path.of(file));
            assertThat(routes).isNotEmpty();
        }
    }

    // ==================== Helpers ====================

    private CompilationUnit parse(String path) throws IOException {
        return StaticJavaParser.parse(Files.readString(Path.of(path)));
    }

    private ClassOrInterfaceDeclaration firstClass(CompilationUnit cu) {
        return cu.findAll(ClassOrInterfaceDeclaration.class).stream()
                .filter(c -> !c.isInterface()).findFirst().orElseThrow();
    }
}
