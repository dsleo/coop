# coop

Spend spare weekly AI subscription tokens (`claude`, `codex`, `gemini`) helping friends with their GitHub issues.

- **No shared credentials.** Everyone runs their own CLI login on their own machine.
- **No servers.** A private GitHub repo (`coop-board`) is the whole backend.
- **One command.** `coop` picks an open issue from a friend's repo, works on it within your budget, and opens a **Draft PR**.
- **Safe by default.** Unknown repos run in the `safe` tier, in a throwaway clone, inside an OS sandbox on macOS. See [Security](#security).

## How it works

```
 you flag an issue ──► label "coop-help" on your repo
 friend runs `coop` ──► finds it via the board ──► claims it ──► agent works in a temp clone
                                                              ──► Draft PR on your repo
```

## Commands

| Command | What it does |
| :-- | :-- |
| `coop` / `coop auto [-p codex] [-t 150000] [-n 2]` | Pick, claim, solve, open Draft PR (`-n` = max issues, `-t` = total token budget) |
| `coop list [--all]` | Open `coop-help` issues across the board |
| `coop run <url \| owner/repo#N>` | Work on one specific issue |
| `coop flag <issue#> [--size small]` | Mark an issue in the current repo as open for help |
| `coop register [owner/repo] [-b 100000]` | Add a repo to the board (needs write access to it) |
| `coop unregister [owner/repo]` | Remove it |
| `coop repos` | Registered repos + your local trust tier |
| `coop trust <user \| owner/repo> -l verified` | Raise your local trust for a friend or repo |
| `coop invite <user> [owner/repo]` | Give a friend write access (default: the board repo) |
| `coop accept` | Review and accept pending repo invitations |
| `coop stats` | Local token usage (estimates) and PRs by provider |
| `coop mcp` | Optional MCP server for desktop apps |

Config lives in `~/.config/coop/` (`config.toml`: board, default provider and budget, `max_turns`, `max_minutes`; `trust.toml`: your trust tiers).

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
- Before pushing, coop unstages symlinks, `.env*`/key/credential files and files >1 MB, and refuses to push at all if added lines look like a private key or API key.
- Everything lands as a **Draft PR**. Read the diff before merging.

Limits you should know about:
- The OS sandbox (blocks reading `~/.ssh`, `~/.aws`, `~/.config/gh`…, blocks writing outside the clone/temp/CLI config dirs) is **macOS only**. On Linux, only the tool restrictions apply; run coop in a container if you want more.
- Token counts are **estimates** (cache reads aren't counted; Gemini reports only at the end). The reliable hard stops are `max_minutes` (default 20) and, for Claude, `max_turns` (default 25).
- Claiming is a deterministic tie-break, not a lock; two friends starting in the same second resolve it by login order.
- Everyone on the board is a writer on `coop-board`, and can register any repo they have write access to. Only invite people you trust.

More detail in [docs/architecture.md](docs/architecture.md).

---

# Getting started, step by step

The group needs: **one** person creates the board (Part A); **everyone** does Part B; each person who wants help on a repo does Part C; Part D is daily use.

## Part A: one person creates the board (once)

1. Install the prerequisites (see Part B, steps 1 to 3).
2. Create the private board repo and configure yourself against it. Replace `my-group` with your GitHub user or org:
   ```bash
   coop init my-group/coop-board --create            # private (default)
   coop init my-group/coop-board --create --public   # public: anyone can read the repo list
   ```
   Public or private, only people you `coop invite` can edit the board or claim issues.
3. Invite each friend (write access to the board):
   ```bash
   coop invite alice
   coop invite bob
   ```
4. Tell your friends the board name (`my-group/coop-board`).

## Part B: everyone, once

1. **Python 3.11+** and **[uv](https://docs.astral.sh/uv/)** (`brew install uv`).
2. **GitHub CLI**, logged in, with git credentials set up (needed to push branches):
   ```bash
   brew install gh
   gh auth login
   gh auth setup-git
   ```
3. **At least one AI CLI** logged into your subscription: `claude`, `codex` or `gemini`.
4. **Install coop** from wherever you pushed this repo:
   ```bash
   uv tool install git+https://github.com/<you>/coop
   ```
   (or from a local checkout: `uv tool install .`)
5. **Join the board** (everyone except the creator):
   ```bash
   coop accept                      # accept the board invitation
   coop init my-group/coop-board    # add -p codex / -t 150000 to change defaults
   ```

## Part C: get help on your repos (per repo)

1. Let your friends write to the repo (needed to claim issues and push branches):
   ```bash
   coop invite alice my-name/my-project
   coop invite bob my-name/my-project
   ```
   Friends then run `coop accept`.
2. Register the repo, with the max tokens a helper may spend per issue:
   ```bash
   cd my-project
   coop register --budget 100000
   ```
3. Flag an issue you want help with (it must already exist on GitHub):
   ```bash
   coop flag 42 --size small
   ```

## Part D: spend your spare tokens

```bash
coop list                         # what's open?
coop                              # pick the best one, solve it, open a Draft PR
coop auto -p codex -t 150000 -n 2 # up to 2 issues, 150k tokens total, using Codex
coop stats                        # what you've donated
```

Optional, once you know a friend well: `coop trust alice -l verified`.

## Troubleshooting

- `Cannot read repos.toml in …`: you haven't accepted the invitation (`coop accept`) or the board name is wrong.
- `You need write access to …`: the repo owner must run `coop invite <you> <repo>`.
- `No OS sandbox on this platform`: you're not on macOS; see Limits above.
- Nothing happens / queue clear: nobody has flagged an unclaimed issue in a repo you aren't the owner of (`--include-own` to test on your own repos).
- Develop: `uv venv && uv pip install -e ".[dev]" && .venv/bin/pytest`.
