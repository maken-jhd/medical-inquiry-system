"""问诊大脑配置加载入口。"""

from __future__ import annotations

from pathlib import Path

from ..app.brain import load_brain_config


def default_brain_config_path(project_root: Path | None = None) -> Path:
    """返回仓库内默认 brain 配置路径。"""

    root = project_root or Path(__file__).resolve().parents[2]
    return root / "configs" / "brain.yaml"


__all__ = ["default_brain_config_path", "load_brain_config"]
