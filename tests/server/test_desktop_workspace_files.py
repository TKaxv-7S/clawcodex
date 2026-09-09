"""Workspace-confined file reading for the web client's sidebar.

The contract these pin: a page is lines, a failure is a code, and nothing
outside the workspace root is ever read — not through ``..``, not through a
symlink that points out of the tree.
"""

from __future__ import annotations

import os

import pytest

from src.server.desktop_workspace_files import (
    MAX_BYTES,
    MAX_ENTRIES,
    MAX_LINES,
    list_dir,
    read_file,
)


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return root


# ── read_file ────────────────────────────────────────────────────────────────


def test_reads_a_whole_small_file_as_one_page(workspace):
    (workspace / "notes.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    page = read_file(str(workspace), "notes.txt")

    assert page["ok"] is True
    assert page["text"] == "alpha\nbeta\ngamma"
    assert page["lines"] == 3
    assert page["offset"] == 1
    assert page["eof"] is True
    assert page["absolute_path"] == str(workspace / "notes.txt")


def test_a_page_keeps_its_last_line_when_the_file_has_no_final_newline(workspace):
    (workspace / "notes.txt").write_text("alpha\nbeta", encoding="utf-8")

    page = read_file(str(workspace), "notes.txt")

    assert page["text"] == "alpha\nbeta"
    assert page["lines"] == 2


def test_one_empty_line_and_a_page_past_the_end_read_differently(workspace):
    (workspace / "blank.txt").write_text("\n", encoding="utf-8")

    empty_line = read_file(str(workspace), "blank.txt")
    past_end = read_file(str(workspace), "blank.txt", offset=5)

    assert (empty_line["lines"], empty_line["text"]) == (1, "")
    assert (past_end["lines"], past_end["text"]) == (0, "")
    assert past_end["eof"] is True


def test_an_empty_file_is_a_page_of_no_lines(workspace):
    (workspace / "empty.txt").write_text("", encoding="utf-8")

    page = read_file(str(workspace), "empty.txt")

    assert page["ok"] is True
    assert (page["lines"], page["text"], page["eof"]) == (0, "", True)


def test_pages_walk_the_file_in_order_and_only_the_last_one_is_eof(workspace):
    (workspace / "long.txt").write_text(
        "".join(f"line {n}\n" for n in range(1, 8)), encoding="utf-8"
    )

    first = read_file(str(workspace), "long.txt", offset=1, limit=3)
    second = read_file(str(workspace), "long.txt", offset=4, limit=3)
    third = read_file(str(workspace), "long.txt", offset=7, limit=3)

    assert (first["text"], first["eof"]) == ("line 1\nline 2\nline 3", False)
    assert (second["text"], second["eof"]) == ("line 4\nline 5\nline 6", False)
    assert (third["text"], third["eof"], third["lines"]) == ("line 7", True, 1)


def test_a_page_that_ends_exactly_on_the_last_line_is_not_guessed_to_be_eof(workspace):
    """The final page's ``eof`` is decided by a read past it, not by arithmetic."""
    (workspace / "six.txt").write_text(
        "".join(f"line {n}\n" for n in range(1, 7)), encoding="utf-8"
    )

    page = read_file(str(workspace), "six.txt", offset=4, limit=3)

    assert page["lines"] == 3
    assert page["eof"] is True


def test_limit_narrows_but_never_widens_the_page(workspace):
    (workspace / "long.txt").write_text(
        "".join(f"line {n}\n" for n in range(1, 20)), encoding="utf-8"
    )

    page = read_file(str(workspace), "long.txt", limit=MAX_LINES * 10)

    assert page["lines"] == 19


def test_crlf_endings_do_not_reach_the_reader(workspace):
    (workspace / "dos.txt").write_bytes(b"alpha\r\nbeta\r\n")

    page = read_file(str(workspace), "dos.txt")

    assert page["text"] == "alpha\nbeta"
    assert page["lines"] == 2


def test_an_absolute_path_inside_the_workspace_is_accepted(workspace):
    (workspace / "notes.txt").write_text("hello\n", encoding="utf-8")

    page = read_file(str(workspace), str(workspace / "notes.txt"))

    assert page["ok"] is True
    assert page["text"] == "hello"


def test_a_missing_file_is_reported_as_gone(workspace):
    result = read_file(str(workspace), "nope.txt")

    assert result["ok"] is False
    assert result["error"]["code"] == "workspace-file/not-found"


def test_a_directory_has_no_text_to_show(workspace):
    (workspace / "src").mkdir()

    result = read_file(str(workspace), "src")

    assert result["error"]["code"] == "workspace-file/not-regular-file"


def test_a_binary_file_is_refused_as_not_text(workspace):
    (workspace / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00binary")

    result = read_file(str(workspace), "logo.png")

    assert result["error"]["code"] == "workspace-file/not-text"


def test_a_page_over_the_byte_cap_is_refused_with_its_limit(workspace):
    line = ("x" * 4096) + "\n"
    (workspace / "huge.txt").write_text(line * ((MAX_BYTES // len(line)) + 8), encoding="utf-8")

    result = read_file(str(workspace), "huge.txt")

    assert result["error"]["code"] == "workspace-file/too-large"
    assert result["error"]["details"]["limit"] == MAX_BYTES


def test_a_path_climbing_out_of_the_workspace_is_refused(workspace, tmp_path):
    (tmp_path / "secret.txt").write_text("private\n", encoding="utf-8")

    result = read_file(str(workspace), "../secret.txt")

    assert result["error"]["code"] == "workspace-file/outside-workspace"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_symlink_pointing_out_of_the_workspace_is_refused(workspace, tmp_path):
    (tmp_path / "secret.txt").write_text("private\n", encoding="utf-8")
    (workspace / "escape.txt").symlink_to(tmp_path / "secret.txt")

    result = read_file(str(workspace), "escape.txt")

    assert result["error"]["code"] == "workspace-file/outside-workspace"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_symlink_staying_inside_the_workspace_is_read(workspace):
    (workspace / "real.txt").write_text("inside\n", encoding="utf-8")
    (workspace / "link.txt").symlink_to(workspace / "real.txt")

    page = read_file(str(workspace), "link.txt")

    assert page["ok"] is True
    assert page["text"] == "inside"


def test_a_filename_keeps_the_spaces_it_was_given(workspace):
    # A POSIX name may legally begin or end with a space; trimming it would
    # read a different file, or none.
    (workspace / " spaced.txt").write_text("held\n", encoding="utf-8")

    page = read_file(str(workspace), " spaced.txt")

    assert page["ok"] is True
    assert page["text"] == "held"


def test_a_session_without_a_workspace_says_so(workspace):
    result = read_file("", "notes.txt")

    assert result["error"]["code"] == "workspace-file/unknown-workspace"


def test_the_version_changes_when_the_file_does(workspace):
    target = workspace / "notes.txt"
    target.write_text("one\n", encoding="utf-8")
    before = read_file(str(workspace), "notes.txt")["version"]

    target.write_text("one\ntwo\n", encoding="utf-8")
    after = read_file(str(workspace), "notes.txt")["version"]

    assert before != after


# ── list_dir ─────────────────────────────────────────────────────────────────


def test_lists_the_root_when_no_path_is_given(workspace):
    (workspace / "src").mkdir()
    (workspace / "README.md").write_text("hi\n", encoding="utf-8")

    listing = list_dir(str(workspace))

    assert listing["ok"] is True
    assert listing["truncated"] is False
    assert {entry["name"]: entry["type"] for entry in listing["entries"]} == {
        "src": "directory",
        "README.md": "file",
    }


def test_a_file_entry_carries_its_size(workspace):
    # Bytes, not text: Python's text mode would write "\r\n" on Windows and
    # make this six-byte file seven bytes long there.
    (workspace / "README.md").write_bytes(b"hello\n")

    entry = list_dir(str(workspace))["entries"][0]

    assert entry["size"] == 6


def test_dotfiles_are_listed_like_any_other_name(workspace):
    (workspace / ".env").write_text("SECRET=1\n", encoding="utf-8")

    names = [entry["name"] for entry in list_dir(str(workspace))["entries"]]

    assert names == [".env"]


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_broken_link_is_listed_as_something_that_cannot_be_opened(workspace):
    (workspace / "dangling").symlink_to(workspace / "gone.txt")

    entries = list_dir(str(workspace))["entries"]

    assert entries == [{"name": "dangling", "type": "other"}]


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_a_link_out_of_the_workspace_is_not_offered_as_openable(workspace, tmp_path):
    """Reported, but not as a file: opening it would be refused every time."""
    (tmp_path / "outside.txt").write_text("private\n", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    (workspace / "escape.txt").symlink_to(tmp_path / "outside.txt")
    (workspace / "escape-dir").symlink_to(tmp_path / "outside")

    entries = list_dir(str(workspace))["entries"]

    assert {entry["name"]: entry["type"] for entry in entries} == {
        "escape.txt": "other",
        "escape-dir": "other",
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_an_unreadable_directory_does_not_read_as_a_missing_one(workspace):
    locked = workspace / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        result = list_dir(str(workspace), "locked")
    finally:
        locked.chmod(0o755)

    assert result["error"]["code"] == "workspace-file/unavailable"


def test_a_level_of_exactly_the_cap_is_not_reported_as_cut(workspace):
    for index in range(MAX_ENTRIES):
        (workspace / f"file-{index:04d}.txt").write_text("x", encoding="utf-8")

    listing = list_dir(str(workspace))

    assert len(listing["entries"]) == MAX_ENTRIES
    assert listing["truncated"] is False


def test_a_long_level_is_cut_and_says_so(workspace):
    for index in range(MAX_ENTRIES + 5):
        (workspace / f"file-{index:04d}.txt").write_text("x", encoding="utf-8")

    listing = list_dir(str(workspace))

    assert len(listing["entries"]) == MAX_ENTRIES
    assert listing["truncated"] is True


def test_listing_a_file_is_reported_as_not_a_directory(workspace):
    (workspace / "README.md").write_text("hi\n", encoding="utf-8")

    result = list_dir(str(workspace), "README.md")

    assert result["error"]["code"] == "workspace-file/not-directory"


def test_listing_a_missing_directory_is_reported_as_gone(workspace):
    result = list_dir(str(workspace), "nowhere")

    assert result["error"]["code"] == "workspace-file/not-found"


def test_listing_outside_the_workspace_is_refused(workspace):
    result = list_dir(str(workspace), "..")

    assert result["error"]["code"] == "workspace-file/outside-workspace"


# ── the gateway methods ──────────────────────────────────────────────────────


def test_the_reads_are_reachable_over_the_socket(tmp_path):
    """Registered, dispatched, and defaulted to the session's workspace.

    The module above is pure; this is the wiring — a method missing from the
    handler table is a sidebar that shows nothing, and no unit test of
    ``read_file`` would notice.
    """
    from starlette.testclient import TestClient

    from src.server.desktop_serve import build_app
    from tests.server.test_desktop_gateway import (
        TOKEN,
        _drain_for_response,
        _fake_state,
        _rpc,
    )

    (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    state, _ = _fake_state(tmp_path)

    with TestClient(build_app(state)) as client, client.websocket_connect(
        f"/api/ws?token={TOKEN}"
    ) as ws:
        ws.receive_json()  # gateway.ready

        # The root comes from the server, not the call: an unknown session id
        # falls back to this server's workspace.
        _rpc(ws, "r1", "fs.list_dir", {"session_id": "nobody"})
        listing = _drain_for_response(ws, "r1", [])["result"]

        _rpc(ws, "r2", "fs.read_file", {"path": "notes.txt"})
        page = _drain_for_response(ws, "r2", [])["result"]

        _rpc(ws, "r3", "fs.read_file", {"path": "../outside.txt"})
        refused = _drain_for_response(ws, "r3", [])["result"]

        # The boundary is not a parameter: naming another root does not move it.
        _rpc(ws, "r4", "fs.read_file", {"cwd": "/", "path": "/etc/hosts"})
        elsewhere = _drain_for_response(ws, "r4", [])["result"]

        _rpc(ws, "r5", "fs.list_dir", {"cwd": "/", "path": "/etc"})
        listed_elsewhere = _drain_for_response(ws, "r5", [])["result"]

    assert listing["ok"] is True
    assert [entry["name"] for entry in listing["entries"]] == ["notes.txt"]
    assert (page["text"], page["lines"], page["eof"]) == ("alpha\nbeta", 2, True)
    assert refused["error"]["code"] == "workspace-file/outside-workspace"
    assert elsewhere["error"]["code"] == "workspace-file/outside-workspace"
    assert listed_elsewhere["error"]["code"] == "workspace-file/outside-workspace"
