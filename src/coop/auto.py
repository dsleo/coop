"""Pick / claim / run logic shared by the CLI and the MCP server."""

from __future__ import annotations

from typing import Callable

from coop.board import CoopIssue, GhError, claim_issue, current_github_user, fetch_open_coop_issues, release_issue, select_auto_issue
from coop.config import CoopConfig, ProviderName, TrustLevel, effective_trust, load_trust, require_board_repo
from coop.runner import RunResult, execute_coop_run

OnPick = Callable[[CoopIssue, TrustLevel, int], None]
OnProgress = Callable[[int, str], None]


def claim_and_run(
    issue: CoopIssue,
    cfg: CoopConfig,
    provider: ProviderName,
    cap: int,
    trust: TrustLevel,
    me: str,
    claim: bool = True,
    on_progress: OnProgress | None = None,
) -> RunResult:
    """Claim, run, and release the claim again if the run failed or produced nothing."""
    if claim and not claim_issue(issue.repo, issue.number, me):
        raise GhError(f"{issue.repo}#{issue.number} was claimed by someone else just now.")
    try:
        res = execute_coop_run(issue, provider, cap, trust, cfg.max_turns, cfg.max_minutes, on_progress)
    except BaseException:
        release_issue(issue.repo, issue.number)
        raise
    if not res.pr_url:
        release_issue(issue.repo, issue.number)
    return res


def run_auto_once(
    cfg: CoopConfig,
    provider: ProviderName,
    max_tokens: int,
    include_own: bool = False,
    on_pick: OnPick | None = None,
    on_progress: OnProgress | None = None,
) -> tuple[CoopIssue, RunResult] | None:
    board = require_board_repo(cfg)
    me, trust = current_github_user(), load_trust()
    issues = fetch_open_coop_issues(board)
    trust_of = lambda i: effective_trust(i.repo, i.author, trust)
    while issues:
        picked = select_auto_issue(issues, me, max_tokens, trust_of, include_own)
        if not picked:
            return None
        issues.remove(picked)
        try:
            if not claim_issue(picked.repo, picked.number, me):
                continue  # lost a race: try the next best issue
        except GhError:
            continue  # e.g. no write access to that repo
        cap, level = min(max_tokens, picked.default_budget), trust_of(picked)
        if on_pick:
            on_pick(picked, level, cap)
        return picked, claim_and_run(picked, cfg, provider, cap, level, me, claim=False, on_progress=on_progress)
    return None
