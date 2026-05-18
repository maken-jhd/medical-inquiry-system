"""保留旧导入路径的病例 schema 兼容壳。"""

from __future__ import annotations

from .cases.schema import BehaviorStyle, SlotTruth, VirtualPatientCase

__all__ = ["BehaviorStyle", "SlotTruth", "VirtualPatientCase"]
