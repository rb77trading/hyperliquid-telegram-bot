"""
config.py – Zentrale Konfiguration für den Hyperliquid Telegram Bot.

Alle Werte, die je nach Umgebung (Testnet/Mainnet, anderer Wallet, etc.)
variieren, sind hier zusammengefasst. So muss bei Änderungen nur diese
eine Datei angepasst werden.
"""

# ─── Telegram ──────────────────────────────────────────────────────────────────

# Token deines Bots (von @BotFather erhalten)
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

# Chat-ID für WebSocket-Notifications.
# So findest du deine Chat-ID:
#   1. /start an deinen Bot senden
#   2. https://api.telegram.org/bot<TOKEN>/getUpdates im Browser öffnen
#   3. Unter "result"[0].message.chat.id steht deine ID (z. B. 123456789)
#   4. Für Gruppen: ID ist negativ (z. B. -1001234567890)
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"

# ─── Hyperliquid ───────────────────────────────────────────────────────────────

# Wallet-Adresse, für die der Bot Kontodaten anzeigt
WALLET_ADDRESS = "YOUR_WALLET_ADDRESS_HERE"

# Liste der Builder-DEXe, die mitabgefragt werden sollen.
# None  → automatisch ALLE aktiven DEXe via perpDexs-Endpoint.
# ["xyz"]  → nur nativer DEX + xyz.
# ["xyz", "km"]  → nativer DEX + xyz + km.
HIP3_DEXES = None

# API-Endpoint. Standard: Mainnet.
# Für Testnet: "https://api.hyperliquid-testnet.xyz"
from hyperliquid.utils import constants
API_URL = constants.MAINNET_API_URL

# ─── WebSocket-Notifications ───────────────────────────────────────────────────

# True  → Benachrichtigung bei neuen/veränderten/stornierten Orders
# False → Nur Fills und Deposits/Withdrawals
NOTIFY_ORDER_UPDATES = True

# Fenster in Sekunden, innerhalb dessen ein "canceled" + "open"
# als Edit erkannt wird (statt 2 separate Nachrichten).
# Empfehlung: 3 (reicht in der Praxis immer, da beide Events
# innerhalb von <1s eintreffen).
EDIT_WINDOW = 3

# ─── Trading ───────────────────────────────────────────────────────────────────

# True  → Order-Buttons (Stornieren/Bearbeiten) in der Orders-Ansicht
# False → Nur read-only (Standard)
TRADING_ENABLED = False

# Private Key der Agent-Wallet (NUR für Trading, nicht die Main-Wallet!)
# Empfohlen: Separate Agent-Wallet mit begrenztem USDC-Bestand anlegen.
AGENT_PRIVATE_KEY = "YOUR_AGENT_PRIVATE_KEY_HERE"

# ─── Web Dashboard ─────────────────────────────────────────────────────────────
# True  → Menü-Button zeigt auf Web-Dashboard (falls erreichbar)
# False → Reiner Telegram-Betrieb (Standard)
WEB_ENABLED = False
WEB_HOST = "0.0.0.0"
WEB_PORT = 8000
WEB_URL = "http://192.168.178.11:8000"  # Externe URL (für Telegram-Menü-Button)
