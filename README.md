# Neo4j Dependency Graph Reference

## Graph Schema

```
:InternalModule -[:DEPENDS_ON]-> :ExternalPackage
```

| Label | Represents | Example |
|---|---|---|
| `:InternalModule` | Your project's Python modules (relative file paths) | `utils/spark/io/file_reader` |
| `:ExternalPackage` | Imported dependencies (standard library + third-party) | `sys`, `boto3`, `pyspark` |

## Node Properties

| Property | Type | Example |
|---|---|---|
| `name` | `STRING` | `utils/spark/io/file_reader` |

## Relationship Properties

| Type | Direction | Meaning |
|---|---|---|
| `[:DEPENDS_ON]` | InternalModule → ExternalPackage | This module imports this package |

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
// All packages imported by a specific module
MATCH (m:InternalModule {name: 'utils/spark/io/file_reader'})-[:DEPENDS_ON]->(p:ExternalPackage)
RETURN p.name AS dependency ORDER BY p.name;

// All dependencies of modules matching a pattern
MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage)
WHERE m.name STARTS WITH 'utils/spark/io'
RETURN m.name AS module, collect(p.name) AS dependencies;
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

### 7. Graph Statistics

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