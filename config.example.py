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
WALLET_ADDRESS = "YOUR_WALLET_ADRESS_HERE"

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
