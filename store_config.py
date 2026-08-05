"""
Per-store configuration loader.

Phase A of the multi-store pivot (see plan file
`the-original-plan-was-vectorized-puddle.md`).

Goal: a single source of truth for the values that today are hardcoded all
over the codebase as Oxygen-specific (`STORE_ID = "OSP"`, printer IPs, hot
folder path). Each store's PC will have its own `store_config.json`; the
platform side never reads this file — it deals with stores via the database
`partners` table.

Design contract:
- This module is import-safe even if no config file exists. Missing config
  falls back to the legacy Oxygen defaults so nothing breaks during cutover.
- Config is read once at process start and frozen. Restart to pick up edits.
- Search order for the JSON file:
    1. Path in env var `PRINTOSKY_STORE_CONFIG` (absolute path)
    2. `<repo_root>/store_config.json` (gitignored)
    3. `~/.printosky/store_config.json`
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Legacy Oxygen defaults — used as fallback if no config is found.
# Keep these in sync with what is currently hardcoded so behaviour is
# preserved during the cutover.
_LEGACY_OXYGEN_DEFAULTS: dict[str, Any] = {
    "store_id": "OSP",
    "store_name": "Oxygen Students Paradise, Thrissur",
    "printers": {
        "konica_ip": "192.168.55.110",
        "epson_ip": "192.168.55.214",  # Epson EM-C8100 (replaced WF-C21000 2026-06-29)
    },
    "hot_folder": r"C:\Printosky\Jobs\Incoming",
    "db_path": r"C:\Printosky\Data\jobs.db",
    "agent_secret": None,
    "platform_url": None,
}


@dataclass(frozen=True)
class PrinterConfig:
    konica_ip: str
    epson_ip: str


@dataclass(frozen=True)
class StoreConfig:
    store_id: str
    store_name: str
    printers: PrinterConfig
    hot_folder: str
    db_path: str
    agent_secret: str | None = None
    platform_url: str | None = None
    # Per-store Windows print-queue names. When set, print_server.py overrides
    # its hardcoded PRINTERS dict at import time. Use this on dev/test stores
    # to redirect 'epson' to 'Microsoft Print to PDF' so test dispatches don't
    # consume real ink. Keys: 'konica', 'epson'. Missing keys keep defaults.
    printer_queue_names: dict[str, str] | None = None
    source_path: str | None = field(default=None, compare=False)

    @property
    def is_legacy_fallback(self) -> bool:
        """True iff this config was synthesized from legacy defaults rather
        than read from a real file. Useful for logging during cutover."""
        return self.source_path is None


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env_override = os.environ.get("PRINTOSKY_STORE_CONFIG")
    if env_override:
        paths.append(Path(env_override))
    paths.append(Path(__file__).resolve().parent / "store_config.json")
    paths.append(Path.home() / ".printosky" / "store_config.json")
    return paths


def _merge_with_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge the loaded JSON over the legacy defaults so a partial
    config file works (e.g. a store that only overrides printer IPs)."""
    merged: dict[str, Any] = json.loads(json.dumps(_LEGACY_OXYGEN_DEFAULTS))
    for key, value in raw.items():
        if key == "printers" and isinstance(value, dict):
            merged["printers"] = {**merged["printers"], **value}
        else:
            merged[key] = value
    return merged


def _build(raw: dict[str, Any], source: str | None) -> StoreConfig:
    printers_raw = raw.get("printers", {}) or {}
    pq_raw = raw.get("printer_queue_names")
    # Normalise to a plain dict[str, str] or None — never trust the input shape.
    printer_queue_names: dict[str, str] | None = None
    if isinstance(pq_raw, dict) and pq_raw:
        printer_queue_names = {
            str(k): str(v) for k, v in pq_raw.items() if v is not None
        } or None
    return StoreConfig(
        store_id=str(raw["store_id"]),
        store_name=str(raw["store_name"]),
        printers=PrinterConfig(
            konica_ip=str(printers_raw["konica_ip"]) if printers_raw.get("konica_ip") is not None else "",
            epson_ip=str(printers_raw["epson_ip"]) if printers_raw.get("epson_ip") is not None else "",
        ),
        hot_folder=str(raw["hot_folder"]),
        db_path=str(raw["db_path"]),
        agent_secret=raw.get("agent_secret"),
        platform_url=raw.get("platform_url"),
        printer_queue_names=printer_queue_names,
        source_path=source,
    )


@lru_cache(maxsize=1)
def get_store_config() -> StoreConfig:
    """Return the active store config, reading from disk on first call."""
    for path in _candidate_paths():
        if not path.is_file():
            continue
        try:
            # utf-8-sig tolerates the UTF-8 BOM that Notepad and
            # PowerShell's Set-Content -Encoding UTF8 emit on Windows;
            # plain utf-8 files decode identically.
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("store_config: failed to read %s (%s); skipping", path, exc)
            continue
        merged = _merge_with_defaults(raw)
        cfg = _build(merged, source=str(path))
        log.info("store_config: loaded store_id=%s from %s", cfg.store_id, path)
        return cfg

    log.info(
        "store_config: no config file found; falling back to legacy Oxygen "
        "defaults (store_id=%s)",
        _LEGACY_OXYGEN_DEFAULTS["store_id"],
    )
    return _build(_LEGACY_OXYGEN_DEFAULTS, source=None)


def reload_store_config() -> StoreConfig:
    """Force a re-read. Useful in tests; not used in production."""
    get_store_config.cache_clear()
    return get_store_config()
