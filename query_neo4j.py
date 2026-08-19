#!/usr/bin/env python3
"""Explore the Neo4j codebase dependency graph (repo-aware).

Usage:
    python query_neo4j.py                 # graph stats + sample
    python query_neo4j.py <module_name>   # explore one module (e.g. utils/spark/io)
    python query_neo4j.py --repo <name>   # stats for one repo
    python query_neo4j.py --who-uses <module_name>   # which repos/modules import it
"""
import os
import sys

from neo4j import GraphDatabase

URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
AUTH = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))


def run(session, query, **params):
    return [dict(r) for r in session.run(query, **params)]


def explore_module(session, name: str):
    """Impact analysis: what this module imports, what imports it, and its docstring."""
    rows = run(session,
        "MATCH (m:InternalModule) WHERE m.name = $n OR m.name STARTS WITH $n + '/' "
        "RETURN m.name AS name, m.repo AS repo, m.doc AS doc, m.path AS path "
        "ORDER BY repo, name", n=name)
    if not rows:
        print(f"No module matching '{name}'. Try one of the names below:")
        for r in run(session, "MATCH (m:InternalModule) RETURN m.name AS name ORDER BY name LIMIT 25"):
            print(f"  - {r['name']}")
        return
    for r in rows:
        print(f"\n== {r['name']}  (repo: {r['repo']})")
        print(f"   path: {r['path']}")
        print(f"   doc: {r['doc'] or '(no docstring)'}")
        deps = run(session,
            "MATCH (a:InternalModule {repo: $repo, name: $n})-[:DEPENDS_ON]->(b) "
            "RETURN b.name AS dep, b.repo AS dep_repo ORDER BY dep", n=r["name"], repo=r["repo"])
        print(f"   imports: {[(d['dep'], d['dep_repo']) for d in deps] or '(none)'}")
        who = run(session,
            "MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule {repo: $repo, name: $n}) "
            "RETURN a.name AS user, a.repo AS user_repo ORDER BY user", n=r["name"], repo=r["repo"])
        print(f"   used by: {[(u['user'], u['user_repo']) for u in who] or '(none - top level)'}")


def repo_stats(session, repo: str):
    """Stats scoped to a single repo."""
    total = run(session,
        "MATCH (m:InternalModule {repo: $repo}) RETURN count(m) AS c", repo=repo)[0]["c"]
    within = run(session,
        "MATCH (a:InternalModule {repo: $repo})-[:DEPENDS_ON]->(b:InternalModule {repo: $repo}) "
        "RETURN count(*) AS c", repo=repo)[0]["c"]
    cross = run(session,
        "MATCH (a:InternalModule {repo: $repo})-[:DEPENDS_ON]->(b:InternalModule) "
        "WHERE b.repo <> $repo RETURN count(*) AS c", repo=repo)[0]["c"]
    ext = run(session,
        "MATCH (a:InternalModule {repo: $repo})-[:DEPENDS_ON]->(p:ExternalPackage) "
        "RETURN count(*) AS c", repo=repo)[0]["c"]
    print(f"Repo '{repo}': {total} modules, {within} within-repo edges, "
          f"{cross} cross-repo edges, {ext} external-package edges")

    print("\n  Internal libraries this repo depends on (cross-repo):")
    for r in run(session,
        "MATCH (a:InternalModule {repo: $repo})-[:DEPENDS_ON]->(b:InternalModule) "
        "WHERE b.repo <> $repo "
        "RETURN b.repo AS lib_repo, b.name AS module, count(a) AS users "
        "ORDER BY lib_repo, users DESC", repo=repo):
        print(f"    {r['lib_repo']:>20}  {r['module']:<40}  used by {r['users']} module(s)")

    print("\n  Most-depended-on internal modules in this repo:")
    for r in run(session,
        "MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule {repo: $repo}) "
        "RETURN b.name AS name, count(a) AS users ORDER BY users DESC LIMIT 5", repo=repo):
        print(f"    {r['users']:>3}  {r['name']}")


def who_uses(session, name: str):
    """Which repos and modules import a given module (across all repos)."""
    rows = run(session,
        "MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule {name: $n}) "
        "RETURN a.repo AS repo, a.name AS module ORDER BY repo, module", n=name)
    if not rows:
        print(f"No internal module imports '{name}'.")
        return
    print(f"Modules importing '{name}':")
    for r in rows:
        print(f"  {r['repo']:>20}  {r['module']}")


def stats(session):
    repos = run(session, "MATCH (r:Repository) RETURN r.name AS name ORDER BY name")
    total = run(session, "MATCH (m:InternalModule) RETURN count(m) AS c")[0]["c"]
    pkgs = run(session, "MATCH (p:ExternalPackage) RETURN count(p) AS c")[0]["c"]
    edges = run(session, "MATCH ()-[r:DEPENDS_ON]->() RETURN count(r) AS c")[0]["c"]
    cross = run(session,
        "MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule) "
        "WHERE a.repo <> b.repo RETURN count(*) AS c")[0]["c"]
    print(f"Graph: {len(repos)} repos, {total} internal modules, {pkgs} external packages, "
          f"{edges} dependency edges ({cross} cross-repo)")

    print("\nRepos loaded:")
    for r in repos:
        cnt = run(session,
            "MATCH (m:InternalModule {repo: $repo}) RETURN count(m) AS c", repo=r["name"])[0]["c"]
        print(f"  {r['name']:<24} {cnt} modules")

    print("\nMost-depended-on internal modules across all repos (highest fan-in):")
    for r in run(session,
        "MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule) "
        "RETURN b.name AS name, b.repo AS repo, count(a) AS users "
        "ORDER BY users DESC LIMIT 5"):
        print(f"  {r['users']:>3}  {r['name']:<40} (repo: {r['repo']})")

    print("\nTop external packages by number of modules importing them:")
    for r in run(session,
        "MATCH (a:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage) "
        "RETURN p.name AS name, count(a) AS users ORDER BY users DESC LIMIT 5"):
        print(f"  {r['users']:>3}  {r['name']}")


def main():
    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            args = sys.argv[1:]
            if args and args[0] == "--repo" and len(args) > 1:
                repo_stats(session, args[1])
            elif args and args[0] == "--who-uses" and len(args) > 1:
                who_uses(session, args[1])
            else:
                stats(session)
                if args:
                    explore_module(session, args[0])


if __name__ == "__main__":
    main()