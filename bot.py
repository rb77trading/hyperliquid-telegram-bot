"""
bot.py – Telegram-Bot-Entry-Point.

Verdrahtet die Telegram-Handler mit der Hyperliquid-API-Logik.
Diese Datei enthält:
- Das Inline-Keyboard (Menü-Buttons)
- Die Callback-Handler (Button-Klicks)
- Den /start- und /menu-Command
- Den WebSocket-Listener-Start (via post_init)
- main() – startet den Bot

Abhängigkeiten:
    pip install hyperliquid-python-sdk python-telegram-bot
"""

import asyncio
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
    ContextTypes,
)

from config import BOT_TOKEN, WALLET_ADDRESS, HIP3_DEXES, API_URL
from hl_api import get_account_summary
from formatters import format_balance, format_positions, format_orders
from ws import start_ws_listener

# ─── Pfade zu den Bildern ──────────────────────────────────────────────────────
# Relativ zum Projektordner (bot.py liegt in hyperliquid-bot/)
IMAGE_DIR = Path(__file__).parent / "images"

IMAGE_MENU      = IMAGE_DIR / "menu.png"
IMAGE_BALANCE   = IMAGE_DIR / "balance.png"
IMAGE_POSITIONS = IMAGE_DIR / "positions.png"
IMAGE_ORDERS    = IMAGE_DIR / "orders.png"

# ═══════════════════════════════════════════════════════════════════════════════
# INLINE-KEYBOARD
# ═══════════════════════════════════════════════════════════════════════════════

# Das Menü-Keyboard wird in JEDER Nachricht angezeigt (auch nach Edit).
# callback_data wird an on_button() übergeben, wenn ein Button gedrückt wird.
MENU_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📊 Kontostand", callback_data="balance"),
        InlineKeyboardButton("📈 Positionen", callback_data="positions"),
    ],
    [
        InlineKeyboardButton("⏳ Offene Orders", callback_data="orders"),
    ],
])

# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM-HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Wird bei /start oder /menu aufgerufen.
    Sendet die Menü-Nachricht mit den 3 Buttons.
    Speichert zusätzlich die Chat-ID in bot_data für den WS-Listener.

    WICHTIG: context.bot_data ist ein reguläres Dict, das über alle
    Updates persistiert. Es ist der richtige Ort für Daten, die von
    verschiedenen Threads (Bot + WS) gelesen werden müssen.
    """
    # Chat-ID für den WS-Listener speichern (dynamisch, bei jedem /start)
    context.bot_data["hl_chat_id"] = update.effective_chat.id

    await update.message.reply_photo(
        photo=open(IMAGE_MENU, "rb"),          # Dateihandle
        caption="🌐 <b>Hyperliquid Dashboard</b>\n\nWähle eine Ansicht:",
        parse_mode="HTML",
        reply_markup=MENU_KEYBOARD,
    )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Button-Klick → editiert die Nachricht mit themenbezogenem Bild + Caption.
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
    elif section == "positions":
        image_path = IMAGE_POSITIONS
        caption = format_positions(summary)
    elif section == "orders":
        image_path = IMAGE_ORDERS
        caption = format_orders(summary)
    else:
        # Fallback: Text-Nachricht (sollte nicht vorkommen)
        await query.edit_message_text("❓ Unbekannter Button.")
        return

    # Nachricht als FOTO editieren:
    # - photo: neues themenbezogenes Bild
    # - caption: der formatierte Text (mit HTML)
    # - reply_markup: Buttons bleiben erhalten
    media = InputMediaPhoto(
        media=open(image_path, "rb"),
        caption=caption,
        parse_mode="HTML",
    )

    await query.edit_message_media(
        media=media,
        reply_markup=MENU_KEYBOARD,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# POST-INIT CALLBACK (WebSocket-Start)
# ═══════════════════════════════════════════════════════════════════════════════

async def _post_init(app: Application) -> None:
    """
    Wird EINMALIG nach dem Bot-Start aufgerufen.
    - Registriert die Bot-Commands (erscheinen im blauen Menü-Button)
    - Startet den WebSocket-Listener
    """
    # ── Bot-Commands registrieren ─────────────────────────────────────────────
    # Diese Commands erscheinen im blauen Menü-Button (links neben dem
    # Eingabefeld). Beim Tippen auf den Button öffnet sich ein Dropdown
    # mit der Command-Liste.
    #
    # WICHTIG: Ohne set_my_commands() ist das blaue Menü leer!
    await app.bot.set_my_commands([
        BotCommand("menu", "🌐 Hyperliquid Dashboard"),
    ])

    # ── Menü-Button explizit auf "Commands" setzen ────────────────────────────
    # Das ist zwar das Telegram-Default, aber explizit setzen ist sicherer
    # (falls ein anderer Bot zuvor einen WebApp-Button gesetzt hat).
    await app.bot.set_chat_menu_button(
        menu_button=MenuButtonCommands()
    )

    # ── WebSocket-Listener starten ────────────────────────────────────────────
    start_ws_listener(app)

async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Globaler Error-Handler: Fängt alle ungesicherten Exceptions ab.
    Ohne diesen Handler loggt PTB nur "No error handlers are registered".

    Typische Ursachen:
    - httpcore.ReadError / ConnectError → transienter Netzwerkfehler
    - RetryAfter → Rate-Limit (Telegram: max ~30 Msg/s global, ~1/s pro Chat)
    - InvalidToken → Bot-Token falsch
    """
    error = context.error

    # Rate-Limit: kurz warten und ignorieren
    if isinstance(error, RetryAfter):
        logger.warning(f"Rate-Limit: {error.retry_after}s warten.")
        return

    # Transiente Netzwerkfehler: loggen, nicht crashen
    if isinstance(error, TelegramError):
        logger.warning(f"Telegram-Fehler: {error}")
        return

    # Alle anderen Fehler
    logger.error(f"Unerwarteter Fehler: {error}", exc_info=error)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """
    Startet den Telegram-Bot im Long-Polling-Modus.

    Long Polling: Der Bot fragt regelmäßig das Telegram-Server-API ab,
    ob neue Updates (Nachrichten, Callbacks) vorliegen.
    Kein Webserver/Webhook nötig – läuft überall.

    Der WebSocket-Listener wird via post_init nach dem Bot-Start
    in einem separaten Thread gestartet.
    """
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)  # ← WS-Listener nach Bot-Start
        .build()
    )

    # Handler registrieren:
    # - /start → Menü öffnen + Chat-ID speichern
    # - /menu  → Menü öffnen (Alias)
    # - CallbackQuery → Button-Klicks bearbeiten
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CallbackQueryHandler(on_button))
    # ← Error-Handler registrieren (fängt alle Exceptions ab)
    app.add_error_handler(_error_handler)

    print("🤖 Hyperliquid Telegram Bot läuft…")
    print(f"   Wallet: {WALLET_ADDRESS}")
    print(f"   DEXe:   {HIP3_DEXES or 'alle automatisch'}")
    print("   Stoppe mit Ctrl+C\n")

    # run_polling() blockiert bis Ctrl+C
    app.run_polling()

if __name__ == "__main__":
    main()
