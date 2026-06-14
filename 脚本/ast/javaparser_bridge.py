"""javaparser_bridge.py — Python bridge to JavaParser via JPype.

Uses JavaParser 3.28.2 to parse Java source and extract method calls
with symbol resolution (declaring class) when available.

Key API:
    with JavaParserBridge(jar_dir="tools/javaparser", source_roots=[...]) as bridge:
        calls = bridge.extract_method_calls("path/to/Foo.java")
        # Each call: {line, selector, args_text, declaring_class, full_invocation, resolved}
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, List, Optional, Union

try:
    import jpype
    import jpype.imports  # noqa: F401 - needed for class imports
except ImportError:
    raise ImportError("JPype1 is required. Install: pip install JPype1")


# --- Default jar directory ---
# Resolved relative to this file: 脚本/ast/ -> ../../tools/javaparser
_HERE = Path(__file__).resolve().parent
DEFAULT_JAR_DIR = _HERE.parent.parent / "tools" / "javaparser"

# Symbol-solver fallback sentinel
UNRESOLVED = "**UNRESOLVED**"


def _safe_java_exception_text(e: Exception) -> str:
    """Extract a short readable message from a Java exception wrapped by JPype."""
    try:
        msg = str(e)
    except Exception:
        msg = repr(e)
    msg = re.sub(r"(java\.lang\.[A-Za-z]+Error: )+", "", msg).strip()
    return msg or type(e).__name__


class JavaParserBridge:
    """Python bridge to JavaParser with optional symbol resolution.

    Parameters:
        jar_dir: Directory containing javaparser-core-*.jar (and optionally symbol-solver jar)
        source_roots: Project source roots for JavaParserTypeSolver
        additional_jars: Extra jar paths to add to classpath
        maven_classpath_file: Path to mvn dependency:build-classpath output
        jvmpath: Optional explicit JVM shared library path
        convertStrings: Forward to jpype.startJVM()
    """

    def __init__(
        self,
        jar_dir: Union[str, Path] = DEFAULT_JAR_DIR,
        source_roots: Optional[List[Union[str, Path]]] = None,
        additional_jars: Optional[List[Union[str, Path]]] = None,
        maven_classpath_file: Optional[Union[str, Path]] = None,
        jvmpath: Optional[str] = None,
        convertStrings: bool = False,
    ):
        self.jar_dir = Path(jar_dir)
        self.source_roots = [Path(r) for r in (source_roots or [])]
        self.additional_jars = [Path(p) for p in (additional_jars or [])]
        self.maven_classpath_file = (
            Path(maven_classpath_file) if maven_classpath_file else None
        )
        self._jvmpath = jvmpath
        self._convertStrings = convertStrings

        if not self.jar_dir.exists():
            raise FileNotFoundError(f"Jar directory not found: {self.jar_dir}")

        # Collect jars (deduped, ordered)
        discovered = sorted(self.jar_dir.glob("*.jar"))
        all_jars = list(discovered) + [p for p in self.additional_jars if p.exists()]
        seen = set()
        self._classpath: List[str] = []
        for p in all_jars:
            ap = str(Path(p).resolve())
            if ap not in seen:
                seen.add(ap)
                self._classpath.append(ap)

        # Maven classpath integration
        if self.maven_classpath_file and self.maven_classpath_file.exists():
            sep = ";" if os.name == "nt" else ":"
            with open(self.maven_classpath_file, encoding="utf-8") as f:
                for line in f:
                    for entry in line.strip().split(sep):
                        entry = entry.strip()
                        if entry and Path(entry).exists() and entry not in seen:
                            seen.add(entry)
                            self._classpath.append(entry)

        # Lazy JVM init state
        self._jvm_started = False
        self._symbol_solver_ready = False
        self._type_solver = None
        self._JStaticJavaParser = None
        self._JMethodCallExpr = None
        self._JStringReader = None

    @property
    def classpath(self) -> List[str]:
        """Read-only list of jar paths on classpath."""
        return list(self._classpath)

    # --- JVM lifecycle ---

    def _ensure_jvm(self) -> None:
        """Start JVM lazily + wire symbol solver if classes loaded."""
        if self._jvm_started:
            return
        if not jpype.isJVMStarted():
            kwargs = {"classpath": self._classpath, "convertStrings": self._convertStrings}
            if self._jvmpath:
                kwargs["jvmpath"] = self._jvmpath
            jpype.startJVM(**kwargs)
        self._jvm_started = True

        # Cache class references
        self._JStaticJavaParser = jpype.JClass("com.github.javaparser.StaticJavaParser")
        self._JMethodCallExpr = jpype.JClass("com.github.javaparser.ast.expr.MethodCallExpr")
        self._JStringReader = jpype.JClass("java.io.StringReader")

        # Best-effort symbol solver setup
        self._setup_symbol_solver()

    def _setup_symbol_solver(self) -> None:
        """Wire symbol solver if its classes are on classpath (silently fallback)."""
        try:
            JClass = jpype.JClass
            CombinedTypeSolver = JClass("com.github.javaparser.symbolsolver.resolution.typesolvers.CombinedTypeSolver")
            JavaParserTypeSolver = JClass("com.github.javaparser.symbolsolver.resolution.typesolvers.JavaParserTypeSolver")
            ReflectionTypeSolver = JClass("com.github.javaparser.symbolsolver.resolution.typesolvers.ReflectionTypeSolver")
            JarTypeSolver = JClass("com.github.javaparser.symbolsolver.resolution.typesolvers.JarTypeSolver")
            JavaSymbolSolver = JClass("com.github.javaparser.symbolsolver.JavaSymbolSolver")
        except Exception as e:
            detail = _safe_java_exception_text(e)
            print(f"[javaparser_bridge] symbol-solver not loadable ({detail}) — symbol resolution disabled")
            return

        try:
            type_solver = CombinedTypeSolver()

            # Jar solvers (skip javaparser itself)
            loaded_jars = 0
            for jar in self._classpath:
                p = Path(jar)
                if "javaparser" in p.name.lower():
                    continue
                try:
                    type_solver.add(JarTypeSolver(jarFile=str(p)))
                    loaded_jars += 1
                except Exception:
                    pass

            # Source roots
            for src in self.source_roots:
                if src.exists() and src.is_dir():
                    try:
                        type_solver.add(JavaParserTypeSolver(str(src)))
                    except Exception:
                        pass

            # JDK reflection
            try:
                type_solver.add(ReflectionTypeSolver())
            except Exception:
                pass

            symbol_solver = JavaSymbolSolver(type_solver)
            self._JStaticJavaParser.getParserConfiguration().setSymbolResolver(symbol_solver)
            self._type_solver = type_solver
            self._symbol_solver_ready = True
            print(f"[javaparser_bridge] symbol solver initialized: {loaded_jars} jar(s) loaded")
        except Exception as e:
            print(f"[javaparser_bridge] symbol solver setup failed: {_safe_java_exception_text(e)}")

    def has_symbol_solver(self) -> bool:
        """True if symbol resolution is active."""
        return self._symbol_solver_ready

    def shutdown(self) -> None:
        """Shut down JVM (cannot restart in same process)."""
        if self._jvm_started and jpype.isJVMStarted():
            try:
                jpype.shutdownJVM()
            finally:
                self._jvm_started = False
                self._symbol_solver_ready = False
                self._type_solver = None

    def __enter__(self) -> "JavaParserBridge":
        return self

    def __exit__(self, *args) -> None:
        self.shutdown()

    def __del__(self) -> None:
        try:
            self.shutdown()
        except Exception:
            pass

    # --- Parsing primitives ---

    def parse_string(self, code: str):
        """Parse Java source string → CompilationUnit."""
        self._ensure_jvm()
        return self._JStaticJavaParser.parse(self._JStringReader(str(code)))

    def parse_file(self, file_path: Union[str, Path]):
        """Parse Java source file → CompilationUnit."""
        self._ensure_jvm()
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        with open(file_path, encoding="utf-8") as f:
            source = f.read()
        return self._JStaticJavaParser.parse(self._JStringReader(source))

    # --- High-level: extract method calls ---

    def extract_method_calls(
        self,
        file_path: Union[str, Path],
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> List[dict]:
        """Extract all method calls from a Java file.

        Returns list of dicts with keys:
            line             - 1-based line number
            selector         - method name
            args_text        - formatted args (a, b)
            call_expression  - raw call text (truncated)
            declaring_class  - FQN, or "**UNRESOLVED**"
            full_invocation  - declaring_class.selector(args) or UNRESOLVED
            resolved         - True if symbol solver worked
        """
        self._ensure_jvm()

        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        with open(file_path, encoding="utf-8") as f:
            source = f.read()

        cu = self._JStaticJavaParser.parse(self._JStringReader(source))
        calls = cu.findAll(self._JMethodCallExpr)

        results: List[dict] = []
        for i in range(calls.size()):
            call = calls.get(i)

            # Line number (1-based)
            try:
                line = call.getBegin().map(lambda p: p.line).orElse(-1)
            except Exception:
                line = -1
            if line is None or line < 0:
                continue

            if start_line is not None and line < start_line:
                continue
            if end_line is not None and line > end_line:
                continue

            # Selector (method name)
            try:
                selector = str(call.getNameAsString())
            except Exception:
                selector = "?"

            # Arguments text
            try:
                args_raw = str(call.getArguments())
                if args_raw.startswith("[") and args_raw.endswith("]"):
                    args_raw = args_raw[1:-1]
                args_text = f"({args_raw})"
            except Exception:
                args_text = "(...)"

            # Raw call expression (truncated)
            try:
                call_expression = str(call.toString())
            except Exception:
                call_expression = f"{selector}{args_text}"
            call_expression = re.sub(r"//[^\n]*", "", call_expression)
            call_expression = re.sub(r"/\*.*?\*/", "", call_expression, flags=re.DOTALL)
            call_expression = re.sub(r"\s+", " ", call_expression).strip()
            if len(call_expression) > 300:
                call_expression = call_expression[:297] + "..."

            # Resolve declaring class
            declaring_class = UNRESOLVED
            resolved = False
            if self._symbol_solver_ready:
                try:
                    decl = call.resolve()
                    declaring_class = str(decl.declaringType().getQualifiedName())
                    resolved = True
                except Exception:
                    pass

            # Build full_invocation
            if resolved and declaring_class != UNRESOLVED:
                full_invocation = f"{declaring_class}.{selector}{args_text}"
            else:
                full_invocation = f"{UNRESOLVED}:{call_expression}"

            results.append({
                "line": int(line),
                "selector": selector,
                "args_text": args_text,
                "call_expression": call_expression,
                "declaring_class": declaring_class,
                "full_invocation": full_invocation,
                "resolved": bool(resolved),
            })

        return results

    def find_all_type_decls(self, file_path: Union[str, Path]):
        """Return all TypeDeclaration nodes (raw Java objects)."""
        self._ensure_jvm()
        cu = self.parse_file(file_path)
        JTypeDeclaration = jpype.JClass("com.github.javaparser.ast.body.TypeDeclaration")
        return cu.findAll(JTypeDeclaration)


@contextmanager
def bridge(*args, **kwargs) -> Iterator[JavaParserBridge]:
    """Context manager sugar: with bridge(...) as b: ... — auto-shutdown."""
    b = JavaParserBridge(*args, **kwargs)
    try:
        yield b
    finally:
        b.shutdown()


# --- CLI smoke test ---
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="JavaParser bridge smoke test")
    parser.add_argument("--jar-dir", default=str(DEFAULT_JAR_DIR))
    parser.add_argument("--file", help="Java file to parse")
    args = parser.parse_args()

    print(f"[smoke] jar_dir: {args.jar_dir}")
    print(f"[smoke] jars found: {sorted(p.name for p in Path(args.jar_dir).glob('*.jar'))}")

    with JavaParserBridge(jar_dir=args.jar_dir) as b:
        print(f"[smoke] symbol solver: {b.has_symbol_solver()}")
        if args.file:
            calls = b.extract_method_calls(args.file)
            print(f"[smoke] {len(calls)} method calls found")
            for c in calls[:5]:
                print(f"  L{c['line']}: {c['full_invocation']}")
