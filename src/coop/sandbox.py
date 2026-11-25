"""OS-level containment for the agent (macOS `sandbox-exec`).

Blocks reading credential dirs and writing anywhere except the worktree, temp dirs and the
agent CLIs' own config/cache dirs. Linux has no equivalent here: tool restrictions only.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

SECRET_DIRS = [".ssh", ".aws", ".gnupg", ".config/gh", ".config/gcloud", ".azure", ".kube", ".docker", ".netrc", ".npmrc"]
WRITABLE_HOME = [".claude", ".codex", ".gemini", ".cache", ".local", ".npm", "Library/Caches", "Library/Logs"]
WRITABLE_HOME_REGEX = [r"\.claude\.json.*", r"\.gemini.*", r"\.codex.*"]


def available() -> bool:
    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


def profile(workdir: Path, home: Path | None = None) -> str:
    home = (home or Path.home()).resolve()
    tmp = Path(tempfile.gettempdir()).resolve()
    quote = lambda p: '"' + str(p).replace("\\", "\\\\").replace('"', '\\"') + '"'
    deny_read = " ".join(f"(subpath {quote(home / d)})" for d in SECRET_DIRS)
    writable = [workdir.resolve(), tmp, Path("/dev"), Path("/private/var/folders"), *(home / d for d in WRITABLE_HOME)]
    allow_write = " ".join(f"(subpath {quote(p)})" for p in writable)
    allow_write += " " + " ".join(f'(regex #"^{re.escape(str(home))}/{r}")' for r in WRITABLE_HOME_REGEX)
    return (
        "(version 1)\n(allow default)\n"
        f"(deny file-read* {deny_read})\n"
        f"(deny file-write* (require-not (require-any {allow_write})))\n"
    )


def wrap(cmd: list[str], workdir: Path) -> list[str]:
    return ["sandbox-exec", "-p", profile(workdir), *cmd]
