from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from neo4j import GraphDatabase

KG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = KG_ROOT.parent

SEARCH_LABELS = [
    "Disease",
    "DiseasePhase",
    "OpportunisticInfection",
    "Comorbidity",
    "SyndromeOrComplication",
    "Tumor",
    "Pathogen",
    "Symptom",
    "Sign",
    "ClinicalAttribute",
    "LabTest",
    "LabFinding",
    "ImagingFinding",
    "RiskFactor",
    "RiskBehavior",
    "PopulationGroup",
]

EVIDENCE_LABELS = [
    "Pathogen",
    "Symptom",
    "Sign",
    "ClinicalAttribute",
    "LabTest",
    "LabFinding",
    "ImagingFinding",
    "RiskFactor",
    "RiskBehavior",
    "PopulationGroup",
]

SEARCH_RELATIONSHIP_TYPES = [
    "MANIFESTS_AS",
    "HAS_LAB_FINDING",
    "HAS_IMAGING_FINDING",
    "HAS_PATHOGEN",
    "DIAGNOSED_BY",
    "REQUIRES_DETAIL",
    "RISK_FACTOR_FOR",
    "COMPLICATED_BY",
    "APPLIES_TO",
]

DEPRECATED_LABELS = [
    "GuidelineDocument",
    "GuidelineSection",
    "EvidenceSpan",
    "Assertion",
    "Recommendation",
    "Medication",
    "DrugClass",
    "TreatmentRegimen",
    "PreventionStrategy",
    "TransmissionRoute",
    "ManagementAction",
    "ExposureScenario",
]


@dataclass
class ValidationConfig:
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str
    report_file: Path
    sample_limit: int


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


def read_env_config() -> ValidationConfig:
    default_report = PROJECT_ROOT / "test_outputs" / "search_kg" / "neo4j_validation_report.json"
    return ValidationConfig(
        neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", ""),
        neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
        report_file=Path(os.getenv("NEO4J_VALIDATION_REPORT_FILE", str(default_report))).resolve(),
        sample_limit=parse_positive_int(os.getenv("NEO4J_VALIDATION_SAMPLE_LIMIT"), 20),
    )


def run_count_query(session: Any, query: str, **parameters: Any) -> int:
    result = session.run(query, **parameters)
    record = result.single()

    if record is None:
        return 0

    return int(record[0])


def rows_to_dict(rows: List[Dict[str, Any]], key_name: str, value_name: str) -> Dict[str, int]:
    return {
        str(row[key_name]): int(row[value_name])
        for row in rows
    }


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

            node_count = run_count_query(session, "MATCH (n) RETURN count(n)")
            relationship_count = run_count_query(session, "MATCH ()-[r]->() RETURN count(r)")
            isolated_node_count = run_count_query(session, "MATCH (n) WHERE NOT (n)--() RETURN count(n)")
            missing_id_node_count = run_count_query(
                session,
                "MATCH (n) WHERE n.id IS NULL OR toString(n.id) = '' RETURN count(n)",
            )
            duplicate_id_count = run_count_query(
                session,
                """
                MATCH (n)
                WITH n.id AS id, count(n) AS count
                WHERE id IS NOT NULL AND count > 1
                RETURN count(*)
                """,
            )
            self_loop_count = run_count_query(
                session,
                "MATCH (n)-[r]->(n) RETURN count(r)",
            )

            label_rows = [
                dict(row)
                for row in session.run(
                    """
                    MATCH (n)
                    UNWIND labels(n) AS label
                    RETURN label, count(*) AS count
                    ORDER BY count DESC, label
                    """
                )
            ]
            relationship_type_rows = [
                dict(row)
                for row in session.run(
                    """
                    MATCH ()-[r]->()
                    RETURN type(r) AS type, count(*) AS count
                    ORDER BY count DESC, type
                    """
                )
            ]
            isolated_by_label_rows = [
                dict(row)
                for row in session.run(
                    """
                    MATCH (n)
                    WHERE NOT (n)--()
                    UNWIND labels(n) AS label
                    RETURN label, count(*) AS count
                    ORDER BY count DESC, label
                    """
                )
            ]
            deprecated_label_rows = [
                dict(row)
                for row in session.run(
                    """
                    MATCH (n)
                    UNWIND labels(n) AS label
                    WITH label, count(*) AS count
                    WHERE label IN $deprecated_labels
                    RETURN label, count
                    ORDER BY count DESC, label
                    """,
                    deprecated_labels=DEPRECATED_LABELS,
                )
            ]
            unexpected_label_rows = [
                dict(row)
                for row in session.run(
                    """
                    MATCH (n)
                    UNWIND labels(n) AS label
                    WITH label, count(*) AS count
                    WHERE NOT label IN $search_labels
                    RETURN label, count
                    ORDER BY count DESC, label
                    """,
                    search_labels=SEARCH_LABELS,
                )
            ]
            unexpected_relationship_rows = [
                dict(row)
                for row in session.run(
                    """
                    MATCH ()-[r]->()
                    WITH type(r) AS type, count(*) AS count
                    WHERE NOT type IN $search_relationship_types
                    RETURN type, count
                    ORDER BY count DESC, type
                    """,
                    search_relationship_types=SEARCH_RELATIONSHIP_TYPES,
                )
            ]
            acquisition_rows = [
                dict(row)
                for row in session.run(
                    """
                    MATCH (n)
                    WHERE any(label IN labels(n) WHERE label IN $evidence_labels)
                    RETURN
                      count(n) AS evidence_node_count,
                      count(n.acquisition_mode) AS acquisition_mode_count,
                      count(n.evidence_cost) AS evidence_cost_count
                    """,
                    evidence_labels=EVIDENCE_LABELS,
                )
            ]
            isolated_samples = [
                dict(row)
                for row in session.run(
                    """
                    MATCH (n)
                    WHERE NOT (n)--()
                    RETURN n.id AS id, labels(n) AS labels, n.name AS name
                    ORDER BY labels(n), n.name
                    LIMIT $limit
                    """,
                    limit=config.sample_limit,
                )
            ]

            acquisition = acquisition_rows[0] if len(acquisition_rows) > 0 else {}
    finally:
        driver.close()

    label_counts = rows_to_dict(label_rows, "label", "count")
    relationship_type_counts = rows_to_dict(relationship_type_rows, "type", "count")
    isolated_by_label = rows_to_dict(isolated_by_label_rows, "label", "count")
    deprecated_label_counts = rows_to_dict(deprecated_label_rows, "label", "count")
    unexpected_label_counts = rows_to_dict(unexpected_label_rows, "label", "count")
    unexpected_relationship_type_counts = rows_to_dict(unexpected_relationship_rows, "type", "count")
    evidence_node_count = int(acquisition.get("evidence_node_count", 0) or 0)
    acquisition_mode_count = int(acquisition.get("acquisition_mode_count", 0) or 0)
    evidence_cost_count = int(acquisition.get("evidence_cost_count", 0) or 0)

    report = {
        "neo4j_uri": config.neo4j_uri,
        "neo4j_database": config.neo4j_database,
        "node_count": node_count,
        "relationship_count": relationship_count,
        "isolated_node_count": isolated_node_count,
        "isolated_node_ratio": isolated_node_count / node_count if node_count > 0 else 0,
        "missing_id_node_count": missing_id_node_count,
        "duplicate_id_count": duplicate_id_count,
        "self_loop_count": self_loop_count,
        "label_counts": label_counts,
        "relationship_type_counts": relationship_type_counts,
        "isolated_by_label": isolated_by_label,
        "deprecated_label_counts": deprecated_label_counts,
        "unexpected_label_counts": unexpected_label_counts,
        "unexpected_relationship_type_counts": unexpected_relationship_type_counts,
        "evidence_node_count": evidence_node_count,
        "acquisition_mode_count": acquisition_mode_count,
        "acquisition_mode_coverage": acquisition_mode_count / evidence_node_count if evidence_node_count > 0 else 0,
        "evidence_cost_count": evidence_cost_count,
        "evidence_cost_coverage": evidence_cost_count / evidence_node_count if evidence_node_count > 0 else 0,
        "isolated_samples": isolated_samples,
    }

    config.report_file.parent.mkdir(parents=True, exist_ok=True)
    config.report_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"[validate] report={config.report_file}")
    print(f"[validate] nodes={node_count} relationships={relationship_count}")
    print(
        "[validate] "
        f"isolated_nodes={isolated_node_count} "
        f"isolated_ratio={report['isolated_node_ratio']:.4f}"
    )
    print(
        "[validate] "
        f"missing_id_nodes={missing_id_node_count} "
        f"duplicate_id_groups={duplicate_id_count} "
        f"self_loops={self_loop_count}"
    )
    print(f"[validate] labels={label_counts}")
    print(f"[validate] relationship_types={relationship_type_counts}")
    print(f"[validate] isolated_by_label={isolated_by_label}")
    print(f"[validate] deprecated_label_counts={deprecated_label_counts}")
    print(f"[validate] unexpected_label_counts={unexpected_label_counts}")
    print(f"[validate] unexpected_relationship_type_counts={unexpected_relationship_type_counts}")
    print(
        "[validate] "
        f"acquisition_mode_coverage={report['acquisition_mode_coverage']:.4f} "
        f"evidence_cost_coverage={report['evidence_cost_coverage']:.4f}"
    )


if __name__ == "__main__":
    main()
