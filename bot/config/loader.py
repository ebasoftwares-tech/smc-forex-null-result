"""Load, layer, freeze and hash the configuration.

Layering (ARCHITECTURE.md section 6.1):

    defaults.yaml  ->  profile.yaml  ->  symbol overrides  ->  explicit overrides

The result is one frozen ``AppConfig`` plus a ``config_hash`` that is stamped on every
object, event and result row.  A run whose hash is not in the registry is not a result
(BACKTEST_PROTOCOL.md section 7).

The hash is taken over the *fully resolved* config including defaults, so two runs that
differ only in which values were written down explicitly hash identically -- what is
being identified is the configuration that ran, not the file that expressed it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Mapping

import yaml

from .schema import AppConfig

DEFAULTS_PATH = Path(__file__).with_name("defaults.yaml")


def _deep_merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    """Recursive dict merge.  Lists replace wholesale -- they are never merged elementwise.

    Merging lists positionally would make a profile that overrides one session window
    silently inherit the rest by index, which is exactly the kind of half-applied
    configuration that is impossible to notice in a report.
    """
    out = dict(base)
    for k, v in over.items():
        if k in out and isinstance(out[k], Mapping) and isinstance(v, Mapping):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _canonical(obj: Any) -> Any:
    """Convert to a JSON-safe structure with deterministic ordering and formatting."""
    if isinstance(obj, Mapping):
        return {k: _canonical(obj[k]) for k in sorted(obj)}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    if isinstance(obj, time):
        return obj.isoformat(timespec="seconds")
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, float):
        # repr round-trips exactly in Python 3; format explicitly so that 1 and 1.0
        # cannot hash differently through YAML's int/float ambiguity.
        return f"{obj:.12g}"
    return obj


def config_hash(cfg: AppConfig) -> str:
    payload = _canonical(cfg.model_dump(mode="python"))
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def load_config(
    *,
    defaults: Path | None = None,
    profile: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> tuple[AppConfig, str]:
    """Return ``(config, config_hash)``.

    Raises on any unknown key at any level (``extra="forbid"`` in the schema), so a
    typo'd parameter name fails loudly instead of silently testing the default.
    """
    raw: dict[str, Any] = yaml.safe_load((defaults or DEFAULTS_PATH).read_text(encoding="utf-8"))
    if profile is not None:
        raw = _deep_merge(raw, yaml.safe_load(profile.read_text(encoding="utf-8")) or {})
    if overrides:
        raw = _deep_merge(raw, overrides)
    cfg = AppConfig.model_validate(raw)
    return cfg, config_hash(cfg)


def tzdata_version() -> str:
    """The IANA database version in use.

    Recorded in the dataset manifest because it decides every historical DST
    transition, and therefore every session boundary in the backtest.  Two runs on
    different tzdata releases are not strictly comparable, and on Windows there is no
    system tz database at all -- ``tzdata`` is a hard runtime dependency, not a
    convenience.
    """
    try:
        from importlib.metadata import version

        return version("tzdata")
    except Exception:  # pragma: no cover - only on a platform with a system tz db
        return "system"
