"""测试前端配置桥接时，命令行环境变量可临时覆盖模型名。"""

from __future__ import annotations

import os

from frontend.config_loader import apply_config_to_environment


# 验证显式传入的 OPENAI_MODEL 不会被 frontend.local.yaml 中的默认模型覆盖。
def test_apply_config_to_environment_preserves_existing_openai_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "qwen3.5-plus")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    apply_config_to_environment(
        {
            "llm": {
                "base_url": "https://example.test/v1",
                "model": "qwen3-max",
            }
        }
    )

    assert os.getenv("OPENAI_MODEL") == "qwen3.5-plus"
    assert os.getenv("OPENAI_BASE_URL") == "https://example.test/v1"
