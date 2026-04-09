"""测试默认配置文件会被读取并映射到运行参数。"""

from pathlib import Path

from brain.service import load_brain_config


# 验证 load_brain_config 能从 YAML 文件中读出结构化配置。
def test_load_brain_config_reads_yaml_file(tmp_path: Path) -> None:
    config_path = tmp_path / "brain.yaml"
    config_path.write_text(
        "\n".join(
            [
                "search:",
                "  num_rollouts: 5",
                "path_evaluation:",
                "  agent_eval_mode: llm_verifier",
                "a4:",
                "  use_llm_deductive_judge: true",
                "stop:",
                "  min_turn_index_before_final_answer: 3",
            ]
        ),
        encoding="utf-8",
    )

    config = load_brain_config(config_path)

    assert config["search"]["num_rollouts"] == 5
    assert config["path_evaluation"]["agent_eval_mode"] == "llm_verifier"
    assert config["a4"]["use_llm_deductive_judge"] is True
    assert config["stop"]["min_turn_index_before_final_answer"] == 3
