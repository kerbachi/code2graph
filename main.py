"""Load a Python codebase's dependency graph into Neo4j (for RAG / code onboarding).

Usage:
    python main.py [path_to_project]        # defaults to current directory

Env vars (all optional):
    NEO4J_URI        default neo4j://localhost:7687
    NEO4J_USER       default neo4j
    NEO4J_PASSWORD   default password

Note: only *absolute* imports are captured (`import x` / `from x import y`).
Relative imports (`from . import x`) are intentionally skipped to keep this
small - extend extract_graph() if your codebase relies on them.
"""
import ast
import os
import sys
from pathlib import Path

from neo4j import GraphDatabase

SKIP_DIRS = {"__pycache__", "venv", ".venv", "node_modules", "build", "dist", ".git"}


def _skip(rel: Path) -> bool:
    """Skip hidden dirs and common non-source dirs."""
    return any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts)


def extract_graph(project_root: str):
    """Scan the project and return (modules, edges).

    modules: {module_name: (file_path, docstring)}   module_name uses '/' form
    edges:   [(source, target)]  unique import relationships
    """
    root = Path(project_root)
    modules: dict[str, tuple[str, str]] = {}
    edge_set: set[tuple[str, str]] = set()

    for py in root.rglob("*.py"):
        if _skip(py.relative_to(root)):
            continue
        parts = list(py.with_suffix("").relative_to(root).parts)
        if parts and parts[-1] == "__init__":      # a/b/__init__.py -> a/b
            parts = parts[:-1]
        name = "/".join(parts)
        if not name:
            continue

        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"), filename=str(py))
        except (SyntaxError, ValueError):
            continue                                 # unparseable file: skip, keep going
        modules[name] = (str(py), ast.get_docstring(tree) or "")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    edge_set.add((name, alias.name.replace(".", "/")))
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                edge_set.add((name, node.module.replace(".", "/")))

    return modules, list(edge_set)


def load_graph(modules: dict, edges: list, uri: str, auth: tuple) -> int:
    """Bulk-load nodes + relationships. Idempotent: safe to re-run in CI."""
    internal = set(modules)
    rels_int, rels_ext, external = [], [], set()
    for src, tgt in edges:
        if tgt in internal:                          # internal -> internal edge
            rels_int.append({"s": src, "t": tgt})
        else:                                        # internal -> external edge
            external.add(tgt)
            rels_ext.append({"s": src, "t": tgt})

    with GraphDatabase.driver(uri, auth=auth) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (m:InternalModule) REQUIRE m.name IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (m:ExternalPackage) REQUIRE m.name IS UNIQUE")

            if modules:
                session.run(
                    "UNWIND $rows AS r MERGE (m:InternalModule {name: r.name}) "
                    "SET m.path = r.path, m.doc = r.doc",
                    rows=[{"name": n, "path": p, "doc": d} for n, (p, d) in modules.items()],
                )
            if external:
                session.run(
                    "UNWIND $rows AS r MERGE (e:ExternalPackage {name: r.name})",
                    rows=[{"name": n} for n in external],
                )
            if rels_int:
                session.run(
                    "UNWIND $rows AS r MERGE (a:InternalModule {name: r.s}) "
                    "MERGE (b:InternalModule {name: r.t}) MERGE (a)-[:DEPENDS_ON]->(b)",
                    rows=rels_int,
                )
            if rels_ext:
                session.run(
                    "UNWIND $rows AS r MERGE (a:InternalModule {name: r.s}) "
                    "MERGE (b:ExternalPackage {name: r.t}) MERGE (a)-[:DEPENDS_ON]->(b)",
                    rows=rels_ext,
                )
    return len(edges)


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    modules, edges = extract_graph(root)
    uri = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
    auth = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))
    total = load_graph(modules, edges, uri, auth)
    print(f"Loaded {len(modules)} modules and {total} dependency edges into Neo4j")