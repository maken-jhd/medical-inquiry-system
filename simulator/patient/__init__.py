"""封装虚拟病人回答、开场与问题匹配逻辑。"""

from .agent import VirtualPatientAgent
from .types import PatientAnswerDraft, PatientOpening, PatientOpeningDraft, PatientReply

__all__ = [
    "PatientAnswerDraft",
    "PatientOpening",
    "PatientOpeningDraft",
    "PatientReply",
    "VirtualPatientAgent",
]
