#!/usr/bin/env bash
# 一次性验收：后端 pytest -> 前端真实请求 Vitest -> 前端生产构建。
# Vitest 通过 http://api:8000 调用 Compose 中真实运行的 API。
set -euo pipefail

API_BASE="${VITE_API_BASE:-http://api:8000}"

echo "==> [1/3] 等待 API 就绪：${API_BASE}/api/health"
for i in $(seq 1 30); do
  if python - "${API_BASE}" <<'PY'
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + "/api/health", timeout=2) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
  then
    echo "API 已就绪"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "API 未在规定时间内就绪" >&2
    exit 1
  fi
  sleep 2
done

echo "==> [2/3] 后端 pytest"
cd /verify/api
PYTHONPATH=. python -m pytest -q

echo "==> [3/3] 前端 Vitest（真实请求 ${API_BASE}）与生产构建"
cd /verify/web
VITE_API_BASE="${API_BASE}" npx vitest run
npm run build

echo ""
echo "✅ 验收通过：pytest、Vitest 真实请求与前端构建全部成功"
