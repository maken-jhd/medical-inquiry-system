"""虚拟病人与离线回放模块的统一导出入口。"""

from .case_schema import VirtualPatientCase
from .patient_agent import VirtualPatientAgent

__all__ = ["VirtualPatientAgent", "VirtualPatientCase"]
