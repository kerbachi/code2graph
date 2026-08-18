# Codebase Graph — Python Dependency Graph in Neo4j

Turn your Python codebase into a queryable dependency graph in Neo4j. Answer "who depends on what" in seconds, and give an AI the precise modules, file paths, and docstrings behind a feature — for RAG, impact analysis, and onboarding.

## Introduction

This repository contains a small pipeline for turning a Python codebase into a Neo4j dependency graph:

- **`main.py`** — scans a project's Python imports (via `ast`) and loads modules, packages, and `DEPENDS_ON` relationships into Neo4j
- **`query_neo4j.py`** — explores the graph: stats, impact analysis, and module lookups for RAG context
- **`docker-compose.yml`** — starts a local Neo4j instance for running the pipeline

## Quickstart

```bash
docker compose up -d                      # start Neo4j (neo4j/password)
pip install -r requirements.txt
python main.py /path/to/your/codebase     # load the graph
python query_neo4j.py                     # stats + top modules
python query_neo4j.py utils/spark/io      # impact analysis for one module
```

## Graph Schema

```
:InternalModule -[:DEPENDS_ON]-> :InternalModule     (intra-codebase import)
:InternalModule -[:DEPENDS_ON]-> :ExternalPackage     (imports a lib / stdlib)
```

| Label | Represents | Example |
|---|---|---|
| `:InternalModule` | Your project's Python modules (relative file paths) | `utils/spark/io/file_reader` |
| `:ExternalPackage` | Imported dependencies (standard library + third-party) | `sys`, `boto3`, `pyspark` |

An import target is classified **internal** only if it resolves to a real module
in the scanned project - this is what enables the internal→internal edges that
make "who depends on X" impact queries possible.

## Node Properties

| Node | Property | Type | Example |
|---|---|---|---|
| `:InternalModule` | `name` | `STRING` | `utils/spark/io/file_reader` |
| `:InternalModule` | `path` | `STRING` | `/repo/utils/spark/io/file_reader.py` |
| `:InternalModule` | `doc` | `STRING` | module docstring (RAG context for the LLM) |
| `:ExternalPackage` | `name` | `STRING` | `boto3` |

> `path` and `doc` on `:InternalModule` are what make this useful for RAG: the
> LLM can pull a module's docstring and file location to answer "where is X
> implemented?" and "what does module Y do?"

## Relationship Properties

| Type | Direction | Meaning |
|---|---|---|
| `[:DEPENDS_ON]` | InternalModule → InternalModule / ExternalPackage | This module imports that module/package |

---

## Cypher Queries

### 1. Explore Nodes

```cypher
// List all internal modules
MATCH (m:InternalModule) RETURN m.name AS name ORDER BY name;

// List all external packages
MATCH (p:ExternalPackage) RETURN p.name AS name ORDER BY name;

// Count nodes by type
MATCH (n) RETURN labels(n) AS label, count(n) AS count ORDER BY count DESC;
```

### 2. Find Dependencies of a Module

```cypher
// All dependencies of a module (internal modules + external packages)
MATCH (m:InternalModule {name: 'utils/spark/io/file_reader'})-[:DEPENDS_ON]->(dep)
RETURN dep.name AS dependency, labels(dep) AS type ORDER BY dep.name;

// All external packages imported by a module
MATCH (m:InternalModule {name: 'utils/spark/io/file_reader'})-[:DEPENDS_ON]->(p:ExternalPackage)
RETURN p.name AS dependency ORDER BY p.name;

// Dependencies of all modules matching a pattern
MATCH (m:InternalModule)-[:DEPENDS_ON]->(dep)
WHERE m.name STARTS WITH 'utils/spark/io'
RETURN m.name AS module, collect(dep.name) AS dependencies;
```

### 3. Find Who Uses a Package

```cypher
// Which modules import a specific package?
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage {name: 'boto3'})
RETURN m.name AS module ORDER BY m.name;

// Which modules import any of a set of packages?
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage)
WHERE p.name IN ['boto3', 'pyspark', 'yaml']
RETURN p.name AS package, collect(m.name) AS modules;
```

### 4. Find Common Dependencies

```cypher
// Packages used by multiple modules
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage)
RETURN p.name AS package, count(m) AS usage_count
ORDER BY usage_count DESC LIMIT 10;
```

### 5. Find Orphaned / Unused Nodes

```cypher
// External packages with no incoming dependencies (isolated)
MATCH (p:ExternalPackage)
WHERE NOT (p)<-[:DEPENDS_ON]-()
RETURN p.name;

// (Should return empty if graph is correctly populated)
```

### 6. Path Analysis

```cypher
// Full dependency chain (if internal modules also depend on other internal modules)
MATCH path = (a:InternalModule)-[:DEPENDS_ON*1..3]->(b)
WHERE a.name CONTAINS 'file_reader'
RETURN path;
```

### 7. RAG: Locate & Describe a Feature (the LLM's job)

```cypher
// Pull a module's docstring + file path to feed an LLM
MATCH (m:InternalModule)
WHERE m.name CONTAINS 'file_reader'
RETURN m.name AS module, m.doc AS doc, m.path AS path;

// What does a module touch? (context to give the LLM before it reads code)
MATCH (m:InternalModule {name: 'utils/spark/io/file_reader'})-[:DEPENDS_ON]->(dep)
RETURN dep.name AS dependency, labels(dep) AS type;
```

**How this feeds RAG:** given a user question like *"where are Iceberg files
read?"*, an LLM (or vector search over the `doc` property) identifies the
candidate modules, then the graph expands the neighborhood (`-[:DEPENDS_ON]->`)
so the LLM gets *exact* file paths and imports to retrieve — rather than
guessing. The graph turns "fuzzy" questions into precise files to read.

### 8. Graph Statistics

```cypher
// Total nodes and relationships
MATCH (m) RETURN count(DISTINCT m) AS total_nodes;
MATCH ()-[r]->() RETURN count(r) AS total_relationships;

// Most dependent module (imports most packages)
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage)
RETURN m.name AS module, count(p) AS dep_count
ORDER BY dep_count DESC LIMIT 5;

// Most popular package (used by most modules)
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage)
RETURN p.name AS package, count(m) AS user_count
ORDER BY user_count DESC LIMIT 10;
```