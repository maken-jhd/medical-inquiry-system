from __future__ import annotations

import os
from dataclasses import dataclass

from neo4j import GraphDatabase


@dataclass
class ClearConfig:
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str
    batch_size: int


def parse_positive_int(value: str | None, fallback: int) -> int:
    if value is None:
        return fallback

    try:
        parsed = int(value)
    except ValueError:
        return fallback

    if parsed > 0:
        return parsed

    return fallback


def read_env_config() -> ClearConfig:
    return ClearConfig(
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", ""),
        neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
        batch_size=parse_positive_int(os.getenv("NEO4J_CLEAR_BATCH_SIZE"), 1000),
    )


def count_nodes(tx: object) -> int:
    result = tx.run("MATCH (n) RETURN count(n) AS count")
    return int(result.single()["count"])


def count_relationships(tx: object) -> int:
    result = tx.run("MATCH ()-[r]->() RETURN count(r) AS count")
    return int(result.single()["count"])


def delete_node_batch(tx: object, batch_size: int) -> int:
    result = tx.run(
        """
        MATCH (n)
        WITH n LIMIT $batch_size
        DETACH DELETE n
        RETURN count(n) AS deleted
        """,
        batch_size=batch_size,
    )
    return int(result.single()["deleted"])


def main() -> None:
    config = read_env_config()

    if len(config.neo4j_password) == 0:
        raise RuntimeError("Missing NEO4J_PASSWORD.")

    driver = GraphDatabase.driver(
        config.neo4j_uri,
        auth=(config.neo4j_user, config.neo4j_password),
    )

    try:
        with driver.session(database=config.neo4j_database) as session:
            session.run("RETURN 1").consume()
            before_nodes = session.execute_read(count_nodes)
            before_relationships = session.execute_read(count_relationships)
            deleted_total = 0

            while True:
                deleted = session.execute_write(delete_node_batch, config.batch_size)

                if deleted <= 0:
                    break

                deleted_total += deleted
                print(f"[clear] deleted_node_batch={deleted} deleted_nodes_total={deleted_total}")

            after_nodes = session.execute_read(count_nodes)
            after_relationships = session.execute_read(count_relationships)
    finally:
        driver.close()

    print(f"[clear] neo4j_uri={config.neo4j_uri}")
    print(f"[clear] neo4j_database={config.neo4j_database}")
    print(f"[clear] before_nodes={before_nodes} before_relationships={before_relationships}")
    print(f"[clear] after_nodes={after_nodes} after_relationships={after_relationships}")


if __name__ == "__main__":
    main()
