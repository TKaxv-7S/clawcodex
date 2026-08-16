#!/usr/bin/env bash
# Run the clawcodex --nano arm of the TB 2.1 comparison, matched to the
# existing pi run at eval/harbor/jobs/tb21-pi-flash-max-2:
#   same model (deepseek/deepseek-v4-flash), same reasoning level
#   (effort=max == pi thinking=max on DeepSeek's low|high|max vocabulary),
#   same retry policy (2x on ApiRateLimitError). Advisor is structurally
#   off under nano. See RUN_NANO_TB21.md "Fair nano-vs-pi setup".
#
# Usage, from the repo root:
#   bash eval/harbor/run_tb21_nano_max.sh [job-name]
# Default job name: tb21-nano-flash-max-1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
JOB_NAME="${1:-tb21-nano-flash-max-1}"

# Both harnesses at explicit levels; stray env fallbacks must not leak in.
unset CLAWCODEX_EFFORT PI_THINKING 2>/dev/null || true

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  DEEPSEEK_API_KEY="$(python3 -c "import json,pathlib;print(json.loads((pathlib.Path.home()/'.clawcodex'/'config.json').read_text())['providers']['deepseek']['api_key'])")"
  export DEEPSEEK_API_KEY
fi
if [ -z "${DEEPSEEK_API_KEY}" ]; then
  echo "error: no DeepSeek API key (env or ~/.clawcodex/config.json)" >&2
  exit 2
fi

WHEEL="$(ls "$ROOT"/dist/clawcodex_cli-*.whl 2>/dev/null | head -1 || true)"
if [ -z "$WHEEL" ]; then
  echo "building wheel from the current checkout..."
  uv build --wheel
  WHEEL="$(ls "$ROOT"/dist/clawcodex_cli-*.whl | head -1)"
fi
echo "wheel: $WHEEL"
echo "job:   eval/harbor/jobs/$JOB_NAME"

PYTHONPATH="$ROOT/eval/harbor" harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent clawcodex_agent:Clawcodex \
  --model deepseek/deepseek-v4-flash \
  --jobs-dir eval/harbor/jobs \
  --job-name "$JOB_NAME" \
  --n-concurrent 4 \
  --max-retries 2 \
  --retry-include ApiRateLimitError \
  --ak "source=$WHEEL" \
  --ak nano=1 \
  --ak effort=max

echo
echo "Compare against the pi run with:"
echo "  python3 eval/harbor/compare_trajectories.py eval/harbor/jobs/tb21-pi-flash-max-2 eval/harbor/jobs/$JOB_NAME"
