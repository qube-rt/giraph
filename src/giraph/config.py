"""Load and merge giraph config; precedence: CLI > ``giraph.toml`` > defaults.

Empty ``eligible_paths`` puts every path in scope: no merge is downgraded to ineligible,
and any merge-introduced resolution still counts as edited transport.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from giraph.models import GiraphError

DEFAULT_ELIGIBLE_PATHS: tuple[str, ...] = ()
DEFAULT_ACCEPT_REFS: tuple[str, ...] = ("main",)
DEFAULT_ACCEPT_PREFIXES: tuple[str, ...] = ("release/",)
DEFAULT_CONVENTION = "plain"
DEFAULT_DROP_ORPHAN_ISLANDS = False
DEFAULT_MERGED_IN_ARROW = "in"
MERGED_IN_ARROW_CHOICES = ("in", "out")
CONFIG_FILENAME = "giraph.toml"
KNOWN_KEYS = frozenset(
    {
        "eligible_paths",
        "accept_target_refs",
        "accept_target_ref_prefixes",
        "convention",
        "drop_orphan_islands",
        "merged_in_arrow",
    }
)


@dataclass(frozen=True)
class Config:
    eligible_paths: tuple[str, ...]
    accept_refs: tuple[str, ...]
    accept_prefixes: tuple[str, ...]
    convention: str
    drop_orphan_islands: bool
    merged_in_arrow: str


def name_rev_patterns(config: Config) -> tuple[str, ...]:
    """``git name-rev --refs`` globs matching the accepted integration refs."""
    return tuple(f"*/{ref}" for ref in config.accept_refs) + tuple(
        f"*/{prefix}*" for prefix in config.accept_prefixes
    )


def find_config(repo_root: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        if not explicit.is_file():
            raise GiraphError(f"config file not found: {explicit}")
        return explicit
    candidate = repo_root / CONFIG_FILENAME
    return candidate if candidate.is_file() else None


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GiraphError(f"could not read config {path}: {exc}") from exc
    section = data.get("giraph", {})
    if not isinstance(section, dict):
        raise GiraphError(f"[giraph] in {path} must be a table, got {type(section).__name__}")
    unknown = sorted(key for key in section if key not in KNOWN_KEYS)
    if unknown:
        raise GiraphError(
            f"unknown key(s) in [giraph] of {path}: {', '.join(unknown)}; "
            f"valid keys: {', '.join(sorted(KNOWN_KEYS))}"
        )
    return section


def _str_tuple(section: dict[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    if key not in section:
        return default
    value = section[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise GiraphError(f"config key '{key}' must be a list of strings")
    return tuple(value)


def _str_value(section: dict[str, Any], key: str, default: str) -> str:
    if key not in section:
        return default
    value = section[key]
    if not isinstance(value, str):
        raise GiraphError(f"config key '{key}' must be a string")
    return value


def _bool_value(section: dict[str, Any], key: str, default: bool) -> bool:
    if key not in section:
        return default
    value = section[key]
    if not isinstance(value, bool):
        raise GiraphError(f"config key '{key}' must be a boolean")
    return value


def resolve_config(
    section: dict[str, Any],
    *,
    convention: str | None = None,
    eligible_paths: list[str] | None = None,
    drop_orphan_islands: bool | None = None,
    merged_in_arrow: str | None = None,
) -> Config:
    arrow = (
        merged_in_arrow
        if merged_in_arrow is not None
        else _str_value(section, "merged_in_arrow", DEFAULT_MERGED_IN_ARROW)
    )
    if arrow not in MERGED_IN_ARROW_CHOICES:
        raise GiraphError(f"merged_in_arrow must be one of {MERGED_IN_ARROW_CHOICES}, got {arrow!r}")
    return Config(
        eligible_paths=tuple(eligible_paths)
        if eligible_paths
        else _str_tuple(section, "eligible_paths", DEFAULT_ELIGIBLE_PATHS),
        accept_refs=_str_tuple(section, "accept_target_refs", DEFAULT_ACCEPT_REFS),
        accept_prefixes=_str_tuple(section, "accept_target_ref_prefixes", DEFAULT_ACCEPT_PREFIXES),
        convention=convention or _str_value(section, "convention", DEFAULT_CONVENTION),
        drop_orphan_islands=drop_orphan_islands
        if drop_orphan_islands is not None
        else _bool_value(section, "drop_orphan_islands", DEFAULT_DROP_ORPHAN_ISLANDS),
        merged_in_arrow=arrow,
    )
