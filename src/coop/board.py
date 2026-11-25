from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from typing import Any, Callable

import tomli_w

from coop.config import TrustLevel

SIZE_WEIGHTS = {"size:small": 30, "size:medium": 20, "size:large": 5}
ISSUE_URL_RE = re.compile(r"github\.com/([^/]+/[^/]+)/issues/(\d+)")
LABEL_HELP, LABEL_CLAIMED = "coop-help", "coop-claimed"


class GhError(RuntimeError):
    pass


def run_gh(args: list[str], check: bool = True, cwd: str | None = None) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=cwd)
    if check and proc.returncode != 0:
        raise GhError(proc.stderr.strip() or f"gh {' '.join(args)} failed")
    return proc.stdout.strip()


def current_github_user() -> str:
    return run_gh(["api", "user", "--jq", ".login"])


def infer_current_repo() -> str:
    return run_gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])


def has_push_access(repo: str) -> bool:
    return run_gh(["api", f"repos/{repo}", "--jq", ".permissions.push"], check=False) == "true"


@dataclass
class BoardRepoEntry:
    repo: str
    default_budget: int = 100_000


@dataclass
class CoopIssue:
    repo: str
    number: int
    title: str
    url: str
    body: str
    labels: list[str]
    assignees: list[str]
    author: str
    default_budget: int


# ---------------------------------------------------------------- board manifest
# repos.toml lives at the root of the shared board repo:  [[repos]] repo = "a/b"


def fetch_board_manifest(board_repo: str) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(run_gh(["api", f"repos/{board_repo}/contents/repos.toml"]))
    except GhError as e:
        raise GhError(
            f"Cannot read repos.toml in {board_repo} ({e}). "
            "Does the repo exist, and have you accepted the invitation (`coop accept`)?"
        ) from e
    decoded = base64.b64decode(payload["content"]).decode("utf-8")
    return tomllib.loads(decoded), payload["sha"]


def _put_manifest(board_repo: str, data: dict[str, Any], sha: str | None, message: str) -> None:
    content = base64.b64encode(tomli_w.dumps(data).encode()).decode("ascii")
    args = ["api", "-X", "PUT", f"repos/{board_repo}/contents/repos.toml", "-f", f"message={message}", "-f", f"content={content}"]
    if sha:
        args += ["-f", f"sha={sha}"]
    run_gh(args)


def _edit_manifest(board_repo: str, message: str, mutate: Callable[[dict[str, Any]], bool]) -> bool:
    """Read-modify-write with retries (concurrent edits make GitHub reject a stale sha)."""
    for attempt in range(3):
        data, sha = fetch_board_manifest(board_repo)
        data.setdefault("repos", [])
        if not mutate(data):
            return False
        try:
            _put_manifest(board_repo, data, sha, message)
            return True
        except GhError:
            if attempt == 2:
                raise
    return False


def create_board(board_repo: str, public: bool = False) -> None:
    run_gh(["repo", "create", board_repo, "--public" if public else "--private", "--add-readme", "--description", "coop board: repos friends can help on"])
    _put_manifest(board_repo, {"repos": []}, None, "coop: init board")


def list_registered_repos(board_repo: str) -> list[BoardRepoEntry]:
    data, _ = fetch_board_manifest(board_repo)
    return [BoardRepoEntry(r["repo"], int(r.get("default_budget", 100_000))) for r in data.get("repos", [])]


def ensure_repo_labels(repo: str) -> None:
    for name, color, desc in (
        (LABEL_HELP, "0E8A16", "Open for coop AI token help"),
        (LABEL_CLAIMED, "FBCA04", "Claimed by a coop contributor"),
    ):
        run_gh(["label", "create", name, "--repo", repo, "--color", color, "--description", desc, "--force"], check=False)


def register_repo_on_board(board_repo: str, target_repo: str, default_budget: int = 100_000) -> str:
    # Only repos you can write to may be registered: coop contributors need the same access.
    if not has_push_access(target_repo):
        raise GhError(f"You need write access to {target_repo} to register it.")
    ensure_repo_labels(target_repo)

    def mutate(data: dict[str, Any]) -> bool:
        for e in data["repos"]:
            if e["repo"].lower() == target_repo.lower():
                e["default_budget"] = default_budget
                return True
        data["repos"].append({"repo": target_repo, "default_budget": default_budget})
        return True

    _edit_manifest(board_repo, f"coop: register {target_repo}", mutate)
    return f"Registered {target_repo} (default budget {default_budget:,}) in {board_repo}."


def unregister_repo_from_board(board_repo: str, target_repo: str) -> str:
    def mutate(data: dict[str, Any]) -> bool:
        kept = [r for r in data["repos"] if r["repo"].lower() != target_repo.lower()]
        changed = len(kept) != len(data["repos"])
        data["repos"] = kept
        return changed

    if _edit_manifest(board_repo, f"coop: unregister {target_repo}", mutate):
        return f"Removed {target_repo} from {board_repo}."
    return f"{target_repo} was not listed in {board_repo}."


def invite_collaborator(repo: str, user: str, permission: str = "push") -> None:
    run_gh(["api", "-X", "PUT", f"repos/{repo}/collaborators/{user}", "-f", f"permission={permission}"])


def pending_invitations() -> list[dict[str, Any]]:
    return json.loads(run_gh(["api", "user/repository_invitations"]) or "[]")


def accept_invitation(invitation_id: int) -> None:
    run_gh(["api", "-X", "PATCH", f"user/repository_invitations/{invitation_id}"])


# ---------------------------------------------------------------- issues


def _to_issue(repo: str, item: dict[str, Any], budget: int) -> CoopIssue:
    return CoopIssue(
        repo=repo,
        number=item["number"],
        title=item["title"],
        url=item["html_url"],
        body=item.get("body") or "",
        labels=[lbl["name"] for lbl in item.get("labels", [])],
        assignees=[a["login"] for a in item.get("assignees", [])],
        author=item["user"]["login"],
        default_budget=budget,
    )


def fetch_open_coop_issues(board_repo: str, include_claimed: bool = False) -> list[CoopIssue]:
    """One API call per registered repo (the search API caps queries at 256 chars)."""
    issues: list[CoopIssue] = []
    for e in list_registered_repos(board_repo):
        try:
            raw = run_gh(
                ["api", f"repos/{e.repo}/issues", "-X", "GET", "-f", f"labels={LABEL_HELP}", "-f", "state=open", "-f", "per_page=100"]
            )
        except GhError as err:
            print(f"warning: skipping {e.repo}: {err}", file=sys.stderr)
            continue
        for item in json.loads(raw):
            if "pull_request" in item:
                continue
            issue = _to_issue(e.repo, item, e.default_budget)
            if not include_claimed and (LABEL_CLAIMED in issue.labels or issue.assignees):
                continue
            issues.append(issue)
    return issues


def select_auto_issue(
    issues: list[CoopIssue],
    current_user: str,
    max_tokens: int,
    trust_of: Callable[[CoopIssue], TrustLevel],
    include_own: bool = False,
) -> CoopIssue | None:
    candidates = [i for i in issues if include_own or i.repo.split("/")[0].lower() != current_user.lower()]
    if not candidates:
        return None

    def score(i: CoopIssue) -> tuple[int, int, int]:
        fits = 1 if i.default_budget <= max_tokens else 0
        size = max((SIZE_WEIGHTS.get(lbl, 10) for lbl in i.labels), default=10)
        return (fits, size + {"trusted": 3, "verified": 2, "safe": 1}[trust_of(i)], -i.number)

    return max(candidates, key=score)


def parse_issue_ref(issue_ref: str, fallback_repo: str | None = None) -> tuple[str, int]:
    m = ISSUE_URL_RE.search(issue_ref)
    if m:
        return m.group(1), int(m.group(2))
    if "#" in issue_ref:
        repo, num = issue_ref.split("#", 1)
        return repo, int(num)
    if issue_ref.isdigit() and fallback_repo:
        return fallback_repo, int(issue_ref)
    raise ValueError(f"Cannot parse issue reference: {issue_ref}")


def fetch_single_issue(repo: str, number: int, default_budget: int = 100_000) -> CoopIssue:
    return _to_issue(repo, json.loads(run_gh(["api", f"repos/{repo}/issues/{number}"])), default_budget)


def claim_issue(repo: str, number: int, me: str) -> bool:
    """Assign + label, then re-read: if someone else claimed concurrently, the alphabetically
    first assignee wins and the loser backs off. Not a lock, but races resolve deterministically."""
    if not has_push_access(repo):
        raise GhError(f"You need write access to {repo} to claim issues (ask the owner: `coop invite {me} {repo}`).")
    ensure_repo_labels(repo)
    run_gh(["issue", "edit", str(number), "--repo", repo, "--add-assignee", "@me", "--add-label", LABEL_CLAIMED])
    assignees = sorted(a.lower() for a in fetch_single_issue(repo, number).assignees)
    if assignees and assignees[0] != me.lower():
        run_gh(["issue", "edit", str(number), "--repo", repo, "--remove-assignee", "@me"], check=False)
        return False
    return True


def release_issue(repo: str, number: int) -> None:
    run_gh(["issue", "edit", str(number), "--repo", repo, "--remove-assignee", "@me", "--remove-label", LABEL_CLAIMED], check=False)
