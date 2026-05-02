"""证据节点 family 分类与疾病最低证据组建议。"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Sequence


SYMPTOM_FAMILY_LABELS: dict[str, str] = {
    "respiratory_symptom": "呼吸系统症状",
    "neurologic_symptom": "神经系统症状",
    "constitutional_symptom": "全身症状",
    "gastrointestinal_symptom": "消化系统症状",
    "dermatologic_symptom": "皮肤黏膜症状",
    "oral_ent_symptom": "口腔/耳鼻咽喉症状",
    "lymphatic_symptom": "淋巴结相关症状",
    "genitourinary_symptom": "泌尿生殖系统症状",
    "musculoskeletal_symptom": "肌肉骨骼症状",
    "cardiovascular_symptom": "心血管相关症状",
    "hematologic_symptom": "血液/出血相关症状",
    "ocular_symptom": "眼部症状",
    "metabolic_symptom": "代谢/体重相关症状",
    "mental_symptom": "精神心理/睡眠症状",
    "severe_systemic_symptom": "重症/器官衰竭线索",
    "immune_status": "免疫状态/免疫抑制线索",
    "worsening": "病情进展/恶化线索",
    "general_symptom": "未细分症状",
}

NON_SYMPTOM_FAMILY_LABELS: dict[str, str] = {
    "underlying_infection": "基础感染/感染背景",
    "art_or_reconstitution": "ART/免疫重建线索",
    "exposure_risk": "暴露/行为风险",
    "medication_risk": "用药相关风险",
    "comorbidity_risk": "合并症/既往史风险",
    "population_risk": "人群/年龄/妊娠风险",
    "metabolic_definition": "代谢核心定义证据",
    "viral_load": "病毒载量证据",
    "oxygenation": "氧合/低氧证据",
    "fungal_marker": "真菌标志物证据",
    "disease_specific_lab": "疾病特异实验室证据",
    "cns_lab": "脑脊液/中枢实验室证据",
    "inflammatory_marker": "炎症指标证据",
    "blood_count": "血常规/血细胞证据",
    "liver_renal_function": "肝肾功能证据",
    "serology": "血清学/抗体证据",
    "pathology": "病理/活检证据",
    "general_lab": "未细分实验室证据",
    "imaging": "影像学证据",
    "pulmonary_imaging": "肺部影像证据",
    "cns_imaging": "中枢神经影像证据",
    "abdominal_imaging": "腹部影像证据",
    "lymph_node_imaging": "淋巴结影像证据",
    "cardiovascular_imaging": "心血管影像证据",
    "bone_imaging": "骨骼影像证据",
    "pathogen": "病原体证据",
    "fungal_pathogen": "真菌病原体证据",
    "mycobacterial_pathogen": "分枝杆菌病原体证据",
    "viral_pathogen": "病毒病原体证据",
    "parasitic_pathogen": "寄生虫病原体证据",
    "bacterial_pathogen": "细菌病原体证据",
    "onset_timing": "起病/病程时间线索",
    "severity": "严重程度/分级线索",
    "treatment_response": "治疗反应/耐受线索",
    "location_detail": "部位/范围细节",
    "general_risk": "未细分风险证据",
    "general_detail": "未细分细节证据",
}

EVIDENCE_FAMILY_LABELS: dict[str, str] = {
    **SYMPTOM_FAMILY_LABELS,
    **NON_SYMPTOM_FAMILY_LABELS,
}

EVIDENCE_GROUP_LABELS: dict[str, str] = {
    "symptom": "症状/体征",
    "risk": "风险/人群背景",
    "detail": "病程/细节属性",
    "lab": "实验室检查",
    "imaging": "影像检查",
    "pathogen": "病原学",
}

GENERIC_FAMILIES = {
    "general_symptom",
    "general_lab",
    "general_risk",
    "general_detail",
}

LABEL_TO_EVIDENCE_GROUP: dict[str, str] = {
    "ClinicalFinding": "symptom",
    "RiskFactor": "risk",
    "PopulationGroup": "risk",
    "ClinicalAttribute": "detail",
    "LabFinding": "lab",
    "LabTest": "lab",
    "ImagingFinding": "imaging",
    "Pathogen": "pathogen",
}

FAMILY_PRIORITY: dict[str, int] = {
    "fungal_pathogen": 104,
    "mycobacterial_pathogen": 104,
    "viral_pathogen": 104,
    "parasitic_pathogen": 104,
    "bacterial_pathogen": 104,
    "disease_specific_lab": 102,
    "pathogen": 100,
    "immune_status": 98,
    "viral_load": 98,
    "oxygenation": 98,
    "fungal_marker": 98,
    "cns_lab": 98,
    "worsening": 96,
    "respiratory_symptom": 94,
    "neurologic_symptom": 94,
    "pulmonary_imaging": 92,
    "cns_imaging": 92,
    "abdominal_imaging": 90,
    "lymph_node_imaging": 90,
    "cardiovascular_imaging": 90,
    "bone_imaging": 90,
    "imaging": 88,
    "severe_systemic_symptom": 88,
    "underlying_infection": 88,
    "art_or_reconstitution": 88,
    "metabolic_symptom": 86,
    "metabolic_definition": 86,
    "pathology": 84,
    "serology": 84,
    "inflammatory_marker": 83,
    "dermatologic_symptom": 82,
    "blood_count": 82,
    "gastrointestinal_symptom": 80,
    "liver_renal_function": 80,
    "genitourinary_symptom": 78,
    "onset_timing": 78,
    "ocular_symptom": 76,
    "severity": 76,
    "oral_ent_symptom": 74,
    "treatment_response": 74,
    "hematologic_symptom": 72,
    "exposure_risk": 72,
    "lymphatic_symptom": 70,
    "medication_risk": 70,
    "comorbidity_risk": 69,
    "cardiovascular_symptom": 68,
    "population_risk": 68,
    "musculoskeletal_symptom": 66,
    "location_detail": 64,
    "mental_symptom": 62,
    "constitutional_symptom": 50,
    "general_lab": 10,
    "general_risk": 8,
    "general_detail": 6,
    "general_symptom": 1,
}

SYMPTOM_FAMILY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "respiratory_symptom",
        (
            "咳",
            "咳嗽",
            "干咳",
            "咳痰",
            "咯血",
            "呼吸",
            "气促",
            "气短",
            "喘",
            "胸闷",
            "胸痛",
            "低氧",
            "紫绀",
            "发绀",
            "呼吸窘迫",
            "肺部",
            "粘液痰",
            "黏液痰",
            "肺炎",
        ),
    ),
    (
        "neurologic_symptom",
        (
            "头痛",
            "癫痫",
            "抽搐",
            "偏瘫",
            "意识",
            "认知",
            "失语",
            "神经",
            "脑膜",
            "颈强直",
            "喷射性呕吐",
            "畏光",
            "昏迷",
            "谵妄",
            "肢体无力",
            "麻木",
            "感觉障碍",
            "偏盲",
            "共济失调",
            "嗜睡",
            "昏睡",
            "步态",
            "痴呆",
            "眩晕",
            "行为变化",
            "言语异常",
            "记忆力",
            "辨距",
            "迟钝",
            "面瘫",
        ),
    ),
    (
        "constitutional_symptom",
        (
            "发热",
            "高热",
            "低热",
            "寒战",
            "盗汗",
            "夜汗",
            "乏力",
            "疲乏",
            "消瘦",
            "体重下降",
            "食欲下降",
            "纳差",
            "倦怠",
            "全身不适",
            "厌食",
            "生长停滞",
        ),
    ),
    (
        "gastrointestinal_symptom",
        (
            "腹泻",
            "腹痛",
            "恶心",
            "呕吐",
            "吞咽",
            "食欲",
            "便血",
            "黑便",
            "黄疸",
            "肝脾",
            "肝大",
            "脾大",
            "腹胀",
            "腹水",
            "厌食",
            "呕血",
            "大便",
            "水样便",
            "消化道",
            "直肠",
            "肛门",
            "穿孔",
            "肠梗阻",
            "胃肠",
            "腹部",
            "血水样便",
            "胸骨后",
        ),
    ),
    (
        "dermatologic_symptom",
        (
            "皮疹",
            "皮损",
            "丘疹",
            "水疱",
            "瘙痒",
            "溃疡",
            "脓肿",
            "结节",
            "斑丘疹",
            "糜烂",
            "色素",
            "红斑",
            "紫癜",
            "脱屑",
            "皮肤损害",
            "斑疹",
            "斑片",
            "黏膜受累",
            "注射部位硬结",
        ),
    ),
    (
        "oral_ent_symptom",
        (
            "口腔",
            "咽",
            "咽喉",
            "吞咽痛",
            "白斑",
            "鹅口疮",
            "舌",
            "口疮",
            "口角",
            "声音嘶哑",
            "鼻",
            "耳",
            "口唇",
            "口干",
            "味觉",
            "嗅觉",
        ),
    ),
    (
        "lymphatic_symptom",
        (
            "淋巴",
            "淋巴结",
            "肿大",
            "腺病",
        ),
    ),
    (
        "genitourinary_symptom",
        (
            "尿",
            "排尿",
            "尿频",
            "尿痛",
            "血尿",
            "阴道",
            "外阴",
            "生殖",
            "阴茎",
            "宫颈",
            "分泌物",
            "会阴",
        ),
    ),
    (
        "musculoskeletal_symptom",
        (
            "关节",
            "肌痛",
            "肌肉",
            "骨痛",
            "腰痛",
            "背痛",
            "肌无力",
            "畸形",
            "骨折",
            "身高下降",
            "背部疼痛",
        ),
    ),
    (
        "cardiovascular_symptom",
        (
            "心悸",
            "胸痛",
            "水肿",
            "晕厥",
            "血压",
            "收缩压",
            "舒张压",
            "心衰",
            "心力衰竭",
        ),
    ),
    (
        "hematologic_symptom",
        (
            "贫血",
            "出血",
            "瘀斑",
            "淤血",
            "紫癜",
        ),
    ),
    (
        "ocular_symptom",
        (
            "视力",
            "视物",
            "眼",
            "畏光",
            "眼痛",
            "视野",
            "飞蚊",
            "偏盲",
            "复视",
            "盲点",
            "视觉",
            "闪光",
        ),
    ),
    (
        "metabolic_symptom",
        (
            "体重增加",
            "肥胖",
            "多饮",
            "多尿",
            "多食",
            "血脂",
            "脂肪",
            "腰围",
            "体重难以控制",
        ),
    ),
    (
        "mental_symptom",
        (
            "焦虑",
            "抑郁",
            "睡眠",
            "失眠",
            "精神",
            "情绪",
            "幻觉",
            "激越",
        ),
    ),
    (
        "severe_systemic_symptom",
        (
            "多器官衰竭",
            "危重",
            "重型",
            "衰竭",
        ),
    ),
)


def normalize_text(value: str) -> str:
    """将中文和英文医学短语归一成便于关键词匹配的紧凑字符串。"""

    text = value.strip().lower()
    return re.sub(r"[\s\u3000]+", "", text)


def iter_string_values(value: Any) -> Iterable[str]:
    """递归展开节点属性中可能参与分类的字符串。"""

    if value is None:
        return
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            yield stripped
        return
    if isinstance(value, Mapping):
        for nested_value in value.values():
            yield from iter_string_values(nested_value)
        return
    if isinstance(value, (list, tuple, set)):
        for nested_value in value:
            yield from iter_string_values(nested_value)
        return
    if isinstance(value, (int, float)):
        yield str(value)


def build_node_search_text(node: Mapping[str, Any]) -> str:
    """拼接证据节点名称、别名和属性，用于轻量规则分类。"""

    parts: list[str] = []
    for key in (
        "evidence_name",
        "symptom_name",
        "target_name",
        "node_name",
        "name",
        "canonical_name",
        "aliases",
        "evidence_aliases",
        "symptom_aliases",
        "attributes",
        "description",
        "relation_type",
    ):
        parts.extend(iter_string_values(node.get(key)))
    return normalize_text(" ".join(parts))


def classify_symptom_families(node: Mapping[str, Any]) -> list[str]:
    """把一个 ClinicalFinding 症状节点归入一个或多个最低症状证据族。"""

    search_text = build_node_search_text(node)
    families: set[str] = set()

    for family, keywords in SYMPTOM_FAMILY_KEYWORDS:
        if any(normalize_text(keyword) in search_text for keyword in keywords):
            families.add(family)

    # 这些线索跨器官系统，作为附加证据族保留。
    if any(term in search_text for term in ("hiv", "aids", "艾滋", "免疫抑制", "免疫功能低下", "cd4")):
        families.add("immune_status")
    if any(term in search_text for term in ("恶化", "加重", "无改善", "新发", "进展", "复发", "新病灶", "活动性感染")):
        families.add("worsening")

    if not families:
        families.add("general_symptom")

    return sorted(families, key=family_sort_key)


def infer_evidence_group(item: Mapping[str, Any]) -> str:
    """根据 label / relation_type 判断证据所属大组。"""

    explicit_group = str(item.get("evidence_group") or item.get("group") or "").strip()
    if explicit_group in EVIDENCE_GROUP_LABELS:
        return explicit_group

    label = str(
        item.get("evidence_label")
        or item.get("target_label")
        or item.get("symptom_label")
        or item.get("label")
        or ""
    ).strip()
    if label in LABEL_TO_EVIDENCE_GROUP:
        return LABEL_TO_EVIDENCE_GROUP[label]

    relation_type = str(item.get("relation_type") or "").strip()
    if relation_type == "MANIFESTS_AS":
        return "symptom"
    if relation_type == "HAS_LAB_FINDING" or relation_type == "DIAGNOSED_BY":
        return "lab"
    if relation_type == "HAS_IMAGING_FINDING":
        return "imaging"
    if relation_type == "HAS_PATHOGEN":
        return "pathogen"
    if relation_type in {"RISK_FACTOR_FOR", "APPLIES_TO"}:
        return "risk"
    if relation_type == "REQUIRES_DETAIL":
        return "detail"
    return ""


def classify_evidence_families(item: Mapping[str, Any]) -> list[str]:
    """把任一问诊证据节点归入可审计 evidence family。"""

    group = infer_evidence_group(item)
    if group == "symptom":
        return classify_symptom_families(item)

    search_text = build_node_search_text(item)
    families: set[str] = set()

    # 跨组通用线索先统一收口，避免 CD4 / ART / HIV 在不同 label 下被分散。
    if any(term in search_text for term in ("hiv", "aids", "艾滋", "免疫抑制", "免疫功能低下", "cd4")):
        families.add("immune_status")
    if any(term in search_text for term in ("hiv感染", "hiv阳性", "aids", "艾滋", "结核感染", "潜伏感染", "机会性感染")):
        families.add("underlying_infection")
    if any(term in search_text for term in ("art", "抗逆转录病毒", "抗病毒治疗", "免疫重建", "启动治疗", "启动时间")):
        families.add("art_or_reconstitution")
    if any(term in search_text for term in ("恶化", "加重", "无改善", "新发", "进展", "复发", "新病灶", "活动性感染")):
        families.add("worsening")
    if any(term in search_text for term in ("bmi", "身体质量指数", "体脂", "腰围", "肥胖", "体重增加", "体重难以控制")):
        families.add("metabolic_definition")

    if group == "risk":
        families.update(_classify_risk_families(search_text))
    elif group == "detail":
        families.update(_classify_detail_families(search_text))
    elif group == "lab":
        families.update(_classify_lab_families(search_text))
    elif group == "imaging":
        families.update(_classify_imaging_families(search_text))
    elif group == "pathogen":
        families.update(_classify_pathogen_families(search_text))

    if not families:
        fallback = {
            "risk": "general_risk",
            "detail": "general_detail",
            "lab": "general_lab",
            "imaging": "imaging",
            "pathogen": "pathogen",
        }.get(group, "general_detail")
        families.add(fallback)

    return sorted(families, key=family_sort_key)


def _classify_risk_families(search_text: str) -> set[str]:
    families: set[str] = set()
    if any(
        term in search_text
        for term in (
            "男男性行为",
            "msm",
            "性接触",
            "性传播",
            "性伴",
            "无保护",
            "吸烟",
            "饮酒",
            "接触史",
            "密切接触",
            "接触",
            "暴露",
            "共用针具",
            "流行地区",
            "性工作者",
            "prep",
            "pep",
        )
    ):
        families.add("exposure_risk")
    if any(
        term in search_text
        for term in (
            "依非韦伦",
            "洛匹那韦",
            "利托那韦",
            "蛋白酶抑制剂",
            "整合酶抑制剂",
            "激素",
            "糖皮质激素",
            "免疫抑制剂",
            "利奈唑胺",
            "替诺福韦",
            "异烟肼",
            "氟喹诺酮",
            "环丝氨酸",
            "齐多夫定",
            "广谱抗菌",
            "肾毒性药物",
            "药物使用",
            "使用",
        )
    ):
        families.add("medication_risk")
    if any(
        term in search_text
        for term in ("儿童", "婴儿", "老年", "年龄", "妊娠", "孕", "产后", "人群", "妇女", "女性", "男性", "患者", "受者", "使用者")
    ):
        families.add("population_risk")
    if any(
        term in search_text
        for term in (
            "合并",
            "感染者",
            "病史",
            "家族史",
            "肿瘤",
            "骨折史",
            "器官移植",
            "肾损伤",
            "功能亢进",
            "功能减退",
            "吸收不良",
            "曲菌感染",
            "结核病",
            "骨质疏松",
            "心血管疾病",
        )
    ):
        families.add("comorbidity_risk")
    if not families:
        families.add("general_risk")
    return families


def _classify_detail_families(search_text: str) -> set[str]:
    families: set[str] = set()
    if any(term in search_text for term in ("免疫功能", "cd4", "aids", "hiv")):
        families.add("immune_status")
    if any(term in search_text for term in ("合并感染", "感染状态", "感染背景")):
        families.add("underlying_infection")
    if any(term in search_text for term in ("暴露强度", "暴露史", "接触强度")):
        families.add("exposure_risk")
    if any(term in search_text for term in ("性别", "年龄", "妊娠", "孕")):
        families.add("population_risk")
    if any(term in search_text for term in ("ascvd", "ldl", "血脂", "胆固醇", "风险等级")):
        families.add("metabolic_definition")
    if any(term in search_text for term in ("时间", "近期", "持续", "病程", "起病", "急性", "慢性", "天", "周", "月", "年")):
        families.add("onset_timing")
    if any(term in search_text for term in ("严重", "重型", "危重", "分级", "程度", "评分", "大量", "明显", "压迫")):
        families.add("severity")
    if any(term in search_text for term in ("疗效", "耐受", "反应", "无改善", "治疗失败", "复发")):
        families.add("treatment_response")
    if any(term in search_text for term in ("部位", "范围", "双肺", "单侧", "局部", "全身", "皮损", "病灶")):
        families.add("location_detail")
    if not families:
        families.add("general_detail")
    return families


def _classify_lab_families(search_text: str) -> set[str]:
    families: set[str] = set()
    if any(term in search_text for term in ("hivrna", "hbvdna", "病毒载量", "viral", "rna载量", "dna定量")):
        families.add("viral_load")
    if any(term in search_text for term in ("低氧", "血氧", "氧分压", "pao", "spo", "fio", "氧合")):
        families.add("oxygenation")
    if any(term in search_text for term in ("βd葡聚糖", "bdg", "葡聚糖", "g试验", "gm试验", "乳酸脱氢酶", "ldh")):
        families.add("fungal_marker")
    if any(term in search_text for term in ("脑脊液", "csf", "墨汁染色", "颅压")):
        families.add("cns_lab")
    if any(
        term in search_text
        for term in (
            "pcr",
            "核酸",
            "dna检测",
            "rna检测",
            "dna阳性",
            "rna阳性",
            "抗原阳性",
            "培养阳性",
            "检测阳性",
            "阳性",
            "检出",
            "病原",
            "培养",
            "测序",
            "分子检测",
            "lamp",
            "xpert",
            "truenat",
            "lf-lam",
            "涂片",
            "药敏",
            "药物敏感",
            "敏感性检测",
        )
    ):
        families.add("disease_specific_lab")
    if any(term in search_text for term in ("抗体", "血清学", "igm", "igg", "抗原", "隐球菌抗原")):
        families.add("serology")
    if any(term in search_text for term in ("crp", "c反应蛋白", "esr", "血沉", "pct", "降钙素", "炎症")):
        families.add("inflammatory_marker")
    if any(term in search_text for term in ("白细胞", "中性粒", "淋巴细胞", "血红蛋白", "血小板", "贫血", "全血细胞")):
        families.add("blood_count")
    if any(term in search_text for term in ("alt", "ast", "转氨酶", "胆红素", "肌酐", "尿素", "肝功能", "肾功能", "egfr", "尿常规", "acr")):
        families.add("liver_renal_function")
    if any(term in search_text for term in ("ldl", "hdl", "甘油三酯", "总胆固醇", "血脂", "胆固醇", "血糖", "糖化血红蛋白", "t值", "z值")):
        families.add("metabolic_definition")
    if any(term in search_text for term in ("病理", "活检", "组织学", "细胞学")):
        families.add("pathology")
    if not families:
        families.add("general_lab")
    return families


def _classify_imaging_families(search_text: str) -> set[str]:
    families = {"imaging"}
    if any(term in search_text for term in ("肺", "胸", "磨玻璃", "结节", "空洞", "浸润", "间质", "纵隔")):
        families.add("pulmonary_imaging")
    if any(term in search_text for term in ("脑", "颅", "中枢", "脑膜", "白质", "环形强化", "占位", "脑室")):
        families.add("cns_imaging")
    if any(term in search_text for term in ("腹", "肝", "脾", "肠", "胃", "胆", "胰", "腹腔")):
        families.add("abdominal_imaging")
    if any(term in search_text for term in ("淋巴", "纵隔淋巴")):
        families.add("lymph_node_imaging")
    if any(term in search_text for term in ("冠脉", "心脏", "血管", "动脉", "静脉")):
        families.add("cardiovascular_imaging")
    if any(term in search_text for term in ("骨", "椎", "骨密度", "骨折")):
        families.add("bone_imaging")
    return families


def _classify_pathogen_families(search_text: str) -> set[str]:
    families = {"pathogen"}
    if any(term in search_text for term in ("肺孢子", "曲霉", "隐球菌", "念珠菌", "组织胞浆", "马尔尼菲", "真菌", "histoplasma", "cryptococcus", "candida", "aspergillus")):
        families.add("fungal_pathogen")
    if any(term in search_text for term in ("结核", "分枝杆菌", "ntm", "鸟分枝", "脓肿分枝", "偶然分枝", "龟分枝", "mycobacter")):
        families.add("mycobacterial_pathogen")
    if any(term in search_text for term in ("巨细胞", "cmv", "hsv", "疱疹", "水痘", "带状疱疹", "hpv", "hbv", "新冠", "sars", "病毒", "hiv")):
        families.add("viral_pathogen")
    if any(term in search_text for term in ("弓形虫", "利什曼", "黑热", "寄生虫", "toxoplasma", "leishmania")):
        families.add("parasitic_pathogen")
    if any(term in search_text for term in ("细菌", "金黄色葡萄球菌", "链球菌", "杆菌")):
        families.add("bacterial_pathogen")
    return families


def family_sort_key(family: str) -> tuple[int, str]:
    """证据族排序：更适合作为最低证据组的类别排在前面。"""

    return (-FAMILY_PRIORITY.get(family, 0), family)


def build_disease_symptom_catalog(
    diseases: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    *,
    max_groups_per_disease: int = 4,
) -> dict[str, Any]:
    """根据 Disease-ClinicalFinding 边构建症状族与疾病最低组建议。"""

    disease_by_id = {str(item.get("disease_id") or ""): dict(item) for item in diseases}
    edges_by_disease: dict[str, list[dict[str, Any]]] = defaultdict(list)
    symptom_by_id: dict[str, dict[str, Any]] = {}
    symptom_disease_ids: dict[str, set[str]] = defaultdict(set)

    # 先给每条疾病-症状边补上 symptom family，后续按疾病和按症状两种视角聚合。
    for edge in edges:
        disease_id = str(edge.get("disease_id") or "")
        symptom_id = str(edge.get("symptom_id") or "")
        if not disease_id or not symptom_id:
            continue

        enriched_edge = dict(edge)
        enriched_edge["families"] = classify_symptom_families(enriched_edge)
        edges_by_disease[disease_id].append(enriched_edge)
        symptom_by_id.setdefault(
            symptom_id,
            {
                "symptom_id": symptom_id,
                "symptom_name": str(edge.get("symptom_name") or ""),
                "symptom_label": str(edge.get("symptom_label") or "ClinicalFinding"),
                "aliases": list(edge.get("symptom_aliases") or []),
                "families": enriched_edge["families"],
            },
        )
        symptom_disease_ids[symptom_id].add(disease_id)

    symptom_nodes = []
    for symptom_id, symptom in sorted(symptom_by_id.items(), key=lambda item: item[1]["symptom_name"]):
        disease_ids = sorted(symptom_disease_ids.get(symptom_id) or [])
        symptom_nodes.append(
            {
                **symptom,
                "disease_count": len(disease_ids),
                "diseases": [
                    {
                        "disease_id": disease_id,
                        "disease_name": str(disease_by_id.get(disease_id, {}).get("disease_name") or ""),
                    }
                    for disease_id in disease_ids
                ],
            }
        )

    disease_records = []
    for disease_id, disease in sorted(disease_by_id.items(), key=lambda item: str(item[1].get("disease_name") or "")):
        symptom_edges = sorted(
            edges_by_disease.get(disease_id) or [],
            key=lambda item: (str(item.get("symptom_name") or ""), str(item.get("symptom_id") or "")),
        )
        family_counts = _count_families(symptom_edges)
        disease_records.append(
            {
                **disease,
                "symptom_count": len({str(item.get("symptom_id") or "") for item in symptom_edges}),
                "symptom_family_counts": dict(sorted(family_counts.items(), key=lambda item: family_sort_key(item[0]))),
                "symptom_family_coverage": sorted(family_counts, key=family_sort_key),
                "minimum_evidence_groups": suggest_minimum_evidence_groups(
                    family_counts,
                    max_groups_per_disease=max_groups_per_disease,
                ),
                "symptoms": symptom_edges,
            }
        )

    return {
        "scope": "disease_clinical_finding_only",
        "disease_count": len(disease_records),
        "symptom_node_count": len(symptom_nodes),
        "disease_symptom_edge_count": len(edges),
        "family_labels": SYMPTOM_FAMILY_LABELS,
        "symptom_family_node_counts": _count_symptom_nodes_by_family(symptom_nodes),
        "diseases": disease_records,
        "symptom_nodes": symptom_nodes,
        "unclassified_symptom_nodes": [
            symptom
            for symptom in symptom_nodes
            if symptom.get("families") == ["general_symptom"]
        ],
    }


def build_disease_evidence_catalog(
    diseases: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    *,
    max_groups_per_disease: int = 8,
    max_groups_per_evidence_group: int = 2,
) -> dict[str, Any]:
    """根据 Disease 与各类证据边构建 full evidence family 目录。"""

    disease_by_id = {str(item.get("disease_id") or ""): dict(item) for item in diseases}
    edges_by_disease: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evidence_by_id: dict[str, dict[str, Any]] = {}
    evidence_disease_ids: dict[str, set[str]] = defaultdict(set)

    # 将每条疾病-证据边归入 evidence group 和 family，供疾病视角与证据视角复用。
    for edge in edges:
        disease_id = str(edge.get("disease_id") or "")
        evidence_id = str(edge.get("evidence_id") or edge.get("symptom_id") or "")
        if not disease_id or not evidence_id:
            continue

        enriched_edge = dict(edge)
        evidence_group = infer_evidence_group(enriched_edge)
        enriched_edge["evidence_group"] = evidence_group
        enriched_edge["families"] = classify_evidence_families(enriched_edge)
        edges_by_disease[disease_id].append(enriched_edge)
        evidence_by_id.setdefault(
            evidence_id,
            {
                "evidence_id": evidence_id,
                "evidence_name": str(edge.get("evidence_name") or edge.get("symptom_name") or ""),
                "evidence_label": str(edge.get("evidence_label") or edge.get("symptom_label") or ""),
                "evidence_group": evidence_group,
                "aliases": list(edge.get("evidence_aliases") or edge.get("symptom_aliases") or []),
                "families": enriched_edge["families"],
            },
        )
        evidence_disease_ids[evidence_id].add(disease_id)

    evidence_nodes = []
    for evidence_id, evidence in sorted(evidence_by_id.items(), key=lambda item: item[1]["evidence_name"]):
        disease_ids = sorted(evidence_disease_ids.get(evidence_id) or [])
        evidence_nodes.append(
            {
                **evidence,
                "disease_count": len(disease_ids),
                "diseases": [
                    {
                        "disease_id": disease_id,
                        "disease_name": str(disease_by_id.get(disease_id, {}).get("disease_name") or ""),
                    }
                    for disease_id in disease_ids
                ],
            }
        )

    disease_records = []
    for disease_id, disease in sorted(disease_by_id.items(), key=lambda item: str(item[1].get("disease_name") or "")):
        disease_edges = sorted(
            edges_by_disease.get(disease_id) or [],
            key=lambda item: (
                str(item.get("evidence_group") or ""),
                str(item.get("evidence_name") or ""),
                str(item.get("evidence_id") or ""),
            ),
        )
        family_counts = _count_families(disease_edges)
        group_counts = Counter(str(item.get("evidence_group") or "") for item in disease_edges if item.get("evidence_group"))
        family_counts_by_group = _count_families_by_evidence_group(disease_edges)
        disease_records.append(
            {
                **disease,
                "evidence_count": len({str(item.get("evidence_id") or "") for item in disease_edges}),
                "evidence_counts_by_group": dict(sorted(group_counts.items())),
                "evidence_family_counts": dict(sorted(family_counts.items(), key=lambda item: family_sort_key(item[0]))),
                "evidence_family_counts_by_group": family_counts_by_group,
                "evidence_family_coverage": sorted(family_counts, key=family_sort_key),
                "minimum_evidence_groups": suggest_minimum_evidence_groups(
                    family_counts,
                    max_groups_per_disease=max_groups_per_disease,
                ),
                "minimum_evidence_groups_by_evidence_group": suggest_minimum_evidence_groups_by_evidence_group(
                    family_counts_by_group,
                    max_groups_per_evidence_group=max_groups_per_evidence_group,
                ),
                "evidence": disease_edges,
            }
        )

    return {
        "scope": "disease_evidence_full",
        "disease_count": len(disease_records),
        "evidence_node_count": len(evidence_nodes),
        "disease_evidence_edge_count": len(edges),
        "family_labels": EVIDENCE_FAMILY_LABELS,
        "evidence_group_labels": EVIDENCE_GROUP_LABELS,
        "evidence_node_count_by_group": _count_evidence_nodes_by_group(evidence_nodes),
        "evidence_family_node_counts": _count_evidence_nodes_by_family(evidence_nodes),
        "diseases": disease_records,
        "evidence_nodes": evidence_nodes,
        "unclassified_evidence_nodes": [
            evidence
            for evidence in evidence_nodes
            if set(evidence.get("families") or []).issubset(GENERIC_FAMILIES)
        ],
    }


def suggest_minimum_evidence_groups(
    family_counts: Mapping[str, int],
    *,
    max_groups_per_disease: int = 4,
) -> list[list[str]]:
    """从一个疾病的症状族覆盖中抽取可作为最低症状证据组的建议。"""

    specific_families = {
        family: count
        for family, count in family_counts.items()
        if family != "general_symptom"
    }
    if not specific_families:
        return [["general_symptom"]] if family_counts.get("general_symptom", 0) > 0 else []

    ordered = sorted(
        specific_families,
        key=lambda family: (
            -FAMILY_PRIORITY.get(family, 0),
            -int(specific_families[family]),
            family,
        ),
    )
    return [[family] for family in ordered[:max_groups_per_disease]]


def suggest_minimum_evidence_groups_by_evidence_group(
    family_counts_by_group: Mapping[str, Mapping[str, int]],
    *,
    max_groups_per_evidence_group: int = 2,
) -> dict[str, list[list[str]]]:
    """按 symptom/lab/imaging 等证据大组分别抽取最低证据族建议。"""

    suggestions: dict[str, list[list[str]]] = {}
    for evidence_group in sorted(family_counts_by_group):
        counts = family_counts_by_group[evidence_group]
        groups = suggest_minimum_evidence_groups(
            counts,
            max_groups_per_disease=max_groups_per_evidence_group,
        )
        if groups:
            suggestions[evidence_group] = groups
    return suggestions


def render_disease_evidence_catalog_markdown(catalog: Mapping[str, Any]) -> str:
    """将 full evidence catalog 渲染成人工检查友好的 Markdown。"""

    lines = [
        "# 疾病-全证据族目录",
        "",
        "- scope: disease_evidence_full",
        f"- disease_count: {int(catalog.get('disease_count') or 0)}",
        f"- evidence_node_count: {int(catalog.get('evidence_node_count') or 0)}",
        f"- disease_evidence_edge_count: {int(catalog.get('disease_evidence_edge_count') or 0)}",
        "",
        "## 证据大组概览",
        "",
        "| evidence_group | label | evidence_node_count |",
        "| --- | --- | ---: |",
    ]
    group_labels = catalog.get("evidence_group_labels") or {}
    group_counts = catalog.get("evidence_node_count_by_group") or {}
    for evidence_group in sorted(group_counts):
        lines.append(
            f"| `{evidence_group}` | {group_labels.get(evidence_group, '')} | {int(group_counts.get(evidence_group) or 0)} |"
        )

    lines.extend(
        [
            "",
            "## Evidence Family 概览",
            "",
            "| family | label | evidence_node_count |",
            "| --- | --- | ---: |",
        ]
    )
    family_labels = catalog.get("family_labels") or {}
    family_counts = catalog.get("evidence_family_node_counts") or {}
    for family in sorted(family_counts, key=family_sort_key):
        lines.append(f"| `{family}` | {family_labels.get(family, '')} | {int(family_counts.get(family) or 0)} |")

    lines.extend(
        [
            "",
            "## 疾病最低证据组建议",
            "",
            "| disease | evidence_count | group_counts | minimum_evidence_groups | grouped_minimum |",
            "| --- | ---: | --- | --- | --- |",
        ]
    )
    for disease in catalog.get("diseases") or []:
        groups = ", ".join("/".join(group) for group in disease.get("minimum_evidence_groups") or [])
        group_counts_text = _render_inline_mapping(disease.get("evidence_counts_by_group") or {})
        grouped_minimum = _render_grouped_minimum(disease.get("minimum_evidence_groups_by_evidence_group") or {})
        lines.append(
            f"| {disease.get('disease_name') or ''} | {int(disease.get('evidence_count') or 0)} | "
            f"{group_counts_text or '-'} | {groups or '-'} | {grouped_minimum or '-'} |"
        )

    unclassified = catalog.get("unclassified_evidence_nodes") or []
    lines.extend(
        [
            "",
            "## 未细分证据节点",
            "",
            f"- count: {len(unclassified)}",
            "",
        ]
    )
    for evidence in unclassified:
        lines.append(
            f"- {evidence.get('evidence_name') or ''} "
            f"({evidence.get('evidence_group') or ''}, {evidence.get('evidence_id') or ''}, "
            f"diseases={int(evidence.get('disease_count') or 0)})"
        )

    lines.extend(["", "## 按疾病查看证据", ""])
    for disease in catalog.get("diseases") or []:
        lines.extend(
            [
                f"### {disease.get('disease_name') or ''}",
                "",
                f"- disease_id: `{disease.get('disease_id') or ''}`",
                f"- minimum_evidence_groups: {disease.get('minimum_evidence_groups') or []}",
                f"- minimum_evidence_groups_by_evidence_group: {disease.get('minimum_evidence_groups_by_evidence_group') or {}}",
                "",
            ]
        )
        evidence_by_group = _group_evidence_names_by_group_and_family(disease.get("evidence") or [])
        for evidence_group in sorted(evidence_by_group):
            lines.append(f"- **{evidence_group}**")
            for family in sorted(evidence_by_group[evidence_group], key=family_sort_key):
                lines.append(f"  - `{family}`: " + "、".join(evidence_by_group[evidence_group][family]))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_disease_symptom_catalog_markdown(catalog: Mapping[str, Any]) -> str:
    """将 disease-symptom catalog 渲染成人工检查友好的 Markdown。"""

    lines = [
        "# 疾病-症状证据族目录",
        "",
        "- scope: disease_clinical_finding_only",
        f"- disease_count: {int(catalog.get('disease_count') or 0)}",
        f"- symptom_node_count: {int(catalog.get('symptom_node_count') or 0)}",
        f"- disease_symptom_edge_count: {int(catalog.get('disease_symptom_edge_count') or 0)}",
        "",
        "## 症状证据族概览",
        "",
        "| family | label | symptom_node_count |",
        "| --- | --- | ---: |",
    ]
    family_labels = catalog.get("family_labels") or {}
    family_counts = catalog.get("symptom_family_node_counts") or {}
    for family in sorted(family_counts, key=family_sort_key):
        lines.append(f"| `{family}` | {family_labels.get(family, '')} | {int(family_counts.get(family) or 0)} |")

    lines.extend(
        [
            "",
            "## 疾病最低症状证据组建议",
            "",
            "| disease | symptom_count | minimum_evidence_groups | family_coverage |",
            "| --- | ---: | --- | --- |",
        ]
    )
    for disease in catalog.get("diseases") or []:
        groups = ", ".join("/".join(group) for group in disease.get("minimum_evidence_groups") or [])
        coverage = ", ".join(str(item) for item in disease.get("symptom_family_coverage") or [])
        lines.append(
            f"| {disease.get('disease_name') or ''} | {int(disease.get('symptom_count') or 0)} | {groups or '-'} | {coverage or '-'} |"
        )

    unclassified = catalog.get("unclassified_symptom_nodes") or []
    lines.extend(
        [
            "",
            "## 未细分症状节点",
            "",
            f"- count: {len(unclassified)}",
            "",
        ]
    )
    for symptom in unclassified:
        lines.append(
            f"- {symptom.get('symptom_name') or ''} "
            f"({symptom.get('symptom_id') or ''}, diseases={int(symptom.get('disease_count') or 0)})"
        )

    lines.extend(["", "## 按疾病查看症状", ""])
    for disease in catalog.get("diseases") or []:
        lines.extend(
            [
                f"### {disease.get('disease_name') or ''}",
                "",
                f"- disease_id: `{disease.get('disease_id') or ''}`",
                f"- minimum_evidence_groups: {disease.get('minimum_evidence_groups') or []}",
                "",
            ]
        )
        symptoms_by_family = _group_symptoms_by_primary_family(disease.get("symptoms") or [])
        for family in sorted(symptoms_by_family, key=family_sort_key):
            lines.append(f"- `{family}`: " + "、".join(symptoms_by_family[family]))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _count_families(symptom_edges: Sequence[Mapping[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for edge in symptom_edges:
        for family in edge.get("families") or []:
            counts[str(family)] += 1
    return counts


def _count_families_by_evidence_group(edges: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    grouped_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for edge in edges:
        evidence_group = str(edge.get("evidence_group") or "")
        if not evidence_group:
            continue
        for family in edge.get("families") or []:
            grouped_counts[evidence_group][str(family)] += 1
    return {
        evidence_group: dict(sorted(counts.items(), key=lambda item: family_sort_key(item[0])))
        for evidence_group, counts in sorted(grouped_counts.items())
    }


def _count_symptom_nodes_by_family(symptoms: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for symptom in symptoms:
        for family in symptom.get("families") or []:
            counts[str(family)] += 1
    return dict(sorted(counts.items(), key=lambda item: family_sort_key(item[0])))


def _count_evidence_nodes_by_group(evidence_nodes: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for evidence in evidence_nodes:
        evidence_group = str(evidence.get("evidence_group") or "")
        if evidence_group:
            counts[evidence_group] += 1
    return dict(sorted(counts.items()))


def _count_evidence_nodes_by_family(evidence_nodes: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for evidence in evidence_nodes:
        for family in evidence.get("families") or []:
            counts[str(family)] += 1
    return dict(sorted(counts.items(), key=lambda item: family_sort_key(item[0])))


def _group_symptoms_by_primary_family(symptoms: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for symptom in symptoms:
        families = list(symptom.get("families") or ["general_symptom"])
        primary_family = sorted(families, key=family_sort_key)[0]
        name = str(symptom.get("symptom_name") or "")
        if name:
            grouped[primary_family].append(name)
    return {family: sorted(set(names)) for family, names in grouped.items()}


def _group_evidence_names_by_group_and_family(
    evidence_items: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, list[str]]]:
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for evidence in evidence_items:
        evidence_group = str(evidence.get("evidence_group") or "unknown")
        families = list(evidence.get("families") or ["general_detail"])
        primary_family = sorted(families, key=family_sort_key)[0]
        name = str(evidence.get("evidence_name") or "")
        if name:
            grouped[evidence_group][primary_family].append(name)
    return {
        evidence_group: {
            family: sorted(set(names))
            for family, names in families.items()
        }
        for evidence_group, families in grouped.items()
    }


def _render_inline_mapping(values: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}:{values[key]}" for key in sorted(values))


def _render_grouped_minimum(grouped_minimum: Mapping[str, Sequence[Sequence[str]]]) -> str:
    parts: list[str] = []
    for evidence_group in sorted(grouped_minimum):
        groups = grouped_minimum[evidence_group]
        groups_text = "/".join("+".join(group) for group in groups)
        parts.append(f"{evidence_group}:{groups_text}")
    return ", ".join(parts)
