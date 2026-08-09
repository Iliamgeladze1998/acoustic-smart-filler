"""Load app settings from config.json (preferred) or environment variables."""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
EXAMPLE_PATH = Path(__file__).resolve().parent / "config.example.json"

DEFAULTS = {
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "content_language": "Georgian",
    "currency_note": "Prices are for Acoustic.ge store context; use numbers only without currency symbols.",
    # Google Programmable Search (Custom Search JSON API) — preferred for images
    "google_api_key": "",
    "google_cse_id": "",
    # auto | google | duckduckgo
    "image_search_backend": "auto",
}


def load_config() -> dict:
    data = dict(DEFAULTS)
    if CONFIG_PATH.is_file():
        with CONFIG_PATH.open(encoding="utf-8") as fh:
            file_data = json.load(fh)
        if isinstance(file_data, dict):
            data.update({k: v for k, v in file_data.items() if v is not None})

    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        data["openai_api_key"] = env_key

    env_model = os.environ.get("OPENAI_MODEL", "").strip()
    if env_model:
        data["openai_model"] = env_model

    gkey = os.environ.get("GOOGLE_API_KEY", "").strip()
    if gkey:
        data["google_api_key"] = gkey
    gcx = os.environ.get("GOOGLE_CSE_ID", "").strip() or os.environ.get("GOOGLE_CX", "").strip()
    if gcx:
        data["google_cse_id"] = gcx

    return data


def save_config(data: dict) -> None:
    payload = dict(DEFAULTS)
    payload.update(data)
    # Never write placeholder example text back as a real key.
    with CONFIG_PATH.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def ensure_config_exists() -> None:
    """Create config.json from the example if missing."""
    if CONFIG_PATH.is_file():
        return
    if EXAMPLE_PATH.is_file():
        CONFIG_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        save_config(DEFAULTS)


def has_usable_api_key(config: dict) -> bool:
    key = (config.get("openai_api_key") or "").strip()
    if not key:
        return False
    if key.upper().startswith("PASTE_YOUR"):
        return False
    if key == "sk-...":
        return False
    return True
