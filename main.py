import re
from pathlib import Path
from neo4j import GraphDatabase

#URI = "neo4j+s://your-instance.databases.neo4j.io"
URI = "neo4j://localhost:7687"
AUTH = ("neo4j", "password")

driver = GraphDatabase.driver(URI, auth=AUTH)

def extract_module_dependencies(project_root: str) -> list[tuple[str, str]]:
    """Extract (module, dependency) pairs from Python imports."""
    edges = []
    for py_file in Path(project_root).rglob("*.py"):
        with open(py_file) as f:
            content = f.read()
        imports = re.findall(r"^import ([\w\.]+)", content, re.MULTILINE)
        imports += re.findall(r"^from ([\w\.]+) import", content, re.MULTILINE)
        module_name = "/".join(py_file.relative_to(project_root).parts)[:-3]
        print(f"module_name={module_name}")
        for imp in imports:
            # Convert dotted path to slash path: utils.spark.modules.logger → utils/spark/modules/logger
            target_name = imp.replace(".", "/")
            edges.append((module_name, target_name))
    print(f"edges={edges}")
    return edges

def build_dependency_graph(edges: list[tuple[str, str]]):
    with driver.session() as session:
        # Create constraint once
        session.run("CREATE CONSTRAINT IF NOT EXISTS FOR (m:Module) REQUIRE m.name IS UNIQUE")
        
        # for source, target in edges:
        #     session.run("""
        #         MERGE (a:Module {name: $source})
        #         MERGE (b:Module {name: $target})
        #         MERGE (a)-[:DEPENDS_ON]->(b)
        #     """, source=source, target=target)


        for source, target in edges:
            source_label = "InternalModule" if "/" in source else "ExternalPackage"
            target_label = "InternalModule" if "/" in target else "ExternalPackage"
            session.run(f"""
                MERGE (a:{source_label} {{name: $source}})
                MERGE (b:{target_label} {{name: $target}})
                MERGE (a)-[:DEPENDS_ON]->(b)
            """, source=source, target=target)


# Run in CI
if __name__ == "__main__":
    path_src="/Users/mk/code/rcaf-data-platform-artifacts/" #"src/"
    edges = extract_module_dependencies(path_src)
    build_dependency_graph(edges)
    print(f"✅ Inserted {len(edges)} dependency edges into DKG")

