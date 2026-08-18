#!/usr/bin/env python3
"""Explore the Neo4j codebase dependency graph.

Usage:
    python query_neo4j.py                 # graph stats + sample
    python query_neo4j.py <module_name>   # explore one module (e.g. utils/spark/io)
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
        "RETURN m.name AS name, m.doc AS doc, m.path AS path ORDER BY name", n=name)
    if not rows:
        print(f"No module matching '{name}'. Try one of the names below:")
        for r in run(session, "MATCH (m:InternalModule) RETURN m.name AS name ORDER BY name LIMIT 25"):
            print(f"  - {r['name']}")
        return
    for r in rows:
        print(f"\n== {r['name']}  ({r['path']})")
        print(f"   doc: {r['doc'] or '(no docstring)'}")
        deps = run(session,
            "MATCH (a:InternalModule {name: $n})-[:DEPENDS_ON]->(b) "
            "RETURN b.name AS dep ORDER BY dep", n=r["name"])
        print(f"   imports: {[d['dep'] for d in deps] or '(none)'}")
        who = run(session,
            "MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule {name: $n}) "
            "RETURN a.name AS user ORDER BY user", n=r["name"])
        print(f"   used by: {[u['user'] for u in who] or '(none - top level)'}")


def stats(session):
    total = run(session, "MATCH (m:InternalModule) RETURN count(m) AS c")[0]["c"]
    pkgs = run(session, "MATCH (p:ExternalPackage) RETURN count(p) AS c")[0]["c"]
    edges = run(session, "MATCH ()-[r:DEPENDS_ON]->() RETURN count(r) AS c")[0]["c"]
    print(f"Graph: {total} internal modules, {pkgs} external packages, {edges} dependency edges")

    print("\nMost-depended-on internal modules (highest fan-in = most 'load-bearing'):")
    for r in run(session,
        "MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule) "
        "RETURN b.name AS name, count(a) AS users ORDER BY users DESC LIMIT 5"):
        print(f"  {r['users']:>3}  {r['name']}")

    print("\nTop external packages by number of modules importing them:")
    for r in run(session,
        "MATCH (a:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage) "
        "RETURN p.name AS name, count(a) AS users ORDER BY users DESC LIMIT 5"):
        print(f"  {r['users']:>3}  {r['name']}")


def main():
    with GraphDatabase.driver(URI, auth=AUTH) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            stats(session)
            if len(sys.argv) > 1:
                explore_module(session, sys.argv[1])


if __name__ == "__main__":
    main()