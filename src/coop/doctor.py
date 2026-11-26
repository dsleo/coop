"""Environment checks: tells a human or an AI agent exactly what is missing and how to fix it."""

from __future__ import annotations

import shutil
import subprocess
import sys

from coop import __version__, sandbox
from coop.board import GhError, list_registered_repos, pending_invitations
from coop.config import detect_providers, load_config


def _c(name: str, ok: bool, detail: str, fix: str | None = None, required: bool = True) -> dict:
    return {"name": name, "ok": ok, "detail": detail, "fix": None if ok else fix, "required": required}


def run_checks() -> list[dict]:
    checks = [_c("python>=3.11", sys.version_info >= (3, 11), sys.version.split()[0], "brew install python")]
    has_gh = shutil.which("gh") is not None
    checks.append(_c("gh installed", has_gh, shutil.which("gh") or "not found", "brew install gh"))
    authed = has_gh and subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0
    checks.append(_c("gh logged in", authed, "ok" if authed else "not logged in", "gh auth login  (interactive: the USER must run this in a terminal)"))
    helper = subprocess.run(["git", "config", "--get-regexp", r"credential\..*helper"], capture_output=True, text=True).stdout.strip()
    checks.append(_c("git credential helper (for pushing)", bool(helper), helper.splitlines()[0].split()[-1] if helper else "none configured", "gh auth setup-git"))
    providers = detect_providers()
    checks.append(_c("an AI CLI (claude/codex/gemini)", bool(providers), ", ".join(providers) or "none on PATH", "install one and log in to your subscription, e.g. `npm i -g @anthropic-ai/claude-code` then `claude`"))
    cfg = load_config()
    checks.append(_c("coop configured", bool(cfg.board_repo), cfg.board_repo or "no board set", "coop init <owner>/coop-board  (ask the user for the board name)"))
    if cfg.board_repo and authed:
        try:
            n = len(list_registered_repos(cfg.board_repo))
            checks.append(_c("board reachable", True, f"{cfg.board_repo}: {n} registered repos"))
        except GhError as e:
            checks.append(_c("board reachable", False, str(e)[:200], "coop accept  (pending invitation?) or check the board name"))
        try:
            n = len(pending_invitations())
            checks.append(_c("pending invitations", True, f"{n} pending (list: `coop accept --list`)", required=False))
        except GhError:
            pass
    checks.append(_c("OS sandbox", sandbox.available(), "macOS sandbox-exec" if sandbox.available() else "not available: tool restrictions only", required=False) | {"ok": sandbox.available(), "fix": None})
    checks.append(_c(f"coop {__version__}", True, "installed"))
    return checks
