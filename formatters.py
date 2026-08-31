"""
formatters.py – Telegram-HTML-Formatierung.

Wandelt die Dataclasses aus models.py in Telegram-kompatible HTML-Strings um.
Diese Datei enthält KEINE API-Logik – sie formatiert nur vorhandene Daten.

Telegram-HTML-Tags:
- <b>…</b>  → fett
- <code>…</code> → monospace
- <i>…</i>  → kursiv
- <a href="…">…</a> → Link
"""

from datetime import datetime, timezone

from models import AccountSummary


# ─── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _timestamp() -> str:
    """
    Liefert den aktuellen UTC-Zeitstempel als String.
    Format: "2026-08-28 14:32:05 UTC"
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _dex_label(dex_name: str) -> str:
    """
    Liefert ein menschenlesbares Label für einen DEX-Namen.
    "" → "🌐 Krypto-Hauptmarkt" (nativer DEX)
    "xyz" → "🌐 XYZ"
    """
    return "🌐 Krypto-Hauptmarkt" if dex_name == "" else f"🌐 {dex_name.upper()}"


# ─── Format-Funktionen ─────────────────────────────────────────────────────────

def format_balance(summary: AccountSummary) -> str:
    """
    Formatiert den Kontostand als Telegram-HTML-String.

    Zeigt: Modus, Gesamt-Guthaben, unrealisierten PnL,
    abhebbaren Betrag und gebundenes Kapital (Orders + Positionen).
    """
    lines = [
        f"📊 <b>Kontostand</b> <code>{_timestamp()}</code>",
        "",
        f"Modus: <code>{summary.account_mode}</code>",
        f"Gesamt-Guthaben: <b>{summary.total_balance:.2f} USDC</b>",
        f"Unrealisierter PnL: {summary.unrealized_pnl:+.2f} USDC",
        f"Abhebbar: <b>{summary.withdrawable:.2f} USDC</b>",
        f"Gebunden in Orders: {summary.capital_in_orders:.2f} USDC",
        f"Gebunden in Positionen: {summary.capital_in_positions:.2f} USDC",
    ]
    return "\n".join(lines)


def format_positions(summary: AccountSummary) -> str:
    """
    Formatiert alle offenen Positionen, gruppiert nach DEX.

    Pro Position: Coin, Richtung, Hebel, Menge, Einstiegspreis,
    aktueller Preis, PnL und ROE.
    """
    # Falls keine Positionen existieren → kurze Meldung
    if not any(summary.positions_by_dex.values()):
        return f"📊 <b>Positionen</b> <code>{_timestamp()}</code>\n\nKeine offenen Positionen."

    lines = [f"📊 <b>Positionen</b> <code>{_timestamp()}</code>", ""]

    for dex_name, positions in summary.positions_by_dex.items():
        # DEXe ohne Positionen überspringen
        if not positions:
            continue

        # DEX-Header
        lines.append(f"<b>{_dex_label(dex_name)}</b>")
        lines.append("-" * 30)

        for p in positions:
            # Emojis: 🟢 = LONG, 🔴 = SHORT, 📈 = Gewinn, 📉 = Verlust
            side_emoji = "🟢" if p.side == "LONG" else "🔴"
            pnl_emoji = "📈" if p.unrealized_pnl >= 0 else "📉"
            pnl_sign = "+" if p.unrealized_pnl >= 0 else ""

            lines.append(f"{side_emoji} <b>{p.coin}</b> ({p.side} {p.leverage}x)")
            lines.append(f"  Menge: {p.size:.4f} {p.coin}")
            lines.append(f"  Einstieg: ${p.entry_px:,.2f}")
            lines.append(f"  Aktuell: ${p.mark_px:,.2f}")
            lines.append(f"  PnL: {pnl_emoji} {pnl_sign}{p.unrealized_pnl:.2f} USDC ({pnl_sign}{p.roe*100:.2f}%)")
            lines.append("")  # Leerzeile zwischen Positionen

    return "\n".join(lines)


def format_orders(summary: AccountSummary) -> str:
    """
    Formatiert alle offenen Orders, gruppiert nach DEX.

    Pro Order: Richtung, Coin, Größe, Limit-Preis, Nominalwert.
    """
    if not any(summary.orders_by_dex.values()):
        return f"⏳ <b>Offene Orders</b> <code>{_timestamp()}</code>\n\nKeine offenen Orders."

    lines = [f"⏳ <b>Offene Orders</b> <code>{_timestamp()}</code>", ""]

    for dex_name, orders in summary.orders_by_dex.items():
        if not orders:
            continue

        lines.append(f"<b>{_dex_label(dex_name)}</b>")
        lines.append("-" * 30)

        for o in orders:
            side_emoji = "🟢" if o.side == "B" else "🔴"
            side_label = "BUY" if o.side == "B" else "SELL"
            lines.append(
                f"{side_emoji} {side_label} {o.size:.4f} {o.coin} "
                f"@ ${o.limit_px:,.2f} (Wert: {o.notional:.2f} USDC)"
            )
        lines.append("")

    return "\n".join(lines)
