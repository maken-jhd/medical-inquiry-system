"""管理 Streamlit 演示前端内置的离线回放病例。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEMO_DIR = Path(__file__).resolve().parent / "demo_replays"


@dataclass(frozen=True)
class DemoReplay:
    """表示一个可在前端加载的离线演示病例。"""

    key: str
    title: str
    description: str
    path: Path


def list_demo_replays() -> list[DemoReplay]:
    """返回内置 demo 列表，供 Streamlit 下拉框使用。"""

    return [
        DemoReplay(
            key="pcp_provisional_success",
            title="示例 1：PCP 模糊证据逐步收敛",
            description="展示系统如何通过 provisional evidence family 接受正确 PCP 结论。",
            path=DEMO_DIR / "pcp_provisional_success.json",
        ),
        DemoReplay(
            key="tb_vs_pcp_safety_gate",
            title="示例 2：PCP 与结核混淆时的安全拒停",
            description="展示复核器与安全闸门如何阻止过早接受 PCP。",
            path=DEMO_DIR / "tb_vs_pcp_safety_gate.json",
        ),
    ]


def get_demo_by_key(key: str) -> DemoReplay:
    """根据 key 返回 demo；若不存在则回退到第一个示例。"""

    demos = list_demo_replays()
    for demo in demos:
        if demo.key == key:
            return demo
    return demos[0]

