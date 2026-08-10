"""Backend for the desktop gateway's ``projects.tree`` method.

Groups saved + live sessions into the ``SidebarProjectTree[]`` shape the
desktop renderer expects (``ui-desktop/src/app/chat/sidebar/projects/
workspace-groups.ts``): one *project* per git repo root, each holding one
*repo* node whose *lanes* are the repo's checkouts (the main "home" lane plus
one per linked worktree a session sits in). Sessions with no cwd, or a cwd
that is not inside a git repo, fall into the synthetic "Home" bucket.

This is the data behind the sidebar's worktree display. The renderer layers
its live ``git worktree list`` probe on top (injecting EMPTY lanes for
worktrees with no sessions yet), so the backend only has to place the
sessions it knows about — but the grouping MUST agree with the renderer's
ids/labels or the two sets won't dedupe:

* ``NO_PROJECT_ID`` == ``"__no_project__"`` (workspace-groups.ts).
* a main-checkout lane id is ``f"{repo_root}::branch::{branch}"``
  (``branchLaneId``); the renderer folds every ``isMain`` lane into one home
  lane, so the exact branch only has to be self-consistent.
* a linked-worktree lane id/path is the worktree's absolute path.

Git resolution goes through :mod:`src.utils.git`, which now resolves the git
binary explicitly (:func:`src.utils.shell_platform.find_git`) — the whole
reason worktree display was blank on the Windows desktop was that a
GUI-launched backend's bare ``git`` wasn't on PATH, so every repo probe
failed and no repo/worktree lane was ever built.
"""

from __future__ import annotations

import os
from typing import Any, Callable

NO_PROJECT_ID = "__no_project__"


def _segments(path: str) -> list[str]:
    """Path split on either separator, trailing separators ignored."""
    return [seg for seg in path.replace("\\", "/").split("/") if seg]


def _is_windows_path(path: str) -> bool:
    # Drive-letter (``C:\`` / ``C:/``), UNC (``\\srv`` / ``//srv``), or any
    # backslash-rooted path. Mirrors the renderer's ``isWindowsPath`` so the
    # two sides fold case the same way.
    if len(path) >= 3 and path[1] == ":" and path[2] in ("\\", "/"):
        return True
    return path.startswith("\\") or path.startswith("//")


def _fold(path: str) -> list[str]:
    """Comparison segments: case-folded on Windows paths, verbatim on POSIX."""
    segs = _segments(path)
    return [s.lower() for s in segs] if _is_windows_path(path) else segs


def _path_under(parent: str, child: str) -> bool:
    """True when ``child`` equals or nests under ``parent`` (segment-wise,
    separator- and (on Windows) case-insensitive)."""
    p = _fold(parent)
    c = _fold(child)
    if not p or len(p) > len(c):
        return False
    return all(seg == c[i] for i, seg in enumerate(p))


def _basename(path: str) -> str:
    segs = _segments(path)
    return segs[-1] if segs else path


def _recency(row: dict[str, Any]) -> float:
    """Sort key: last activity, then creation. Both may be epoch floats or ISO
    strings (saved rows) — coerce to a comparable float, 0.0 when absent."""
    for key in ("last_active", "started_at"):
        val = row.get(key)
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str) and val:
            # ISO timestamps sort lexicographically within the desktop's own
            # rows; hash to a stable large-ish float only for ordering.
            return _iso_to_epoch(val)
    return 0.0


def _iso_to_epoch(value: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def build_project_tree(
    rows: list[dict[str, Any]],
    *,
    repo_root_of: Callable[[str], str | None],
    worktrees_of: Callable[[str], list[str]],
    active_cwd: str | None = None,
    preview_limit: int = 3,
) -> dict[str, Any]:
    """Group ``rows`` (SessionInfo dicts) into the sidebar project tree.

    ``repo_root_of(cwd)`` → the repo root for a cwd, or None. ``worktrees_of(
    repo_root)`` → the list of worktree paths for a repo (main first). Both are
    injected so this stays pure/testable; the gateway wraps them with the
    git-backed, per-cwd-memoized resolvers.
    """
    # repo_root → { "path", "lanes": {lane_key → lane dict} }
    projects: dict[str, dict[str, Any]] = {}
    home_sessions: list[dict[str, Any]] = []

    def _lane_for(repo_root: str, cwd: str) -> tuple[str, str, bool, str]:
        """(lane_key, label, is_main, lane_path) for a session cwd in a repo.

        The cwd sits in exactly one worktree — the one whose path is the
        longest prefix of cwd. The main worktree (repo_root itself) is the
        home lane; a linked worktree is its own lane keyed by its path.
        """
        best_path = repo_root
        best_len = -1
        for wt in worktrees_of(repo_root):
            if _path_under(wt, cwd) and len(_fold(wt)) > best_len:
                best_len = len(_fold(wt))
                best_path = wt
        is_main = _fold(best_path) == _fold(repo_root)
        if is_main:
            # Branch label is cosmetic here; the renderer relabels the home
            # lane from live git. Use the repo basename as a stable seed.
            return (f"{repo_root}::branch::", _basename(repo_root), True, repo_root)
        return (best_path, _basename(best_path), False, best_path)

    for row in rows:
        cwd = (row.get("cwd") or "").strip()
        repo_root = repo_root_of(cwd) if cwd else None
        if not repo_root:
            home_sessions.append(row)
            continue
        proj = projects.setdefault(repo_root, {"path": repo_root, "lanes": {}})
        lane_key, label, is_main, lane_path = _lane_for(repo_root, cwd)
        lane = proj["lanes"].setdefault(
            lane_key,
            {"id": lane_key, "label": label, "path": lane_path,
             "isMain": is_main, "sessions": []},
        )
        lane["sessions"].append(row)

    active_repo = repo_root_of(active_cwd.strip()) if active_cwd and active_cwd.strip() else None

    project_nodes: list[dict[str, Any]] = []
    for repo_root, proj in projects.items():
        lanes = list(proj["lanes"].values())
        all_sessions = [s for lane in lanes for s in lane["sessions"]]
        preview = sorted(all_sessions, key=_recency, reverse=True)[:preview_limit]
        project_nodes.append({
            "id": repo_root,
            "label": _basename(repo_root),
            "path": repo_root,
            "isAuto": True,
            "repos": [{
                "id": repo_root,
                "label": _basename(repo_root),
                "path": repo_root,
                "groups": lanes,
                "sessionCount": len(all_sessions),
            }],
            "sessionCount": len(all_sessions),
            "lastActive": max((_recency(s) for s in all_sessions), default=0.0),
            "previewSessions": preview,
        })

    project_nodes.sort(key=lambda p: p["lastActive"], reverse=True)

    if home_sessions:
        preview = sorted(home_sessions, key=_recency, reverse=True)[:preview_limit]
        project_nodes.append({
            "id": NO_PROJECT_ID,
            "label": "Home",
            "path": None,
            "isNoProject": True,
            "repos": [{
                "id": NO_PROJECT_ID,
                "label": "Home",
                "path": None,
                "groups": [{
                    "id": f"{NO_PROJECT_ID}::lane",
                    "label": "Home",
                    "path": None,
                    "isMain": False,
                    "sessions": home_sessions,
                }],
                "sessionCount": len(home_sessions),
            }],
            "sessionCount": len(home_sessions),
            "lastActive": max((_recency(s) for s in home_sessions), default=0.0),
            "previewSessions": preview,
        })

    return {
        "projects": project_nodes,
        "active_id": active_repo,
        "scoped_session_ids": [str(r.get("id")) for r in rows if r.get("id")],
    }


__all__ = ["build_project_tree", "NO_PROJECT_ID"]
