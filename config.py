"""
config.py – Zentrale Konfiguration für den Hyperliquid Telegram Bot.

Lädt Konfiguration aus .env-Datei (Best Practice für sensitive Daten).
Fehlende .env-Datei oder Umgebungsvariablen führen zu klaren Fehlermeldungen.
"""

import os
from pathlib import Path
from typing import Optional

# ─── .env Datei laden ──────────────────────────────────────────────────────────
from dotenv import load_dotenv

# .env Datei im Projektverzeichnis suchen
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

# ─── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _get_required_env(key: str) -> str:
    """
    Lädt eine erforderliche Umgebungsvariable.
    Wirft ValueError, wenn die Variable nicht gesetzt ist.
    """
    value = os.getenv(key)
    if value is None or value == "":
        raise ValueError(
            f"Benötigte Umgebungsvariable '{key}' nicht gesetzt. "
            f"Bitte in .env-Datei konfigurieren."
        )
    return value

def _get_optional_env(key: str, default: str = "") -> str:
    """
    Lädt eine optionale Umgebungsvariable mit Default-Wert.
    """
    return os.getenv(key, default)

def _get_bool_env(key: str, default: bool = False) -> bool:
    """
    Lädt eine Boolean-Umgebungsvariable.
    Akzeptiert: "true", "1", "yes" (case-insensitive) → True
    """
    value = os.getenv(key, "").lower()
    if value in ("true", "1", "yes"):
        return True
    if value in ("false", "0", "no"):
        return False
    return default

def _get_int_env(key: str, default: int = 0) -> int:
    """
    Lädt eine Integer-Umgebungsvariable mit Default-Wert.
    """
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default

def _get_list_env(key: str, default: Optional[list] = None) -> Optional[list]:
    """
    Lädt eine Listen-Umgebungsvariable (komma-getrennt).
    "None" oder leer → None
    "xyz,km" → ["xyz", "km"]
    """
    value = os.getenv(key, "").strip()
    if value.lower() == "none" or value == "":
        return default
    
    try:
        return [item.strip() for item in value.split(",")]
    except (ValueError, AttributeError):
        return default

# ─── Telegram ──────────────────────────────────────────────────────────────────

BOT_TOKEN = _get_required_env("BOT_TOKEN")
TELEGRAM_CHAT_ID = _get_optional_env("TELEGRAM_CHAT_ID")

# ─── Hyperliquid ───────────────────────────────────────────────────────────────

WALLET_ADDRESS = _get_required_env("WALLET_ADDRESS")
HIP3_DEXES = _get_list_env("HIP3_DEXES", None)

# API-Endpoint. Standard: Mainnet.
# Für Testnet: "https://api.hyperliquid-testnet.xyz"
API_URL = _get_optional_env("API_URL", "https://api.hyperliquid.xyz")

# ─── WebSocket-Notifications ───────────────────────────────────────────────────

NOTIFY_ORDER_UPDATES = _get_bool_env("NOTIFY_ORDER_UPDATES", True)
EDIT_WINDOW = _get_int_env("EDIT_WINDOW", 3)

# ─── Trading ───────────────────────────────────────────────────────────────────

TRADING_ENABLED = _get_bool_env("TRADING_ENABLED", False)
AGENT_PRIVATE_KEY = _get_required_env("AGENT_PRIVATE_KEY") if TRADING_ENABLED else ""

# ─── Web Dashboard ─────────────────────────────────────────────────────────────

WEB_ENABLED = _get_bool_env("WEB_ENABLED", False)
WEB_HOST = _get_optional_env("WEB_HOST", "0.0.0.0")
WEB_PORT = _get_int_env("WEB_PORT", 8000)
WEB_URL = _get_optional_env("WEB_URL", "http://192.168.178.11:8000")