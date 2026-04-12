#!/usr/bin/env bash

# 一键启动 Streamlit 演示前端。
# 运行配置由 configs/frontend.yaml 与 configs/frontend.local.yaml 自动读取。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[frontend] 配置文件：configs/frontend.yaml"
echo "[frontend] 本机私密覆盖：configs/frontend.local.yaml（可选，已被 .gitignore 忽略）"
echo "[frontend] 启动后浏览器访问终端输出的 Local URL，默认通常是 http://localhost:8501"

conda run -n GraduationDesign streamlit run frontend/app.py --browser.gatherUsageStats false "$@"
