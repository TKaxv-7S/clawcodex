"""Tests for the desktop ``projects.tree`` grouping (src/server/desktop_projects)."""

from __future__ import annotations

from src.server.desktop_projects import NO_PROJECT_ID, build_project_tree


def _row(sid: str, cwd: str | None, last: float = 0.0) -> dict:
    return {"id": sid, "cwd": cwd, "last_active": last, "title": sid}


def test_groups_sessions_by_repo_root_into_auto_projects():
    rows = [_row("a", "/repo/src", 3.0), _row("b", "/repo", 2.0)]
    tree = build_project_tree(
        rows,
        repo_root_of=lambda cwd: "/repo" if cwd.startswith("/repo") else None,
        worktrees_of=lambda root: ["/repo"],
    )
    assert len(tree["projects"]) == 1
    proj = tree["projects"][0]
    assert proj["id"] == "/repo" and proj["isAuto"] is True
    assert proj["sessionCount"] == 2
    # Both sessions are in the main checkout → the single home/main lane.
    lanes = proj["repos"][0]["groups"]
    assert len(lanes) == 1 and lanes[0]["isMain"] is True
    assert {s["id"] for s in lanes[0]["sessions"]} == {"a", "b"}


def test_linked_worktree_session_gets_its_own_lane():
    rows = [_row("main", "/repo", 1.0), _row("feat", "/repo/.worktrees/feature", 2.0)]
    tree = build_project_tree(
        rows,
        repo_root_of=lambda cwd: "/repo",
        worktrees_of=lambda root: ["/repo", "/repo/.worktrees/feature"],
    )
    lanes = tree["projects"][0]["repos"][0]["groups"]
    main = [g for g in lanes if g["isMain"]]
    linked = [g for g in lanes if not g["isMain"]]
    assert len(main) == 1 and len(linked) == 1
    assert linked[0]["path"] == "/repo/.worktrees/feature"
    assert [s["id"] for s in linked[0]["sessions"]] == ["feat"]


def test_non_repo_and_cwdless_sessions_fall_into_home_bucket():
    rows = [_row("x", None), _row("y", "/tmp/scratch")]
    tree = build_project_tree(
        rows,
        repo_root_of=lambda cwd: None,  # nothing is a repo
        worktrees_of=lambda root: [],
    )
    assert len(tree["projects"]) == 1
    home = tree["projects"][0]
    assert home["id"] == NO_PROJECT_ID and home["isNoProject"] is True
    assert home["sessionCount"] == 2


def test_windows_paths_group_case_and_separator_insensitively():
    # git emits forward-slash roots; session cwds are backslash + can differ in
    # drive case. They must still land in ONE repo, one home lane.
    rows = [
        _row("a", "C:\\Repo\\src", 1.0),
        _row("b", "c:/repo", 2.0),
    ]
    tree = build_project_tree(
        rows,
        repo_root_of=lambda cwd: "C:/Repo",
        worktrees_of=lambda root: ["C:/Repo"],
    )
    assert len(tree["projects"]) == 1
    lanes = tree["projects"][0]["repos"][0]["groups"]
    assert len(lanes) == 1 and lanes[0]["isMain"] is True
    assert {s["id"] for s in lanes[0]["sessions"]} == {"a", "b"}


def test_active_id_and_scoped_ids():
    rows = [_row("a", "/repo", 1.0)]
    tree = build_project_tree(
        rows,
        repo_root_of=lambda cwd: "/repo",
        worktrees_of=lambda root: ["/repo"],
        active_cwd="/repo",
    )
    assert tree["active_id"] == "/repo"
    assert tree["scoped_session_ids"] == ["a"]
