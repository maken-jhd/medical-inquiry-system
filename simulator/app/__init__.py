"""对外暴露 simulator 稳定门面的统一入口。"""

from ..cases import SlotTruth, VirtualPatientCase, build_seed_cases, load_cases_jsonl, write_cases_json, write_cases_jsonl
from ..patient import (
    PatientAnswerDraft,
    PatientOpening,
    PatientOpeningDraft,
    PatientReply,
    VirtualPatientAgent,
)
from ..replay import ReplayConfig, ReplayEngine, ReplayResult, ReplayTurn

__all__ = [
    "PatientAnswerDraft",
    "PatientOpening",
    "PatientOpeningDraft",
    "PatientReply",
    "ReplayConfig",
    "ReplayEngine",
    "ReplayResult",
    "ReplayTurn",
    "SlotTruth",
    "VirtualPatientAgent",
    "VirtualPatientCase",
    "build_seed_cases",
    "load_cases_jsonl",
    "write_cases_json",
    "write_cases_jsonl",
]
