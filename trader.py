"""
trader.py – Order-Aktionen (Cancel, Modify, Place) via Hyperliquid Exchange.

Nur aktiv, wenn TRADING_ENABLED=True in config.py.
Verwendet eine SEPARATE Agent-Wallet (AGENT_PRIVATE_KEY).

HIP-3 / Builder-DEXe:
    Die SDK-Methode name_to_asset() kennt nur Coins aus dem nativen Meta.
    Für HIP3-Coins (Format "xyz:SPCX") wird die Methode monkey-patched,
    so dass der korrekte Asset-Index berechnet wird:
        asset_id = 100000 + perpDexIndex × 10000 + indexInMeta

    Danach funktionieren exchange.cancel(), exchange.modify_order()
    und exchange.order() für ALLE Coins identisch – kein separater Pfad.
"""

import logging
from typing import Optional

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.constants import MAINNET_API_URL

from config import AGENT_PRIVATE_KEY, API_URL, WALLET_ADDRESS

logger = logging.getLogger(__name__)

# ─── Singleton-State ───────────────────────────────────────────────────────────

_wallet: Optional[Account] = None
_info: Optional[Info] = None
_exchange: Optional[Exchange] = None

# Cache: DEX-Name → perpDexIndex
_perp_dex_index_cache: dict[str, int] = {}
# Cache: (dex, coin) → asset_id
_asset_id_cache: dict[tuple, int] = {}


def _get_wallet() -> Account:
    """Liefert die Agent-Wallet (lazy init)."""
    global _wallet
    if _wallet is None:
        _wallet = Account.from_key(AGENT_PRIVATE_KEY)
    return _wallet


def _get_info() -> Info:
    """Liefert die Info-Instanz (lazy init)."""
    global _info
    if _info is None:
        _info = Info(API_URL, skip_ws=True)
    return _info


def _get_exchange() -> Exchange:
    """
    Lazy-Initialisierung der Exchange-Instanz (singleton).
    Patcht name_to_asset(), damit HIP3-Coins aufgelöst werden können.
    """
    global _exchange
    if _exchange is None:
        _exchange = Exchange(
            _get_wallet(),
            API_URL,
            account_address=WALLET_ADDRESS,
        )
        # SDK-Methode patchen: name_to_asset() für HIP3-Coins erweitern
        _patch_name_to_asset(_exchange)
    return _exchange


# ═══════════════════════════════════════════════════════════════════════════════
# HIP-3 ASSET-ID-BERECHNUNG
# ═══════════════════════════════════════════════════════════════════════════════

def _get_perp_dex_index(dex: str) -> int:
    """Ermittelt den perpDexIndex für einen DEX-Namen (gecacht)."""
    if dex in _perp_dex_index_cache:
        return _perp_dex_index_cache[dex]

    info = _get_info()
    dexs = info.post("/info", {"type": "perpDexs"})

    for i, d in enumerate(dexs):
        if d is None:
            continue
        if d.get("name", "") == dex:
            _perp_dex_index_cache[dex] = i
            return i

    raise ValueError(
        f"DEX '{dex}' nicht in perpDexs gefunden. "
        f"Verfügbare: {[d.get('name', '') for d in dexs if d is not None]}"
    )


def _resolve_hip3_asset(dex: str, coin: str) -> int:
    """
    Berechnet die Asset-ID für einen HIP3-Coin.
    Formel: 100000 + perpDexIndex × 10000 + indexInMeta
    """
    cache_key = (dex, coin)
    if cache_key in _asset_id_cache:
        return _asset_id_cache[cache_key]

    info = _get_info()
    meta = info.post("/info", {"type": "meta", "dex": dex})
    universe = meta.get("universe", [])

    index_in_meta = None
    for i, asset in enumerate(universe):
        # Meta-Namen sind prefixed: "xyz:SPCX"
        if asset.get("name", "") == f"{dex}:{coin}" or asset.get("name", "") == coin:
            index_in_meta = i
            break

    if index_in_meta is None:
        raise ValueError(
            f"Coin '{coin}' nicht in DEX '{dex}' gefunden. "
            f"Verfügbare: {[a.get('name', '') for a in universe[:10]]}"
        )

    perp_dex_index = _get_perp_dex_index(dex)
    asset_id = 100000 + perp_dex_index * 10000 + index_in_meta
    _asset_id_cache[cache_key] = asset_id
    return asset_id


def _patch_name_to_asset(exchange: Exchange) -> None:
    """
    Ersetzt exchange.info.name_to_asset() durch eine erweiterte Version,
    die HIP3-Coins (Format "dex:coin") korrekt auflöst.

    Nativer DEX:  Unveränderter SDK-Pfad.
    HIP-3:        Berechnet die Asset-ID per Formel.
    """
    original_name_to_asset = exchange.info.name_to_asset

    def patched_name_to_asset(name: str) -> int:
        if ":" in name:
            dex, coin = name.split(":", 1)
            return _resolve_hip3_asset(dex, coin)
        return original_name_to_asset(name)

    exchange.info.name_to_asset = patched_name_to_asset
    logger.info("name_to_asset() gepatcht – HIP3-Coins werden unterstützt.")


# ═══════════════════════════════════════════════════════════════════════════════
# ORDER-AKTIONEN (einheitlich für nativ + HIP3)
# ═══════════════════════════════════════════════════════════════════════════════

def cancel_order(coin: str, oid: int, dex: str = "") -> dict:
    """
    Storniert eine offene Order per Order-ID.

    Args:
        coin: Coin-Name, z. B. "BTC" oder "xyz:SPCX"
        oid:  Order-ID (int)
        dex:  DEX-Name ("", "xyz", etc.) – nur für Logging
    """
    exchange = _get_exchange()
    full_coin = f"{dex}:{coin}" if dex and ":" not in coin else coin

    try:
        result = exchange.cancel(full_coin, oid)

        if result.get("status") == "ok":
            logger.info(f"Order storniert: {full_coin} OID={oid}")
        else:
            logger.warning(f"Cancel-Fehler: {result}")
        return result

    except Exception as e:
        logger.error(f"Cancel-Exception: {e}")
        return {"status": "error", "error": str(e)}


def modify_order(
    coin: str,
    oid: int,
    is_buy: bool,
    new_sz: float,
    new_px: float,
    dex: str = "",
) -> dict:
    """
    Modifiziert eine bestehende Order (Preis und/oder Größe).

    Args:
        coin:   Coin-Name, z. B. "BTC" oder "xyz:SPCX"
        oid:    Order-ID
        is_buy: True = Buy, False = Sell
        new_sz: Neue Größe
        new_px: Neuer Preis
        dex:    DEX-Name ("", "xyz", etc.) – nur für Logging
    """
    exchange = _get_exchange()
    full_coin = f"{dex}:{coin}" if dex and ":" not in coin else coin
    order_type = {"limit": {"tif": "Gtc"}}

    try:
        result = exchange.modify_order(
            oid, full_coin, is_buy, new_sz, new_px, order_type,
        )

        if result.get("status") == "ok":
            logger.info(
                f"Order modifiziert: {full_coin} OID={oid} → {new_sz} @ {new_px}"
            )
        else:
            logger.warning(f"Modify-Fehler: {result}")
        return result

    except Exception as e:
        logger.error(f"Modify-Exception: {e}")
        return {"status": "error", "error": str(e)}


def place_order(
    coin: str,
    is_buy: bool,
    sz: float,
    limit_px: float,
    dex: str = "",
    reduce_only: bool = False,
) -> dict:
    """
    Platziert eine neue Limit-Order.

    Args:
        coin:        Coin-Name
        is_buy:      True = Buy, False = Sell
        sz:          Größe
        limit_px:    Limit-Preis
        dex:         DEX-Name ("", "xyz", etc.)
        reduce_only: Nur Position reduzieren
    """
    exchange = _get_exchange()
    full_coin = f"{dex}:{coin}" if dex and ":" not in coin else coin
    order_type = {"limit": {"tif": "Gtc"}}

    try:
        result = exchange.order(
            full_coin, is_buy, sz, limit_px, order_type,
            reduce_only=reduce_only,
        )

        if result.get("status") == "ok":
            logger.info(
                f"Order plaziert: {'BUY' if is_buy else 'SELL'} "
                f"{sz} {full_coin} @ {limit_px}"
            )
        else:
            logger.warning(f"Order-Fehler: {result}")
        return result

    except Exception as e:
        logger.error(f"Order-Exception: {e}")
        return {"status": "error", "error": str(e)}   
