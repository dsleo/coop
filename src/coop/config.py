from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import BaseModel, Field

CONFIG_DIR = Path(os.environ.get("COOP_CONFIG_DIR", Path.home() / ".config" / "coop"))
CONFIG_FILE = CONFIG_DIR / "config.toml"
TRUST_FILE = CONFIG_DIR / "trust.toml"
STATE_DIR = Path(os.environ.get("COOP_STATE_DIR", Path.home() / ".local" / "share" / "coop"))
RUNS_LOG = STATE_DIR / "runs.jsonl"

ProviderName = Literal["claude", "codex", "gemini"]
TrustLevel = Literal["safe", "verified", "trusted"]
PROVIDERS: tuple[ProviderName, ...] = ("claude", "codex", "gemini")


class CoopConfig(BaseModel):
    board_repo: str = ""
    default_provider: ProviderName = "claude"
    default_max_tokens: int = 100_000
    max_turns: int = 25  # hard stop (claude only)
    max_minutes: int = 20  # hard stop (all providers): wall-clock kill
    available_providers: list[ProviderName] = Field(default_factory=list)


class TrustConfig(BaseModel):
    default_level: TrustLevel = "safe"
    owners: dict[str, TrustLevel] = Field(default_factory=dict)
    repos: dict[str, TrustLevel] = Field(default_factory=dict)


def detect_providers() -> list[ProviderName]:
    return [p for p in PROVIDERS if shutil.which(p)]


def load_config() -> CoopConfig:
    if not CONFIG_FILE.exists():
        return CoopConfig(available_providers=detect_providers())
    return CoopConfig.model_validate(tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8")))


def save_config(cfg: CoopConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(tomli_w.dumps(cfg.model_dump()), encoding="utf-8")


def require_board_repo(cfg: CoopConfig) -> str:
    if not cfg.board_repo:
        raise RuntimeError("coop is not initialized yet. Run: coop init <owner>/coop-board")
    return cfg.board_repo


def load_trust() -> TrustConfig:
    if not TRUST_FILE.exists():
        return TrustConfig()
    return TrustConfig.model_validate(tomllib.loads(TRUST_FILE.read_text(encoding="utf-8")))


def save_trust(trust: TrustConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TRUST_FILE.write_text(tomli_w.dumps(trust.model_dump()), encoding="utf-8")


def resolve_trust(repo: str, trust: TrustConfig | None = None) -> TrustLevel:
    t = trust or load_trust()
    if repo in t.repos:
        return t.repos[repo]
    return t.owners.get(repo.split("/")[0], t.default_level)


def effective_trust(repo: str, issue_author: str, trust: TrustConfig | None = None) -> TrustLevel:
    """Elevated trust only applies to issues written by the repo owner or someone you trust.

    Trusting alice's repo must not mean trusting every stranger who can open an issue in it.
    """
    t = trust or load_trust()
    level = resolve_trust(repo, t)
    if level == "safe":
        return level
    author = issue_author.lower()
    known = {repo.split("/")[0].lower(), *(o.lower() for o in t.owners)}
    return level if author in known else "safe"
