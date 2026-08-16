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
#
# NANO_VISION=provider:model adds the vision arm — seeds the container's
# vision config so nano registers its conditional vision_analyze tool
# (docs/nano.md). Matching jobs/tb21-pi-flash-max-2 (which ran pi's TB
# extension with gpt-5.6-luna vision) means:
#   NANO_VISION=openai:gpt-5.6-luna bash eval/harbor/run_tb21_nano_max.sh
# The vision provider's API key must be in the host env (the adapter
# forwards it), e.g. OPENAI_API_KEY.
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

# Wheel resolution: CLAWCODEX_WHEEL env > existing dist/ wheel > build one.
# The build runs from a `git archive HEAD` export in a temp dir, never the
# live checkout: setuptools' package discovery walks the ENTIRE project tree
# before filtering to src/, and a working checkout carrying multi-GB
# untracked payloads (reference_projects/ ~1.8G, eval/harbor/jobs/ grows
# with every benchmark run) turns `uv build --wheel` into a de-facto hang.
WHEEL="${CLAWCODEX_WHEEL:-}"
if [ -z "$WHEEL" ]; then
  WHEEL="$(ls "$ROOT"/dist/clawcodex_cli-*.whl 2>/dev/null | head -1 || true)"
fi
if [ -z "$WHEEL" ]; then
  echo "building wheel from a clean git-archive export (untracked GBs in the checkout would stall setuptools discovery)..."
  BUILD_DIR="$(mktemp -d)"
  trap 'rm -rf "$BUILD_DIR"' EXIT
  git -C "$ROOT" archive HEAD | tar -x -C "$BUILD_DIR"
  (cd "$BUILD_DIR" && uv build --wheel)
  mkdir -p "$ROOT/dist"
  cp "$BUILD_DIR"/dist/clawcodex_cli-*.whl "$ROOT/dist/"
  WHEEL="$(ls "$ROOT"/dist/clawcodex_cli-*.whl | head -1)"
fi
echo "wheel: $WHEEL"
echo "job:   eval/harbor/jobs/$JOB_NAME"

AK_EXTRA=()
if [ -n "${NANO_VISION:-}" ]; then
  AK_EXTRA+=(--ak "vision=$NANO_VISION")
  echo "vision: $NANO_VISION (nano registers vision_analyze)"
fi

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
  --ak effort=max \
  ${AK_EXTRA[@]+"${AK_EXTRA[@]}"}

echo
echo "Compare against the pi run with:"
echo "  python3 eval/harbor/compare_trajectories.py eval/harbor/jobs/tb21-pi-flash-max-2 eval/harbor/jobs/$JOB_NAME"
