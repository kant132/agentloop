package com.javaparsextract;

import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.javaparsextract.framework.ServletFramework;
import com.javaparsextract.framework.SpringWebSocketFramework;
import com.javaparsextract.framework.SpringRSocketFramework;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Servlet/WebSocket/RSocket framework tests。
 */
class ServletWebSocketRSocketTest {

    @Test
    void servletDetectsWebServlet() throws Exception {
        ServletFramework handler = new ServletFramework();
        Path testFile = Path.of("src/test/resources/testdata/ServletExample.java");
        CompilationUnit cu = StaticJavaParser.parse(Files.readString(testFile));
        ClassOrInterfaceDeclaration cls = cu.findAll(ClassOrInterfaceDeclaration.class).getFirst();
        assertThat(handler.isController(cls)).isTrue();
        assertThat(handler.extractClassBasePath(cls)).contains("/api/*");
    }

    @Test
    void websocketHandlesMessageMapping() throws Exception {
        SpringWebSocketFramework handler = new SpringWebSocketFramework();
        Path testFile = Path.of("src/test/resources/testdata/WebSocketController.java");
        CompilationUnit cu = StaticJavaParser.parse(Files.readString(testFile));
        ClassOrInterfaceDeclaration cls = cu.findAll(ClassOrInterfaceDeclaration.class).getFirst();
        assertThat(handler.isController(cls)).isTrue();
        assertThat(handler.extractClassBasePath(cls)).isEmpty();
        assertThat(handler.supportedAnnotations()).contains("MessageMapping", "SubscribeMapping");
    }

    @Test
    void rSocketDistinguishedByImports() throws Exception {
        Path wsFile = Path.of("src/test/resources/testdata/WebSocketController.java");
        Path rsFile = Path.of("src/test/resources/testdata/RSocketController.java");
        CompilationUnit wsCu = StaticJavaParser.parse(Files.readString(wsFile));
        CompilationUnit rsCu = StaticJavaParser.parse(Files.readString(rsFile));
        assertThat(SpringRSocketFramework.hasRSocketImports(rsCu)).isTrue();
        assertThat(SpringRSocketFramework.hasRSocketImports(wsCu)).isFalse();
    }

    @Test
    void rSocketHandlesMessageMapping() throws Exception {
        SpringRSocketFramework handler = new SpringRSocketFramework();
        Path testFile = Path.of("src/test/resources/testdata/RSocketController.java");
        CompilationUnit cu = StaticJavaParser.parse(Files.readString(testFile));
        ClassOrInterfaceDeclaration cls = cu.findAll(ClassOrInterfaceDeclaration.class).getFirst();
        assertThat(handler.isController(cls)).isTrue();
        assertThat(handler.frameworkName()).isEqualTo("spring-rsocket");
    }
}
