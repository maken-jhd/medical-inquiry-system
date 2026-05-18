"""保留旧导入路径的虚拟病人兼容壳。"""

from __future__ import annotations

from .patient.agent import VirtualPatientAgent
from .patient.types import PatientAnswerDraft, PatientOpening, PatientOpeningDraft, PatientReply

__all__ = [
    "PatientAnswerDraft",
    "PatientOpening",
    "PatientOpeningDraft",
    "PatientReply",
    "VirtualPatientAgent",
]
