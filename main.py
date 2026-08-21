"""Load one or more Python codebases' dependency graphs into Neo4j (for RAG / code onboarding).

Run once per repository - each run is independent and repo-aware:

    python main.py /path/to/repoA
    python main.py /path/to/repoB
    python main.py /path/to/repoC

The argument is a local directory path. The repo name is derived from the
folder's basename (e.g. /code/acme-common -> repo "acme-common").

What each run does:
  - creates/updates a :Repository node for the scanned folder
  - tags every :InternalModule with its owning repo (composite key repo + name,
    so same-named modules in different repos coexist without overwriting)
  - links :Repository -[:CONTAINS]-> :InternalModule
  - resolves imports against the *whole* graph: an import that matches a module
    in another already-loaded repo becomes a cross-repo internal edge instead of
    an :ExternalPackage node
  - upgrades stale :ExternalPackage edges when a later run reveals the target is
    actually an internal module from another repo

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
import subprocess
import sys
from pathlib import Path

from neo4j import GraphDatabase

SKIP_DIRS = {"__pycache__", "venv", ".venv", "node_modules", "build", "dist", ".git"}


def _skip(rel: Path) -> bool:
    """Skip hidden dirs and common non-source dirs."""
    return any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts)


def _git_metadata(root: Path) -> dict:
    """Best-effort git remote + commit from the local .git folder (empty if not a git repo)."""
    meta = {"git_remote": "", "git_commit": ""}
    try:
        remote = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if remote.returncode == 0:
            meta["git_remote"] = remote.stdout.strip()
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if commit.returncode == 0:
            meta["git_commit"] = commit.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return meta


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
        if parts and parts[0] == "src":            # src/ layout: strip leading src/
            parts = parts[1:]
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


def load_graph(repo: str, repo_path: str, modules: dict, edges: list, uri: str, auth: tuple) -> int:
    """Bulk-load nodes + relationships for one repo. Idempotent: safe to re-run in CI."""
    internal = set(modules)

    with GraphDatabase.driver(uri, auth=auth) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            # --- constraints -------------------------------------------------
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (r:Repository) REQUIRE r.name IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (m:InternalModule) REQUIRE (m.repo, m.name) IS UNIQUE")
            session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (p:ExternalPackage) REQUIRE p.name IS UNIQUE")

            # --- global registry of internal modules across all repos --------
            existing = {r["name"] for r in session.run(
                "MATCH (m:InternalModule) RETURN DISTINCT m.name AS name")}

            # --- classify edges ----------------------------------------------
            rels_within, rels_cross, rels_ext, external = [], [], [], set()
            for src, tgt in edges:
                if tgt in internal:                          # same-repo internal
                    rels_within.append({"s": src, "t": tgt})
                elif tgt in existing:                        # cross-repo internal
                    rels_cross.append({"s": src, "t": tgt})
                else:                                        # true external
                    external.add(tgt)
                    rels_ext.append({"s": src, "t": tgt})

            # --- Repository node ---------------------------------------------
            meta = _git_metadata(Path(repo_path))
            session.run(
                "MERGE (r:Repository {name: $name}) "
                "SET r.path = $path, r.git_remote = $remote, r.git_commit = $commit, "
                "    r.loaded_at = datetime()",
                name=repo, path=str(Path(repo_path).resolve()),
                remote=meta["git_remote"], commit=meta["git_commit"],
            )

            # --- InternalModule nodes + CONTAINS ------------------------------
            if modules:
                session.run(
                    "UNWIND $rows AS r "
                    "MERGE (m:InternalModule {repo: r.repo, name: r.name}) "
                    "SET m.path = r.path, m.doc = r.doc",
                    rows=[{"repo": repo, "name": n, "path": p, "doc": d}
                          for n, (p, d) in modules.items()],
                )
                session.run(
                    "UNWIND $rows AS r "
                    "MATCH (repo:Repository {name: r.repo}) "
                    "MATCH (m:InternalModule {repo: r.repo, name: r.name}) "
                    "MERGE (repo)-[:CONTAINS]->(m)",
                    rows=[{"repo": repo, "name": n} for n in modules],
                )

            # --- ExternalPackage nodes ---------------------------------------
            if external:
                session.run(
                    "UNWIND $rows AS r MERGE (e:ExternalPackage {name: r.name})",
                    rows=[{"name": n} for n in external],
                )

            # --- within-repo edges -------------------------------------------
            if rels_within:
                session.run(
                    "UNWIND $rows AS r "
                    "MATCH (a:InternalModule {repo: $repo, name: r.s}) "
                    "MATCH (b:InternalModule {repo: $repo, name: r.t}) "
                    "MERGE (a)-[:DEPENDS_ON]->(b)",
                    rows=rels_within, repo=repo,
                )

            # --- cross-repo edges --------------------------------------------
            if rels_cross:
                session.run(
                    "UNWIND $rows AS r "
                    "MATCH (a:InternalModule {repo: $repo, name: r.s}) "
                    "MATCH (b:InternalModule {name: r.t}) WHERE b.repo <> $repo "
                    "MERGE (a)-[:DEPENDS_ON]->(b)",
                    rows=rels_cross, repo=repo,
                )

            # --- external edges ----------------------------------------------
            if rels_ext:
                session.run(
                    "UNWIND $rows AS r "
                    "MATCH (a:InternalModule {repo: $repo, name: r.s}) "
                    "MATCH (b:ExternalPackage {name: r.t}) "
                    "MERGE (a)-[:DEPENDS_ON]->(b)",
                    rows=rels_ext, repo=repo,
                )

            # --- upgrade stale ExternalPackage edges that now resolve to this repo ---
            # When this repo is loaded after others, earlier repos may have
            # recorded imports of this repo's modules as ExternalPackage.
            if modules:
                session.run(
                    "UNWIND $names AS n "
                    "MATCH (p:ExternalPackage {name: n}) "
                    "MATCH (a:InternalModule)-[r:DEPENDS_ON]->(p) "
                    "MATCH (b:InternalModule {name: n, repo: $repo}) "
                    "DELETE r "
                    "MERGE (a)-[:DEPENDS_ON]->(b)",
                    names=list(modules), repo=repo,
                )
                # drop now-orphaned ExternalPackage nodes
                session.run(
                    "MATCH (p:ExternalPackage) WHERE NOT (p)<-[:DEPENDS_ON]-() DETACH DELETE p"
                )

    return len(edges)


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    repo = Path(root).resolve().name
    modules, edges = extract_graph(root)
    uri = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
    auth = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))
    total = load_graph(repo, root, modules, edges, uri, auth)
    print(f"Loaded {len(modules)} modules and {total} dependency edges for repo '{repo}' into Neo4j")