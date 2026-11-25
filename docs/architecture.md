# Architecture

## Pieces
- **Board repo** (`<group>/coop-board`, private): one file, `repos.toml`, listing registered repos. No server, no workflows.
- **Labels** on each registered repo: `coop-help` (open for help), `coop-claimed` (someone is on it), optional `size:small|medium|large`.
- **`coop` CLI** on each friend's machine, driving their own `claude` / `codex` / `gemini` login via `gh`.

## Lifecycle of `coop auto`
1. **Discover** (`board.fetch_open_coop_issues`): read `repos.toml`, then one issues API call per repo (not the search API: its 256-char query limit breaks past a handful of repos). Skips PRs, claimed and assigned issues. Unreachable repos are skipped with a warning.
2. **Rank** (`select_auto_issue`): skip your own repos; prefer issues whose repo budget fits, then `size:small` > medium > untagged > large plus a small trust bonus, then lowest issue number.
3. **Claim** (`claim_issue`): requires push access. Assign `@me` + label, then re-read assignees. If several people claimed at once, the alphabetically first login wins; the others back off and try the next issue. This is a deterministic tie-break, not a lock.
4. **Isolate** (`runner.prepare_worktree`): shallow clone into a fresh `mkdtemp` dir, branch `coop/<you>-issue-<n>-<MMDDhhmm>`, deleted afterwards. Below `trusted`, repo-local agent configs (`.claude/`, `.mcp.json`, `.gemini/`, `.codex/`) are removed and never committed.
5. **Contain**: on macOS the agent runs under `sandbox-exec` (no reads of `~/.ssh`, `~/.aws`, `~/.config/gh`, …; writes only to the clone, temp dirs and the CLIs' own config dirs). Environment is reduced to a short allowlist (no `GH_TOKEN`, no API keys).
6. **Limits** (`run_agent`): process group killed (SIGTERM, then SIGKILL) on whichever hits first: token estimate ≥ cap, wall-clock `max_minutes`, or (claude) `--max-turns`.
7. **Stage safely** (`stage_changes`): `git add -A`, then unstage symlinks, secret-looking filenames (`.env*`, `*.pem`, `id_rsa*`, …) and files >1 MB. If an added line looks like a secret (private key, AWS/GitHub/Anthropic/OpenAI key) nothing is pushed.
8. **Hand off**: commit, push to `origin`, open a **Draft** PR. `Closes #n` only when the run completed; partial runs say `Refs #n`. If a run fails or produces nothing, the claim is released.

## Token accounting (best effort)
Counted: input + output + cache-creation tokens. Not counted: cache reads. Claude is deduped per message id and reconciled with the final result event; Codex is summed per turn; Gemini only reports at the end, so for Gemini the wall-clock limit is the real cap. Treat the numbers as estimates; they will not match your provider's usage page exactly.

## Not covered
- Linux/Windows have no OS sandbox here; only tool restrictions apply (Linux: consider running `coop` in a container).
- Codex cannot disable its shell; it relies on Codex's own workspace-write sandbox (no network, writes limited to the clone).
- `verified` runs the repo's tests = running that repo's code as you (inside the macOS sandbox, otherwise unprotected).
