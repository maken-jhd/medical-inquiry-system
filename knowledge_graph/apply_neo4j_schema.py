from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from neo4j import GraphDatabase

KG_ROOT = Path(__file__).resolve().parent


@dataclass
class SchemaConfig:
    schema_file: Path
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str


def read_env_config() -> SchemaConfig:
    return SchemaConfig(
        schema_file=Path(os.getenv("NEO4J_SCHEMA_FILE", str(KG_ROOT / "scripts" / "neo4j_init.cypher"))).resolve(),
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", ""),
        neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
    )


def split_cypher_statements(text: str) -> List[str]:
    without_line_comments = re.sub(r"(?m)^\s*//.*$", "", text)
    statements = []

    for raw_statement in without_line_comments.split(";"):
        statement = raw_statement.strip()

        if len(statement) > 0:
            statements.append(statement)

    return statements


def main() -> None:
    config = read_env_config()

    if len(config.neo4j_password) == 0:
        raise RuntimeError("Missing NEO4J_PASSWORD.")

    if not config.schema_file.exists():
        raise RuntimeError(f"Schema file does not exist: {config.schema_file}")

    statements = split_cypher_statements(config.schema_file.read_text(encoding="utf-8"))
    driver = GraphDatabase.driver(
        config.neo4j_uri,
        auth=(config.neo4j_user, config.neo4j_password),
    )

    try:
        with driver.session(database=config.neo4j_database) as session:
            session.run("RETURN 1").consume()

            for statement in statements:
                session.run(statement).consume()
    finally:
        driver.close()

    print(f"[schema] file={config.schema_file}")
    print(f"[schema] applied_statements={len(statements)}")
    print(f"[schema] neo4j_uri={config.neo4j_uri}")
    print(f"[schema] neo4j_database={config.neo4j_database}")


if __name__ == "__main__":
    main()
