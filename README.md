# Codebase Graph — Multi-Repo Python Dependency Graph in Neo4j

Turn your team's Python codebases (multiple repos + internal libraries) into a single queryable dependency graph in Neo4j. Answer "who depends on what" in seconds, and give an AI the precise modules, file paths, docstrings, and **owning repo** behind a feature — for RAG, impact analysis, and onboarding.

## Introduction

This repository contains a small pipeline for turning Python codebases into a Neo4j dependency graph. It is **repo-aware**: you run it once per repository, and each run tags every module with its owning repo, so modules from different repos coexist without collisions and cross-repo dependencies are captured.

- **`main.py`** — scans a project's Python imports (via `ast`) and loads modules, packages, repos, and `DEPENDS_ON` relationships into Neo4j
- **`query_neo4j.py`** — explores the graph: stats, impact analysis, cross-repo lookups, and module searches for RAG context
- **`docker-compose.yml`** — starts a local Neo4j instance for running the pipeline

## Quickstart

```bash
docker compose up -d                      # start Neo4j (neo4j/password)
pip install -r requirements.txt

# Load each repo separately - the repo name is the folder's basename
python main.py /path/to/repoA
python main.py /path/to/repoB
python main.py /path/to/repoC

# Explore
python query_neo4j.py                     # overall stats + top modules
python query_neo4j.py --repo repoA        # stats for one repo
python query_neo4j.py --who-uses acme_common/http   # who imports a library module
python query_neo4j.py utils/spark/io      # impact analysis for one module
```

> **Order matters for cross-repo edges.** When repoA imports a module from repoB, the cross-repo edge is created when repoB is loaded *after* repoA (the loader upgrades stale `ExternalPackage` edges). For best results, load library repos first, then the repos that consume them. Re-running any repo is safe and idempotent.

## Graph Schema

```
(:Repository)-[:CONTAINS]->(:InternalModule)
(:InternalModule)-[:DEPENDS_ON]->(:InternalModule)     # within-repo AND cross-repo
(:InternalModule)-[:DEPENDS_ON]->(:ExternalPackage)    # true third-party / stdlib
```

| Label | Represents | Example |
|---|---|---|
| `:Repository` | A scanned codebase (folder basename) | `acme-common`, `billing-service` |
| `:InternalModule` | A Python module, tagged with its owning repo | `{repo: 'acme-common', name: 'http/client'}` |
| `:ExternalPackage` | Imported dependencies (stdlib + third-party) | `sys`, `boto3`, `pyspark` |

An import target is classified **internal** if it resolves to a module in *any* loaded repo — this is what enables cross-repo edges and "who uses this library" queries.

## Node Properties

| Node | Property | Type | Example |
|---|---|---|---|
| `:Repository` | `name` | `STRING` | `acme-common` |
| `:Repository` | `path` | `STRING` | `/code/acme-common` |
| `:Repository` | `git_remote` | `STRING` | `git@github.com:org/acme-common.git` (optional) |
| `:Repository` | `git_commit` | `STRING` | `abc123...` (optional) |
| `:Repository` | `loaded_at` | `DATETIME` | last load time |
| `:InternalModule` | `repo` | `STRING` | `acme-common` |
| `:InternalModule` | `name` | `STRING` | `http/client` |
| `:InternalModule` | `path` | `STRING` | `/code/acme-common/http/client.py` |
| `:InternalModule` | `doc` | `STRING` | module docstring (RAG context for the LLM) |
| `:ExternalPackage` | `name` | `STRING` | `boto3` |

> `repo`, `path`, and `doc` on `:InternalModule` are what make this useful for RAG: the LLM can pull a module's docstring, file location, **and owning repo** to answer "where is X implemented?" and "which repo provides library Y?"

## Relationship Properties

| Type | Direction | Meaning |
|---|---|---|
| `[:CONTAINS]` | Repository → InternalModule | This repo contains this module |
| `[:DEPENDS_ON]` | InternalModule → InternalModule / ExternalPackage | This module imports that module/package |

---

## Cypher Queries

### 1. Explore Nodes

```cypher
// List all repos
MATCH (r:Repository) RETURN r.name AS repo, r.path AS path ORDER BY repo;

// List all internal modules in one repo
MATCH (m:InternalModule {repo: 'acme-common'}) RETURN m.name AS name ORDER BY name;

// Count nodes by type
MATCH (n) RETURN labels(n) AS label, count(n) AS count ORDER BY count DESC;
```

### 2. Find Dependencies of a Module

```cypher
// All dependencies of a module (internal + external), with owning repo
MATCH (m:InternalModule {repo: 'billing-service', name: 'utils/spark/io/file_reader'})-[:DEPENDS_ON]->(dep)
RETURN dep.name AS dependency, labels(dep) AS type, dep.repo AS repo ORDER BY dep.name;

// All external packages imported by a module
MATCH (m:InternalModule {repo: 'billing-service', name: 'utils/spark/io/file_reader'})-[:DEPENDS_ON]->(p:ExternalPackage)
RETURN p.name AS dependency ORDER BY p.name;
```

### 3. Cross-Repo: Which Repos Use a Library?

```cypher
// Which repos import modules from repo 'acme-common'?
MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule {repo: 'acme-common'})
RETURN a.repo AS consumer_repo, count(DISTINCT a) AS modules_using
ORDER BY modules_using DESC;

// Which specific modules of 'acme-common' are most reused across the org?
MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule {repo: 'acme-common'})
RETURN b.name AS library_module, count(DISTINCT a) AS users
ORDER BY users DESC LIMIT 10;

// Which repos does 'billing-service' depend on internally?
MATCH (a:InternalModule {repo: 'billing-service'})-[:DEPENDS_ON]->(b:InternalModule)
WHERE b.repo <> 'billing-service'
RETURN DISTINCT b.repo AS internal_dependency;
```

### 4. Find Who Uses a Package

```cypher
// Which modules import a specific external package?
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage {name: 'boto3'})
RETURN m.repo AS repo, m.name AS module ORDER BY repo, module;

// Which modules import any of a set of packages?
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage)
WHERE p.name IN ['boto3', 'pyspark', 'yaml']
RETURN p.name AS package, collect(m.repo + '/' + m.name) AS modules;
```

### 5. Find Common Dependencies

```cypher
// External packages used by multiple modules
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage)
RETURN p.name AS package, count(m) AS usage_count
ORDER BY usage_count DESC LIMIT 10;

// Internal modules used across multiple repos (the org's shared libraries)
MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule)
WHERE a.repo <> b.repo
RETURN b.repo AS library_repo, b.name AS module, count(DISTINCT a.repo) AS repos_using
ORDER BY repos_using DESC LIMIT 10;
```

### 6. Path Analysis

```cypher
// Full dependency chain (within and across repos)
MATCH path = (a:InternalModule)-[:DEPENDS_ON*1..3]->(b)
WHERE a.name CONTAINS 'file_reader'
RETURN path;
```

### 7. RAG: Locate & Describe a Feature (the LLM's job)

```cypher
// Pull a module's docstring + file path + repo to feed an LLM
MATCH (m:InternalModule)
WHERE m.name CONTAINS 'file_reader'
RETURN m.repo AS repo, m.name AS module, m.doc AS doc, m.path AS path;

// What does a module touch? (context to give the LLM before it reads code)
MATCH (m:InternalModule {repo: 'billing-service', name: 'utils/spark/io/file_reader'})-[:DEPENDS_ON]->(dep)
RETURN dep.name AS dependency, labels(dep) AS type, dep.repo AS repo;
```

**How this feeds RAG:** given a user question like *"where are Iceberg files read?"*, an LLM (or vector search over the `doc` property) identifies the candidate modules, then the graph expands the neighborhood (`-[:DEPENDS_ON]->`) so the LLM gets *exact* file paths, imports, **and owning repos** to retrieve — rather than guessing. The graph turns "fuzzy" questions into precise files to read, and tells the developer *which repo* provides a reusable library.

### 8. Graph Statistics

```cypher
// Total nodes and relationships
MATCH (m) RETURN count(DISTINCT m) AS total_nodes;
MATCH ()-[r]->() RETURN count(r) AS total_relationships;

// Most dependent module (imports most packages)
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage)
RETURN m.repo AS repo, m.name AS module, count(p) AS dep_count
ORDER BY dep_count DESC LIMIT 5;

// Most popular external package (used by most modules)
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage)
RETURN p.name AS package, count(m) AS user_count
ORDER BY user_count DESC LIMIT 10;

// Cross-repo dependency count
MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule)
WHERE a.repo <> b.repo
RETURN count(*) AS cross_repo_edges;