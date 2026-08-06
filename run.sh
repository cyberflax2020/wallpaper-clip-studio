#!/bin/bash
# 启动本地工作台（无浏览器、无弹窗，纯 headless）。
# 分阶段：环境 → venv → 自检 → 起服务。
set -euo pipefail
cd "$(dirname "$0")"

PORT="${WPCLIP_PORT:-8799}"

echo "[1/4] 检查依赖（ffmpeg/ffprobe/python3）…"
command -v ffmpeg >/dev/null || { echo "缺少 ffmpeg，请先 brew install ffmpeg"; exit 1; }
command -v ffprobe >/dev/null || { echo "缺少 ffprobe"; exit 1; }
PY="$(command -v python3)"

echo "[2/4] 准备隔离 venv…"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
if ! .venv/bin/python -c "import fastapi,uvicorn,numpy" 2>/dev/null; then
  .venv/bin/python -m pip install -q -r requirements.txt
fi

echo "[3/4] 引擎自检…"
.venv/bin/python -m py_compile wpclip/*.py
if [ -f tests/test_engine.py ]; then
  "$PY" tests/test_engine.py >/dev/null 2>&1 && echo "  测试通过" || echo "  （测试未过，见 bash tests/run_all.sh）"
else
  echo "  （公开包不含测试，跳过）"
fi

echo "[4/4] 启动服务 http://127.0.0.1:$PORT …"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
