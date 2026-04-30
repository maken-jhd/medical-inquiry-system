"""对指定 replay 目录中的 failed opening 做 LLM payload 审计并输出报告。"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys
from typing import Any

# 将项目根目录加入导入路径，确保脚本可直接运行。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brain.evidence_parser import EvidenceParser
from brain.errors import LlmEmptyExtractionError, LlmOutputInvalidError
from brain.llm_client import LlmClient
from brain.med_extractor import MedExtractor
from brain.types import A1ExtractionResult, KeyFeature
from frontend.config_loader import apply_config_to_environment, load_frontend_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审计 replay 失败病例的 MedExtractor / A1 LLM payload。")
    parser.add_argument(
        "--replay-dir",
        required=True,
        help="包含 replay_results.jsonl 的回放输出目录。",
    )
    return parser.parse_args()


def _load_replay_rows(replay_dir: Path) -> list[dict[str, Any]]:
    replay_file = replay_dir / "replay_results.jsonl"
    return [
        json.loads(line)
        for line in replay_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _serialize_patient_context(patient_context: Any) -> dict[str, Any]:
    return {
        "metadata": dict(patient_context.metadata),
        "general_info": {
            "age": patient_context.general_info.age,
            "sex": patient_context.general_info.sex,
            "pregnancy_status": patient_context.general_info.pregnancy_status,
            "past_history": list(patient_context.general_info.past_history),
            "epidemiology": list(patient_context.general_info.epidemiology),
        },
        "clinical_features": [
            {
                "name": item.name,
                "normalized_name": item.normalized_name,
                "category": item.category,
                "mention_state": item.mention_state,
                "evidence_text": item.evidence_text,
                "metadata": dict(item.metadata),
            }
            for item in patient_context.clinical_features
        ],
        "raw_text": patient_context.raw_text,
    }


def _serialize_a1_result(a1_result: Any) -> dict[str, Any]:
    return {
        "reasoning": a1_result.reasoning,
        "selection_decision": a1_result.selection_decision,
        "metadata": dict(a1_result.metadata),
        "key_features": [
            {
                "name": item.name,
                "normalized_name": item.normalized_name,
                "category": item.category,
                "reasoning": item.reasoning,
                "metadata": dict(item.metadata),
            }
            for item in a1_result.key_features
        ],
    }


def _coerce_a1_payload(parser: EvidenceParser, patient_context: Any, payload: Any) -> A1ExtractionResult:
    if not isinstance(payload, dict):
        raise LlmOutputInvalidError(
            stage="a1_key_symptom_extraction",
            prompt_name="a1_key_symptom_extraction",
            attempts=1,
            message="A1 key symptom extraction 收到的 payload 不是 JSON object。",
        )

    key_features: list[KeyFeature] = []
    normalized_names: set[str] = set()
    for item in payload.get("key_features", []):
        if isinstance(item, str):
            raw_name = item
            raw_category = "symptom"
            reasoning = "由 LLM 提取。"
            metadata = {}
        elif isinstance(item, dict):
            raw_name = str(item.get("normalized_name", item.get("name", "")) or item.get("name", ""))
            raw_category = str(item.get("category", "symptom") or "symptom")
            reasoning = str(item.get("reasoning", "由 LLM 提取。") or "由 LLM 提取。")
            metadata = dict(item.get("metadata", {}))
        else:
            continue

        normalized_name = parser.normalizer.normalize_feature_name(raw_name)
        if len(normalized_name) == 0 or normalized_name in normalized_names:
            continue
        key_features.append(
            KeyFeature(
                name=str(raw_name or normalized_name),
                normalized_name=normalized_name,
                category=parser.normalizer.normalize_feature_category(
                    normalized_name,
                    raw_category,
                ),
                reasoning=reasoning,
                metadata=metadata,
            )
        )
        normalized_names.add(normalized_name)

    if len(key_features) == 0:
        raise LlmEmptyExtractionError(
            stage="a1_key_symptom_extraction",
            prompt_name="a1_key_symptom_extraction",
            attempts=1,
            message="A1 未从当前患者上下文中抽取出任何关键特征。",
        )

    return A1ExtractionResult(
        key_features=key_features,
        selection_decision=str(payload.get("selection_decision", "selected") or "selected"),
        reasoning=str(payload.get("reasoning_summary", "已由 LLM 提取核心线索。") or "已由 LLM 提取核心线索。"),
        metadata={"source": "llm"},
    )


def _run_probe(replay_dir: Path) -> dict[str, Any]:
    config = load_frontend_config()
    apply_config_to_environment(config)
    llm_client = LlmClient()
    extractor = MedExtractor(llm_client)
    parser = EvidenceParser(llm_client=llm_client)

    results: dict[str, Any] = {
        "replay_dir": str(replay_dir),
        "llm_available": llm_client.is_available(),
        "cases": [],
    }
    for row in _load_replay_rows(replay_dir):
        opening_text = str(row.get("opening_text") or "")
        case_result: dict[str, Any] = {
            "case_id": row.get("case_id"),
            "case_title": row.get("case_title"),
            "opening_text": opening_text,
            "replay_status": row.get("status"),
            "replay_error": dict(row.get("error") or {}),
            "med_extractor_probe": {},
            "a1_probe": {},
        }

        try:
            med_payload = llm_client.run_structured_prompt(
                "med_extractor",
                {"patient_text": opening_text},
                dict,
            )
            case_result["med_extractor_probe"]["status"] = "ok"
            case_result["med_extractor_probe"]["raw_payload"] = med_payload
        except Exception as exc:
            case_result["med_extractor_probe"]["status"] = "error"
            case_result["med_extractor_probe"]["error_type"] = type(exc).__name__
            case_result["med_extractor_probe"]["error_message"] = str(exc)

        patient_context = None
        if "raw_payload" in case_result["med_extractor_probe"]:
            try:
                patient_context = extractor._coerce_llm_payload(  # type: ignore[attr-defined]
                    opening_text,
                    case_result["med_extractor_probe"]["raw_payload"],
                )
                case_result["med_extractor_probe"]["coerced_context"] = _serialize_patient_context(patient_context)
            except Exception as exc:
                case_result["med_extractor_probe"]["coerced_context_error_type"] = type(exc).__name__
                case_result["med_extractor_probe"]["coerced_context_error_message"] = str(exc)

        if patient_context is not None:
            try:
                a1_payload = llm_client.run_structured_prompt(
                    "a1_key_symptom_extraction",
                    {
                        "patient_context": patient_context,
                        "known_feature_names": [],
                    },
                    dict,
                )
                case_result["a1_probe"]["status"] = "ok"
                case_result["a1_probe"]["raw_payload"] = a1_payload
            except Exception as exc:
                case_result["a1_probe"]["status"] = "error"
                case_result["a1_probe"]["error_type"] = type(exc).__name__
                case_result["a1_probe"]["error_message"] = str(exc)

            if "raw_payload" in case_result["a1_probe"]:
                try:
                    a1_result = _coerce_a1_payload(
                        parser,
                        patient_context,
                        case_result["a1_probe"]["raw_payload"],
                    )
                    case_result["a1_probe"]["coerced_result"] = _serialize_a1_result(a1_result)
                except Exception as exc:
                    case_result["a1_probe"]["coerced_result_error_type"] = type(exc).__name__
                    case_result["a1_probe"]["coerced_result_error_message"] = str(exc)
        else:
            case_result["a1_probe"]["status"] = "skipped"
            case_result["a1_probe"]["reason"] = "med_extractor_context_unavailable"

        results["cases"].append(case_result)

    return results


def _build_summary(results: dict[str, Any]) -> dict[str, Any]:
    med_probe_status = Counter()
    a1_probe_status = Counter()
    replay_stage = Counter()
    med_empty_like = 0
    a1_empty_like = 0

    for case in results["cases"]:
        replay_stage[str(case["replay_error"].get("stage") or "none")] += 1
        med_probe_status[str(case["med_extractor_probe"].get("status") or "missing")] += 1
        a1_probe_status[str(case["a1_probe"].get("status") or "missing")] += 1

        med_payload = case["med_extractor_probe"].get("raw_payload")
        if isinstance(med_payload, dict):
            if med_payload.get("clinical_features") in ([], "", None):
                med_empty_like += 1

        a1_payload = case["a1_probe"].get("raw_payload")
        if isinstance(a1_payload, dict):
            if a1_payload.get("key_features") in ([], "", None):
                a1_empty_like += 1

    return {
        "case_count": len(results["cases"]),
        "llm_available": bool(results.get("llm_available")),
        "replay_stage_counts": dict(replay_stage),
        "med_probe_status_counts": dict(med_probe_status),
        "a1_probe_status_counts": dict(a1_probe_status),
        "med_raw_empty_like_count": med_empty_like,
        "a1_raw_empty_like_count": a1_empty_like,
    }


def _write_markdown_report(replay_dir: Path, results: dict[str, Any], summary: dict[str, Any]) -> Path:
    report_file = replay_dir / "llm_payload_audit_report.md"
    lines: list[str] = []
    lines.append("# smoke10 失败 opening 的 LLM payload 审计")
    lines.append("")
    lines.append("## 实验过程")
    lines.append("")
    lines.append("1. 读取当前 replay 目录下的 `replay_results.jsonl`。")
    lines.append("2. 对每条 failed opening 单独调用 `med_extractor` prompt，记录原始 payload。")
    lines.append("3. 若 `MedExtractor` 能成功构造 `PatientContext`，继续调用 `a1_key_symptom_extraction` prompt。")
    lines.append("4. 同时记录 raw payload 与业务层 coercion 后的结果或错误。")
    lines.append("")
    lines.append("## 实验摘要")
    lines.append("")
    lines.append(f"- `case_count`: {summary['case_count']}")
    lines.append(f"- `llm_available`: {summary['llm_available']}")
    lines.append(f"- `replay_stage_counts`: `{json.dumps(summary['replay_stage_counts'], ensure_ascii=False)}`")
    lines.append(f"- `med_probe_status_counts`: `{json.dumps(summary['med_probe_status_counts'], ensure_ascii=False)}`")
    lines.append(f"- `a1_probe_status_counts`: `{json.dumps(summary['a1_probe_status_counts'], ensure_ascii=False)}`")
    lines.append(f"- `med_raw_empty_like_count`: {summary['med_raw_empty_like_count']}")
    lines.append(f"- `a1_raw_empty_like_count`: {summary['a1_raw_empty_like_count']}")
    lines.append("")
    lines.append("## 单病例结果")
    lines.append("")
    for case in results["cases"]:
        lines.append(f"### {case['case_id']}")
        lines.append("")
        lines.append(f"- opening: `{case['opening_text']}`")
        lines.append(f"- replay_error: `{json.dumps(case['replay_error'], ensure_ascii=False)}`")
        lines.append(f"- med_extractor_probe_status: `{case['med_extractor_probe'].get('status')}`")
        if "error_type" in case["med_extractor_probe"]:
            lines.append(
                f"- med_extractor_probe_error: `{case['med_extractor_probe'].get('error_type')}: {case['med_extractor_probe'].get('error_message')}`"
            )
        if "raw_payload" in case["med_extractor_probe"]:
            lines.append(
                f"- med_extractor_raw_payload: `{json.dumps(case['med_extractor_probe']['raw_payload'], ensure_ascii=False)}`"
            )
        if "coerced_context_error_type" in case["med_extractor_probe"]:
            lines.append(
                f"- med_extractor_coerced_error: `{case['med_extractor_probe'].get('coerced_context_error_type')}: {case['med_extractor_probe'].get('coerced_context_error_message')}`"
            )
        if "coerced_context" in case["med_extractor_probe"]:
            lines.append(
                f"- med_extractor_coerced_context: `{json.dumps(case['med_extractor_probe']['coerced_context'], ensure_ascii=False)}`"
            )
        lines.append(f"- a1_probe_status: `{case['a1_probe'].get('status')}`")
        if "error_type" in case["a1_probe"]:
            lines.append(
                f"- a1_probe_error: `{case['a1_probe'].get('error_type')}: {case['a1_probe'].get('error_message')}`"
            )
        if "raw_payload" in case["a1_probe"]:
            lines.append(
                f"- a1_raw_payload: `{json.dumps(case['a1_probe']['raw_payload'], ensure_ascii=False)}`"
            )
        if "coerced_result_error_type" in case["a1_probe"]:
            lines.append(
                f"- a1_coerced_error: `{case['a1_probe'].get('coerced_result_error_type')}: {case['a1_probe'].get('coerced_result_error_message')}`"
            )
        if "coerced_result" in case["a1_probe"]:
            lines.append(
                f"- a1_coerced_result: `{json.dumps(case['a1_probe']['coerced_result'], ensure_ascii=False)}`"
            )
        lines.append("")

    report_file.write_text("\n".join(lines), encoding="utf-8")
    return report_file


def main() -> int:
    args = parse_args()
    replay_dir = Path(args.replay_dir)
    results = _run_probe(replay_dir)
    summary = _build_summary(results)

    audit_file = replay_dir / "llm_payload_audit.json"
    summary_file = replay_dir / "llm_payload_audit_summary.json"
    report_file = _write_markdown_report(replay_dir, results, summary)

    audit_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(str(audit_file))
    print(str(summary_file))
    print(str(report_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
