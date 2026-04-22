"""生成更贴近真实问诊场景的虚拟病人病例集。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List

from .case_schema import SlotTruth, VirtualPatientCase


# 便捷构造单个槽位真值，减少重复书写。
def _slot(
    node_id: str,
    value: object,
    *,
    mention_style: str = "direct",
    reveal_only_if_asked: bool = True,
    aliases: list[str] | None = None,
) -> SlotTruth:
    return SlotTruth(
        node_id=node_id,
        value=value,
        mention_style=mention_style,
        reveal_only_if_asked=reveal_only_if_asked,
        aliases=list(aliases or []),
    )


# 构造典型 PCP 病例。
def _build_pcp_typical_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="pcp_typical_001",
        title="典型 PCP 场景",
        true_disease_phase="AIDS期",
        true_conditions=["肺孢子菌肺炎 (PCP)"],
        chief_complaint="最近一直发热、干咳，走几步就气促。",
        behavior_style="guarded",
        slot_truth_map={
            "发热": _slot("发热", True, aliases=["发烧", "体温高"]),
            "干咳": _slot("干咳", True, aliases=["咳嗽"]),
            "呼吸困难": _slot("呼吸困难", True, aliases=["气促", "喘不上气"]),
            "低氧血症": _slot("低氧血症", True, mention_style="vague", aliases=["缺氧", "低氧"]),
            "高危性行为": _slot("高危性行为", True, mention_style="vague", aliases=["不安全性行为", "无保护性行为"]),
        },
        hidden_slots=["高危性行为"],
        red_flags=["低氧血症", "呼吸困难"],
        metadata={"age": 34, "sex": "男", "scenario_group": "机会性感染"},
    )


# 构造轻度 PCP 场景，用于测试模糊表达和再验证。
def _build_pcp_vague_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="pcp_vague_001",
        title="模糊表达的 PCP 场景",
        true_disease_phase="AIDS期",
        true_conditions=["肺孢子菌肺炎 (PCP)"],
        chief_complaint="最近总觉得有点低热，偶尔咳嗽，活动后有些喘。",
        behavior_style="vague",
        slot_truth_map={
            "发热": _slot("发热", True, mention_style="vague", aliases=["低热", "发烧"]),
            "干咳": _slot("干咳", True, mention_style="vague", aliases=["咳嗽"]),
            "呼吸困难": _slot("呼吸困难", True, mention_style="vague", aliases=["气促"]),
            "低氧血症": _slot("低氧血症", False, aliases=["缺氧"]),
        },
        red_flags=["呼吸困难"],
        metadata={"age": 42, "sex": "男", "scenario_group": "机会性感染"},
    )


# 构造活动性结核场景。
def _build_tb_active_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="tb_active_001",
        title="活动性结核病场景",
        true_disease_phase="AIDS期",
        true_conditions=["活动性结核病"],
        chief_complaint="咳嗽一个多月了，发热，晚上盗汗，体重也掉了不少。",
        behavior_style="cooperative",
        slot_truth_map={
            "发热": _slot("发热", True),
            "体重下降": _slot("体重下降", True, aliases=["消瘦", "变瘦"]),
            "干咳": _slot("干咳", True, aliases=["咳嗽"]),
            "高危性行为": _slot("高危性行为", True, mention_style="vague", aliases=["不安全性行为"]),
        },
        red_flags=["体重下降"],
        metadata={"age": 39, "sex": "男", "scenario_group": "结核"},
    )


# 构造潜伏结核或风险暴露场景。
def _build_tb_latent_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="tb_latent_001",
        title="潜伏结核感染风险筛查场景",
        true_disease_phase="无症状期",
        true_conditions=["潜伏性结核感染 (LTBI)"],
        chief_complaint="最近没什么特别不舒服，就是来做复查。",
        behavior_style="cooperative",
        slot_truth_map={
            "发热": _slot("发热", False),
            "干咳": _slot("干咳", False, aliases=["咳嗽"]),
            "体重下降": _slot("体重下降", False, aliases=["消瘦"]),
            "输血史": _slot("输血史", False),
        },
        metadata={"age": 28, "sex": "女", "scenario_group": "结核"},
    )


# 构造急性 HIV 感染场景。
def _build_acute_hiv_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="acute_hiv_001",
        title="急性 HIV 感染场景",
        true_disease_phase="急性期",
        true_conditions=["HIV感染"],
        chief_complaint="这两周突然发热、咽痛，还起了点皮疹。",
        behavior_style="concealing",
        slot_truth_map={
            "发热": _slot("发热", True),
            "咽痛": _slot("咽痛", True, aliases=["嗓子痛"]),
            "皮疹": _slot("皮疹", True, aliases=["红疹", "起疹子"]),
            "腹泻": _slot("腹泻", True, mention_style="vague", aliases=["拉肚子"]),
            "高危性行为": _slot("高危性行为", True, mention_style="vague", aliases=["不安全性行为", "无保护性行为"]),
        },
        hidden_slots=["高危性行为"],
        metadata={"age": 25, "sex": "男", "scenario_group": "HIV本身"},
    )


# 构造无症状但有明确高危行为的场景。
def _build_asymptomatic_risk_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="asymptomatic_risk_001",
        title="无症状但存在高危暴露场景",
        true_disease_phase="无症状期",
        true_conditions=["HIV感染"],
        chief_complaint="身体没什么异常，就是想咨询风险。",
        behavior_style="guarded",
        slot_truth_map={
            "发热": _slot("发热", False),
            "干咳": _slot("干咳", False, aliases=["咳嗽"]),
            "皮疹": _slot("皮疹", False),
            "高危性行为": _slot("高危性行为", True, mention_style="vague", aliases=["不安全性行为", "高危行为"]),
        },
        hidden_slots=["高危性行为"],
        metadata={"age": 31, "sex": "男", "scenario_group": "HIV本身"},
    )


# 构造口咽念珠菌病场景。
def _build_oral_candidiasis_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="oral_candidiasis_001",
        title="口咽念珠菌病场景",
        true_disease_phase="AIDS期",
        true_conditions=["口咽念珠菌病"],
        chief_complaint="最近嘴巴里总有白色东西，吃东西疼，还发热。",
        behavior_style="cooperative",
        slot_truth_map={
            "发热": _slot("发热", True),
            "口腔念珠菌感染": _slot("口腔念珠菌感染", True, aliases=["口咽念珠菌病", "口腔白斑"]),
            "吞咽疼痛": _slot("吞咽疼痛", True, aliases=["吃东西疼", "咽痛"]),
        },
        red_flags=["口腔念珠菌感染"],
        metadata={"age": 37, "sex": "女", "scenario_group": "机会性感染"},
    )


# 构造隐球菌性脑膜炎场景。
def _build_cryptococcal_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="cryptococcal_001",
        title="隐球菌性脑膜炎场景",
        true_disease_phase="AIDS期",
        true_conditions=["隐球菌性脑膜炎"],
        chief_complaint="这段时间头痛越来越明显，还发热、恶心。",
        behavior_style="cooperative",
        slot_truth_map={
            "发热": _slot("发热", True),
            "头痛": _slot("头痛", True, aliases=["持续头痛"]),
            "恶心": _slot("恶心", True),
            "呕吐": _slot("呕吐", True, mention_style="vague"),
        },
        red_flags=["头痛", "呕吐"],
        metadata={"age": 40, "sex": "男", "scenario_group": "机会性感染"},
    )


# 构造慢性肾脏病合并管理场景。
def _build_ckd_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="ckd_001",
        title="HIV 合并慢性肾脏病管理场景",
        true_disease_phase="慢性管理期",
        true_conditions=["慢性肾脏病"],
        chief_complaint="最近复查提示肾功能不好，想咨询后续抗病毒方案。",
        behavior_style="cooperative",
        slot_truth_map={
            "慢性肾脏病": _slot("慢性肾脏病", True, aliases=["肾功能异常", "肾功能不好"]),
            "高危性行为": _slot("高危性行为", False),
            "输血史": _slot("输血史", False),
        },
        metadata={"age": 48, "sex": "男", "scenario_group": "慢病共病"},
    )


# 构造 ART 后肥胖管理场景。
def _build_obesity_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="obesity_001",
        title="ART 后肥胖管理场景",
        true_disease_phase="慢性管理期",
        true_conditions=["肥胖"],
        chief_complaint="抗病毒治疗后体重涨得厉害，最近想控制体重。",
        behavior_style="cooperative",
        slot_truth_map={
            "体重下降": _slot("体重下降", False, aliases=["消瘦"]),
            "肥胖": _slot("肥胖", True, aliases=["体重增加", "发胖"]),
        },
        metadata={"age": 36, "sex": "女", "scenario_group": "慢病共病"},
    )


# 构造血脂异常管理场景。
def _build_dyslipidemia_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="dyslipidemia_001",
        title="HIV/AIDS 血脂异常管理场景",
        true_disease_phase="慢性管理期",
        true_conditions=["高血脂"],
        chief_complaint="最近体检提示血脂偏高，担心和抗病毒药有关。",
        behavior_style="cooperative",
        slot_truth_map={
            "高血脂": _slot("高血脂", True, aliases=["血脂异常", "血脂偏高"]),
            "体重下降": _slot("体重下降", False),
            "高危性行为": _slot("高危性行为", False),
        },
        metadata={"age": 45, "sex": "男", "scenario_group": "慢病共病"},
    )


# 构造 HIV 阳性孕产妇管理场景。
def _build_pregnancy_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="pregnancy_hiv_001",
        title="HIV 阳性孕产妇管理场景",
        true_disease_phase="孕产期管理",
        true_conditions=["HIV感染"],
        chief_complaint="现在怀孕了，之前查出 HIV 阳性，想知道后面怎么管理。",
        behavior_style="cooperative",
        slot_truth_map={
            "妊娠": _slot("妊娠", True, aliases=["怀孕", "孕期"]),
            "高危性行为": _slot("高危性行为", False),
            "输血史": _slot("输血史", False),
        },
        metadata={"age": 29, "sex": "女", "scenario_group": "特殊人群"},
    )


# 构造明显隐瞒风险史的场景。
def _build_concealing_risk_case() -> VirtualPatientCase:
    return VirtualPatientCase(
        case_id="concealing_risk_001",
        title="隐瞒高危行为场景",
        true_disease_phase="急性期",
        true_conditions=["HIV感染"],
        chief_complaint="最近有些发热、腹泻，但应该问题不大。",
        behavior_style="concealing",
        slot_truth_map={
            "发热": _slot("发热", True, mention_style="vague"),
            "腹泻": _slot("腹泻", True, mention_style="vague", aliases=["拉肚子"]),
            "高危性行为": _slot("高危性行为", True, mention_style="vague", aliases=["不安全性行为"]),
        },
        hidden_slots=["高危性行为"],
        metadata={"age": 27, "sex": "男", "scenario_group": "困难沟通"},
    )


# 生成一批覆盖典型场景的虚拟病人种子病例。
def build_seed_cases() -> List[VirtualPatientCase]:
    return [
        _build_pcp_typical_case(),
        _build_pcp_vague_case(),
        _build_tb_active_case(),
        _build_tb_latent_case(),
        _build_acute_hiv_case(),
        _build_asymptomatic_risk_case(),
        _build_oral_candidiasis_case(),
        _build_cryptococcal_case(),
        _build_ckd_case(),
        _build_obesity_case(),
        _build_dyslipidemia_case(),
        _build_pregnancy_case(),
        _build_concealing_risk_case(),
    ]


# 将病例列表序列化为 JSONL 文件，便于后续批量回放。
def write_cases_jsonl(cases: Iterable[VirtualPatientCase], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(asdict(case), ensure_ascii=False) + "\n")


# 将病例列表序列化为 JSON 数组文件，便于人工查看和外部程序消费。
def write_cases_json(cases: Iterable[VirtualPatientCase], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(case) for case in cases]
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# 从 JSONL 或 JSON 数组文件中读取病例列表，便于后续批量回放。
def load_cases_jsonl(input_file: Path) -> List[VirtualPatientCase]:
    raw_text = input_file.read_text(encoding="utf-8")
    stripped = raw_text.lstrip()

    if stripped.startswith("["):
        payload = json.loads(raw_text)
        if not isinstance(payload, list):
            raise ValueError(f"病例 JSON 文件不是数组：{input_file}")
        return [_deserialize_case(data) for data in payload if isinstance(data, dict)]

    cases: List[VirtualPatientCase] = []

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()

        if len(line) == 0:
            continue

        data = json.loads(line)
        if not isinstance(data, dict):
            continue
        cases.append(_deserialize_case(data))

    return cases


def _deserialize_case(data: dict) -> VirtualPatientCase:
    slot_truth_map = {
        key: SlotTruth(**value)
        for key, value in data.get("slot_truth_map", {}).items()
    }
    data = dict(data)
    data["slot_truth_map"] = slot_truth_map
    return VirtualPatientCase(**data)
