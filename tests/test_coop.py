import subprocess

import pytest

from coop import sandbox
from coop.board import CoopIssue, parse_issue_ref, select_auto_issue
from coop.config import TrustConfig, effective_trust
from coop.runner import Usage, build_agent_command, sanitize_workspace, stage_changes


def issue(repo="alice/app", n=1, labels=(), budget=50_000, author="alice"):
    return CoopIssue(repo, n, "t", "u", "", list(labels), [], author, budget)


def test_parse_issue_ref():
    assert parse_issue_ref("https://github.com/a/b/issues/7") == ("a/b", 7)
    assert parse_issue_ref("a/b#3") == ("a/b", 3)
    assert parse_issue_ref("5", "x/y") == ("x/y", 5)
    with pytest.raises(ValueError):
        parse_issue_ref("nope")


def test_select_prefers_small_fitting_and_skips_own():
    issues = [issue("me/mine", 1, ["size:small"]), issue("bob/x", 2, ["size:large"]), issue("bob/y", 3, ["size:small"]), issue("bob/z", 4, ["size:small"], budget=900_000)]
    picked = select_auto_issue(issues, "me", 100_000, lambda i: "safe")
    assert picked.number == 3
    assert select_auto_issue(issues, "me", 100_000, lambda i: "safe", include_own=True).number in (1, 3)


def test_effective_trust_needs_known_author():
    t = TrustConfig(owners={"alice": "trusted"})
    assert effective_trust("alice/app", "alice", t) == "trusted"
    assert effective_trust("alice/app", "stranger", t) == "safe"
    assert effective_trust("bob/app", "bob", t) == "safe"


def test_claude_usage_dedupes_message_ids_and_counts_cache_creation():
    u = Usage("claude")
    ev = {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 9999}}}
    u.feed(ev), u.feed(ev)
    assert u.total == 115
    u.feed({"type": "result", "usage": {"input_tokens": 20, "output_tokens": 10, "cache_creation_input_tokens": 100}})
    assert u.total == 130


def test_codex_usage_excludes_cached_input():
    u = Usage("codex")
    u.feed({"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 900, "output_tokens": 50}})
    assert u.total == 150


def test_safe_tier_has_no_shell_for_claude_and_gemini():
    assert "Bash" not in " ".join(build_agent_command("claude", "p", "safe"))
    assert "yolo" not in build_agent_command("gemini", "p", "verified")
    assert "yolo" in build_agent_command("gemini", "p", "trusted")


def git(d, *a):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=d, check=True, capture_output=True)


def make_repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("x=1\n")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_stage_changes_filters_unsafe_and_keeps_sanitizer_deletions_out(tmp_path):
    repo = make_repo(tmp_path)
    sanitize_workspace(repo, "safe")
    (repo / "a.py").write_text("x=2\n")
    (repo / ".env").write_text("A=1\n")
    (repo / "link").symlink_to("/etc/passwd")
    staged, skipped, leak = stage_changes(repo)
    assert staged == ["a.py"]
    assert set(skipped) == {".env", "link"}
    assert leak is None


def test_stage_changes_detects_secret_content(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "notes.txt").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    assert stage_changes(repo)[2]


@pytest.mark.skipif(not sandbox.available(), reason="macOS only")
def test_sandbox_blocks_home_write_and_secret_read(tmp_path):
    from pathlib import Path

    probe = Path.home() / ".coop-sandbox-probe"
    try:
        cmd = sandbox.wrap(["sh", "-c", f"echo hi > '{probe}'"], tmp_path)
        assert subprocess.run(cmd, capture_output=True).returncode != 0 and not probe.exists()
    finally:
        probe.unlink(missing_ok=True)
    ok = sandbox.wrap(["sh", "-c", f"echo hi > '{tmp_path}/inside.txt'"], tmp_path)
    assert subprocess.run(ok, capture_output=True).returncode == 0
    if (Path.home() / ".ssh").is_dir():
        assert subprocess.run(sandbox.wrap(["ls", str(Path.home() / ".ssh")], tmp_path), capture_output=True).returncode != 0


def test_issue_budget_label_overrides_default():
    from coop.board import issue_budget

    assert issue_budget(["coop-help", "budget:50k"], 100_000) == 50_000
    assert issue_budget(["budget:12345"], 100_000) == 12_345
    assert issue_budget(["coop-help"], 100_000) == 100_000


def test_sync_access_invites_only_missing_members_on_admin_repos_with_issues(monkeypatch):
    from coop import board

    monkeypatch.setattr(board, "list_registered_repos", lambda b: [board.BoardRepoEntry("me/a"), board.BoardRepoEntry("me/idle"), board.BoardRepoEntry("bob/x")])
    monkeypatch.setattr(board, "has_admin_access", lambda r: r.startswith("me/"))
    monkeypatch.setattr(board, "has_open_coop_issues", lambda r: r != "me/idle")
    monkeypatch.setattr(board, "repo_collaborators", lambda r: ["me", "alice", "bob", "carol"] if r == "grp/board" else ["me", "alice"])
    sent = []
    monkeypatch.setattr(board, "invite_collaborator", lambda r, u, p="push": sent.append((r, u)))
    assert board.sync_access("grp/board", "me") == [("me/a", "bob"), ("me/a", "carol")]
    assert sent == [("me/a", "bob"), ("me/a", "carol")]
    assert board.sync_access("grp/board", "me", only_repo="me/idle", dry_run=True) == [("me/idle", "bob"), ("me/idle", "carol")]
    assert len(sent) == 2  # dry run sent nothing
