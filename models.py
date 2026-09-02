"""
models.py – Datenstrukturen (Dataclasses) für den Bot.

Diese Dateien enthalten KEINE Logik, nur reine Datencontainer.
Sie werden von hl_api.py befüllt und von formatters.py gelesen.
"""

from dataclasses import dataclass, field


# ─── Position ─────────────────────────────────────────────────────────────────

@dataclass
class PositionInfo:
    """
    Repräsentiert eine einzelne offene Perps-Position.

    Die Werte werden aus dem clearinghouseState-API-Response extrahiert
    (Feld: assetPositions[i].position).
    """
    coin: str              # z. B. "BTC", "ETH"
    side: str              # "LONG" (szi > 0) oder "SHORT" (szi < 0)
    leverage: int          # Hebel, z. B. 5 für 5x
    leverage_type: str     # "cross" oder "isolated"
    size: float            # Absoluter Betrag (immer positiv), z. B. 0.013
    entry_px: float        # Durchschnittlicher Einstiegspreis
    mark_px: float         # Aktueller Mark-Preis (abgeleitet aus positionValue / |szi|)
    unrealized_pnl: float  # Unrealisierter Gewinn/Verlust in USDC
    roe: float             # Return on Equity als Dezimalzahl (-0.5424 = -54.24%)
    margin_used: float     # Margin, die diese Position belegt (USDC)


# ─── Order ─────────────────────────────────────────────────────────────────────

@dataclass
class OrderInfo:
    """
    Repräsentiert eine einzelne offene Limit-Order.

    Die Werte werden aus dem openOrders-API-Response extrahiert.
    """
    coin: str              # z. B. "BTC"
    side: str              # "B" (Buy) oder "S" (Sell)
    limit_px: float        # Limit-Preis
    size: float            # Order-Größe in Base-Asset
    notional: float        # Nominalwert = limit_px * size (USDC)
    oid: int = 0           # Order-ID (für Cancel/Modify)


# ─── Account Summary ───────────────────────────────────────────────────────────

@dataclass
class AccountSummary:
    """
    Aggregierter Kontostand über ALLE DEXe (nativer + Builder).

    Wird von get_account_summary() in hl_api.py erstellt
    und von den formatters.py-Funktionen für die Telegram-Ausgabe genutzt.
    """
    account_mode: str          # "unifiedAccount", "portfolioMargin" oder "disabled"
    total_balance: float       # Gesamt-Guthaben inkl. offener Positionen (USDC)
    unrealized_pnl: float      # Summe aller unrealisierten PnLs (USDC)
    withdrawable: float        # Abhebbarer Betrag (USDC)
    capital_in_orders: float   # In offenen Orders gebundenes Kapital (USDC)
    capital_in_positions: float  # In Positionen gebundenes Kapital = Margin (USDC)

    # Positionen und Orders, gruppiert nach DEX-Namen.
    # Schlüssel: "" = nativer DEX, "xyz" = Builder xyz, etc.
    positions_by_dex: dict[str, list[PositionInfo]] = field(default_factory=dict)
    orders_by_dex: dict[str, list[OrderInfo]] = field(default_factory=dict)
