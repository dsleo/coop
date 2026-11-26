from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from collections import defaultdict
from enum import Enum

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from coop import sandbox
from coop import doctor as doctor_mod
from coop.auto import claim_and_run, run_auto_once
from coop.board import (
    GhError,
    accept_invitation,
    sync_access,
    create_board,
    current_github_user,
    flag_issue,
    fetch_open_coop_issues,
    fetch_single_issue,
    infer_current_repo,
    invite_collaborator,
    list_registered_repos,
    parse_issue_ref,
    pending_invitations,
    register_repo_on_board,
    unregister_repo_from_board,
)
from coop.config import (
    RUNS_LOG,
    detect_providers,
    effective_trust,
    load_config,
    load_trust,
    require_board_repo,
    resolve_trust,
    save_config,
    save_trust,
)


class Provider(str, Enum):
    claude = "claude"
    codex = "codex"
    gemini = "gemini"


class Tier(str, Enum):
    safe = "safe"
    verified = "verified"
    trusted = "trusted"


app = typer.Typer(no_args_is_help=False, help="Spend spare AI subscription tokens helping friends on GitHub. Bare `coop` runs `coop auto`.")
console = Console()


def _progress_runner(provider: str, trust: str, cap: int, fn):
    """Run fn(on_progress) under a spinner showing live token usage."""
    if not sandbox.available() and trust != "trusted":
        console.print("[yellow]No OS sandbox on this platform (macOS only): relying on tool restrictions.[/yellow]")
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task(f"Starting {provider}...", total=None)
        return fn(lambda tokens, status: progress.update(task, description=f"[{provider} | {trust}] ~{tokens:,} / {cap:,} tokens ({status})"))


def _report(res) -> None:
    console.print(f"[bold]Outcome:[/bold] {res.status} — ~{res.tokens_used:,} / {res.max_tokens:,} tokens (estimate)")
    if res.skipped_files:
        console.print(f"[yellow]Left out as unsafe:[/yellow] {', '.join(res.skipped_files)}")
    if res.pr_url:
        console.print(f"[green]Opened Draft PR:[/green] {res.pr_url}")
    else:
        console.print("[yellow]No changes produced; no PR opened and the issue was released.[/yellow]")


def emit(obj) -> None:
    """Machine-readable output for AI agents (stdout only, no rich markup)."""
    print(json.dumps(obj, indent=2))


def _res_dict(issue, res) -> dict:
    return {
        "status": res.status,  # complete | budget | timeout | failed
        "issue": f"{issue.repo}#{issue.number}",
        "issue_url": res.issue_url,
        "pr_url": res.pr_url,
        "provider": res.provider,
        "trust": res.trust_level,
        "tokens_used": res.tokens_used,
        "max_tokens": res.max_tokens,
        "skipped_files": res.skipped_files,
    }


def _auto(provider: Provider | None, max_tokens: int | None, count: int, include_own: bool, json_out: bool = False) -> None:
    console.quiet = json_out
    cfg = load_config()
    prov = provider.value if provider else cfg.default_provider
    remaining = max_tokens or cfg.default_max_tokens
    results: list[dict] = []
    for step in range(1, count + 1):
        if remaining < 10_000:
            console.print("[yellow]Under 10k tokens left; stopping.[/yellow]")
            break

        def on_pick(issue, level, cap):
            console.print(f"[cyan][{step}/{count}] {issue.repo}#{issue.number}:[/cyan] {issue.title} [dim]({level}, cap {cap:,})[/dim]")

        out = run_auto_once(cfg, prov, remaining, include_own, on_pick=on_pick, on_progress=_spinner_hook())
        if out is None:
            console.print("[green]Queue clear: no matching open coop-help issues right now.[/green]")
            break
        remaining -= out[1].tokens_used
        results.append(_res_dict(*out))
        _report(out[1])
    if json_out:
        emit({"status": "completed" if results else "empty", "runs": results})


def _spinner_hook():
    # Plain line updates keep auto mode simple; `coop run` shows the spinner.
    last = {"t": 0}

    def hook(tokens: int, status: str) -> None:
        if tokens - last["t"] >= 5_000:
            last["t"] = tokens
            console.print(f"[dim]  ~{tokens:,} tokens ({status})[/dim]")

    return hook


@app.callback(invoke_without_command=True)
def _default(
    ctx: typer.Context,
    board: str | None = typer.Option(None, "--board", help="Use this board for this command only (default: the one saved by `coop init`)"),
) -> None:
    if board:
        os.environ["COOP_BOARD"] = board
    if ctx.invoked_subcommand is None:
        _auto(None, None, 1, False)


@app.command()
def auto(
    provider: Provider | None = typer.Option(None, "--provider", "-p"),
    max_tokens: int | None = typer.Option(None, "--max-tokens", "-t", help="Total token budget for this session"),
    count: int = typer.Option(1, "--count", "-n", help="Max issues to tackle sequentially"),
    include_own: bool = typer.Option(False, "--include-own"),
    json_out: bool = typer.Option(False, "--json", help="Print a JSON result (for AI agents)"),
) -> None:
    """Pick the best open friend issue, claim it, solve it within budget, open a Draft PR."""
    _auto(provider, max_tokens, count, include_own, json_out)


@app.command()
def init(
    board_repo: str | None = typer.Argument(None, help="Shared board repo, e.g. my-group/coop-board (omit it to use the board you were invited to)"),
    create: bool = typer.Option(False, "--create", help="Create the board repo first. Only one person does this."),
    public: bool = typer.Option(False, "--public", help="With --create: make the board public (default private)."),
    provider: Provider = typer.Option(Provider.claude, "--provider", "-p"),
    max_tokens: int = typer.Option(100_000, "--max-tokens", "-t"),
) -> None:
    """Configure coop with the group's board repo (and optionally create it)."""
    me = current_github_user()  # fails early if `gh` is not logged in
    if board_repo is None:
        if create:
            raise ValueError("--create needs a board name, e.g. `coop init my-group/coop-board --create`")
        found = [i for i in pending_invitations() if i["repository"]["full_name"].lower().endswith("/coop-board")]
        if len(found) != 1:
            raise ValueError("Pass the board name: `coop init <owner>/coop-board`" + (f" (invitations to choose from: {', '.join(i['repository']['full_name'] for i in found)})" if found else " (no pending coop-board invitation found; ask whoever runs the board to `coop invite` you)"))
        board_repo = found[0]["repository"]["full_name"]
        if not typer.confirm(f"Join the board {board_repo} (invited by {found[0]['inviter']['login']})?", default=True):
            raise typer.Abort()
    if create:
        create_board(board_repo, public)
        console.print(f"[green]Created {'public' if public else 'private'} board repo {board_repo}[/green]")
    try:
        list_registered_repos(board_repo)  # verifies access
    except GhError:
        # a private board is unreadable until its invitation is accepted: you named this board, so accept it
        inv = next((i for i in pending_invitations() if i["repository"]["full_name"].lower() == board_repo.lower()), None)
        if not inv:
            raise
        accept_invitation(inv["id"])
        console.print(f"[green]Accepted your invitation to {board_repo}[/green]")
        list_registered_repos(board_repo)
    providers = detect_providers()
    cfg = load_config()
    cfg.board_repo, cfg.available_providers, cfg.default_max_tokens = board_repo, providers, max_tokens
    cfg.default_provider = provider.value if provider.value in providers or not providers else providers[0]
    save_config(cfg)
    console.print(f"[green]Logged in as {me}. Board:[/green] {board_repo}")
    console.print(f"[green]Detected CLIs:[/green] {', '.join(providers) or 'none found on PATH'}; default {cfg.default_provider}, {max_tokens:,} tokens")


@app.command()
def invite(
    user: str = typer.Argument(..., help="GitHub username"),
    repo: str | None = typer.Argument(None, help="Repo to invite them to (default: the board repo)"),
) -> None:
    """Give a friend write access (needed to claim issues and push branches)."""
    target = repo or require_board_repo(load_config())
    invite_collaborator(target, user.lstrip("@"))
    console.print(f"[green]Invited {user} to {target} with write access. They run `coop accept`.[/green]")


@app.command()
def accept(
    repo: str | None = typer.Argument(None, help="Accept only the invitation to this owner/repo, without asking"),
    list_only: bool = typer.Option(False, "--list", help="Only show pending invitations (accepts nothing)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept, without asking, every invitation to a repo on your board"),
    all_: bool = typer.Option(False, "--all", help="With --yes: also accept invitations to repos that are NOT on your board"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show pending GitHub repo invitations and accept them (asks one by one unless told otherwise)."""
    cfg = load_config()
    on_board = {e.repo.lower() for e in list_registered_repos(cfg.board_repo)} if cfg.board_repo else set()
    rows = [
        {"id": i["id"], "repo": i["repository"]["full_name"], "invited_by": i["inviter"]["login"],
         "permission": i.get("permissions", "?"), "on_board": i["repository"]["full_name"].lower() in on_board}
        for i in pending_invitations()
    ]
    if json_out and list_only:
        emit(rows)
        return
    if not rows:
        emit({"accepted": []}) if json_out else console.print("No pending invitations.")
        return
    if not json_out:
        table = Table(title="Pending repository invitations")
        for col in ("Repository", "Invited by", "Access", "On your coop board?"):
            table.add_column(col)
        for r in rows:
            table.add_row(r["repo"], r["invited_by"], r["permission"], "[green]yes[/green]" if r["on_board"] else "[yellow]no (not a coop repo)[/yellow]")
        console.print(table)
    if list_only:
        return
    accepted = []
    for r in rows:
        if repo and r["repo"].lower() != repo.lower():
            continue
        if repo:
            ok = True
        elif yes:
            ok = r["on_board"] or all_
        else:  # interactive: default yes only for repos on your board
            ok = typer.confirm(f"Accept {r['repo']} (from {r['invited_by']}, {r['permission']} access)?", default=r["on_board"])
        if ok:
            accept_invitation(r["id"])
            accepted.append(r["repo"])
            console.print(f"[green]Joined {r['repo']}[/green]")
    if json_out:
        emit({"accepted": accepted})


@app.command()
def register(
    repo: str | None = typer.Argument(None, help="owner/repo (default: current git repo)"),
    budget: int = typer.Option(100_000, "--budget", "-b", help="Max tokens a helper may spend per issue"),
) -> None:
    """Add a repo to the board and create the coop labels on it."""
    board = require_board_repo(load_config())
    console.print(f"Board: [bold]{board}[/bold]")
    console.print(f"[green]{register_repo_on_board(board, repo or infer_current_repo(), budget)}[/green]")


@app.command()
def unregister(repo: str | None = typer.Argument(None)) -> None:
    """Remove a repo from the board."""
    console.print(f"[yellow]{unregister_repo_from_board(require_board_repo(load_config()), repo or infer_current_repo())}[/yellow]")


@app.command()
def repos(json_out: bool = typer.Option(False, "--json")) -> None:
    """Show registered repos and your local trust tier for each."""
    board = require_board_repo(load_config())
    entries = list_registered_repos(board)
    if json_out:
        emit([{"repo": e.repo, "default_budget": e.default_budget, "trust": resolve_trust(e.repo)} for e in entries])
        return
    table = Table(title=f"Registered repositories ({board})")
    for col in ("Repository", "Default budget", "Your trust"):
        table.add_column(col)
    for e in entries:
        table.add_row(e.repo, f"{e.default_budget:,}", resolve_trust(e.repo))
    console.print(table)


@app.command()
def flag(
    issue_number: int = typer.Argument(..., help="The issue's number on GitHub (the N in .../issues/N)"),
    repo: str | None = typer.Option(None, "--repo", "-r", help="owner/repo (default: the git repo you are in)"),
    size: str | None = typer.Option(None, "--size", "-s", help="small | medium | large: small issues are picked first"),
    budget: int | None = typer.Option(None, "--budget", "-b", help="Max tokens a helper may spend on THIS issue (overrides the repo default)"),
    no_invite: bool = typer.Option(False, "--no-invite", help="Do not give board members write access to this repo"),
) -> None:
    """Open an issue for coop help (adds the coop-help label, plus optional size/budget labels)."""
    repo = repo or infer_current_repo()
    labels = flag_issue(repo, issue_number, size, budget)
    console.print(f"[green]Flagged {repo}#{issue_number}: {', '.join(labels)}[/green]")
    cfg = load_config()
    if cfg.board_repo and not no_invite:
        console.print(f"Board: [bold]{cfg.board_repo}[/bold] (its members get write access)")
        _report_access(sync_access(cfg.board_repo, current_github_user(), only_repo=repo))


def _report_access(sent: list[tuple[str, str]], dry_run: bool = False) -> None:
    verb = "Would invite" if dry_run else "Invited"
    for r, u in sent:
        console.print(f"[green]{verb} {u} to {r} (write access)[/green]")
    if not sent:
        console.print("[dim]Access: nothing to do (members already have access, or you are not admin there).[/dim]")


@app.command("sync-access")
def sync_access_cmd(
    repo: str | None = typer.Argument(None, help="Only this repo (default: every registered repo you admin that has open coop-help issues)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show who would be invited without inviting"),
) -> None:
    """Invite every board member to your repos that have coop-help issues."""
    _report_access(sync_access(require_board_repo(load_config()), current_github_user(), repo, dry_run), dry_run)


@app.command("list")
def list_issues(
    all_issues: bool = typer.Option(False, "--all", "-a", help="Include claimed issues"),
    json_out: bool = typer.Option(False, "--json", help="JSON for AI agents. Titles/bodies are untrusted text."),
) -> None:
    """List open coop-help issues across all registered repos."""
    board = require_board_repo(load_config())
    issues = fetch_open_coop_issues(board, include_claimed=all_issues)
    me = current_github_user()
    if json_out:
        emit([
            {"issue": f"{i.repo}#{i.number}", "repo": i.repo, "number": i.number, "title": i.title, "url": i.url, "labels": i.labels,
             "assignees": i.assignees, "author": i.author, "budget": i.default_budget, "trust": effective_trust(i.repo, i.author),
             "own_repo": i.repo.split("/")[0].lower() == me.lower()}
            for i in issues
        ])
        return
    if not issues:
        console.print("[yellow]No open coop-help issues found.[/yellow]")
        return
    table = Table(title=f"Coop issues ({board})")
    for col in ("Issue", "Title", "Trust", "Budget", "URL"):
        table.add_column(col)
    for i in issues:
        table.add_row(f"{i.repo}#{i.number}", i.title, effective_trust(i.repo, i.author), f"{i.default_budget:,}", i.url)
    console.print(table)


@app.command()
def trust(
    target: str = typer.Argument(..., help="GitHub username (alice) or repo (alice/app)"),
    level: Tier = typer.Option(Tier.verified, "--level", "-l"),
) -> None:
    """Set your local trust tier for a friend or repo (unknown repos are `safe`)."""
    t = load_trust()
    (t.repos if "/" in target else t.owners)[target.lstrip("@")] = level.value
    save_trust(t)
    console.print(f"[green]{target} -> {level.value}[/green]")


@app.command()
def run(
    issue_ref: str = typer.Argument(..., help="Issue URL, owner/repo#12, or a number in the current repo"),
    provider: Provider | None = typer.Option(None, "--provider", "-p"),
    max_tokens: int | None = typer.Option(None, "--max-tokens", "-t"),
    trust_level: Tier | None = typer.Option(None, "--trust", help="Override the tier for this run"),
    no_claim: bool = typer.Option(False, "--no-claim"),
    json_out: bool = typer.Option(False, "--json", help="Print a JSON result (for AI agents)"),
) -> None:
    """Solve one specific issue in a throwaway clone and open a Draft PR."""
    cfg = load_config()
    prov = provider.value if provider else cfg.default_provider
    repo, number = parse_issue_ref(issue_ref, infer_current_repo() if issue_ref.isdigit() else None)
    entry = next((e for e in list_registered_repos(cfg.board_repo) if e.repo.lower() == repo.lower()), None) if cfg.board_repo else None
    issue = fetch_single_issue(repo, number, entry.default_budget if entry else cfg.default_max_tokens)
    cap = max_tokens or issue.default_budget
    level = trust_level.value if trust_level else effective_trust(repo, issue.author)
    me = current_github_user()
    if json_out:
        console.quiet = True
        res = claim_and_run(issue, cfg, prov, cap, level, me, not no_claim)
        emit(_res_dict(issue, res))
        return
    res = _progress_runner(prov, level, cap, lambda hook: claim_and_run(issue, cfg, prov, cap, level, me, not no_claim, hook))
    _report(res)


@app.command()
def stats(json_out: bool = typer.Option(False, "--json")) -> None:
    """Show local token spend (estimates) and PRs by provider."""
    if not RUNS_LOG.exists():
        emit({}) if json_out else console.print("[yellow]No coop runs recorded yet.[/yellow]")
        return
    agg: dict[str, dict[str, int]] = defaultdict(lambda: {"runs": 0, "tokens": 0, "prs": 0})
    for line in RUNS_LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            a = agg[row["provider"]]
            a["runs"] += 1
            a["tokens"] += int(row["tokens_used"])
            a["prs"] += 1 if row.get("pr_url") else 0
    if json_out:
        emit(agg)
        return
    table = Table(title="Local coop contributions (token counts are estimates)")
    for col in ("Provider", "Runs", "Tokens", "PRs"):
        table.add_column(col)
    for prov, a in agg.items():
        table.add_row(prov, str(a["runs"]), f"{a['tokens']:,}", str(a["prs"]))
    console.print(table)


@app.command()
def doctor(json_out: bool = typer.Option(False, "--json", help="JSON for AI agents")) -> None:
    """Check prerequisites and setup; prints the exact fix for anything missing."""
    checks = doctor_mod.run_checks()
    ready = all(c["ok"] or not c["required"] for c in checks)
    if json_out:
        emit({"ready": ready, "checks": checks})
    else:
        for c in checks:
            mark = "[green]ok[/green]" if c["ok"] else ("[red]MISSING[/red]" if c["required"] else "[yellow]warn[/yellow]")
            console.print(f"{mark}  {c['name']}: {c['detail']}" + (f"\n      fix: {c['fix']}" if c["fix"] else ""))
        console.print("\n[green]Ready.[/green]" if ready else "\n[red]Not ready: apply the fixes above.[/red]")
    raise typer.Exit(0 if ready else 1)


class Target(str, Enum):
    all = "all"
    claude = "claude"
    codex = "codex"
    gemini = "gemini"


_BLOCK = re.compile(r"<!-- coop:start -->.*?<!-- coop:end -->\n?", re.S)


@app.command("install-skill")
def install_skill(target: Target = typer.Option(Target.all, "--target", "-t", help="Which agent CLI to teach (all = every one found on PATH)")) -> None:
    """Teach your AI agent CLIs how to use coop (so you can just ask "any issue I can help with?")."""
    text = (Path(__file__).with_name("SKILL.md")).read_text(encoding="utf-8")
    body = text.split("---", 2)[2].strip()
    home = Path.home()
    wanted = detect_providers() if target == Target.all else [target.value]
    for name in wanted:
        if name == "claude":
            dest = home / ".claude" / "skills" / "coop" / "SKILL.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        else:
            dest = home / f".{name}" / ("AGENTS.md" if name == "codex" else "GEMINI.md")
            dest.parent.mkdir(parents=True, exist_ok=True)
            block = f"<!-- coop:start -->\n# coop\n\n{body}\n<!-- coop:end -->\n"
            old = dest.read_text(encoding="utf-8") if dest.exists() else ""
            dest.write_text(_BLOCK.sub("", old).rstrip() + ("\n\n" if old.strip() else "") + block, encoding="utf-8")
        console.print(f"[green]Installed coop instructions for {name}:[/green] {dest}")
    if not wanted:
        console.print("[yellow]No claude/codex/gemini CLI found on PATH.[/yellow]")


@app.command()
def mcp() -> None:
    """Start the optional stdio MCP server (Claude Desktop etc.)."""
    from coop.mcp_server import serve_mcp  # imported lazily: keeps the CLI fast

    serve_mcp()


def main() -> None:
    try:
        app()
    except (GhError, RuntimeError, ValueError) as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
