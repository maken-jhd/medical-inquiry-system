"""读取 Streamlit 实时演示配置，并桥接到现有后端环境变量入口。"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "configs" / "frontend.yaml"
LOCAL_CONFIG_PATH = REPO_ROOT / "configs" / "frontend.local.yaml"


DEFAULT_CONFIG: dict[str, Any] = {
    "neo4j": {
        "uri": "bolt://localhost:7687",
        "user": "neo4j",
        "password": "",
        "database": "neo4j",
    },
    "llm": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3-max",
        "api_key": "",
        "timeout_seconds": 60,
        "enable_thinking": False,
    },
    "brain": {
        "acceptance_profile": "anchor_controlled",
        "verifier_acceptance_profile": "guarded_lenient",
        "agent_eval_mode": "llm_verifier",
    },
}


def load_frontend_config() -> dict[str, Any]:
    """读取默认配置与本机私密配置，后者覆盖前者。"""

    config = deepcopy(DEFAULT_CONFIG)
    config = _deep_merge(config, _load_yaml(DEFAULT_CONFIG_PATH))
    config = _deep_merge(config, _load_yaml(LOCAL_CONFIG_PATH))
    return config


def apply_config_to_environment(config: dict[str, Any]) -> None:
    """把配置写入当前 Python 进程环境，复用现有 build_default_brain_from_env。"""

    neo4j = _as_dict(config.get("neo4j"))
    llm = _as_dict(config.get("llm"))
    brain = _as_dict(config.get("brain"))

    _set_env_if_present("NEO4J_URI", neo4j.get("uri"))
    _set_env_if_present("NEO4J_USER", neo4j.get("user"))
    _set_env_if_present("NEO4J_PASSWORD", neo4j.get("password"))
    _set_env_if_present("NEO4J_DATABASE", neo4j.get("database"))

    _set_env_if_present("OPENAI_BASE_URL", llm.get("base_url"))
    _set_env_if_present("OPENAI_MODEL", llm.get("model"))
    _set_env_if_present("DASHSCOPE_API_KEY", llm.get("api_key"))
    _set_env_if_present("OPENAI_TIMEOUT_SECONDS", llm.get("timeout_seconds"))
    _set_env_if_present("OPENAI_ENABLE_THINKING", llm.get("enable_thinking"))

    acceptance_profile = str(brain.get("acceptance_profile") or "anchor_controlled")
    verifier_profile = str(brain.get("verifier_acceptance_profile") or acceptance_profile)
    agent_eval_mode = str(brain.get("agent_eval_mode") or "llm_verifier")

    _set_env_if_present("BRAIN_ACCEPTANCE_PROFILE", acceptance_profile)
    _set_env_if_present("TRAJECTORY_VERIFIER_ACCEPTANCE_PROFILE", verifier_profile)
    _set_env_if_present("BRAIN_AGENT_EVAL_MODE", agent_eval_mode)


def build_brain_config_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """把前端配置转换成 ConsultationBrain 的 config_overrides。"""

    brain = _as_dict(config.get("brain"))
    acceptance_profile = str(brain.get("acceptance_profile") or "anchor_controlled")
    agent_eval_mode = str(brain.get("agent_eval_mode") or "llm_verifier")
    return {
        "stop": {
            "acceptance_profile": acceptance_profile,
        },
        "path_evaluation": {
            "agent_eval_mode": agent_eval_mode,
        },
    }


def get_config_display_rows(config: dict[str, Any]) -> list[dict[str, str]]:
    """生成前端环境检查表格，不暴露完整 API Key。"""

    neo4j = _as_dict(config.get("neo4j"))
    llm = _as_dict(config.get("llm"))
    brain = _as_dict(config.get("brain"))
    api_key = str(llm.get("api_key") or os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY") or "")

    return [
        {"配置项": "Neo4j 地址", "当前值": str(neo4j.get("uri") or "")},
        {"配置项": "Neo4j 用户", "当前值": str(neo4j.get("user") or "")},
        {"配置项": "Neo4j 密码", "当前值": "已配置" if neo4j.get("password") else "未配置"},
        {"配置项": "Neo4j 数据库", "当前值": str(neo4j.get("database") or "")},
        {"配置项": "LLM Base URL", "当前值": str(llm.get("base_url") or "")},
        {"配置项": "LLM 模型", "当前值": str(llm.get("model") or "")},
        {"配置项": "LLM 请求超时", "当前值": f"{llm.get('timeout_seconds') or 60} 秒"},
        {"配置项": "LLM 深度思考", "当前值": "开启" if bool(llm.get("enable_thinking")) else "关闭"},
        {"配置项": "LLM API Key", "当前值": "已配置" if api_key else "未配置"},
        {"配置项": "安全接受策略", "当前值": str(brain.get("acceptance_profile") or "")},
        {"配置项": "复核器接受策略", "当前值": str(brain.get("verifier_acceptance_profile") or "")},
        {"配置项": "路径代理评估模式", "当前值": str(brain.get("agent_eval_mode") or "")},
    ]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    return payload if isinstance(payload, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)

    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value

    return merged


def _set_env_if_present(name: str, value: Any) -> None:
    text = "" if value is None else str(value).strip()
    if len(text) > 0:
        os.environ[name] = text


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
