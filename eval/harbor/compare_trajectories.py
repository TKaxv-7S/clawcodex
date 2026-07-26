#!/usr/bin/env python3
"""Compare two Harbor jobs' ATIF trajectories on the tasks they share.

Every metric is computed over the INTERSECTION of task names. Mixing task sets
silently inverts conclusions: comparing a 7-task clawcodex job against an
89-task Claude Code job made Claude Code look like it repeated tool calls more
often (6.8% vs 5.3%) and used heredocs less often (7.4% vs 12.1%). Restricted
to the seven shared tasks, both directions flip (0.0% vs 5.3%; 23.4% vs 12.1%).

Trials killed by the harness are reported separately and excluded from the
step-count means: a right-censored trajectory's step count is a lower bound on
what the agent would have spent, so averaging it against completed runs is not
a valid comparison. Rewards are always reported -- a step-count delta on tasks
where both sides already score 1.0 is a cost difference, not a quality one.

Usage:
    python3 eval/harbor/compare_trajectories.py <job_a> <job_b>
"""

from __future__ import annotations

import json
import re
import statistics as st
import sys
from pathlib import Path

HEREDOC = re.compile(r"(cat|tee)\s*>+\s*\S+\s*<<")
PROBE = re.compile(
    r"\b(command -v|which |type -p|ls /usr/bin|ls /usr/local|find / |find /usr"
    r"|apt-get|apt |pip install|pip3 install|--version)"
)
MEMORY = re.compile(r"/memory/|MEMORY\.md|CLAWCODEX\.md|CLAUDE\.md")


def _norm(value: object) -> str:
    return " ".join(str(value or "").split())


def load_trials(job: Path) -> dict[str, dict]:
    """Map task name -> per-trial metrics for one job directory."""
    out: dict[str, dict] = {}
    for traj in sorted(job.glob("*__*/agent/trajectory.json")):
        trial_dir = traj.parent.parent
        task = trial_dir.name.split("__")[0]
        steps = json.loads(traj.read_text()).get("steps") or []

        calls: list[tuple[str, str, str]] = []
        for step in steps:
            for call in step.get("tool_calls") or []:
                args = call.get("arguments") or {}
                calls.append((
                    call.get("function_name") or "?",
                    _norm(args.get("command") or args.get("content")),
                    _norm(args.get("file_path")),
                ))

        bash = [c for c in calls if c[0] == "Bash"]
        stmts = [
            len([x for x in re.split(r"&&|\|\||;|\n", c[1]) if x.strip()])
            for c in bash
        ]
        seen: set[tuple[str, str, str]] = set()
        repeats = 0
        for key in calls:
            if key in seen:
                repeats += 1
            seen.add(key)

        reward_file = trial_dir / "verifier" / "reward.txt"
        out.setdefault(task, {"trials": []})["trials"].append({
            "steps": len([s for s in steps if s.get("tool_calls")]),
            "raw_steps": len(steps),
            "calls": len(calls),
            "bash": len(bash),
            "heredoc": sum(1 for c in bash if HEREDOC.search(c[1])),
            "probe": sum(1 for c in bash if PROBE.search(c[1])),
            "memory": sum(1 for c in calls if MEMORY.search(c[2])),
            "repeats": repeats,
            "stmts": stmts,
            "tools": [c[0] for c in calls],
            "reward": (
                reward_file.read_text().strip()
                if reward_file.exists() else None
            ),
            "killed": (trial_dir / "exception.txt").exists(),
        })
    return out


def agg(trials: list[dict], key: str) -> float:
    return st.mean([t[key] for t in trials]) if trials else float("nan")


def report(job_a: Path, job_b: Path) -> None:
    A, B = load_trials(job_a), load_trials(job_b)
    shared = sorted(set(A) & set(B))
    if not shared:
        sys.exit("no shared tasks between the two jobs")

    print(f"A = {job_a.name}\nB = {job_b.name}")
    print(f"shared tasks: {len(shared)}  (A has {len(A)}, B has {len(B)})\n")

    killed = [
        (lbl, t)
        for lbl, src in (("A", A), ("B", B))
        for t in shared
        for tr in src[t]["trials"] if tr["killed"]
    ]
    if killed:
        print("RIGHT-CENSORED (killed by harness; excluded from step means):")
        for lbl, t in killed:
            print(f"  {lbl}: {t}")
        print()

    print(f"{'task':28}{'A stp':>7}{'B stp':>7}{'A rew':>7}{'B rew':>7}")
    a_ok: list[float] = []
    b_ok: list[float] = []
    for t in shared:
        at, bt = A[t]["trials"], B[t]["trials"]
        alive_a = [x for x in at if not x["killed"]]
        alive_b = [x for x in bt if not x["killed"]]
        if alive_a:
            a_ok.append(agg(alive_a, "steps"))
        if alive_b:
            b_ok.append(agg(alive_b, "steps"))
        mark = "*" if len(alive_a) < len(at) or len(alive_b) < len(bt) else " "
        rw = lambda tr: ",".join(str(x["reward"]) for x in tr)  # noqa: E731
        print(f"{t:28}{agg(at,'steps'):>7.1f}{agg(bt,'steps'):>7.1f}"
              f"{rw(at):>7}{rw(bt):>7}{mark}")

    print(f"\nmean steps (completed only): A={st.mean(a_ok):.2f}  "
          f"B={st.mean(b_ok):.2f}  gap={st.mean(a_ok)-st.mean(b_ok):+.2f}")
    print('  "steps" = steps carrying >=1 tool call. Raw len(steps): '
          f"A={st.mean([agg(A[t]['trials'],'raw_steps') for t in shared]):.1f} "
          f"B={st.mean([agg(B[t]['trials'],'raw_steps') for t in shared]):.1f}")

    print("\nstructural metrics over shared tasks (all trials):")
    fa = [x for t in shared for x in A[t]["trials"]]
    fb = [x for t in shared for x in B[t]["trials"]]
    for lbl, num, den in (
        ("heredoc-authored", "heredoc", "bash"),
        ("env-probe", "probe", "bash"),
        ("exact-repeat", "repeats", "calls"),
    ):
        na, da = sum(x[num] for x in fa), sum(x[den] for x in fa)
        nb, db = sum(x[num] for x in fb), sum(x[den] for x in fb)
        print(f"  {lbl:18} A={na:4}/{da:4} ({na/max(da,1):5.1%})   "
              f"B={nb:4}/{db:4} ({nb/max(db,1):5.1%})")

    for lbl, f in (("A", fa), ("B", fb)):
        s = [v for x in f for v in x["stmts"]]
        tools: dict[str, int] = {}
        for x in f:
            for tool in x["tools"]:
                tools[tool] = tools.get(tool, 0) + 1
        tot = sum(tools.values())
        mix = "  ".join(
            f"{k} {v/tot:.1%}"
            for k, v in sorted(tools.items(), key=lambda kv: -kv[1])[:5]
        )
        print(f"  {lbl}: bash stmts median={st.median(s):.1f} "
              f"mean={st.mean(s):.1f} | memory-writes="
              f"{sum(x['memory'] for x in f)} | {mix}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    report(Path(sys.argv[1]), Path(sys.argv[2]))
