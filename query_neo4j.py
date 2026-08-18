#!/usr/bin/env python3
"""Diagnostic script to query the Neo4j dependency graph."""
from neo4j import GraphDatabase

URI = "neo4j://localhost:7687"
AUTH = ("neo4j", "your_password")  # update with your actual password

def main():
    try:
        with GraphDatabase.driver(URI, auth=AUTH) as driver:
            # Test connection
            driver.verify_connectivity()
            print("✅ Connected to Neo4j successfully!\n")
            
            with driver.session() as session:
                # 1. Total node count
                result = session.run("MATCH (m:Module) RETURN count(m) AS total_nodes")
                row = result.single()
                print(f"Total Module nodes: {row['total_nodes']}\n")
                
                # 2. Find nodes containing 'iceberg'
                result = session.run(
                    "MATCH (m:Module) WHERE m.name CONTAINS 'iceberg' RETURN m.name AS name ORDER BY name"
                )
                iceberg_nodes = [r["name"] for r in result]
                print(f"Nodes containing 'iceberg': {iceberg_nodes}\n")
                
                # 3. If found, show its dependencies
                if iceberg_nodes:
                    for name in iceberg_nodes:
                        result = session.run(
                            "MATCH (a:Module {name: $n})-[:DEPENDS_ON]->(b:Module) RETURN b.name AS dependency ORDER BY b.name",
                            n=name
                        )
                        deps = [r["dependency"] for r in result]
                        print(f"'{name}' depends on: {deps}")
                    
                    # Show reverse: who depends on iceberg
                    for name in iceberg_nodes:
                        result = session.run(
                            "MATCH (a:Module)-[:DEPENDS_ON]->(b:Module {name: $n}) RETURN a.name AS dependent ORDER BY a.name",
                            n=name
                        )
                        dependents = [r["dependent"] for r in result]
                        print(f"Who depends on '{name}': {dependents}")
                else:
                    print("❌ No 'iceberg' node found. Listing all unique module names for reference:")
                    result = session.run(
                        "MATCH (m:Module) RETURN DISTINCT m.name AS name ORDER BY name LIMIT 50"
                    )
                    for r in result:
                        print(f"  - {r['name']}")
                
                # 4. Sample some edges to verify graph structure
                result = session.run(
                    "MATCH (a:Module)-[r:DEPENDS_ON]->(b:Module) RETURN a.name AS source, b.name AS target LIMIT 10"
                )
                print("\nSample edges:")
                for r in result:
                    print(f"  {r['source']} -> {r['target']}")
                    
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()