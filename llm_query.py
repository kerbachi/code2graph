#!/usr/bin/env python3
"""
llm_query.py — Natural Language to Cypher using an OpenAI-compatible API.

Ask questions about your codebase in plain English. The LLM converts your
question into a Cypher query, executes it against Neo4j, and displays the results.

Usage:
    python llm_query.py "Which modules import pytest?"
    python llm_query.py "Show me all dependencies of neomodel/async_/node"
    python llm_query.py "Which repos depend on neomodel?"

Configuration (via environment variables):
    LLM_API_URL      OpenAI-compatible API endpoint (default: http://localhost:11434/v1)
    LLM_API_KEY      API key (default: ollama)
    LLM_MODEL        Model name (default: gpt-4o)
    NEO4J_URI        Neo4j connection string (default: bolt://localhost:7687)
    NEO4J_USER       Neo4j username (default: neo4j)
    NEO4J_PASSWORD   Neo4j password (default: password)
"""

import os
import sys
from openai import OpenAI
from neo4j import GraphDatabase

# --- Configuration ---
LLM_API_URL = os.getenv("LLM_API_URL", "http://localhost:1234/v1") # Default for LMStudio
LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3.6-35b-a3b")


# "gpt-4o")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# System prompt that teaches the LLM the Cypher schema and provides examples
SYSTEM_PROMPT = """\
You are a Cypher query expert for a Neo4j graph database that stores Python codebase dependencies.

## Graph Schema
- (Repository)-[:CONTAINS]->(InternalModule)
- (InternalModule)-[:DEPENDS_ON]->(InternalModule)   # cross-repo or within-repo
- (InternalModule)-[:DEPENDS_ON]->(ExternalPackage)  # stdlib / third-party

## Node Properties
- InternalModule: {repo: STRING, name: STRING, path: STRING, doc: STRING}
- ExternalPackage: {name: STRING}
- Repository: {name: STRING, path: STRING}

## Rules
- Module names use slash notation: 'neomodel/async_/node'
- External packages are plain names: 'pytest', 'typing', '__future__'
- Cross-repo edges exist when a module in repo A imports a module in repo B
- Always return valid Cypher queries

## Examples

User: "Which modules import pytest?"
Cypher: MATCH (m:InternalModule)-[:DEPENDS_ON]->(p:ExternalPackage {name: 'pytest'}) RETURN m.repo AS repo, m.name AS module ORDER BY repo, module;

User: "Who depends on neomodel/async_/node?"
Cypher: MATCH (m:InternalModule)-[:DEPENDS_ON]->(target:InternalModule {name: 'neomodel/async_/node'}) RETURN m.repo AS consumer_repo, m.name AS consumer_module ORDER BY consumer_repo, consumer_module;

User: "Show me all dependencies of the auth module"
Cypher: MATCH (m:InternalModule {name: 'auth'})-[:DEPENDS_ON]->(dep) RETURN dep.name AS dependency, labels(dep) AS type, dep.repo AS repo;

User: "Which repos use the neo4j driver?"
Cypher: MATCH (a:InternalModule)-[:DEPENDS_ON]->(b:InternalModule) WHERE b.name CONTAINS 'neo4j' RETURN DISTINCT a.repo AS consumer_repo;

Return ONLY the Cypher query, nothing else.
"""


def ask_llm(question: str, llm_client: OpenAI, model: str) -> str:
    """Ask the LLM to generate a Cypher query for the given question."""
    response = llm_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
    )
    return response.choices[0].message.content.strip()


def execute_cypher(query: str) -> list[dict]:
    """Execute a Cypher query against Neo4j and return results."""
    with GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)) as driver:
        with driver.session() as session:
            result = session.run(query)
            records = result.data()
            return records


def display_results(results: list[dict]) -> None:
    """Pretty-print query results in a table format."""
    if not results:
        print("No results found.")
        return

    # Calculate column widths
    columns = list(results[0].keys())
    col_widths = {col: len(col) for col in columns}
    for row in results:
        for col in columns:
            col_widths[col] = max(col_widths[col], len(str(row.get(col, ""))))

    # Print table
    header = " | ".join(f"{col:<{col_widths[col]}}" for col in columns)
    separator = "-+-".join("-" * col_widths[col] for col in columns)

    print(header)
    print(separator)
    for row in results:
        print(" | ".join(f"{row.get(col, ''):<{col_widths[col]}}" for col in columns))

    print(f"\n({len(results)} record{'s' if len(results) != 1 else ''})")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python llm_query.py 'Your question here'")
        print("\nExamples:")
        print('  python llm_query.py "Which modules import pytest?"')
        print('  python llm_query.py "Show me all dependencies of neomodel/async_/node"')
        print('  python llm_query.py "Which repos depend on neomodel?"')
        sys.exit(1)

    question = " ".join(sys.argv[1:])

    # Step 1: Ask LLM to generate Cypher query
    llm_client = OpenAI(base_url=LLM_API_URL, api_key=LLM_API_KEY)
    print(f"Question: {question}")
    print("-" * 60)

    try:
        cypher_query = ask_llm(question, llm_client, LLM_MODEL)
    except Exception as e:
        print(f"Error calling LLM API: {e}")
        print("Make sure the LLM API is running and accessible at: %s", LLM_API_URL)
        sys.exit(1)

    print(f"Generated Cypher:\n{cypher_query}\n")

    # Step 2: Execute the Cypher query against Neo4j
    try:
        results = execute_cypher(cypher_query)
    except Exception as e:
        print(f"Error executing Cypher query: {e}")
        sys.exit(1)

    # Step 3: Display results
    display_results(results)


if __name__ == "__main__":
    main()