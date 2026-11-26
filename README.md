# coop

Spend spare weekly AI subscription tokens (`claude`, `codex`, `gemini`) helping friends with their GitHub issues.

- **No shared credentials.** Everyone runs their own CLI login on their own machine.
- **No servers.** One small GitHub repo (the *board*) is the whole backend.
- **One command.** `coop` picks an open issue from a friend's repo, works on it within your token budget, and opens a **Draft PR**.
- **Safe by default.** Unknown repos run in the `safe` tier, in a throwaway clone, inside an OS sandbox on macOS. See [Security](#security).

```
 you flag an issue ──► label "coop-help" on your repo
 a friend runs `coop` ──► finds it via the board ──► claims it ──► agent works in a temp clone
                                                                ──► Draft PR on your repo
```

## Quickstart (TL;DR)

Already installed (see [Installation](#installation)) and part of a group? Everything you need day to day:

```bash
# You want HELP on a repo (the owner):
coop register                 # once per repo, from inside it
coop flag 42                  # open issue #42 for help; friends get access automatically

# You want to HELP others (the helper):
coop accept                   # take pending repo invitations
coop                          # pick the best open issue, fix it, open a Draft PR
```

Starting from zero? Jump to [Setup: step by step](#setup-step-by-step).

## Two roles

Everyone in a group plays both roles, depending on the repo:

| | **Owner** (you want help) | **Helper** (you spend your spare tokens) |
| :-- | :-- | :-- |
| Where | your repo | a friend's repo |
| You run | `coop register`, `coop flag`, `coop sync-access` | `coop accept`, `coop list`, `coop` |
| Tokens spent | none | yours, from your own subscription |
| End result | a Draft PR appears on your repo for you to review | a Draft PR you opened on their repo |

## Glossary

- **Board**: a small GitHub repo (e.g. `dsleo/coop-board`) holding `repos.toml`, the list of repos in the group. Everyone points `coop` at the same one.
- **Member**: anyone invited to the board repo. Members are who `coop flag` and `coop sync-access` share your repos with.
- **GitHub handle**: a GitHub username such as `dsleo` (what appears in `github.com/<handle>`).
- **Flagged issue**: an issue carrying the `coop-help` label.
- **Claim**: when a helper starts, the issue is assigned to them and labelled `coop-claimed` so nobody else duplicates the work.
- **Tier** (`safe` / `verified` / `trusted`): how much freedom the agent gets on someone else's repo. See [Security](#security).

## Contents

1. [Installation](#installation)
2. [Setup: step by step](#setup-step-by-step) (create the board, join it, share your repos)
3. [Daily use](#daily-use)
4. [Labels](#labels)
5. [Budgets](#budgets)
6. [Command reference](#command-reference)
7. [Security](#security)

---

## Installation

Everyone in the group does this once.

1. **Python 3.11+ and [uv](https://docs.astral.sh/uv/)**
   ```bash
   brew install uv
   ```
2. **GitHub CLI**, logged in, with git credentials set up (needed to push branches):
   ```bash
   brew install gh
   gh auth login
   gh auth setup-git
   ```
3. **At least one AI CLI** logged into your subscription: `claude`, `codex` or `gemini`.
4. **coop itself:**
   ```bash
   uv tool install git+https://github.com/dsleo/coop
   ```
   Update later with `uv tool upgrade coop`. From a local checkout: `uv tool install .`

---

## Setup: step by step

Throughout, a **GitHub handle** means a GitHub username such as `dsleo` (what appears in `github.com/<handle>`).

The *board* is a small GitHub repo (e.g. `dsleo/coop-board`) containing one file, `repos.toml`, that lists which repos belong to the group. Everyone points `coop` at the same board.

### Step 1: one person creates the board (once)

```bash
coop init dsleo/coop-board --create --public
```

This single command: **creates a new GitHub repository** `dsleo/coop-board` on your account, writes an empty `repos.toml` into it, and saves it as your coop board in `~/.config/coop/config.toml`. Use `--create` without `--public` for a private board. Public or private, only people you invite can edit it. Skip `--create` if the repo already exists.

Then invite the friends by their GitHub handle (this gives them write access to the board repo, and *membership of the board* is what later lets `coop` share your repos with them automatically):

```bash
coop invite alice
coop invite bob
```

Tell them the board name: `dsleo/coop-board`.

### Step 2: everyone else joins (once)

```bash
coop accept                       # accept the board invitation
coop init dsleo/coop-board        # add -p codex / -t 150000 to change defaults
```

### Step 3: share a repo you want help on

Do this from inside the repo, or pass `owner/repo`:

```bash
cd my-project
coop register --budget 100000
```

`register` adds the repo to the board and creates the coop [labels](#labels) on it. `--budget` is the **default max tokens a helper may spend per issue** in this repo (override per issue: see [Budgets](#budgets)). It is not a global limit.

### Step 4: flag an issue and give friends access

```bash
coop flag 42 --size small
```

`42` is the **issue number on GitHub** (the `42` in `github.com/dsleo/my-project/issues/42`). It applies to the repo you are currently inside; from anywhere else add `--repo dsleo/my-project`. The issue must already exist.

**Access is automatic:** when you flag an issue, `coop` invites every member of the board to that repo with write access (they need it to claim issues and push branches). Opt out with `--no-invite`. Friends run `coop accept` to take the invitation.

Friends then see the issue in `coop list`. When one of them runs `coop`, the issue gets assigned to them and, after a few minutes, a **Draft PR titled `[coop] <issue title>`** appears on your repo. Its description says which provider and tier produced it, the token estimate, and whether the run finished (`Closes #42`) or stopped early (`Refs #42`). Review the diff like any PR, then mark it ready and merge, or close it. If the helper's run fails or changes nothing, the issue is released and goes back to the pool.

Already have flagged issues (for example you added the label by hand)? Run:

```bash
coop sync-access --dry-run        # show who would be invited
coop sync-access                  # invite every board member to all your registered repos that have open coop-help issues
```

It only touches repos where you are admin, and only invites people not yet collaborators. You can also invite one person manually: `coop invite alice dsleo/my-project`.

---

## Daily use

```bash
coop list                           # what's open across the group?
coop                                # pick the best issue, solve it, open a Draft PR
coop auto -p codex -t 150000 -n 2   # up to 2 issues, 150k tokens total, using Codex
coop run dsleo/my-project#42        # work on one specific issue
coop stats                          # what you've donated
```

Optional, once you know a friend well: `coop trust alice -l verified` (see [Security](#security)).

As a helper, the first time a friend flags something you'll have a pending invitation to their repo: run `coop accept` (coop can't claim issues or push branches without write access). `coop` skips your own repos unless you pass `--include-own` (handy for testing).

Prefer not to use the CLI to flag? Open the issue on GitHub, click *Labels*, add `coop-help`. If you want friends to get access automatically afterwards, run `coop sync-access`.

---

## Labels

`coop register` creates all of these on the repo, so they also show up in the GitHub UI label picker. To open an issue for help **without the CLI**, open the issue on GitHub, click *Labels* and add `coop-help`.

| Label | Who sets it | Meaning |
| :-- | :-- | :-- |
| `coop-help` | **You (required)** | The issue is open for friends to pick up. This is the only label that is needed. |
| `coop-claimed` | coop | Someone is working on it. Added when claimed, removed again if their run fails or changes nothing. Don't add it yourself. |
| `size:small` / `size:medium` / `size:large` | You (optional) | Hint for ranking: small issues are picked first, large last. Unlabelled counts as in between. |
| `budget:25k`, `50k`, `100k`, `200k` (or `budget:<number>`) | You (optional) | Max tokens a helper may spend on **this** issue. Overrides the repo's default budget. |

`coop flag 42 --size small --budget 50000` adds `coop-help`, `size:small` and `budget:50k` in one go.

---

## Budgets

There are three levels. The smallest one that applies wins for the cap on a single issue:

| Level | Set with | Meaning |
| :-- | :-- | :-- |
| **Issue** | label `budget:50k`, or `coop flag 42 --budget 50000` | Max for this one issue. |
| **Repo** | `coop register --budget 100000` | Default max per issue in this repo. |
| **Helper session** | `coop auto -t 150000` (default from `coop init -t`) | The helper's own total spare budget. Each issue's cap is `min(session budget left, issue/repo budget)`. |

**Worked example.** Repo default is 100k, one issue has the label `budget:50k`, and a helper runs `coop auto -t 150000`. For that issue the cap is `min(150k left, 50k)` = **50k**; for another issue in the same repo it would be `min(150k left, 100k)` = 100k. After the first run, the helper's session has about 100k left for the next issue.

Token counts are **estimates** (see [Security](#security) for the hard stops: time and turns).

---

## Command reference

| Command | What it does |
| :-- | :-- |
| `coop` / `coop auto [-p provider] [-t tokens] [-n count] [--include-own]` | Pick, claim, solve, open a Draft PR |
| `coop list [--all]` | Open `coop-help` issues across the board |
| `coop run <url \| owner/repo#N>` | Work on one specific issue |
| `coop flag <N> [-r owner/repo] [-s size] [-b tokens] [--no-invite]` | Open issue N for help; invites board members to the repo |
| `coop sync-access [owner/repo] [--dry-run]` | Invite every board member to your repos that have open coop-help issues |
| `coop register [owner/repo] [-b tokens]` / `coop unregister [owner/repo]` | Add / remove a repo on the board |
| `coop repos` | Registered repos + your local trust tier |
| `coop trust <handle \| owner/repo> -l verified` | Raise your local trust for a friend or repo |
| `coop invite <handle> [owner/repo]` | Give someone write access (default: the board repo) |
| `coop accept` | Review and accept pending repo invitations |
| `coop init <board> [--create] [--public]` | Set up coop (and optionally create the board repo) |
| `coop stats` | Local token usage (estimates) and PRs by provider |
| `coop mcp` | Optional MCP server for desktop apps |

Config lives in `~/.config/coop/`: `config.toml` (board, default provider and budget, `max_turns`, `max_minutes`) and `trust.toml` (your trust tiers).

---

## Security

Running an AI agent on someone else's repo, driven by text from their issues, is risky. What coop does about it:

| Tier | Set with | What the agent can do |
| :-- | :-- | :-- |
| `safe` (default) | n/a | Claude: read/edit/write only, no shell. Gemini: edits only, shell auto-denied. Codex: its own workspace-write sandbox (it still has a shell, with no network). Repo-local `.claude/`, `.mcp.json`, `.gemini/`, `.codex/` are deleted first. |
| `verified` | `coop trust alice -l verified` | Adds test runners for Claude (`pytest`, `npm test`, `cargo test`, `go test`). That means **running the repo's code as you**. Only for friends you'd let run code on your laptop. |
| `trusted` | `coop trust bob -l trusted` | Full Bash for Claude, `yolo` for Gemini, your full environment, no sandbox. |

Always, regardless of tier:
- Work happens in a fresh temp clone that is deleted afterwards.
- The issue text is wrapped as untrusted input in the prompt (this reduces, but does not eliminate, prompt-injection risk).
- Elevated tiers only apply to issues written by the repo owner or someone you trust. A stranger's issue in a trusted friend's repo still runs as `safe`.
- Before pushing, coop unstages symlinks, `.env*`/key/credential files and files over 1 MB, and refuses to push at all if added lines look like a private key or API key.
- Everything lands as a **Draft PR**. Read the diff before merging.

Limits you should know about:
- The OS sandbox (blocks reading `~/.ssh`, `~/.aws`, `~/.config/gh`…, blocks writing outside the clone/temp/CLI config dirs) is **macOS only**. On Linux only the tool restrictions apply; run coop in a container for more.
- Token counts are **estimates** (cache reads aren't counted; Gemini reports only at the end). The reliable hard stops are `max_minutes` (default 20) and, for Claude, `max_turns` (default 25).
- Claiming is a deterministic tie-break, not a lock; two friends starting in the same second resolve it by login order.
- Board members get **write access** to every repo you flag, via automatic invites. Only invite people to the board you trust with that.

More detail in [docs/architecture.md](docs/architecture.md).

---

## Troubleshooting

- `Cannot read repos.toml in …`: you haven't accepted the invitation (`coop accept`) or the board name is wrong. Right after `register` the list can lag a few seconds behind GitHub.
- `You need write access to …`: the repo owner must run `coop invite <you> <repo>` (or `coop sync-access`).
- `No OS sandbox on this platform`: you're not on macOS; see Security.
- Queue clear although issues exist: your own repos are skipped (`--include-own`), claimed issues are hidden (`coop list --all`).
- Develop: `uv venv && uv pip install -e ".[dev]" && .venv/bin/pytest`.
