"""
bot.py – Telegram-Bot-Entry-Point.

Abhängigkeiten:
    pip install hyperliquid-python-sdk python-telegram-bot eth-account

Struktur:
    1. Imports & Konstanten
    2. Keyboard-Definitionen
    3. Command-Handler (/start, /menu)
    4. Button-Handler (Dashboard-Buttons + Trading-Buttons)
    5. Text-Handler (Eingabe von Preis/Größe beim Editieren)
    6. Helper-Funktionen
    7. Lifecycle (post_init, error_handler)
    8. main()
"""

import asyncio
import logging
import sys
import urllib.request
import urllib.error
from pathlib import Path

from telegram import (
    BotCommand,
    MenuButtonCommands,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.error import TelegramError, RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import (
    BOT_TOKEN,
    WALLET_ADDRESS,
    HIP3_DEXES,
    API_URL,
    TRADING_ENABLED,
    WEB_ENABLED,
    WEB_PORT,
    WEB_URL,
)
from hl_api import get_account_summary
from formatters import format_balance, format_positions, format_orders
from ws import start_ws_listener


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler()
_handler.setFormatter(
    logging.Formatter("[BOT] %(asctime)s – %(message)s", datefmt="%H:%M:%S")
)
logger.addHandler(_handler)


# ═══════════════════════════════════════════════════════════════════════════════
# KONSTANTEN
# ═══════════════════════════════════════════════════════════════════════════════

IMAGE_DIR = Path(__file__).parent / "images"

IMAGE_MENU      = IMAGE_DIR / "menu.png"
IMAGE_BALANCE   = IMAGE_DIR / "balance.png"
IMAGE_POSITIONS = IMAGE_DIR / "positions.png"
IMAGE_ORDERS    = IMAGE_DIR / "orders.png"

# Haupt-Menü (Basis-Buttons, immer vorhanden)
_BASE_MENU_ROWS = [
    [
        InlineKeyboardButton("📊 Kontostand", callback_data="balance"),
        InlineKeyboardButton("📈 Positionen", callback_data="positions"),
    ],
    [
        InlineKeyboardButton("⏳ Offene Orders", callback_data="orders"),
    ],
]


def _build_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Baut das Haupt-Menü-Keyboard.

    Zusätzlich zu den Telegram-Buttons (Kontostand/Positionen/Orders)
    wird ein verlinkter "🌐 Web-Dashboard"-Button angehängt, WENN das
    Web-Dashboard aktiviert und aktuell erreichbar ist. So bleiben
    beide Wege (Telegram-Ansicht UND Dashboard-Link) parallel nutzbar.
    """
    rows = list(_BASE_MENU_ROWS)
    if WEB_ENABLED and _is_web_reachable(WEB_PORT):
        rows.append([InlineKeyboardButton("🌐 Web-Dashboard", url=WEB_URL)])
    return InlineKeyboardMarkup(rows)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER: Webserver-Erreichbarkeit prüfen
# ═══════════════════════════════════════════════════════════════════════════════

def _is_web_reachable(port: int, timeout: float = 2.0) -> bool:
    """
    Prüft, ob der lokale Webserver auf dem angegebenen Port erreichbar ist.

    Sendet einen HTTP-GET an http://127.0.0.1:<port> und prüft,
    ob eine Antwort (irgendein Status-Code) zurückkommt.

    Args:
        port:    Der Port, auf dem der Webserver lauschen soll.
        timeout: Timeout in Sekunden (Standard: 2s).

    Returns:
        True, wenn der Webserver erreichbar ist, sonst False.
    """
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}", method="GET")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, Exception):
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND-HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Wird bei /start oder /menu aufgerufen.

    Sendet immer die Menü-Nachricht mit den Telegram-Buttons
    (Kontostand/Positionen/Orders). Ist das Web-Dashboard erreichbar,
    wird zusätzlich ein verlinkter "🌐 Web-Dashboard"-Button angezeigt –
    beide Wege bleiben so parallel nutzbar.
    """
    context.bot_data["hl_chat_id"] = update.effective_chat.id

    with open(IMAGE_MENU, "rb") as f:
        await update.message.reply_photo(
            photo=f,
            caption="🌐 <b>Hyperliquid Dashboard</b>\n\nWähle eine Ansicht:",
            parse_mode="HTML",
            reply_markup=_build_menu_keyboard(),
        )

# ═══════════════════════════════════════════════════════════════════════════════
# BUTTON-HANDLER: DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Button-Klick auf die Haupt-Dashboard-Buttons (balance, positions, orders).
    Editiert die Nachricht mit themenbezogenem Bild + Caption.

    Bei "orders" und TRADING_ENABLED=True:
      - Orders-Cache in bot_data aktualisieren (für Trading-Buttons)
      - Nummerierte Inline-Buttons pro Order anhängen
    """
    query = update.callback_query
    await query.answer()

    section = query.data

    # Frische Daten holen (in Thread, da synchron)
    loop = asyncio.get_event_loop()
    summary = await loop.run_in_executor(
        None,
        lambda: get_account_summary(
            WALLET_ADDRESS,
            hip3_dexes=HIP3_DEXES,
            api_url=API_URL,
            debug=False,
        ),
    )

    # Bild + Caption je nach Section bestimmen
    if section == "balance":
        image_path = IMAGE_BALANCE
        caption = format_balance(summary)
        button_rows: list = []
    elif section == "positions":
        image_path = IMAGE_POSITIONS
        caption = format_positions(summary)
        button_rows = []
    elif section == "orders":
        image_path = IMAGE_ORDERS
        caption, button_rows = format_orders(summary, trading_enabled=TRADING_ENABLED)

        if TRADING_ENABLED:
            context.bot_data["orders_cache"] = _build_orders_cache(summary)
    else:
        await query.edit_message_text("❓ Unbekannter Button.")
        return

    # Buttons zusammenstellen: Haupt-Menü + Order-Buttons
    if button_rows:
        all_rows = [
            [
                InlineKeyboardButton("📊 Kontostand", callback_data="balance"),
                InlineKeyboardButton("📈 Positionen", callback_data="positions"),
                InlineKeyboardButton("⏳ Orders", callback_data="orders"),
            ]
        ] + button_rows
        markup = InlineKeyboardMarkup(all_rows)
    else:
        markup = _build_menu_keyboard()

    with open(image_path, "rb") as f:
        media = InputMediaPhoto(
            media=f,
            caption=caption,
            parse_mode="HTML",
        )

    await query.edit_message_media(
        media=media,
        reply_markup=markup,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# BUTTON-HANDLER: TRADING (Order stornieren / bearbeiten)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Callback-Data-Konvention:
#   oc:<idx>            → Stornieren (Sicherheitsabfrage anzeigen)
#   oc_confirm:<idx>    → Storno bestätigen
#   oc_abort:<idx>      → Storno abbrechen
#   oe:<idx>            → Bearbeiten (Auswahl: Preis / Größe)
#   oe_price:<idx>      → Preis ändern (Text-Eingabe anfordern)
#   oe_size:<idx>       → Größe ändern (Text-Eingabe anfordern)
#   oe_confirm:<idx>    → Edit bestätigen
#   oe_abort:<idx>      → Edit abbrechen
#
# <idx> = Index im orders_cache (bot_data["orders_cache"])
#

async def on_order_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Behandelt Order-spezifische Trading-Buttons.
    Wird nur registriert, wenn TRADING_ENABLED=True.
    """
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split(":")
    action = parts[0]
    idx = int(parts[1]) if len(parts) > 1 else None

    # ── Orders-Cache prüfen ───────────────────────────────────────────────────
    orders_cache: list[dict] = context.bot_data.get("orders_cache", [])
    if idx is None or idx >= len(orders_cache):
        await _edit_msg(query,
            "⚠️ Order nicht mehr verfügbar (möglicherweise bereits ausgeführt).\n"
            "Bitte /menu → Orders neu laden."
        )
        return

    order = orders_cache[idx]
    coin = order["coin"]
    oid = order["oid"]
    side = order["side"]
    limit_px = order["limit_px"]
    size = order["size"]
    dex = order.get("dex", "")

    side_emoji = "🟢" if side == "B" else "🔴"
    side_label = "BUY" if side == "B" else "SELL"
    order_desc = f"{side_emoji} {side_label} {size:.4f} {coin} @ ${limit_px:,.2f}"

    # ── STORNIEREN: Sicherheitsabfrage anzeigen ──────────────────────────────
    if action == "oc":
        confirm_text = (
            f"⚠️ <b>Order stornieren?</b>\n"
            f"{'─' * 30}\n"
            f"{order_desc}\n"
            f"Order-ID: <code>{oid}</code>\n"
            f"{'─' * 30}\n"
            f"Diese Aktion kann nicht rückgängig gemacht werden."
        )
        confirm_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Ja, stornieren", callback_data=f"oc_confirm:{idx}"),
                InlineKeyboardButton("↩️ Abbrechen", callback_data=f"oc_abort:{idx}"),
            ]
        ])
        await _edit_msg(query, confirm_text, markup=confirm_kb)

    # ── STORNIEREN: Bestätigt ─────────────────────────────────────────────────
    elif action == "oc_confirm":
        await _edit_msg(query, "⏳ Storniere Order…")

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: _trader_cancel(coin, oid, dex)
        )

        if result.get("status") == "ok":
            await _edit_msg(query,
                f"✅ <b>Order storniert</b>\n"
                f"{'─' * 30}\n"
                f"{order_desc}\n"
                f"Order-ID: <code>{oid}</code>"
            )
        else:
            error_msg = result.get("error", "Unbekannter Fehler")
            await _edit_msg(query,
                f"❌ <b>Storno fehlgeschlagen</b>\n"
                f"Fehler: <code>{error_msg}</code>"
            )

    # ── STORNIEREN: Abbrechen → Orders-Ansicht neu laden ─────────────────────
    elif action == "oc_abort":
        await _refresh_orders_view(query, context)

    # ── BEARBEITEN: Auswahl (Preis / Größe) ──────────────────────────────────
    elif action == "oe":
        edit_text = (
            f"✏️ <b>Order bearbeiten</b>\n"
            f"{'─' * 30}\n"
            f"{order_desc}\n"
            f"Order-ID: <code>{oid}</code>\n"
            f"{'─' * 30}\n"
            f"Was möchtest du ändern?"
        )
        edit_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💰 Preis ändern", callback_data=f"oe_price:{idx}"),
                InlineKeyboardButton("📏 Größe ändern", callback_data=f"oe_size:{idx}"),
            ],
            [InlineKeyboardButton("↩️ Abbrechen", callback_data=f"oe_abort:{idx}")],
        ])
        await _edit_msg(query, edit_text, markup=edit_kb)

    # ── BEARBEITEN: Preis / Größe → Text-Eingabe anfordern ──────────────────
    elif action in ("oe_price", "oe_size"):
        field = "Preis" if action == "oe_price" else "Größe"
        current_val = (
            f"${limit_px:,.2f}" if action == "oe_price"
            else f"{size:.4f} {coin}"
        )

        context.chat_data["pending_edit"] = {
            "idx": idx,
            "field": "price" if action == "oe_price" else "size",
            "coin": coin,
            "oid": oid,
            "side": side,
            "limit_px": limit_px,
            "size": size,
            "dex": dex,
        }

        prompt = (
            f"✏️ <b>{field} ändern</b>\n"
            f"{'─' * 30}\n"
            f"{order_desc}\n"
            f"Aktueller {field.lower()}: <b>{current_val}</b>\n"
            f"{'─' * 30}\n"
            f"Bitte neuen {field.lower()} eingeben:\n"
            f"(nur Zahlen, z. B. <code>69500</code> oder <code>0.015</code>)"
        )
        await _edit_msg(query, prompt)

    # ── BEARBEITEN: Bestätigt (nach Text-Eingabe + Bestätigungs-Button) ──────
    elif action == "oe_confirm":
        pending: dict | None = context.chat_data.pop("pending_edit_confirm", None)
        if not pending:
            await _edit_msg(query,
                "⚠️ Aktion abgelaufen. Bitte neu starten (/menu → Orders)."
            )
            return

        await _edit_msg(query, "⏳ Ändere Order…")

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _trader_modify(
                pending["coin"],
                pending["oid"],
                pending["side"] == "B",
                pending["new_size"],
                pending["new_px"],
                pending.get("dex", ""),
            ),
        )

        s_emoji = "🟢" if pending["side"] == "B" else "🔴"
        if result.get("status") == "ok":
            await _edit_msg(query,
                f"✅ <b>Order geändert</b>\n"
                f"{'─' * 30}\n"
                f"{s_emoji} {pending['side']} {pending['new_size']:.4f} "
                f"{pending['coin']} @ ${pending['new_px']:,.2f}\n"
                f"Order-ID: <code>{pending['oid']}</code>"
            )
        else:
            error_msg = result.get("error", "Unbekannter Fehler")
            await _edit_msg(query,
                f"❌ <b>Änderung fehlgeschlagen</b>\n"
                f"Fehler: <code>{error_msg}</code>"
            )

    # ── BEARBEITEN: Abbrechen → Orders-Ansicht neu laden ─────────────────────
    elif action == "oe_abort":
        context.chat_data.pop("pending_edit", None)
        context.chat_data.pop("pending_edit_confirm", None)
        await _refresh_orders_view(query, context)


# ═══════════════════════════════════════════════════════════════════════════════
# TEXT-HANDLER: Eingabe von Preis/Größe beim Editieren
# ═══════════════════════════════════════════════════════════════════════════════

async def on_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fängt Text-Eingaben ab, wenn eine Edit-Aktion pending ist.
    Validiert den Wert und zeigt die Bestätigungs-Nachricht an.
    """
    if not update.message or not update.message.text:
        return

    pending: dict | None = context.chat_data.get("pending_edit")
    if not pending:
        return

    text = update.message.text.strip()

    try:
        value = float(text.replace(",", ".").replace("$", "").replace(" ", ""))
    except ValueError:
        await update.message.reply_text(
            "❌ Ungültige Eingabe. Bitte nur Zahlen eingeben "
            "(z. B. <code>69500</code> oder <code>0.015</code>)."
        )
        return

    if value <= 0:
        await update.message.reply_text("❌ Wert muss größer als 0 sein.")
        return

    if pending["field"] == "price":
        old_val = f"${pending['limit_px']:,.2f}"
        new_val = f"${value:,.2f}"
        desc = f"Preis: {old_val} → {new_val}"
        new_px, new_size = value, pending["size"]
    else:
        old_val = f"{pending['size']:.4f} {pending['coin']}"
        new_val = f"{value:.4f} {pending['coin']}"
        desc = f"Größe: {old_val} → {new_val}"
        new_px, new_size = pending["limit_px"], value

    context.chat_data["pending_edit_confirm"] = {
        **pending,
        "new_px": new_px,
        "new_size": new_size,
    }
    context.chat_data.pop("pending_edit", None)

    s_emoji = "🟢" if pending["side"] == "B" else "🔴"
    confirm_text = (
        f"✏️ <b>Änderung bestätigen</b>\n"
        f"{'─' * 30}\n"
        f"{s_emoji} {pending['side']} {pending['coin']}\n"
        f"{desc}\n"
        f"{'─' * 30}\n"
        f"Diese Order wird auf der Börse geändert."
    )
    confirm_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Bestätigen", callback_data=f"oe_confirm:{pending['idx']}"),
            InlineKeyboardButton("↩️ Abbrechen", callback_data=f"oe_abort:{pending['idx']}"),
        ]
    ])
    await update.message.reply_text(confirm_text, parse_mode="HTML", reply_markup=confirm_kb)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER-FUNKTIONEN
# ═══════════════════════════════════════════════════════════════════════════════

async def _edit_msg(query, text: str, markup=None, parse_mode="HTML") -> None:
    """
    Editiert die Nachricht – erkennt automatisch, ob Foto oder Text.
    - Foto  → edit_message_caption
    - Text  → edit_message_text

    Dies ist der zentrale Fix für den Fehler
    "There is no text in the message to edit",
    der auftritt, wenn man edit_message_text() auf eine
    Foto-Nachricht (edit_message_media) anwendet.
    """
    if query.message and query.message.photo:
        await query.edit_message_caption(
            caption=text,
            parse_mode=parse_mode,
            reply_markup=markup,
        )
    else:
        await query.edit_message_text(
            text,
            parse_mode=parse_mode,
            reply_markup=markup,
        )


def _build_orders_cache(summary) -> list[dict]:
    """
    Baut eine flache Liste aller offenen Orders aus dem AccountSummary.
    Wird in bot_data["orders_cache"] gespeichert und von den
    Trading-Buttons über den Index (<idx>) adressiert.

    Returns:
        Liste von Dicts: {coin, oid, side, limit_px, size, dex}
    """
    cache: list[dict] = []
    for dex_name, orders in summary.orders_by_dex.items():
        for o in orders:
            cache.append({
                "coin": o.coin,
                "oid": o.oid,
                "side": o.side,
                "limit_px": o.limit_px,
                "size": o.size,
                "dex": dex_name,
            })
    return cache


def _trader_cancel(coin: str, oid: int, dex: str) -> dict:
    """Thread-safe Wrapper für trader.cancel_order()."""
    from trader import cancel_order
    return cancel_order(coin, oid, dex)


def _trader_modify(
    coin: str, oid: int, is_buy: bool,
    new_sz: float, new_px: float, dex: str,
) -> dict:
    """Thread-safe Wrapper für trader.modify_order()."""
    from trader import modify_order
    return modify_order(coin, oid, is_buy, new_sz, new_px, dex)


async def _refresh_orders_view(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Lädt die Orders-Ansicht neu (nach Abbrechen einer Trading-Aktion).
    Holt frische Daten, aktualisiert den Cache und rendert die Ansicht.
    """
    await _edit_msg(query, "⏳ Lade Orders…")

    loop = asyncio.get_event_loop()
    summary = await loop.run_in_executor(
        None,
        lambda: get_account_summary(
            WALLET_ADDRESS,
            hip3_dexes=HIP3_DEXES,
            api_url=API_URL,
            debug=False,
        ),
    )

    caption, button_rows = format_orders(summary, trading_enabled=TRADING_ENABLED)

    if TRADING_ENABLED:
        context.bot_data["orders_cache"] = _build_orders_cache(summary)

    if button_rows:
        all_rows = [
            [
                InlineKeyboardButton("📊 Kontostand", callback_data="balance"),
                InlineKeyboardButton("📈 Positionen", callback_data="positions"),
                InlineKeyboardButton("⏳ Orders", callback_data="orders"),
            ]
        ] + button_rows
        markup = InlineKeyboardMarkup(all_rows)
    else:
        markup = _build_menu_keyboard()

    with open(IMAGE_ORDERS, "rb") as f:
        media = InputMediaPhoto(media=f, caption=caption, parse_mode="HTML")

    await query.edit_message_media(media=media, reply_markup=markup)


# ═══════════════════════════════════════════════════════════════════════════════
# LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════

async def _post_init(app: Application) -> None:
    """
    Wird nach dem Bot-Start aufgerufen.
    - Setzt Bot-Befehle (/menu)
    - Setzt den Chat-Menü-Button
    - Startet den WebSocket-Listener

    Hinweis zum Dashboard-Link:
    Telegram kann den Chat-Menü-Button nur dann direkt eine Web-App
    öffnen lassen (MenuButtonWebApp), wenn die URL mit https:// beginnt.
    WEB_URL ist in der Praxis meist eine lokale http://-Adresse (LAN-IP),
    daher wird hier bewusst KEIN MenuButtonWebApp gesetzt – der Menü-
    Button bleibt immer MenuButtonCommands (öffnet /menu). Den Dashboard-
    Link selbst verschickt cmd_start() als eigene Nachricht, sobald der
    Webserver erreichbar ist.
    """
    await app.bot.set_my_commands([
        BotCommand("menu", "🌐 Hyperliquid Dashboard"),
    ])

    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    # ── Nur fürs Logging: Erreichbarkeit einmalig prüfen ─────────────────────
    if WEB_ENABLED:
        if _is_web_reachable(WEB_PORT):
            logger.info(f"Web-Dashboard erreichbar – Link wird bei /menu gesendet: {WEB_URL}")
        else:
            logger.warning(
                f"Web-Dashboard nicht erreichbar (Port {WEB_PORT}). "
                f"Kein Dashboard-Link, bis der Webserver läuft. "
                f"(Starte den Webserver mit 'make web')"
            )
    else:
        logger.info("Web-Dashboard deaktiviert (WEB_ENABLED=False)")

    # ── WebSocket-Listener starten ────────────────────────────────────────────
    start_ws_listener(app)
    logger.info("Bot initialisiert – WebSocket-Listener aktiv.")


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Globaler Error-Handler: Fängt alle unbehandelten Exceptions ab.
    """
    error = context.error

    if isinstance(error, RetryAfter):
        logger.warning(f"Rate-Limit: {error.retry_after}s warten.")
        return

    if isinstance(error, TelegramError):
        logger.warning(f"Telegram-Fehler: {error}")
        return

    logger.error(f"Unerwarteter Fehler: {error}", exc_info=error)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """
    Entry-Point: Baut die Application, registriert alle Handler
    und startet das Polling.
    """
    # Platzhalter-Check
    if "YOUR_" in BOT_TOKEN or "YOUR_" in WALLET_ADDRESS:
        sys.exit(
            "⚠️  config.py enthält noch Platzhalter.\n"
            "Bitte BOT_TOKEN und WALLET_ADDRESS eintragen."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    # ── Handler registrieren ──────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(
        CallbackQueryHandler(on_button, pattern=r"^(balance|positions|orders)$")
    )

    if TRADING_ENABLED:
        app.add_handler(
            CallbackQueryHandler(
                on_order_button,
                pattern=r"^(oc|oe)(_confirm|_abort|_price|_size)?(:\d+)?$",
            )
        )
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_input)
        )
        logger.info("Trading-Modus: AKTIV")
    else:
        logger.info("Trading-Modus: deaktiviert")

    app.add_error_handler(_error_handler)

    # ── Start ─────────────────────────────────────────────────────────────────
    print("🤖 Hyperliquid Telegram Bot läuft…")
    print(f"   Wallet:  {WALLET_ADDRESS}")
    print(f"   DEXe:    {HIP3_DEXES or 'alle automatisch'}")
    print(f"   Trading: {'AKTIV' if TRADING_ENABLED else 'deaktiviert'}")
    print(f"   Web:     {'AKTIV' if WEB_ENABLED else 'deaktiviert'}")
    print("   Stoppe mit Ctrl+C\n")

    app.run_polling()


if __name__ == "__main__":
    main()
