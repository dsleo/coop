from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from coop.auto import claim_and_run, run_auto_once
from coop.board import current_github_user, fetch_open_coop_issues, fetch_single_issue, parse_issue_ref, register_repo_on_board
from coop.config import ProviderName, effective_trust, load_config, require_board_repo

mcp = FastMCP("coop")


def _summary(res) -> dict:
    return {
        "issue_url": res.issue_url,
        "provider": res.provider,
        "trust_level": res.trust_level,
        "tokens_used": res.tokens_used,
        "max_tokens": res.max_tokens,
        "status": res.status,
        "pr_url": res.pr_url,
    }


@mcp.tool()
def list_coop_issues(include_claimed: bool = False) -> list[dict]:
    """List open `coop-help` issues across all repositories registered on the coop-board."""
    issues = fetch_open_coop_issues(require_board_repo(load_config()), include_claimed)
    return [
        {"repo": i.repo, "number": i.number, "title": i.title, "url": i.url, "labels": i.labels,
         "default_budget": i.default_budget, "trust_tier": effective_trust(i.repo, i.author)}
        for i in issues
    ]


@mcp.tool()
def register_coop_repo(repo: str, default_budget: int = 100_000) -> str:
    """Register a GitHub repository (`owner/repo`, write access required) on the coop-board."""
    return register_repo_on_board(require_board_repo(load_config()), repo, default_budget)


@mcp.tool()
def run_coop_auto(provider: ProviderName | None = None, max_tokens: int | None = None, include_own: bool = False) -> dict:
    """Pick an open issue from a friend's repo, solve it within the budget, and open a Draft PR."""
    cfg = load_config()
    out = run_auto_once(cfg, provider or cfg.default_provider, max_tokens or cfg.default_max_tokens, include_own)
    if out is None:
        return {"status": "empty", "message": "No matching open coop-help issues found."}
    return _summary(out[1])


@mcp.tool()
def run_coop_issue(issue_url: str, provider: ProviderName | None = None, max_tokens: int | None = None) -> dict:
    """Claim a specific issue (URL or `owner/repo#N`) and solve it within a token budget."""
    cfg = load_config()
    repo, number = parse_issue_ref(issue_url)
    issue = fetch_single_issue(repo, number, cfg.default_max_tokens)
    cap = max_tokens or cfg.default_max_tokens
    res = claim_and_run(issue, cfg, provider or cfg.default_provider, cap, effective_trust(repo, issue.author), current_github_user())
    return _summary(res)


def serve_mcp() -> None:
    mcp.run(transport="stdio")
