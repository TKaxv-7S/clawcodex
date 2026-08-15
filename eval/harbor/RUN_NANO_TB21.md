# Running clawcodex --nano on terminal-bench 2.1

Nano mode (`docs/nano.md`) is clawcodex's pi-shaped minimal profile. The
Harbor adapter forwards it with a single agent kwarg, so any run
documented in `README.md` / `RUN_PI_TB21.md` becomes a nano run by adding
`--ak nano=1`.

## A/B a task (nano vs default), DeepSeek

```bash
export DEEPSEEK_API_KEY=sk-...

# nano
PYTHONPATH=$PWD/eval/harbor harbor run \
  --dataset terminal-bench/terminal-bench-2-1 \
  --agent clawcodex_agent:Clawcodex \
  --model deepseek/deepseek-v4-flash \
  --jobs-dir eval/harbor/jobs-nano \
  --n-concurrent 4 \
  --ak nano=1

# baseline: same command, no --ak nano=1, --jobs-dir eval/harbor/jobs-default
```

Filter to single tasks with `-i 'terminal-bench/fix-git'` (hub datasets
namespace task names). To eval unreleased working-tree changes, build a
wheel and point `source` at it:

```bash
uv build --wheel
...  --ak source=$PWD/dist/clawcodex_cli-<version>-py3-none-any.whl --ak nano=1
```

Then compare with the existing tooling (`compare_results.py`,
`compare_trajectories.py`) across the two jobs dirs.

## Measured (2026-08-15, deepseek-v4-flash, this adapter, k=1)

Identical wheel, only `--ak nano=1` differing:

**`terminal-bench/fix-git`**

| | nano | default | ratio |
|---|---|---|---|
| reward | **1.0** | **1.0** | = |
| input tokens | 54,847 | 282,601 | **5.2× less** |
| cache tokens | 48,896 | 233,344 | 4.8× less |
| output tokens | 2,421 | 9,283 | 3.8× less |
| cost | **$0.00165** | **$0.01015** | **6.2× cheaper** |
| job runtime | 75 s | 128 s | 1.7× faster |

**`terminal-bench/pypi-server`**

| | nano | default | ratio |
|---|---|---|---|
| reward | **1.0** | **1.0** | = |
| input tokens | 111,554 | 300,198 | **2.7× less** |
| output tokens | 4,941 | 7,362 | 1.5× less |
| cost | **$0.00255** | **$0.00849** | **3.3× cheaper** |
| job runtime | 100 s | 117 s | 1.2× faster |

Aggregate: **2/2 pass in both modes; nano $0.0042 vs default $0.0186 —
4.4× cheaper** at equal quality. Nano's fix-git trajectory was 10 focused
Bash calls, zero tool errors.

Nano sends six tools and a ~2K-token fixed payload (vs ~16K default), no
per-turn injections, /eco on. A trivial live A/B outside Harbor
(deepseek-v4-pro, write-and-verify, both solved in 4 turns) showed the
same shape: 3,439 vs 27,173 fresh input tokens.

Comparability notes mirror `RUN_PI_TB21.md`: nano has no
vision_analyze/websearch, so image- and web-dependent TB tasks will lose
capability relative to default clawcodex — that difference is part of
what the A/B measures. Don't describe the runs as "same tools".
