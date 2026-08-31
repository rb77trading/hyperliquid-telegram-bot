"""
ws.py – Hyperliquid WebSocket-Listener.

Lauscht auf Echtzeit-Ereignisse und sendet EIGENE Telegram-Nachrichten
(mit Push-Benachrichtigung) für:
  1. Orderausführungen (Fills): Limit, Market, Liquidation, StopLoss, TakeProfit
  2. Order-Lebenszyklus (optional): Plaziert, Geändert (Edit), Storniert,
     Trigger ausgelöst, Abgelehnt
  3. Ein- und Auszahlungen (Deposits / Withdrawals / interne Transfers)

══════════════════════════════════════════════════════════════════════════════════
ARCHITEKTUR
══════════════════════════════════════════════════════════════════════════════════

Der Hyperliquid-SDK-WebSocket ist SYNCHRON (blockierend).
Die `Info.subscribe()`-Methode startet eine eigene WebSocket-Verbindung
und dispatcht eingehende Messages an die übergebene Callback-Funktion.

Da der Telegram-Bot einen asyncio-Event-Loop nutzt, muss der WebSocket
in einem separaten THREAD laufen. Um Telegram-Nachrichten von diesem
Thread aus zu senden, wird `asyncio.run_coroutine_threadsafe()` genutzt,
um den Send-Call in den Bot-Event-Loop zu übergeben.

Thread-Modell:
┌─────────────────────────────────────────────────────────────────────────────┐
│ Main Thread (asyncio)                                                       │
│   └── Telegram-Bot (run_polling)                                            │
│       ├── /start, /menu, Button-Callbacks                                   │
│       └── send_message() ← wird von WS-Thread angestoßen                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ WS Thread (blocking)                                                        │
│   └── Info.subscribe() → WebSocket-Connection                               │
│       ├── userFills → _on_fills() → Telegram-Nachricht                      │
│       ├── orderUpdates → _on_order_updates() → Telegram-Nachricht           │
│       └── userNonFundingLedgerUpdates → _on_ledger() → Telegram-Nachricht   │
└─────────────────────────────────────────────────────────────────────────────┘

WICHTIG:
- Der WebSocket nutzt ein EIGENES Info-Objekt (skip_ws=False).
  Das persistente _get_info() aus hl_api.py hat skip_ws=True und
  kann keine WebSocket-Verbindung aufbauen.
- Die erste `userFills`-Message ist immer ein SNAPSHOT (historische
  Fills) → wird übersprungen (isSnapshot=true).
- Jede Notification ist eine EIGENE neue Nachricht (send_message),
  die automatisch eine Push-Benachrichtigung auf dem Smartphone auslöst.
- Der Callback erhält das KOMPLETTE ws_msg-Objekt:
  {"channel": "userFills", "data": {...}}
  → Man muss immer ws_msg["data"] extrahieren!

══════════════════════════════════════════════════════════════════════════════════
DISCONNECT-DETEKTION
══════════════════════════════════════════════════════════════════════════════════

Die SDK fängt WebSocket-Disconnects INTERN ab und loggt sie über
den Logger "websocket" – sie wirft KEINE Exception nach oben.

Ohne Gegenmaßnahme würde der WS-Thread endlos weiterlaufen,
während die Verbindung tot ist.

Lösung:
  1. Einen Handler auf den SDK-Logger "websocket" setzen.
  2. Der Handler erkennt Disconnect-Meldungen ("goodbye", "lost", etc.)
     und setzt ein threading.Event.
  3. Der WS-Thread wartet auf dieses Event (statt blind zu sleepen).
  4. Wenn das Event gesetzt ist → ConnectionError werfen → Reconnect-Loop.

Hyperliquid trennt die Verbindung typischerweise alle ~2.5 Stunden
("Expired - goodbye" oder "Connection to remote host was lost").

══════════════════════════════════════════════════════════════════════════════════
EDIT-ERKENNUNG (BIDIREKTIONAL)
══════════════════════════════════════════════════════════════════════════════════

Ein Order-Edit (Preis oder Größe ändern) wird von Hyperliquid intern
als Cancel + Place abgewickelt. Die API sendet daher zwei Events:
  - "open"     → neue Order wird plaziert
  - "canceled" → alte Order wird storniert

Die REIHENFOLGE ist NICHT garantiert:
  Variante A: "canceled" (alte) → "open" (neue)
  Variante B: "open" (neue)     → "canceled" (alte)   ← passiert bei Hyperliquid!

Lösung: ZWEI Puffer + symmetrische Logik:

  Bei "open":
    1. _recent_cancels durchsuchen → Match? → EDIT (Variante A)
    2. Kein Match → Order in _recent_opens puffern + Timer starten

  Bei "canceled":
    1. _recent_opens durchsuchen → Match? → EDIT (Variante B)
    2. Kein Match → Order in _recent_cancels puffern + Timer starten

  Timer abgelaufen ohne Match → normale Nachricht senden.

Match-Regel (beide Varianten):
  - Coin + Side müssen gleich sein
  - UND (gleiche Größe ODER gleicher Preis)

Das schließt aus, dass zwei komplett verschiedene Orders
(unterschiedlicher Preis UND unterschiedliche Größe) fälschlich
als Edit erkannt werden.

══════════════════════════════════════════════════════════════════════════════════
ABHÄNGIGKEITEN:
    pip install hyperliquid-python-sdk python-telegram-bot
══════════════════════════════════════════════════════════════════════════════════
"""
import json
import asyncio
import threading
import time
import logging
from datetime import datetime, timezone
from typing import Optional

from hyperliquid.info import Info
from hyperliquid.utils import constants

from config import (
    WALLET_ADDRESS,
    API_URL,
    NOTIFY_ORDER_UPDATES,
    TELEGRAM_CHAT_ID,
    EDIT_WINDOW,
)


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════
#
# Eigener Logger für den WS-Thread, damit die Logs klar vom Bot-Log
# getrennt sind. Format: [WS] HH:MM:SS – Nachricht
#
logger = logging.getLogger("ws")
logger.setLevel(logging.INFO)
logger.propagate = False  # ← NEU: verhindert doppelte Ausgabe via Root-Logger
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("[WS] %(asctime)s – %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(_handler)


# ═══════════════════════════════════════════════════════════════════════════════
# DISCONNECT-DETEKTION
# ═══════════════════════════════════════════════════════════════════════════════
#
# Die SDK fängt WebSocket-Disconnects INTERN ab und loggt sie über
# den Logger "websocket" – sie wirft KEINE Exception nach oben.
#
# Ohne diesen Mechanismus würde der WS-Thread endlos weiterlaufen,
# während die Verbindung tot ist (keine Events mehr empfangen).
#
# Lösung:
#   1. Handler auf den SDK-Logger "websocket" setzen.
#   2. Handler erkennt Disconnect-Meldungen und setzt _ws_disconnected.
#   3. Der WS-Thread wartet auf _ws_disconnected (statt blind zu sleepen).
#   4. Wenn gesetzt → ConnectionError → Reconnect-Loop.
#
# Typische SDK-Log-Meldungen bei Disconnect:
#   - "fin=1 opcode=8 data=b'\\x03\\xe8Expired' - goodbye"
#   - "Connection to remote host was lost. - goodbye"
#

_ws_disconnected = threading.Event()


def _ws_disconnect_handler(record: logging.LogRecord) -> None:
    """
    Wird aufgerufen, wenn der SDK-Logger "websocket" einen Eintrag schreibt.
    Erkennt Disconnect-Meldungen und setzt das _ws_disconnected-Event.

    Args:
        record: Der LogRecord aus dem SDK-Logger.
    """
    msg = record.getMessage()
    # Relevante Schlüsselwörter in SDK-Disconnect-Meldungen:
    # - "goodbye" → SDKs interne Bezeichnung für geschlossene Verbindungen
    # - "lost"    → "Connection to remote host was lost"
    # - "Connection" → generischer Fallback
    if "goodbye" in msg or "lost" in msg or "Connection" in msg:
        logger.warning(f"Disconnect erkannt via Logger: {msg}")
        _ws_disconnected.set()


# Handler auf den SDK-internen Logger setzen (einmalig, beim Import).
# Der SDK-Logger heißt "websocket" (aus der websocket-client-Library).
_ws_sdk_logger = logging.getLogger("websocket")

class _DisconnectHandler(logging.Handler):
    """
    Kleiner Logging-Handler, der jeden LogRecord an _ws_disconnect_handler
    weiterleitet. Wird auf den SDK-Logger "websocket" gesetzt, um
    Disconnect-Meldungen zu erkennen.
    """
    def emit(self, record: logging.LogRecord) -> None:
        _ws_disconnect_handler(record)

_ws_sdk_logger.addHandler(_DisconnectHandler())


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM-SEND-HELPER (Thread-Safe)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Der WS-Thread ist NICHT im asyncio-Event-Loop des Bots.
# Um eine Telegram-Nachricht zu senden, übergeben wir den async-Call
# via asyncio.run_coroutine_threadsafe() in den Bot-Event-Loop.
#
# Jede Notification ist eine EIGENE neue Nachricht (send_message),
# die automatisch eine Push-Benachrichtigung auf dem Smartphone auslöst.

# Referenz auf das telegram.ext.Application-Objekt
# Wird in start_ws_listener() gesetzt.
_ws_app: Optional["object"] = None

# Referenz auf den asyncio-Event-Loop des Bots
# Wird in start_ws_listener() gesetzt.
_ws_event_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_chat_id() -> Optional[int]:
    """
    Liefert die aktuelle Chat-ID für Notifications.

    Priorität:
      1. _ws_app.bot_data["hl_chat_id"] → gesetzt bei /start (dynamisch)
      2. TELEGRAM_CHAT_ID aus config.py → Fallback (hartkodiert)

    Returns:
        Chat-ID als int, oder None wenn keine verfügbar ist.
    """
    # 1. Dynamische Chat-ID aus bot_data (gesetzt bei /start)
    if _ws_app is not None:
        chat_id = _ws_app.bot_data.get("hl_chat_id")
        if chat_id is not None:
            return chat_id

    # 2. Fallback aus config.py
    if TELEGRAM_CHAT_ID is not None:
        return TELEGRAM_CHAT_ID

    return None


async def _send_telegram(text: str) -> None:
    """
    Sendet eine EIGENE neue Telegram-Nachricht (async).
    Wird von _send_telegram_sync() via run_coroutine_threadsafe aufgerufen.

    send_message() löst automatisch eine Push-Benachrichtigung aus –
    im Gegensatz zu edit_message_text(), die nur die bestehende
    Nachricht aktualisiert und KEINEN Push erzeugt.

    Args:
        text: Der Nachrichtentext (HTML-Format).
    """
    chat_id = _get_chat_id()
    if chat_id is None:
        logger.warning("Keine Chat-ID verfügbar – Nachricht verworfen.")
        return

    if _ws_app is None:
        logger.warning("App-Referenz nicht gesetzt – Nachricht verworfen.")
        return

    try:
        await _ws_app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Telegram-Send-Fehler: {e}")


def _send_telegram_sync(text: str) -> None:
    """
    Thread-Safe-Wrapper: Sendet VOM WS-THREAD aus eine eigene Telegram-Nachricht.

    Überträgt den async-Call in den Bot-Event-Loop und wartet
    (blockierend im WS-Thread) auf den Abschluss.

    Args:
        text: Der Nachrichtentext (HTML-Format).
    """
    if _ws_event_loop is None:
        logger.warning("Event-Loop nicht initialisiert – Nachricht verworfen.")
        return

    try:
        future = asyncio.run_coroutine_threadsafe(
            _send_telegram(text),
            _ws_event_loop,
        )
        # Warten bis die Nachricht gesendet ist (Timeout: 10s)
        future.result(timeout=10)
    except Exception as e:
        logger.error(f"run_coroutine_threadsafe-Fehler: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# ZEITSTEMPEL-HILFE
# ═══════════════════════════════════════════════════════════════════════════════

def _ts(ms_timestamp: int) -> str:
    """
    Wandelt einen Unix-Millisekunden-Timestamp in einen lesbaren String um.
    Format: "2026-08-28 14:32:05 UTC"

    Args:
        ms_timestamp: Unix-Zeit in Millisekunden.
    """
    dt = datetime.fromtimestamp(ms_timestamp / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _side_label(order: dict) -> str:
    """
    Kurz-Helfer für Logging und Formatierung.
    Liefert "BUY" oder "SELL" basierend auf dem Side-Feld.

    Args:
        order: Das Order-Objekt.

    Returns:
        "BUY" oder "SELL"
    """
    return "BUY" if order.get("side") == "B" else "SELL"


# ═══════════════════════════════════════════════════════════════════════════════
# EDIT-ERKENNUNG (BIDIREKTIONAL)
# ═══════════════════════════════════════════════════════════════════════════════
#
# ZWEI Puffer für die bidirektionale Edit-Erkennung:
#
#   _recent_opens:   Puffert "open"-Events, die noch auf ein mögliches
#                    "canceled"-Event warten (Variante B: open zuerst).
#
#   _recent_cancels: Puffert "canceled"-Events, die noch auf ein mögliches
#                    "open"-Event warten (Variante A: canceled zuerst).
#
# Puffer-Struktur:
#   Key:   (coin, side, limitPx, origSz) – eindeutig pro Order
#   Value: (timestamp, order_dict, threading.Timer)
#
# Ablauf:
#   1. Event kommt rein ("open" oder "canceled")
#   2. Gegenseitigen Puffer durchsuchen → Match?
#      → JA: Timer abbrechen, "✏️ Order geändert" senden
#      → NEIN: In eigenen Puffer puffern + Timer starten (EDIT_WINDOW)
#   3. Timer abgelaufen ohne Match → normale Nachricht senden
#

_recent_opens: dict[tuple, tuple[float, dict, threading.Timer]] = {}
_recent_cancels: dict[tuple, tuple[float, dict, threading.Timer]] = {}

def _make_key(order: dict) -> tuple:
    """
    Erstellt einen eindeutigen Key aus den Order-Merkmalen.
    Wird als Dict-Key in den Puffern verwendet.

    Args:
        order: Das Order-Objekt.

    Returns:
        Tupel (coin, side, limitPx, origSz)
    """
    return (
        order.get("coin", ""),
        order.get("side", "B"),
        float(order.get("limitPx", 0)),
        float(order.get("origSz", 0)),
    )


def _find_match(order: dict, buffer: dict) -> Optional[dict]:
    """
    Durchsucht einen Puffer nach einem Edit-Match.

    Match-Regel:
      - Coin + Side müssen gleich sein
      - UND (gleiche Größe ODER gleicher Preis)

    Das bedeutet:
      - Preis-Edit (gleiche Größe, anderer Preis) → ✅ Match
      - Größen-Edit (gleicher Preis, andere Größe) → ✅ Match
      - Beides geändert (andere Größe AND anderer Preis) → ❌ kein Match
        (zwei komplett verschiedene Orders)

    Args:
        order:  Die aktuelle Order (aus dem neuen Event).
        buffer: Der Puffer-Dict, der durchsucht werden soll.

    Returns:
        Das Order-Objekt des Matches (aus dem Puffer), oder None.
    """
    new_coin = order.get("coin", "")
    new_side = order.get("side", "B")
    new_px = float(order.get("limitPx", 0))
    new_sz = float(order.get("origSz", 0))

    now = time.time()
    cutoff = now - EDIT_WINDOW

    for key, (ts, old_order, timer) in list(buffer.items()):
        # Verfallene Einträge aufräumen (Timer abbrechen + Eintrag löschen)
        if ts < cutoff:
            timer.cancel()
            del buffer[key]
            continue

        # Coin + Side müssen matchen
        if old_order.get("coin") != new_coin:
            continue
        if old_order.get("side") != new_side:
            continue

        old_px = float(old_order.get("limitPx", 0))
        old_sz = float(old_order.get("origSz", 0))

        # Edit nur wenn Größe ODER Preis gleich ist
        # (bei zwei komplett verschiedenen Orders sind beide anders)
        if old_sz == new_sz or old_px == new_px:
            # Match gefunden → Timer abbrechen, Eintrag entfernen
            timer.cancel()
            del buffer[key]
            return old_order

    return None


def _buffer_order(order: dict, buffer: dict, flush_func) -> None:
    """
    Puffert eine Order und startet einen Timer für die verzögerte
    Benachrichtigung.

    Wenn innerhalb von EDIT_WINDOW ein passendes Gegen-Event kommt,
    wird der Timer abgebrochen und stattdessen eine Edit-Nachricht
    gesendet. Wenn der Timer abläuft, wird die normale Nachricht
    (z. B. "📝 Order plaziert" oder "❌ Order storniert") gesendet.

    Args:
        order:      Das Order-Objekt.
        buffer:     Der Puffer-Dict, in den die Order gelegt wird.
        flush_func: Funktion, die bei Timer-Ablauf aufgerufen wird
                    (sendet die "normale" Nachricht).
    """
    key = _make_key(order)

    # Timer erstellen: nach EDIT_WINDOW Sekunden → flush_func(order, key)
    timer = threading.Timer(EDIT_WINDOW, flush_func, args=[order, key])
    timer.daemon = True  # Thread beendet sich mit dem Hauptprozess
    timer.start()

    # In den Puffer eintragen
    buffer[key] = (time.time(), order, timer)


def _flush_open_notify(order: dict, key: tuple) -> None:
    """
    Wird nach EDIT_WINDOW Sekunden aufgerufen, wenn KEIN Edit erkannt wurde.
    Sendet die "📝 Order plaziert"-Nachricht.

    Args:
        order: Das Order-Objekt.
        key:   Der Key im _recent_opens-Dict (zum Aufräumen).
    """
    _recent_opens.pop(key, None)
    _notify_order_update(order, "open")


def _flush_cancel_notify(order: dict, key: tuple) -> None:
    """
    Wird nach EDIT_WINDOW Sekunden aufgerufen, wenn KEIN Edit erkannt wurde.
    Sendet die "❌ Order storniert"-Nachricht.

    Args:
        order: Das Order-Objekt.
        key:   Der Key im _recent_cancels-Dict (zum Aufräumen).
    """
    _recent_cancels.pop(key, None)
    _notify_order_update(order, "canceled")


# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACK 1: USER FILLS (Orderausführungen)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Channel: userFills
# Subscription: {"type": "userFills", "user": "0x…"}
#
# Data-Format (ws_msg["data"]):
# {
#   "isSnapshot": true/false,    ← true = historischer Snapshot (überspringen!)
#   "fills": [
#     {
#       "coin": "BTC",
#       "px": "69120.0",          ← Ausführungspreis (String!)
#       "sz": "0.001",            ← Ausführungsgröße (String!)
#       "side": "B",              ← "B" = Buy, "A" = Ask/Sell
#       "time": 1744813351456,    ← Unix-ms
#       "startPosition": "0.013", ← Position VOR dem Fill
#       "dir": "Close Short",     ← "Open Long", "Open Short",
#                                     "Close Long", "Close Short"
#       "closedPnl": "12.5",      ← Realisierter PnL (nur bei Close)
#       "hash": "0x…",            ← L1-Transaktions-Hash
#       "oid": 123456,            ← Order-ID
#       "crossed": true,          ← true = Taker (Market), false = Maker (Limit)
#       "fee": "-0.0005",         ← Gebühr (negativ = Gebühr, positiv = Rebate)
#       "tid": 789,               ← Trade-ID (einzigartig)
#       "feeToken": "USDC",
#       "liquidation": null,      ← null = normal, Objekt = LIQUIDATION!
#       "builderFee": null
#     }
#   ]
# }
#
# WICHTIG:
# - Die ERSTE Message nach Subscription ist IMMER isSnapshot=true
#   (historische Fills) → MÜSSEN übersprungen werden.
# - "crossed": true → Taker (Market-Order oder aggressiver Limit)
# - "crossed": false → Maker (Limit-Order hat im Orderbuch geruht)
# - "dir" verrät die Positionswirkung:
#     "Open Long"   → neue Long-Position eröffnet
#     "Open Short"  → neue Short-Position eröffnet
#     "Close Long"  → Long-Position geschlossen
#     "Close Short" → Short-Position geschlossen
# - "liquidation" != null → die Fill ist Teil einer LIQUIDATION.
# - StopLoss / TakeProfit erscheinen als normale Fills mit
#   "dir" = "Close Long" oder "Close Short".
# ═══════════════════════════════════════════════════════════════════════════════

def _on_fills(ws_msg: dict) -> None:
    """
    Callback für den userFills-Channel.
    Wird vom WebSocket-Thread aufgerufen.

    Args:
        ws_msg: Das komplette WebSocket-Message-Objekt.
                Format: {"channel": "userFills", "data": {"isSnapshot": …, "fills": […]}}
    """
    data = ws_msg.get("data", {})

    # ── Snapshot überspringen ─────────────────────────────────────────────────
    # Die erste Message nach Subscription enthält historische Fills.
    # Diese sind NICHT neu → nicht benachrichtigen.
    if data.get("isSnapshot", False):
        logger.info("Trade-Snapshot empfangen – übersprungen.")
        return

    fills = data.get("fills", [])
    if not fills:
        return

    # Für jeden Fill eine eigene Telegram-Nachricht senden.
    for fill in fills:
        _notify_fill(fill)


def _notify_fill(fill: dict) -> None:
    """
    Formatiert und sendet eine EIGENE Telegram-Nachricht für einen Fill.

    Args:
        fill: Ein Fill-Objekt aus der userFills-Message.
    """
    coin = fill.get("coin", "?")
    px = float(fill.get("px", 0))
    sz = float(fill.get("sz", 0))
    side = fill.get("side", "B")
    dir_str = fill.get("dir", "")
    closed_pnl = float(fill.get("closedPnl", 0))
    crossed = fill.get("crossed", False)
    fee = float(fill.get("fee", 0))
    liquidation = fill.get("liquidation")
    time_ms = fill.get("time", int(time.time() * 1000))

    # ── Emoji und Label bestimmen ─────────────────────────────────────────────
    side_emoji = "🟢" if side == "B" else "🔴"
    side_label = "BUY" if side == "B" else "SELL"

    # Order-Typ:
    # - crossed=true → Taker (Market-Order oder aggressiver Limit)
    # - crossed=false → Maker (Limit-Order hat im Buch geruht)
    order_type = "Market" if crossed else "Limit"

    # Liquidation?
    is_liquidation = liquidation is not None

    # PnL-Emoji
    if closed_pnl > 0:
        pnl_emoji = "📈"
        pnl_sign = "+"
    elif closed_pnl < 0:
        pnl_emoji = "📉"
        pnl_sign = ""
    else:
        pnl_emoji = ""
        pnl_sign = ""

    # ── Nachricht aufbauen ────────────────────────────────────────────────────
    if is_liquidation:
        # Liquidation: besonders hervorheben
        header = "🚨 <b>LIQUIDATION</b>"
        body = (
            f"{side_emoji} {side_label} {sz:.4f} {coin} @ ${px:,.2f}\n"
            f"Richtung: {dir_str}\n"
            f"Realisierter PnL: {pnl_emoji} {pnl_sign}{closed_pnl:.2f} USDC\n"
            f"Zeit: <code>{_ts(time_ms)}</code>"
        )
    else:
        # Normale Fill
        header = "⚡ <b>Order ausgeführt</b>"
        body = (
            f"{side_emoji} {side_label} {sz:.4f} {coin} @ ${px:,.2f} ({order_type})\n"
            f"Richtung: {dir_str}\n"
        )
        # Realisierten PnL nur bei Close anzeigen
        if dir_str.startswith("Close") and closed_pnl != 0:
            body += f"Realisierter PnL: {pnl_emoji} {pnl_sign}{closed_pnl:.2f} USDC\n"
        body += f"Fee: {fee:.4f} USDC\n"
        body += f"Zeit: <code>{_ts(time_ms)}</code>"

    text = f"{header}\n{'─' * 30}\n{body}"
    _send_telegram_sync(text)
    logger.info(f"Fill: {side_label} {sz} {coin} @ {px} ({order_type})")


# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACK 2: ORDER UPDATES (Order-Lebenszyklus) – OPTIONAL
# ═══════════════════════════════════════════════════════════════════════════════
#
# Channel: orderUpdates
# Subscription: {"type": "orderUpdates", "user": "0x…"}
#
# Data-Format (ws_msg["data"]):
# DIREKT eine LISTE von WsOrder-Objekten (kein Dict mit "updates"-Key!):
# [
#   {
#     "order": {
#       "coin": "BTC",
#       "side": "B",
#       "limitPx": "69120.0",
#       "sz": "0.001",
#       "oid": 123456,
#       "timestamp": 1744813351456,
#       "triggerCondition": "N/A",
#       "isTrigger": false,
#       "triggerPx": "0.0",
#       "isPositionTpsl": false,
#       "reduceOnly": false,
#       "orderType": "Limit",
#       "origSz": "0.001",
#       "tif": "Gtc",
#       "cloid": null
#     },
#     "status": "open",
#     "statusTimestamp": 1744813351456
#   }
# ]
#
# Status-Werte (relevante):
# - "open"           → Order erfolgreich plaziert
# - "filled"         → Order vollständig ausgeführt (→ NICHT hier melden,
#                      da userFills das bereits abdeckt)
# - "canceled"       → Order storniert (vom User)
# - "triggered"      → Trigger-Order (SL/TP) wurde ausgelöst
# - "rejected"       → Order abgelehnt
#
# WIR BENACHRICHTIGEN BEI:
# - "open"      → neue Order plaziert ODER Edit (je nach Puffer-Check)
# - "canceled"  → nur wenn KEIN Edit folgt (nach EDIT_WINDOW Sekunden)
# - "triggered" → Trigger-Order (SL/TP) ausgelöst
# - "rejected"  → Order abgelehnt
#
# NICHT BENACHRICHTIGEN:
# - "filled" → wird bereits via userFills gemeldet
# ═══════════════════════════════════════════════════════════════════════════════

def _on_order_updates(ws_msg: dict) -> None:
    """
    Callback für den orderUpdates-Channel.
    Wird vom WebSocket-Thread aufgerufen.

    WICHTIG: data ist hier DIREKT eine LISTE von WsOrder-Objekten,
    NICHT ein Dict mit "updates"-Key.

    Bidirektionale Edit-Erkennung:
      - "open" prüft _recent_cancels (Variante A: canceled kam zuerst)
      - "canceled" prüft _recent_opens (Variante B: open kam zuerst)

    Args:
        ws_msg: Das komplette WebSocket-Message-Objekt.
                Format: {"channel": "orderUpdates", "data": [WsOrder, ...]}
    """
    data = ws_msg.get("data")

    # data ist direkt eine LISTE (WsOrder[])
    if isinstance(data, list):
        updates = data
    elif isinstance(data, dict):
        # Fallback: falls die API doch ein Dict mit "updates"-Key liefert
        updates = data.get("updates", [])
    else:
        return

    for update in updates:
        status = update.get("status", "")
        order = update.get("order", {})

        if status not in ("open", "canceled", "triggered", "rejected"):
            continue

        # ── "open": Prüfe _recent_cancels (Variante A) ────────────────────────
        if status == "open":
            # Gibt es eine kürzlich stornierte Order, die zu dieser passt?
            match = _find_match(order, _recent_cancels)
            if match is not None:
                # EDIT erkannt (canceled kam zuerst)
                # match = die ALTE (stornierte) Order
                # order  = die NEUE (geänderte) Order
                _notify_order_edit(match, order)
                continue

            # Kein Match → Order puffern, Timer starten
            _buffer_order(order, _recent_opens, _flush_open_notify)
            continue

        # ── "canceled": Prüfe _recent_opens (Variante B) ──────────────────────
        if status == "canceled":
            # Gibt es eine kürzlich plazierte Order, die zu dieser passt?
            # ← DAS IST DER FALL BEI HYPERLIQUID (open kommt zuerst)!
            match = _find_match(order, _recent_opens)
            if match is not None:
                # EDIT erkannt (open kam zuerst)
                # match = die NEUE (geänderte) Order
                # order  = die ALTE (stornierte) Order
                # → _notify_order_edit erwartet (old, new)
                _notify_order_edit(order, match)
                continue

            # Kein Match → Order puffern, Timer starten
            _buffer_order(order, _recent_cancels, _flush_cancel_notify)
            continue

        # ── "triggered" / "rejected": sofort benachrichtigen ──────────────────
        _notify_order_update(order, status)


def _notify_order_update(order: dict, status: str) -> None:
    """
    Formatiert und sendet eine EIGENE Telegram-Nachricht für ein Order-Update.

    Args:
        order:  Das Order-Objekt.
        status: Der Status-String ("open", "canceled", "triggered", "rejected").
    """
    coin = order.get("coin", "?")
    side = order.get("side", "B")
    limit_px = float(order.get("limitPx", 0))
    orig_sz = float(order.get("origSz", 0))
    oid = order.get("oid", 0)
    order_type = order.get("orderType", "Limit")
    is_trigger = order.get("isTrigger", False)
    trigger_px = float(order.get("triggerPx", 0))
    trigger_condition = order.get("triggerCondition", "N/A")
    time_ms = order.get("timestamp", int(time.time() * 1000))

    side_emoji = "🟢" if side == "B" else "🔴"
    side_label = "BUY" if side == "B" else "SELL"

    # ── Header je nach Status ─────────────────────────────────────────────────
    if status == "open":
        if is_trigger:
            header = (
                f"🔔 <b>Trigger-Order plaziert</b> "
                f"({trigger_condition} ${trigger_px:,.2f})"
            )
        else:
            header = "📝 <b>Order plaziert</b>"
    elif status == "canceled":
        header = "❌ <b>Order storniert</b>"
    elif status == "triggered":
        header = "🔔 <b>Trigger ausgelöst</b>"
    elif status == "rejected":
        header = "⛔ <b>Order abgelehnt</b>"
    else:
        header = f"📋 <b>Order-Update: {status}</b>"

    # ── Body aufbauen ─────────────────────────────────────────────────────────
    body = (
        f"{side_emoji} {side_label} {orig_sz:.4f} {coin} "
        f"@ ${limit_px:,.2f} ({order_type})\n"
        f"Order-ID: <code>{oid}</code>\n"
        f"Zeit: <code>{_ts(time_ms)}</code>"
    )

    text = f"{header}\n{'─' * 30}\n{body}"
    _send_telegram_sync(text)
    logger.info(
        f"Order-Update: {status} – {side_label} {orig_sz} {coin} @ {limit_px}"
    )


def _notify_order_edit(old_order: dict, new_order: dict) -> None:
    """
    Sendet eine EIGENE Telegram-Nachricht: "✏️ Order geändert".
    Wird aufgerufen, wenn ein Edit erkannt wurde.

    Zeigt an, was sich geändert hat (Preis, Größe, oder beides)
    und die Order-IDs von alter und neuer Order.

    WICHTIG: Die Parameter sind IMMER:
      old_order = die Order mit dem ALTEN Preis/der ALTEN Größe
      new_order = die Order mit dem NEUEN Preis/der NEUEN Größe

    Bei Variante A (canceled zuerst):
      _notify_order_edit(match, order)
      match = stornierte (alte) Order, order = neue Order ✅

    Bei Variante B (open zuerst):
      _notify_order_edit(order, match)
      order = stornierte (alte) Order, match = neue Order ✅

    Args:
        old_order: Das Order-Objekt der ALTEN (stornierten) Order.
        new_order: Das Order-Objekt der NEUEN (geänderten) Order.
    """
    coin = new_order.get("coin", "?")
    side = new_order.get("side", "B")
    old_px = float(old_order.get("limitPx", 0))
    new_px = float(new_order.get("limitPx", 0))
    old_sz = float(old_order.get("origSz", 0))
    new_sz = float(new_order.get("origSz", 0))
    old_oid = old_order.get("oid", 0)
    new_oid = new_order.get("oid", 0)
    time_ms = new_order.get("timestamp", int(time.time() * 1000))

    side_emoji = "🟢" if side == "B" else "🔴"
    side_label = "BUY" if side == "B" else "SELL"

    # ── Preis-Änderung ────────────────────────────────────────────────────────
    if old_px != new_px:
        arrow = "↑" if new_px > old_px else "↓"
        px_line = f"Preis: ${old_px:,.2f} {arrow} ${new_px:,.2f}"
    else:
        px_line = f"Preis: ${new_px:,.2f} (unverändert)"

    # ── Größen-Änderung ───────────────────────────────────────────────────────
    if old_sz != new_sz:
        sz_arrow = "↑" if new_sz > old_sz else "↓"
        sz_line = f"Größe: {old_sz:.4f} {sz_arrow} {new_sz:.4f} {coin}"
    else:
        sz_line = f"Größe: {new_sz:.4f} {coin} (unverändert)"

    header = "✏️ <b>Order geändert</b>"
    body = (
        f"{side_emoji} {side_label} {coin}\n"
        f"{px_line}\n"
        f"{sz_line}\n"
        f"Order-ID: <code>{old_oid}</code> → <code>{new_oid}</code>\n"
        f"Zeit: <code>{_ts(time_ms)}</code>"
    )

    text = f"{header}\n{'─' * 30}\n{body}"
    _send_telegram_sync(text)
    logger.info(
        f"Order-EDIT: {side_label} {coin} "
        f"px {old_px}→{new_px}, sz {old_sz}→{new_sz}, oid {old_oid}→{new_oid}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACK 3: USER NON-FUNDING LEDGER UPDATES (Deposits / Withdrawals)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Channel: userNonFundingLedgerUpdates
# Subscription: {"type": "userNonFundingLedgerUpdates", "user": "0x…"}
#
# Data-Format (ws_msg["data"]):
# DIREKT eine LISTE von Ledger-Update-Objekten:
# [
#   {
#     "time": 1731999196516,
#     "hash": "0x09ddd9f712b5…",
#     "delta": {
#       "type": "deposit",       ← Typ des Ledger-Events
#       "usdc": "2703997.45"    ← Betrag in USDC
#     }
#   }
# ]
#
# Mögliche "delta.type"-Werte:
# - "deposit"                  → Externe Einzahlung (z. B. von Arbitrum)
# - "withdraw"                 → Externe Auszahlung (enthält: nonce, fee)
# - "accountClassTransfer"     → Spot ↔ Perp (intern, in Unified Mode oft
#                                 der eigentliche "Deposit"-Pfad)
# - "internalTransfer"         → Interne Überweisung
# - "spotTransfer"             → Spot-Token-Transfer
# - "liquidation"              → Liquidation
# - "vaultCreate" / "vaultDeposit" / "vaultDistribution"
# - "rewardsClaim"             → Rewards abgeholt
#
# WIR BENACHRICHTIGEN BEI:
# - "deposit"              → ✅ Externe Einzahlung
# - "withdraw"             → ✅ Externe Auszahlung
# - "accountClassTransfer" → ✅ Interne Überweisung (Spot ↔ Perp)
# - Alles andere           → ❌ Ignorieren
# ═══════════════════════════════════════════════════════════════════════════════

def _on_ledger_updates(ws_msg: dict) -> None:
    """
    Callback für den userNonFundingLedgerUpdates-Channel.

    WICHTIG: data ist ein DICT mit folgender Struktur:
    {
      "isSnapshot": true/false,
      "user": "0x…",
      "nonFundingLedgerUpdates": [
        {"time": …, "hash": "…", "delta": {"type": "deposit", "usdc": "…"}},
        {"time": …, "hash": "…", "delta": {"type": "send", "sourceDex": "spot", …}},
        …
      ]
    }
    """
    data = ws_msg.get("data", {})

    # Snapshot überspringen (historische Ledger-Einträge)
    if data.get("isSnapshot", False):
        logger.info("Ledger-Snapshot empfangen – übersprungen.")
        return

    # Die eigentlichen Updates liegen in "nonFundingLedgerUpdates"
    updates = data.get("nonFundingLedgerUpdates", [])

    for update in updates:
        delta = update.get("delta", {})
        delta_type = delta.get("type", "")

        # Benachrichtigen bei:
        # - "deposit" → externe Einzahlung (z. B. von Arbitrum)
        # - "withdraw" → externe Auszahlung
        # - "send"    → interne Überweisung (Spot ↔ Perp, verbundenen Wallet)
        if delta_type not in ("deposit", "withdraw", "send"):
            continue

        _notify_ledger(update, delta)


def _notify_ledger(update: dict, delta: dict) -> None:
    """
    Formatiert und sendet eine EIGENE Telegram-Nachricht für ein Ledger-Event.

    Unterstützte Typen:
    - "deposit":  Externe Einzahlung (Feld: "usdc")
    - "withdraw": Externe Auszahlung (Feld: "usdc", "fee")
    - "send":     Interne Überweisung (Feld: "usdcValue", "sourceDex", "destinationDex")
    """
    delta_type = delta.get("type", "")
    time_ms = update.get("time", int(time.time() * 1000))
    tx_hash = update.get("hash", "")

    if delta_type == "deposit":
        usdc = float(delta.get("usdc", 0))
        header = "💰 <b>Einzahlung</b>"
        amount_str = f"+{usdc:.2f} USDC"
        extra = ""

    elif delta_type == "withdraw":
        usdc = float(delta.get("usdc", 0))
        header = "🏧 <b>Auszahlung</b>"
        amount_str = f"−{abs(usdc):.2f} USDC"
        fee = float(delta.get("fee", 0))
        extra = f"Fee: {fee:.4f} USDC\n" if fee > 0 else ""

    elif delta_type == "send":
        usdc = float(delta.get("usdcValue", 0))
        source_dex = delta.get("sourceDex", "")
        dest_dex = delta.get("destinationDex", "")
        token = delta.get("token", "USDC")

        if source_dex == "spot" and dest_dex == "":
            # Externe Einzahlung via verbundene Wallet (Arbitrum → HL)
            # Intern: Spot → Perp, aber für den User einfach "Einzahlung"
            header = "💰 <b>Einzahlung</b>"
            amount_str = f"+{usdc:.2f} {token}"
        elif source_dex == "" and dest_dex == "spot":
            # Manuelle Überweisung Perp → Spot
            header = "🏧 <b>Überweisung (Perp → Spot)</b>"
            amount_str = f"−{usdc:.2f} {token}"
        else:
            header = "🔄 <b>Interne Überweisung</b>"
            amount_str = f"{usdc:.2f} {token}"

        extra = ""

    else:
        return

    # Kurze Transaktions-Hash-Anzeige
    short_hash = f"{tx_hash[:10]}…{tx_hash[-6:]}" if len(tx_hash) > 16 else tx_hash

    body = (
        f"{amount_str}\n"
        f"{extra}"
        f"Tx: <code>{short_hash}</code>\n"
        f"Zeit: <code>{_ts(time_ms)}</code>"
    )

    text = f"{header}\n{'─' * 30}\n{body}"
    _send_telegram_sync(text)
    logger.info(f"Ledger: {delta_type} {delta.get('usdc', delta.get('usdcValue', '?'))} USDC")


# ═══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET-START
# ═══════════════════════════════════════════════════════════════════════════════
#
# Diese Funktion wird von bot.py via post_init aufgerufen,
# NACHDEM der Telegram-Bot gestartet und der Event-Loop aktiv ist.
# ═══════════════════════════════════════════════════════════════════════════════

def start_ws_listener(app) -> None:
    """
    Startet den WebSocket-Listener in einem separaten Daemon-Thread.

    WIRD VON BOT.PY GENANNT (via post_init-Callback).

    Args:
        app: Das telegram.ext.Application-Objekt.
             Wird benötigt, um Telegram-Nachrichten zu senden
             (app.bot.send_message).
    """
    global _ws_app, _ws_event_loop

    # Referenzen speichern (werden von _send_telegram_sync genutzt)
    _ws_app = app
    _ws_event_loop = asyncio.get_event_loop()

    # Chat-ID-Check (nur Warnung – kein Abbruch)
    chat_id = _get_chat_id()
    if chat_id is None:
        logger.warning(
            "Keine Chat-ID verfügbar! "
            "TELEGRAM_CHAT_ID in config.py setzen ODER /start senden."
        )
    else:
        logger.info(f"Notifications → Chat-ID: {chat_id}")

    # ── WebSocket-Thread starten ──────────────────────────────────────────────
    # daemon=True: Der Thread beendet sich automatisch, wenn der
    # Hauptprozess (Telegram-Bot) gestoppt wird (Ctrl+C).
    ws_thread = threading.Thread(
        target=_run_websocket,
        name="HL-WebSocket",
        daemon=True,
    )
    ws_thread.start()
    logger.info("WebSocket-Thread gestartet.")


def _run_websocket() -> None:
    """
    Läuft IM WS-THREAD: Stellt die WebSocket-Verbindung her und
    abonniert alle gewünschten Channels.

    BEI VERBINDUNGSABBRUCH (z. B. "Expired - goodbye" nach ~2.5h):
    - Der Disconnect wird via Logger-Handler erkannt (_ws_disconnected)
    - ConnectionError wird geworfen
    - Neues Info-Objekt wird erstellt
    - Alle Channels werden erneut abonniert
    - Exponential Backoff: 1s → 2s → 4s → … max 30s
    - Nach erfolgreichem Reconnect: Backoff wird auf 1s zurückgesetzt

    Diese Funktion kehrt NIE zurück (läuft bis Prozess-Ende).
    """
    delay = 1  # Anfangs-Backoff in Sekunden

    while True:
        try:
            logger.info("WebSocket-Verbindung wird aufgebaut…")

            # Event zurücksetzen vor jeder neuen Verbindung
            _ws_disconnected.clear()

            # EIGENES Info-Objekt pro Verbindung (skip_ws=False → WS-fähig)
            # Das persistente _get_info() aus hl_api.py hat skip_ws=True!
            info = Info(API_URL, skip_ws=False)

            # ── Channels abonnieren ───────────────────────────────────────────
            #
            # Reihenfolge ist egal – alle Subscriptions laufen parallel.
            # Jeder subscribe()-Call registriert einen Callback für den Channel.

            # 1) Orderausführungen (Fills) – IMMER aktiv
            info.subscribe(
                {"type": "userFills", "user": WALLET_ADDRESS},
                _on_fills,
            )
            logger.info("Abonniert: userFills")

            # 2) Order-Lebenszyklus – NUR wenn in config.py aktiviert
            if NOTIFY_ORDER_UPDATES:
                info.subscribe(
                    {"type": "orderUpdates", "user": WALLET_ADDRESS},
                    _on_order_updates,
                )
                logger.info("Abonniert: orderUpdates")
            else:
                logger.info("orderUpdates: deaktiviert (NOTIFY_ORDER_UPDATES=False)")

            # 3) Ein- und Auszahlungen – IMMER aktiv
            info.subscribe(
                {"type": "userNonFundingLedgerUpdates", "user": WALLET_ADDRESS},
                _on_ledger_updates,
            )
            logger.info("Abonniert: userNonFundingLedgerUpdates")

            logger.info("Alle Channels abonniert – lausche auf Events…")

            # ── Warten auf Disconnect ─────────────────────────────────────────
            #
            # Statt blind zu sleepen: Auf das _ws_disconnected-Event warten.
            # Das Event wird gesetzt, wenn der SDK-Logger einen Disconnect
            # meldet ("goodbye", "lost", etc.).
            #
            # Timeout=60s: Fallback, falls der Logger nicht feuert.
            # (In der Praxis feuert er immer, aber Sicherheit geht vor.)
            #
            while True:
                if _ws_disconnected.wait(timeout=60):
                    _ws_disconnected.clear()
                    # Disconnect erkannt → Exception werfen, damit der
                    # except-Block den Reconnect-Loop auslöst.
                    raise ConnectionError(
                        "WebSocket-Verbindung getrennt (via Logger-Detektion)"
                    )

        except Exception as e:
            # Verbindung verloren (Timeout, "Expired", Netzwerkfehler, etc.)
            logger.warning(
                f"WebSocket-Verbindung verloren: {e} – "
                f"Reconnect in {delay}s…"
            )
            time.sleep(delay)
            # Exponential Backoff: 1 → 2 → 4 → 8 → 16 → 30 (max)
            delay = min(delay * 2, 30)

        # Backoff-Reset nach erfolgreichem (Re)Connect
        # Wenn wir hier sind, wurde die Verbindung erfolgreich aufgebaut.
        # Bei einem erneuten Disconnect startet der Backoff wieder bei 1s.
        delay = 1   
