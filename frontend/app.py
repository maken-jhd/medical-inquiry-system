"""HIV/AIDS 智能问诊系统 Streamlit 演示界面。"""

from __future__ import annotations

import os
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any

import streamlit as st

CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from brain.errors import BrainDomainError
from frontend.config_loader import (
    apply_config_to_environment,
    build_brain_config_overrides,
    get_config_display_rows,
    load_frontend_config,
)
from frontend.demo_cases import get_demo_by_key, list_demo_replays
from frontend.output_browser import (
    build_case_replay,
    case_record_label,
    list_case_records,
    list_output_runs,
    load_run_overview,
    summarize_case_record,
)
from frontend.ui_adapter import (
    boolean_label,
    format_score,
    load_demo_replay,
    normalize_backend_turn,
    score_to_progress,
    translate_existence,
    translate_resolution,
)


st.set_page_config(
    page_title="HIV/AIDS 智能问诊系统演示",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)


# 页面样式尽量克制：突出中期检查需要看的关键信息，而不是做花哨效果。
st.markdown(
    """
    <style>
    .main-title {
        font-size: 2.15rem;
        font-weight: 800;
        letter-spacing: -0.02em;
        margin-bottom: 0.15rem;
    }
    .subtitle {
        color: #4b5563;
        font-size: 1.02rem;
        margin-bottom: 0.8rem;
    }
    .tag {
        display: inline-block;
        padding: 0.22rem 0.58rem;
        margin: 0 0.35rem 0.35rem 0;
        border-radius: 999px;
        background: #eef6f0;
        color: #166534;
        border: 1px solid #bbdec4;
        font-size: 0.86rem;
        font-weight: 650;
    }
    .chat-card {
        border: 1px solid #e5e7eb;
        border-radius: 0.9rem;
        padding: 0.8rem 0.95rem;
        margin-bottom: 0.7rem;
        background: #ffffff;
    }
    .patient-card {
        border-left: 0.35rem solid #2563eb;
    }
    .system-card {
        border-left: 0.35rem solid #16a34a;
        background: #fbfefc;
    }
    .final-card {
        border-left: 0.35rem solid #b45309;
        background: #fff7ed;
    }
    .small-muted {
        color: #6b7280;
        font-size: 0.86rem;
    }
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.45rem;
    }
    .metric-box {
        border: 1px solid #e5e7eb;
        border-radius: 0.65rem;
        padding: 0.55rem;
        background: #fafafa;
    }
    .metric-label {
        color: #6b7280;
        font-size: 0.78rem;
    }
    .metric-value {
        font-weight: 750;
        font-size: 1.02rem;
    }
    .candidate-title {
        font-weight: 800;
        font-size: 1.02rem;
        margin-bottom: 0.15rem;
    }
    .candidate-badge {
        display: inline-block;
        padding: 0.1rem 0.45rem;
        border-radius: 999px;
        font-size: 0.74rem;
        font-weight: 750;
        border: 1px solid #d1d5db;
        background: #f9fafb;
        color: #374151;
        margin-left: 0.35rem;
    }
    .primary-badge {
        background: #ecfdf5;
        border-color: #bbf7d0;
        color: #166534;
    }
    .evidence-line {
        font-size: 0.89rem;
        line-height: 1.48;
        margin: 0.08rem 0;
    }
    .evidence-matched {
        color: #166534;
        font-weight: 650;
    }
    .evidence-negative {
        color: #991b1b;
        font-weight: 650;
    }
    .evidence-unknown {
        color: #374151;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def main() -> None:
    """渲染 Streamlit 页面入口。"""

    _ensure_session_defaults()
    _render_header()
    _render_sidebar()

    left_col, right_col = st.columns([1.0, 1.28], gap="large")
    turns = _current_turns()
    current_turn = turns[-1] if turns else None

    with left_col:
        _render_chat_panel(turns)

    with right_col:
        if st.session_state.mode == "实验复盘模式":
            _render_experiment_overview_card()
        _render_decision_panel(current_turn)


def _ensure_session_defaults() -> None:
    """初始化 Streamlit session_state，保证回放模式无外部依赖可用。"""

    st.session_state.setdefault("mode", "演示回放模式")
    st.session_state.setdefault("demo_key", "pcp_provisional_success")
    st.session_state.setdefault("demo_replay", None)
    st.session_state.setdefault("demo_turn_index", 0)
    st.session_state.setdefault("live_brain", None)
    st.session_state.setdefault("live_session_id", "")
    st.session_state.setdefault("live_turns", [])
    st.session_state.setdefault("live_error", "")
    st.session_state.setdefault("frontend_config", load_frontend_config())
    st.session_state.setdefault("experiment_run_key", "")
    st.session_state.setdefault("experiment_run_path", "")
    st.session_state.setdefault("experiment_overview", {})
    st.session_state.setdefault("experiment_case_records", [])
    st.session_state.setdefault("experiment_case_index", 0)
    st.session_state.setdefault("experiment_replay", None)
    st.session_state.setdefault("experiment_turn_index", 0)
    st.session_state.setdefault("experiment_error", "")

    if st.session_state.demo_replay is None:
        _load_demo(st.session_state.demo_key)


def _render_header() -> None:
    """渲染页面顶部标题与系统特点标签。"""

    st.markdown('<div class="main-title">HIV/AIDS 场景智能问诊系统演示</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">基于知识图谱增强、局部树搜索与安全门控的多轮结构化问诊系统</div>',
        unsafe_allow_html=True,
    )
    tags = ["多轮问诊", "知识图谱增强", "候选诊断动态更新", "树搜索选择下一问", "安全门控防止过早停止"]
    st.markdown("".join(f'<span class="tag">{tag}</span>' for tag in tags), unsafe_allow_html=True)
    st.divider()


def _render_sidebar() -> None:
    """渲染模式选择、回放控制和实时模式说明。"""

    with st.sidebar:
        st.header("演示控制")
        mode_options = ["演示回放模式", "实验复盘模式", "实时运行模式"]
        current_mode = st.session_state.mode
        mode_index = mode_options.index(current_mode) if current_mode in mode_options else 0
        mode = st.radio(
            "运行模式",
            mode_options,
            index=mode_index,
            help="回放模式不依赖 Neo4j / LLM；实验复盘模式读取本地 test_outputs；实时模式调用后端。",
        )
        st.session_state.mode = mode

        if mode == "演示回放模式":
            _render_replay_controls()
        elif mode == "实验复盘模式":
            _render_experiment_controls()
        else:
            _render_live_controls()

        st.divider()
        st.caption("说明：页面只展示经过适配层整理后的中文摘要，不直接堆叠后端原始 JSON。")


def _render_replay_controls() -> None:
    """渲染离线 demo 回放控制。"""

    demos = list_demo_replays()
    labels = [demo.title for demo in demos]
    current_key = st.session_state.demo_key
    current_index = next((idx for idx, demo in enumerate(demos) if demo.key == current_key), 0)
    selected_label = st.selectbox("选择示例病例", labels, index=current_index)
    selected_demo = demos[labels.index(selected_label)]

    if selected_demo.key != current_key:
        _load_demo(selected_demo.key)

    replay = st.session_state.demo_replay or {}
    turns = replay.get("turns", [])
    max_index = max(len(turns) - 1, 0)
    st.session_state.demo_turn_index = min(st.session_state.demo_turn_index, max_index)
    slider_key = f"demo_turn_slider_{selected_demo.key}"

    if slider_key not in st.session_state:
        st.session_state[slider_key] = st.session_state.demo_turn_index + 1

    st.markdown(f"**病例简介：** {replay.get('description', selected_demo.description)}")
    st.caption(f"当前显示：第 {st.session_state.demo_turn_index + 1} / {len(turns) if turns else 0} 轮")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.button(
            "上一轮",
            use_container_width=True,
            disabled=st.session_state.demo_turn_index <= 0,
            on_click=_change_demo_turn,
            args=(-1, max_index, slider_key),
        )
    with col_b:
        st.button(
            "下一轮",
            use_container_width=True,
            disabled=st.session_state.demo_turn_index >= max_index,
            on_click=_change_demo_turn,
            args=(1, max_index, slider_key),
        )
    with col_c:
        st.button(
            "加载示例病例",
            use_container_width=True,
            on_click=_load_demo,
            args=(selected_demo.key,),
        )

    if turns:
        st.slider(
            "回放轮次",
            min_value=1,
            max_value=len(turns),
            key=slider_key,
            on_change=_sync_demo_slider,
            args=(slider_key, max_index),
        )


def _render_live_controls() -> None:
    """渲染实时运行模式的会话控制。"""

    st.info("实时模式会调用现有后端逻辑，需要 Neo4j 和 LLM 环境变量；若失败，回放模式仍可正常展示。")
    with st.expander("实时模式配置检查", expanded=False):
        config = load_frontend_config()
        st.session_state.frontend_config = config
        st.table(get_config_display_rows(config))
        st.caption("配置来源：`configs/frontend.yaml` + 可选的 `configs/frontend.local.yaml`。")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("开始新会话", use_container_width=True):
            _start_live_session()
            st.rerun()
    with col_b:
        if st.button("重置会话", use_container_width=True):
            _reset_live_session()
            st.rerun()

    if st.session_state.live_session_id:
        st.caption(f"当前实时会话：`{st.session_state.live_session_id}`")

    if st.session_state.live_error:
        st.error(st.session_state.live_error)


def _render_experiment_controls() -> None:
    """渲染实验输出复盘模式控制区。"""

    st.info("实验复盘模式会扫描 `test_outputs/simulator_replay`，适合回看 focused replay、acceptance sweep 和 ablation 输出。")
    if st.button("刷新实验索引", use_container_width=True):
        _cached_output_runs.clear()
        st.session_state.experiment_run_key = ""
        st.session_state.experiment_replay = None
        st.session_state.experiment_case_records = []
        st.rerun()

    runs = _cached_output_runs()
    if not runs:
        st.warning("暂未在 `test_outputs/simulator_replay` 下找到可识别的实验输出文件。")
        return

    current_key = st.session_state.experiment_run_key
    current_index = next((idx for idx, run in enumerate(runs) if run.key == current_key), 0)
    labels = [run.label for run in runs]
    selected_label = st.selectbox("选择实验输出目录", labels, index=current_index)
    selected_run = runs[labels.index(selected_label)]

    if selected_run.key != current_key:
        _load_experiment_run(selected_run.key, selected_run.path)

    if st.session_state.experiment_error:
        st.error(st.session_state.experiment_error)
        return

    records = st.session_state.experiment_case_records
    if not records:
        st.warning("该目录只有汇总或审计文件，暂未找到可逐病例浏览的 JSONL。")
        return

    case_labels = [case_record_label(record) for record in records]
    current_case_index = min(st.session_state.experiment_case_index, len(records) - 1)
    selected_case_label = st.selectbox("选择病例记录", case_labels, index=current_case_index)
    selected_case_index = case_labels.index(selected_case_label)
    if selected_case_index != st.session_state.experiment_case_index or st.session_state.experiment_replay is None:
        _load_experiment_case(selected_case_index)

    replay = st.session_state.experiment_replay or {}
    turns = replay.get("turns", [])
    max_index = max(len(turns) - 1, 0)
    st.session_state.experiment_turn_index = min(st.session_state.experiment_turn_index, max_index)
    slider_key = f"experiment_turn_slider_{_safe_widget_key(selected_run.key)}_{selected_case_index}"

    if slider_key not in st.session_state:
        st.session_state[slider_key] = st.session_state.experiment_turn_index + 1

    st.markdown(f"**病例简介：** {replay.get('description', '')}")
    st.caption(f"当前显示：第 {st.session_state.experiment_turn_index + 1} / {len(turns) if turns else 0} 轮")

    col_a, col_b = st.columns(2)
    with col_a:
        st.button(
            "上一轮",
            use_container_width=True,
            disabled=st.session_state.experiment_turn_index <= 0,
            on_click=_change_experiment_turn,
            args=(-1, max_index, slider_key),
        )
    with col_b:
        st.button(
            "下一轮",
            use_container_width=True,
            disabled=st.session_state.experiment_turn_index >= max_index,
            on_click=_change_experiment_turn,
            args=(1, max_index, slider_key),
        )

    if turns:
        st.slider(
            "复盘轮次",
            min_value=1,
            max_value=len(turns),
            key=slider_key,
            on_change=_sync_experiment_slider,
            args=(slider_key, max_index),
        )


def _render_chat_panel(turns: list[dict[str, Any]]) -> None:
    """渲染左侧问诊对话区。"""

    st.subheader("问诊对话区")

    if not turns:
        st.info("请选择一个示例病例、实验输出病例，或切换到实时运行模式后输入患者描述。")

    for turn in turns:
        turn_index = turn.get("turn_index", 0)
        patient_text = turn.get("patient_text", "")
        system_question = turn.get("system_question", "")
        final_answer = turn.get("final_answer", {})

        if patient_text:
            st.markdown(
                f"""
                <div class="chat-card patient-card">
                  <div class="small-muted">第 {turn_index} 轮 · 患者回答</div>
                  <div>{patient_text}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        if system_question:
            st.markdown(
                f"""
                <div class="chat-card system-card">
                  <div class="small-muted">第 {turn_index} 轮 · 系统下一问</div>
                  <div>{system_question}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        if turn.get("is_final"):
            answer_name = final_answer.get("answer_name", "暂无结论")
            why = final_answer.get("why", "")
            st.markdown(
                f"""
                <div class="chat-card final-card">
                  <div class="small-muted">最终结论</div>
                  <div><b>{answer_name}</b></div>
                  <div class="small-muted">{why}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    if st.session_state.mode == "实时运行模式":
        _render_live_input()


def _render_live_input() -> None:
    """渲染实时患者输入框。"""

    st.divider()
    with st.form("live_patient_form", clear_on_submit=True):
        patient_text = st.text_area(
            "患者输入",
            height=110,
            placeholder="例如：最近一周发热、干咳，活动后气短，之前查过 HIV 阳性……",
        )
        submitted = st.form_submit_button("发送", type="primary", use_container_width=True)

    if submitted:
        if not patient_text.strip():
            st.warning("请先输入患者描述或回答。")
            return
        _run_live_turn(patient_text.strip())
        st.rerun()


def _render_decision_panel(turn: dict[str, Any] | None) -> None:
    """渲染右侧结构化决策展示区。"""

    st.subheader("决策过程展示区")

    if turn is None:
        st.info("当前还没有可展示的问诊轮次。")
        return

    _render_state_card(turn)
    _render_a1_card(turn.get("a1", {}))
    _render_a2_card(turn.get("a2", {}))
    _render_a3_card(turn.get("a3", {}))
    _render_a4_card(turn.get("a4", {}))
    _render_search_card(turn.get("search", {}))
    _render_safety_card(turn.get("safety", {}))


def _render_state_card(turn: dict[str, Any]) -> None:
    """展示当前会话状态摘要。"""

    state = turn.get("state", {})
    with st.container(border=True):
        st.markdown("### 当前会话状态")
        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("当前轮次", state.get("turn_index", turn.get("turn_index", 0)))
        col_b.metric("仍在问诊", boolean_label(state.get("is_running", not turn.get("is_final"))))
        col_c.metric("已有最终结论", boolean_label(state.get("has_final_report", turn.get("is_final"))))
        col_d.metric("存在待验证动作", boolean_label(state.get("has_pending_action", bool(turn.get("system_question")))))
        primary = state.get("primary_hypothesis") or turn.get("search", {}).get("best_answer") or "暂无"
        st.caption(f"当前主假设：**{primary}**")
        pending = state.get("pending_action_name") or turn.get("a3", {}).get("selected_action_name") or "暂无"
        st.caption(f"当前 pending action：**{pending}**")


def _render_a1_card(a1: dict[str, Any]) -> None:
    """展示 A1 关键线索提取。"""

    with st.container(border=True):
        st.markdown("### A1 关键线索提取")
        st.caption("系统先从患者原话中抽取症状、风险因素和关键医学线索。")
        features = a1.get("features", [])
        if not features:
            st.info("本轮暂无新的关键线索，可能是回答上一轮验证问题。")
            return
        st.table(
            [
                {
                    "关键线索": item.get("name", ""),
                    "类别": item.get("category", ""),
                    "说明": item.get("reasoning", ""),
                }
                for item in features
            ]
        )


def _render_a2_card(a2: dict[str, Any]) -> None:
    """展示 A2 候选诊断证据画像。"""

    with st.container(border=True):
        st.markdown("### A2 候选诊断排序")
        st.caption("系统结合患者上下文与知识图谱候选，动态更新 top-k 诊断；分数仅作辅助，重点看证据命中、否定与待验证情况。")
        candidates = a2.get("candidates", [])
        if not candidates:
            st.info("暂无候选诊断排序。")
            return

        max_score = max([float(item.get("score", 0) or 0) for item in candidates] + [1.0])
        for idx, item in enumerate(candidates[:3], start=1):
            name = item.get("name", "未知候选")
            label = "主假设" if item.get("is_primary") or idx == 1 else "备选假设"
            badge_class = "candidate-badge primary-badge" if label == "主假设" else "candidate-badge"

            with st.container(border=True):
                st.markdown(
                    f"""
                    <div class="candidate-title">
                      {idx}. {name}
                      <span class="{badge_class}">{label}</span>
                      <span class="candidate-badge">分数 {format_score(item.get('score'))}</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.progress(score_to_progress(item.get("score"), fallback_max=max_score))

                count_cols = st.columns(3)
                count_cols[0].success(f"☑ 已命中 {int(item.get('matched_count', 0) or 0)}")
                count_cols[1].warning(f"☐ 待验证 {int(item.get('unknown_count', 0) or 0)}")
                count_cols[2].error(f"✖ 已否定 {int(item.get('negative_count', 0) or 0)}")

                if item.get("score_breakdown"):
                    st.caption(item["score_breakdown"])
                elif item.get("reasoning"):
                    st.caption(item["reasoning"])

                evidence_groups = item.get("evidence_groups") or {}
                if evidence_groups:
                    _render_candidate_evidence_groups(evidence_groups)
                else:
                    st.caption("暂无可展示的关键证据画像，当前仅显示候选排序结果。")


def _render_candidate_evidence_groups(evidence_groups: dict[str, Any]) -> None:
    """渲染只读复选框风格的候选诊断证据清单。"""

    group_labels = {
        "symptom": "症状 / 体征",
        "risk": "风险背景 / 风险行为",
        "lab": "实验室 / 化验",
        "imaging": "影像",
        "pathogen": "病原学",
        "detail": "其他关键细节",
    }
    ordered_groups = ["symptom", "risk", "lab", "imaging", "pathogen", "detail"]

    for group_key in ordered_groups:
        items = evidence_groups.get(group_key) or []

        if not items:
            continue

        st.markdown(f"**{group_labels.get(group_key, '其他关键细节')}**")

        for evidence in items[:5]:
            status = str(evidence.get("status") or "unknown")
            icon = evidence.get("status_icon") or {"matched": "☑", "negative": "✖", "unknown": "☐"}.get(status, "☐")
            css_class = {
                "matched": "evidence-matched",
                "negative": "evidence-negative",
                "unknown": "evidence-unknown",
            }.get(status, "evidence-unknown")
            status_label = evidence.get("status_label") or {
                "matched": "已命中",
                "negative": "已否定",
                "unknown": "待验证",
            }.get(status, "待验证")
            name = evidence.get("name", "未命名证据")
            relation_type = evidence.get("relation_type", "")
            relation_text = f" · {relation_type}" if relation_type else ""
            st.markdown(
                f"""
                <div class="evidence-line {css_class}">
                  {icon} {name}
                  <span class="small-muted">（{status_label}{relation_text}）</span>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_a3_card(a3: dict[str, Any]) -> None:
    """展示 A3 下一问选择。"""

    with st.container(border=True):
        st.markdown("### A3 下一问选择")
        st.caption("系统根据 R2 检索、树搜索评分和 repair 策略选择最值得追问的证据。")
        question = a3.get("question_text") or "暂无下一问"
        st.markdown(f"**当前下一问：** {question}")
        st.markdown(f"**问题类型：** {a3.get('question_type_label', '未知类型')}")
        st.markdown(f"**为什么问这个问题：** {a3.get('reasoning', '暂无说明')}")

        if a3.get("root_best_action_name") or a3.get("repair_selected_action_name"):
            col_a, col_b = st.columns(2)
            col_a.info(f"根节点最优动作：{a3.get('root_best_action_name') or '暂无'}")
            col_b.success(f"修复后选择动作：{a3.get('repair_selected_action_name') or '暂无'}")
            if a3.get("is_repair_override"):
                st.caption("说明：repair action 与 root best action 不同，表示系统正在根据证据缺口修复下一问。")

        tags = a3.get("evidence_tags") or []
        if tags:
            st.caption("证据标签：" + "、".join(str(item) for item in tags))


def _render_a4_card(a4: dict[str, Any]) -> None:
    """展示 A4 回答解释与路由。"""

    with st.container(border=True):
        st.markdown("### A4 回答解释与路由")
        st.caption("系统把上一轮患者回答解释成存在 / 不存在 / 不确定，并决定下一步路由。")
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("证据状态", a4.get("existence_label") or translate_existence(a4.get("existence")))
        col_b.metric("回答清晰度", a4.get("resolution_label") or translate_resolution(a4.get("resolution")))
        col_c.metric("路由结果", a4.get("route_label", "暂无"))
        st.markdown(f"**解释：** {a4.get('reasoning', '暂无 A4 解释')}")

        family_bits = []
        if a4.get("evidence_families"):
            family_bits.append("证据 family：" + "、".join(a4.get("evidence_families", [])))
        if a4.get("entered_confirmed_family"):
            family_bits.append("已进入 confirmed family")
        if a4.get("provisional_family_candidate"):
            family_bits.append("进入 provisional family 候选")
        if family_bits:
            st.caption("；".join(family_bits))


def _render_search_card(search: dict[str, Any]) -> None:
    """展示搜索摘要，不画复杂树图。"""

    with st.container(border=True):
        st.markdown("### 搜索摘要")
        st.caption("这里展示局部树搜索和路径聚合结果，不展开完整树结构。")
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("rollout 次数", search.get("rollouts", "暂无"))
        col_b.metric("树节点数量", search.get("tree_node_count", "暂无"))
        col_c.metric("轨迹数量", search.get("trajectory_count", "暂无"))

        st.markdown(f"**当前 best answer：** {search.get('best_answer') or '暂无'}")
        col_d, col_e, col_f = st.columns(3)
        col_d.metric("一致性", format_score(search.get("consistency")))
        col_e.metric("路径多样性", format_score(search.get("diversity")))
        col_f.metric("代理评估 / 复核器", format_score(search.get("agent_evaluation")))
        st.caption(f"复核器结果：{search.get('verifier_result', '暂无')}")

        scores = search.get("final_answer_scores", [])
        if scores:
            with st.expander("查看候选答案路径评分", expanded=False):
                st.table(
                    [
                        {
                            "答案": item.get("answer_name", item.get("name", "")),
                            "综合分": format_score(item.get("final_score")),
                            "一致性": format_score(item.get("consistency")),
                            "多样性": format_score(item.get("diversity")),
                            "代理评估": format_score(item.get("agent_evaluation")),
                        }
                        for item in scores
                    ]
                )


def _render_safety_card(safety: dict[str, Any]) -> None:
    """展示复核器与安全接受闸门。"""

    with st.container(border=True):
        st.markdown("### 安全机制展示")
        st.caption("复核器（verifier）和安全接受闸门（guarded acceptance）用于防止过早停止。")
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("复核器允许停止", boolean_label(safety.get("verifier_should_accept")))
        col_b.metric("安全闸门阻止", boolean_label(safety.get("guarded_acceptance_blocked")))
        col_c.metric("搜索树换根", boolean_label(safety.get("tree_rerooted")))

        st.markdown(f"**复核拒停原因（verifier reject reason）：** {safety.get('reject_reason_label', '无')}")
        st.markdown(f"**安全接受闸门（guarded acceptance）：** {safety.get('guarded_block_reason_label', '未阻止')}")
        st.markdown(f"**修复模式（repair mode）：** {safety.get('repair_mode_label', '未触发修复')}")

        if safety.get("missing_evidence_families"):
            st.warning("缺少关键证据 family：" + "、".join(safety.get("missing_evidence_families", [])))
        if safety.get("recommended_next_evidence"):
            st.info("复核器建议补充：" + "、".join(safety.get("recommended_next_evidence", [])[:5]))
        if safety.get("alternative_candidates"):
            with st.expander("竞争诊断候选", expanded=False):
                st.table(
                    [
                        {
                            "候选诊断": item.get("answer_name", item.get("name", "")),
                            "强度": item.get("strength", "未标注"),
                            "原因": item.get("reason", ""),
                        }
                        for item in safety.get("alternative_candidates", [])[:5]
                    ]
                )
        if safety.get("pcp_combo_uses_provisional"):
            st.success("本轮接受使用了 provisional evidence family：说明模糊但高价值的证据被结构化纳入安全判断。")


def _render_experiment_overview_card() -> None:
    """展示实验目录级与病例级摘要，帮助快速复盘。"""

    overview = st.session_state.experiment_overview or {}
    replay = st.session_state.experiment_replay or {}
    records = st.session_state.experiment_case_records or []
    if not overview and not replay:
        return

    with st.container(border=True):
        st.markdown("### 实验复盘总览")
        st.caption("该模块直接读取 `test_outputs/simulator_replay` 下的历史实验输出，便于测试后复盘。")
        if overview.get("relative_path"):
            st.markdown(f"**当前输出目录：** `{overview.get('relative_path')}`")
        if overview.get("available_files"):
            st.caption("识别到的文件：" + "、".join(overview.get("available_files", [])))

        metrics = _pick_experiment_metrics(overview)
        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("病例数", _metric_value(metrics, "case_count", len(records)))
        col_b.metric("正确接受", _metric_value(metrics, "accepted_correct_count", "—"))
        col_c.metric("错误接受", _metric_value(metrics, "accepted_wrong_count", "—"))
        col_d.metric("正确但被拒停", _metric_value(metrics, "correct_best_answer_but_rejected_count", "—"))

        col_e, col_f, col_g = st.columns(3)
        col_e.metric("repair 轮数", _metric_value(metrics, "repair_turns", "—"))
        col_f.metric("语义重复轮数", _metric_value(metrics, "semantic_repeat_turns", "—"))
        col_g.metric("闸门拒绝次数", _metric_value(metrics, "verifier_positive_but_gate_rejected_count", "—"))

        if records:
            case_summary = summarize_case_record(records[st.session_state.experiment_case_index])
            st.markdown("**当前病例摘要**")
            st.table(
                [
                    {"指标": "病例 ID", "值": case_summary.get("case_id", "")},
                    {"指标": "真实诊断", "值": "、".join(case_summary.get("true_conditions", [])) or "未标注"},
                    {"指标": "最终答案", "值": case_summary.get("best_answer", "") or "暂无"},
                    {"指标": "停止原因", "值": case_summary.get("stop_reason", "") or "暂无"},
                    {"指标": "接受分类", "值": case_summary.get("acceptance_category", "") or "未标注"},
                    {
                        "指标": "是否正确",
                        "值": boolean_label(case_summary.get("is_best_answer_correct"))
                        if case_summary.get("is_best_answer_correct") is not None
                        else "未标注",
                    },
                ]
            )

        if overview.get("profile_summary"):
            with st.expander("查看 profile_summary.tsv", expanded=False):
                st.table(overview["profile_summary"])
        if overview.get("status"):
            with st.expander("查看 status.json", expanded=False):
                st.json(overview["status"])
        if overview.get("log_tail"):
            with st.expander("查看 run.log 末尾", expanded=False):
                st.code(overview["log_tail"], language="text")


def _pick_experiment_metrics(overview: dict[str, Any]) -> dict[str, Any]:
    """从不同类型汇总文件中选一个最适合展示的 metrics dict。"""

    for key in ("metrics", "ablation_metrics", "benchmark_summary"):
        payload = overview.get(key)
        if not isinstance(payload, dict) or not payload:
            continue
        if "metrics" in payload and isinstance(payload["metrics"], dict):
            return payload["metrics"]
        return payload
    return {}


def _metric_value(metrics: dict[str, Any], key: str, fallback: Any) -> Any:
    """兼容不同实验脚本的指标命名。"""

    if key in metrics:
        return metrics[key]
    for value in metrics.values():
        if isinstance(value, dict) and key in value:
            return value[key]
    return fallback


def _current_turns() -> list[dict[str, Any]]:
    """根据当前模式返回需要展示到左侧聊天区的轮次。"""

    if st.session_state.mode == "实时运行模式":
        return st.session_state.live_turns
    if st.session_state.mode == "实验复盘模式":
        replay = st.session_state.experiment_replay or {}
        turns = replay.get("turns", [])
        if not turns:
            return []
        return turns[: st.session_state.experiment_turn_index + 1]

    replay = st.session_state.demo_replay or {}
    turns = replay.get("turns", [])
    if not turns:
        return []
    return turns[: st.session_state.demo_turn_index + 1]


def _load_demo(key: str) -> None:
    """加载内置 demo JSON。"""

    demo = get_demo_by_key(key)
    st.session_state.demo_key = demo.key
    st.session_state.demo_replay = load_demo_replay(demo.path)
    st.session_state.demo_turn_index = 0
    st.session_state[f"demo_turn_slider_{demo.key}"] = 1


def _sync_demo_slider(slider_key: str, max_index: int) -> None:
    """同步回放 slider 与当前轮次索引。"""

    value = int(st.session_state.get(slider_key, 1))
    st.session_state.demo_turn_index = max(0, min(max_index, value - 1))


def _change_demo_turn(delta: int, max_index: int, slider_key: str) -> None:
    """通过上一轮 / 下一轮按钮改变回放轮次，并同步 slider。"""

    next_index = max(0, min(max_index, int(st.session_state.demo_turn_index) + delta))
    st.session_state.demo_turn_index = next_index
    st.session_state[slider_key] = next_index + 1


@st.cache_data(show_spinner=False)
def _cached_output_runs() -> list[Any]:
    """缓存实验输出目录索引，避免 Streamlit 每次刷新都重新扫描。"""

    return list_output_runs()


def _load_experiment_run(run_key: str, run_path: Path) -> None:
    """加载一个实验输出目录的汇总与病例记录。"""

    st.session_state.experiment_error = ""
    st.session_state.experiment_run_key = run_key
    st.session_state.experiment_run_path = str(run_path)
    st.session_state.experiment_case_index = 0
    st.session_state.experiment_turn_index = 0
    st.session_state.experiment_replay = None
    try:
        st.session_state.experiment_overview = load_run_overview(run_path)
        st.session_state.experiment_case_records = list_case_records(run_path)
        if st.session_state.experiment_case_records:
            _load_experiment_case(0)
    except Exception as exc:  # noqa: BLE001 - 复盘模式需要容忍历史文件格式差异
        st.session_state.experiment_error = f"加载实验输出失败：{exc}"
        st.session_state.experiment_overview = {}
        st.session_state.experiment_case_records = []


def _load_experiment_case(case_index: int) -> None:
    """加载选中的实验病例，并转换成可逐轮展示的 replay。"""

    records = st.session_state.experiment_case_records
    if not records:
        st.session_state.experiment_replay = None
        return
    bounded_index = max(0, min(case_index, len(records) - 1))
    record = records[bounded_index]
    st.session_state.experiment_case_index = bounded_index
    st.session_state.experiment_turn_index = 0
    st.session_state.experiment_replay = build_case_replay(record)


def _sync_experiment_slider(slider_key: str, max_index: int) -> None:
    """同步实验复盘 slider 与当前轮次索引。"""

    value = int(st.session_state.get(slider_key, 1))
    st.session_state.experiment_turn_index = max(0, min(max_index, value - 1))


def _change_experiment_turn(delta: int, max_index: int, slider_key: str) -> None:
    """通过上一轮 / 下一轮按钮改变实验复盘轮次。"""

    next_index = max(0, min(max_index, int(st.session_state.experiment_turn_index) + delta))
    st.session_state.experiment_turn_index = next_index
    st.session_state[slider_key] = next_index + 1


def _safe_widget_key(value: str) -> str:
    """把路径变成适合 Streamlit widget key 的短字符串。"""

    safe = "".join(ch if ch.isalnum() else "_" for ch in value)
    return safe[-120:] or "root"


def _start_live_session() -> None:
    """启动实时问诊会话，失败时保留错误信息并不影响回放模式。"""

    st.session_state.live_error = ""
    st.session_state.live_turns = []
    st.session_state.live_session_id = f"streamlit-{uuid.uuid4().hex[:10]}"
    try:
        brain = _get_or_build_live_brain()
        brain.start_session(st.session_state.live_session_id)
    except Exception as exc:  # noqa: BLE001 - 演示界面需要捕获后端环境异常
        st.session_state.live_error = _format_live_error(exc)


def _reset_live_session() -> None:
    """重置实时模式状态。"""

    st.session_state.live_session_id = ""
    st.session_state.live_turns = []
    st.session_state.live_error = ""


def _run_live_turn(patient_text: str) -> None:
    """调用后端 process_turn 处理一轮实时问诊。"""

    st.session_state.live_error = ""
    if not st.session_state.live_session_id:
        _start_live_session()

    if st.session_state.live_error:
        return

    try:
        brain = _get_or_build_live_brain()
        result = brain.process_turn(st.session_state.live_session_id, patient_text)
        st.session_state.live_turns.append(normalize_backend_turn(result))
    except Exception as exc:  # noqa: BLE001 - 前端必须优雅降级
        st.session_state.live_error = _format_live_error(exc)


@st.cache_resource(show_spinner=False)
def _build_brain_cached() -> Any:
    """缓存后端问诊大脑，避免 Streamlit 每次刷新都重建连接。"""

    from brain.service import build_default_brain_from_env

    config = load_frontend_config()
    apply_config_to_environment(config)
    overrides = build_brain_config_overrides(config)
    return build_default_brain_from_env(config_overrides=overrides)


def _get_or_build_live_brain() -> Any:
    """获取实时模式后端实例。"""

    if st.session_state.live_brain is None:
        st.session_state.live_brain = _build_brain_cached()
    return st.session_state.live_brain


def _format_live_error(exc: Exception) -> str:
    """把实时后端异常转成对老师友好的中文提示。"""

    if isinstance(exc, BrainDomainError):
        detail = exc.to_dict()
        return (
            "实时模式调用后端失败。"
            f"错误代码：{detail.get('code', '')}；"
            f"阶段：{detail.get('stage', '')}；"
            f"说明：{detail.get('message', '')}"
        )

    detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return (
        "实时模式暂时不可用，可能是 Neo4j、LLM API Key 或依赖环境未配置。"
        f"请切换到演示回放模式继续展示。错误摘要：{detail}"
    )


if __name__ == "__main__":
    main()
