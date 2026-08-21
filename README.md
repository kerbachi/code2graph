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
python query_neo4j.py --who-uses neomodel/async_/node   # who imports a library module
python query_neo4j.py neomodel/async_/node      # impact analysis for one module
```

## LLM Natural Language Query

Ask questions about your codebase in plain English. The `llm_query.py` script uses an OpenAI-compatible
API to convert your question into a Cypher query, executes it against Neo4j, and displays the results.

```bash
# Install the OpenAI Python client
pip install -r requirements.txt

# Ask natural language questions (requires an LLM API: OpenAI, Ollama, vLLM, etc.)
python llm_query.py "Which modules import pytest?"
python llm_query.py "Show me all dependencies of neomodel/async_/node"
python llm_query.py "Which repos depend on neomodel?"
```

**Configuration** (via environment variables):

| Variable | Default | Description |
|---|---|---|
| `LLM_API_URL` | `http://localhost:11434/v1` | OpenAI-compatible API endpoint |
| `LLM_API_KEY` | `ollama` | API key |
| `LLM_MODEL` | `gpt-4o` | Model name |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection string |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `password` | Neo4j password |

**How it works:** The script injects the graph schema and example question→Cypher mappings into the LLM's
system prompt. This teaches the LLM your specific schema so it can accurately translate natural language
questions into valid Cypher queries — no fine-tuning required.

> **Order matters for cross-repo edges.** When repoA imports a module from repoB, the cross-repo edge is created when repoB is loaded *after* repoA (the loader upgrades stale `ExternalPackage` edges). For best results, load library repos first, then the repos that consume them. Re-running any repo is safe and idempotent.

## Graph Schema

```
(:Repository)-[:CONTAINS]->(:InternalModule)
(:InternalModule)-[:DEPENDS_ON]->(:InternalModule)     # within-repo AND cross-repo
(:InternalModule)-[:DEPENDS_ON]->(:ExternalPackage)    # true third-party / stdlib
```

| Label | Represents | Example |
|---|---|---|
| `:Repository` | A scanned codebase (folder basename) | `code2graph`, `neomodel` |
| `:InternalModule` | A Python module, tagged with its owning repo | `{repo: 'neomodel', name: 'neomodel/async_/node'}` |
| `:ExternalPackage` | Imported dependencies (stdlib + third-party) | `pytest`, `typing`, `__future__` |

An import target is classified **internal** if it resolves to a module in *any* loaded repo — this is what enables cross-repo edges and "who uses this library" queries.

## Node Properties

| Node | Property | Type | Example |
|---|---|---|---|
| `:Repository` | `name` | `STRING` | `code2graph` |
| `:Repository` | `path` | `STRING` | `/Users/mk/code/code2graph` |
| `:Repository` | `git_remote` | `STRING` | `https://github.com/kerbachi/code2graph.git` (optional) |
| `:Repository` | `git_commit` | `STRING` | `392b9668a47cd1e9034328484aad41c7358725cd` (optional) |
| `:Repository` | `loaded_at` | `DATETIME` | last load time |
| `:InternalModule` | `repo` | `STRING` | `neomodel` |
| `:InternalModule` | `name` | `STRING` | `neomodel/async_/node` |
| `:InternalModule` | `path` | `STRING` | `/Users/mk/code/neomodel/neomodel/async_/node.py` |
| `:InternalModule` | `doc` | `STRING` | module docstring (RAG context for the LLM) |
| `:ExternalPackage` | `name` | `STRING` | `pytest` |

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
MATCH (m:InternalModule {repo: 'neomodel'}) RETURN m.name AS name ORDER BY name;

// Count nodes by type
MATCH (n) RETURN labels(n) AS label, count(n) AS count ORDER BY count DESC;
```

### 2. Find Dependencies of a Module

```cypher
// All dependencies of a module (internal + external), with owning repo
MATCH (m:InternalModule {repo: 'neomodel', name: 'neomodel/async_/node'})-[:DEPENDS_ON]->(dep)
RETURN dep.name AS dependency, labels(dep) AS type, dep.repo AS repo ORDER BY dep.name;

// All external packages imported by a module
MATCH (m:InternalModule {repo: 'neomodel', name: 'neomodel/async_/node'})-[:DEPENDS_ON]->(p:ExternalPackage)
RETURN p.name AS dependency ORDER BY p.name;
```

### 3. Cross-Repo: Which Repos Use a Library?

```cypher
// Which repos import modules from repo 'neo4j-python-driver'?
MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule {repo: 'neo4j-python-driver'})
RETURN a.repo AS consumer_repo, count(DISTINCT a) AS modules_using
ORDER BY modules_using DESC;

// Which specific modules of 'neo4j-python-driver' are most reused across the org?
MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule {repo: 'neo4j-python-driver'})
RETURN b.name AS library_module, count(DISTINCT a) AS users
ORDER BY users DESC LIMIT 10;

// Which repos does 'neomodel' depend on internally?
MATCH (a:InternalModule {repo: 'neomodel'})-[:DEPENDS_ON]->(b:InternalModule)
WHERE b.repo <> 'neomodel'
RETURN DISTINCT b.repo AS internal_dependency;
```

### 4. Find Who Uses a Package

```cypher
// Which modules import a specific external package?
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage {name: 'pytest'})
RETURN m.repo AS repo, m.name AS module ORDER BY repo, module;

// Which modules import any of a set of packages?
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage)
WHERE p.name IN ['pytest', '__future__', 'typing']
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
WHERE a.name CONTAINS 'async_/node'
RETURN path;
```

### 7. RAG: Locate & Describe a Feature (the LLM's job)

```cypher
// Pull a module's docstring + file path + repo to feed an LLM
MATCH (m:InternalModule)
WHERE m.name CONTAINS 'async_/node'
RETURN m.repo AS repo, m.name AS module, m.doc AS doc, m.path AS path;

// What does a module touch? (context to give the LLM before it reads code)
MATCH (m:InternalModule {repo: 'neomodel', name: 'neomodel/async_/node'})-[:DEPENDS_ON]->(dep)
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