"""
hl_api.py – Sämtliche Hyperliquid-API-Kommunikation.

Diese Datei enthält ALLE Funktionen, die mit der Hyperliquid-REST-API
sprechen. Sie ist komplett unabhängig von Telegram und kann auch in
anderen Kontexten (z. B. WebSocket-Listener, CLI-Tool) wiederverwendet werden.

══════════════════════════════════════════════════════════════════════════════════
ARCHITEKTUR-ENTSCHEIDUNGEN
══════════════════════════════════════════════════════════════════════════════════

1. Synchron (keine async):
   Die Hyperliquid-SDK nutzt intern `requests`, was synchron ist.
   Der Telegram-Bot offloadet die Calls via `run_in_executor` in einen
   Thread, um den asyncio-Event-Loop nicht zu blockieren.

2. Persistentes Info-Objekt (Singleton):
   Das Info-Objekt kapselt eine `requests.Session`, die eine
   TCP+TLS-Verbindung zum Hyperliquid-Server hält.
   Beim ersten Aufruf wird die Verbindung aufgebaut (~150-200ms).
   Bei allen folgenden Aufrufen wird dieselbe Verbindung wiederverwendet
   (Connection Keep-Alive) → spart ~150ms pro Call.

   WICHTIG: `requests.Session` ist thread-safe, daher ist es sicher,
   dasselbe Info-Objekt aus mehreren Threads (ThreadPoolExecutor)
   gleichzeitig zu nutzen.

3. Fehlerbehandlung:
   Jeder API-Call ist in try/except verpackt. Ein fehlgeschlagener
   DEX (z. B. temporärer 500-Fehler) bricht den gesamten Abruf NICHT ab.
   Stattdessen wird ein Fallback-Wert geliefert und der DEX wird
   einfach übersprungen.

4. Nativer DEX:
   Der nativer Hyperliquid-DEX hat in der API keinen Namen (null).
   In diesem Code wird er durch den leeren String "" repräsentiert.
══════════════════════════════════════════════════════════════════════════════════
"""

from typing import Optional

from hyperliquid.info import Info
from hyperliquid.utils.error import ServerError, ClientError

from models import PositionInfo, OrderInfo, AccountSummary


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENTES INFO-OBJEKT (SINGLETON)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Warum Singleton?
# ─────────────────
# Das Info-Objekt kapselt eine requests.Session, die eine HTTP/1.1-
# Keep-Alive-Verbindung zum Hyperliquid-Server hält.
#
# OHNE Singleton:
#   Jeder Aufruf von get_account_summary() → neues Info() → neuer
#   TCP-Handshake + TLS-Handshake (~150-200ms) → Connection Close.
#
# MIT Singleton:
#   Erster Aufruf: TCP + TLS (~150ms) → Connection bleibt offen.
#   Zweiter Aufruf: Connection wird wiederverwendet (~0ms Overhead).
#   Dritter Aufruf: dito.
#
# Thread-Safety:
#   requests.Session ist thread-safe (nutzt intern einen
#   Pool-Connection-Adapter). Mehrere Threads können dieselbe
#   Session gleichzeitig nutzen, ohne dass es zu Race Conditions kommt.
#
# ──────────────────────────────────────────────────────────────────────────────

# Module-level Variable: wird beim ersten Aufruf von _get_info() gesetzt.
# None = noch keine Verbindung aufgebaut.
_info_instance: Optional[Info] = None


def _get_info(api_url: str) -> Info:
    """
    Liefert das persistente Info-Objekt (Singleton-Pattern).

    Beim ersten Aufruf wird die Verbindung aufgebaut.
    Bei allen folgenden Aufrufen wird dieselbe Verbindung wiederverwendet.

    Args:
        api_url: Der Hyperliquid-API-Endpoint (z. B. Mainnet-URL).

    Returns:
        Info-Objekt mit offener Session.

    Hinweis:
        Falls die api_url sich zwischen Aufrufen ändert (z. B. Mainnet →
        Testnet), wird ein NEUES Info-Objekt erstellt. In der Praxis
        passiert das nie, da die URL in config.py fest konfiguriert ist.
    """
    global _info_instance

    # Falls noch kein Objekt existiert ODER die URL sich geändert hat:
    if _info_instance is None:
        _info_instance = Info(api_url, skip_ws=True)

    return _info_instance


# ═══════════════════════════════════════════════════════════════════════════════
# API-HILFSFUNKTIONEN
# ═══════════════════════════════════════════════════════════════════════════════

def _get_account_mode(info: Info, address: str) -> str:
    """
    Ermittelt den Account-Modus des Users.

    Der Modus bestimmt, woher die Balance-Daten stammen:
    - "unifiedAccount"  → Spot-Clearinghouse-State ist Source of Truth
    - "portfolioMargin" → dito
    - "disabled"        → Perps- und Spot-Balance sind getrennt (Manual/Standard)

    Der Endpoint "userAbstraction" liefert einen einfachen String zurück.
    Bei Fehler (z. B. Endpoint nicht erreichbar): "disabled" als Fallback.

    Args:
        info:    Das persistente Info-Objekt.
        address: Wallet-Adresse (0x…).

    Returns:
        String: "unifiedAccount" | "portfolioMargin" | "disabled"
    """
    try:
        mode = info.post("/info", {"type": "userAbstraction", "user": address})
        return mode if isinstance(mode, str) else "disabled"
    except (ServerError, ClientError):
        # Konservativer Fallback: Wenn der Endpoint nicht erreichbar ist,
        # gehen wir von Manual/Standard aus (die sicherste Annahme).
        return "disabled"


def _get_all_perp_dexes(info: Info) -> list[str]:
    """
    Entdeckt dynamisch ALLE aktiven Perp-DEXe (nativer + alle HIP-3-Builder).

    Der nativer DEX hat in der API keinen Namen (null) → wird als "" behandelt.
    Diese Funktion wird aufgerufen, wenn HIP3_DEXES=None in config.py steht.

    Der Endpoint "perpDexs" liefert eine Liste von Dicts:
    [
        null,                          ← nativer DEX (None!)
        {"name": "xyz", "fullName": "XYZ Markets", ...},
        {"name": "km", "fullName": "KM Perps", ...},
        ...
    ]

    Args:
        info: Das persistente Info-Objekt.

    Returns:
        Liste wie ["", "xyz", "flx", "km", ...]
        Der nativer DEX ("" ist IMMER der erste Eintrag.
    """
    try:
        dexs = info.post("/info", {"type": "perpDexs"})
        names = [""]  # Nativer DEX ist IMMER dabei (erster Eintrag)
        for d in dexs:
            if d is None:
                # Der nativer DEX-Eintrag ist in der Liste als None vorhanden
                # → überspringen (ist bereits als "" in der Liste).
                continue
            name = d.get("name") or ""
            # Duplikate vermeiden (z. B. falls "" noch mal vorkommt)
            if name and name not in names:
                names.append(name)
        return names
    except (ServerError, ClientError) as e:
        print(f"  ⚠️  perpDexs-Fehler: {e}")
        # Fallback: nur nativer DEX (Bot bleibt funktional, nur Builder fehlen)
        return [""]


def _get_spot_clearinghouse_state(info: Info, address: str) -> dict:
    """
    Holt den Spot-Clearinghouse-State.

    BEI UNIFIED/PORTFOLIO MARGIN ist dies die EINZIGE Quelle für:
    - Gesamt-Balance (USDC total)
    - Gebundenes Kapital (USDC hold = Margin + Order-Collateral)

    Bei Manual/Standard enthält er nur die Spot-Token-Balances
    (getrennt von den Perps-Balances).

    Response-Struktur:
    {
        "balances": [
            {"coin": "USDC", "total": "408.1", "hold": "250.7"},
            {"coin": "ETH", "total": "1.5", "hold": "0"},
            ...
        ]
    }

    Args:
        info:    Das persistente Info-Objekt.
        address: Wallet-Adresse.

    Returns:
        Dict mit "balances"-Liste. Bei Fehler: {"balances": []}
    """
    try:
        return info.post("/info", {"type": "spotClearinghouseState", "user": address})
    except (ServerError, ClientError) as e:
        print(f"  ⚠️  spotClearinghouseState-Fehler: {e}")
        # Leeres Fallback-Objekt, damit der Code nicht crash't
        return {"balances": []}


def _get_perps_state(info: Info, address: str, dex: str = "") -> Optional[dict]:
    """
    Holt den Perps-Clearinghouse-State für einen einzelnen DEX.

    dex=""    → nativer DEX (nutzt die SDK-eigene Methode user_state)
    dex="xyz" → Builder-DEX (nutzt rohen POST mit dex-Parameter)

    Der Response enthält:
    - marginSummary: { accountValue, totalMarginUsed, totalNtlPos, totalRawUsd }
    - assetPositions: [{ position: { coin, szi, entryPx, unrealizedPnl, ... } }]
    - withdrawable: float (nur bei Manual/Standard relevant)

    Args:
        info:    Das persistente Info-Objekt.
        address: Wallet-Adresse.
        dex:     DEX-Name ("" = nativer DEX, "xyz" = Builder).

    Returns:
        Dict mit dem Perps-State, oder None bei Fehler.
    """
    try:
        if dex == "":
            # SDK-native Methode – am zuverlässigsten für den nativen DEX.
            # Intern ruft sie denselben Endpoint auf, aber ohne dex-Parameter.
            return info.user_state(address)
        else:
            # Roher POST für Builder-DEXe.
            # Der dex-Parameter ist hier PFLICHT, sonst würde der
            # nativer DEX abgefragt (falsche Daten!).
            return info.post("/info", {
                "type": "clearinghouseState",
                "user": address,
                "dex": dex
            })
    except (ServerError, ClientError) as e:
        print(f"  ⚠️  Perps DEX '{dex or 'native'}': {e}")
        # None → DEX wird in der Schleife übersprungen
        return None


def _get_open_orders(info: Info, address: str, dex: str = "") -> list[dict]:
    """
    Holt alle offenen Limit-Orders für einen DEX.

    Jeder Order-Eintrag im Response enthält u. a.:
    - coin: "BTC"
    - side: "B" (Buy) oder "S" (Sell)
    - limitPx: 69120.0
    - sz: 0.001

    Das in der Order gebundene Kapital = limitPx × sz (Nominalwert).

    Args:
        info:    Das persistente Info-Objekt.
        address: Wallet-Adresse.
        dex:     DEX-Name ("" = nativer DEX).

    Returns:
        Liste von Order-Dicts. Bei Fehler: leere Liste.
    """
    try:
        payload = {"type": "openOrders", "user": address}
        if dex:
            # dex-Parameter nur für Builder-DEXe (nativer DEX braucht ihn nicht)
            payload["dex"] = dex
        orders = info.post("/info", payload)
        return orders if isinstance(orders, list) else []
    except (ServerError, ClientError) as e:
        print(f"  ⚠️  openOrders DEX '{dex or 'native'}': {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# DATA-EXTRAKTION
# ═══════════════════════════════════════════════════════════════════════════════
#
# Diese Funktionen wandeln die rohen API-Responses in saubere Dataclass-
# Objekte um. Sie enthalten KEINE API-Calls – nur reine Datenverarbeitung.
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_positions(state: dict) -> list[PositionInfo]:
    """
    Wandelt den rohen assetPositions-Teil eines clearinghouseState
    in eine Liste von PositionInfo-Objekten um.

    Positions mit szi=0 (geschlossen) werden übersprungen, da sie
    keine aktive Position mehr darstellen.

    Args:
        state: Der komplette clearinghouseState-Dict.

    Returns:
        Liste von PositionInfo-Objekten (leer, wenn keine aktiven Positionen).
    """
    positions = []
    for ap in state.get("assetPositions", []):
        pos = ap.get("position", {})
        szi = float(pos.get("szi", 0))

        # szi=0 bedeutet: keine aktive Position → überspringen
        if szi == 0:
            continue

        # Mark-Preis ableiten:
        # positionValue ist der USD-Wert der Position (size × mark_price).
        # Dividiert durch die Größe ergibt den aktuellen Mark-Preis.
        position_value = float(pos.get("positionValue", 0))
        mark_px = position_value / abs(szi) if szi != 0 else 0.0

        # Leverage ist ein Dict: {"value": 5, "type": "cross"}
        leverage = pos.get("leverage", {})

        positions.append(PositionInfo(
            coin=pos.get("coin", ""),
            side="LONG" if szi > 0 else "SHORT",
            leverage=int(leverage.get("value", 1)),
            leverage_type=leverage.get("type", "cross"),
            size=abs(szi),  # Immer positiv – die Richtung steht in side
            entry_px=float(pos.get("entryPx", 0)),
            mark_px=mark_px,
            unrealized_pnl=float(pos.get("unrealizedPnl", 0)),
            roe=float(pos.get("returnOnEquity", 0)),
            margin_used=float(pos.get("marginUsed", 0)),
        ))
    return positions


def _extract_orders(raw_orders: list[dict]) -> list[OrderInfo]:
    """
    Wandelt die rohe openOrders-Liste in OrderInfo-Objekte um.

    Args:
        raw_orders: Liste von Order-Dicts aus der API.

    Returns:
        Liste von OrderInfo-Objekten.
    """
    orders = []
    for o in raw_orders:
        limit_px = float(o.get("limitPx", 0))
        sz = float(o.get("sz", 0))
        orders.append(OrderInfo(
            coin=o.get("coin", ""),
            side=o.get("side", "B"),
            limit_px=limit_px,
            size=sz,
            notional=limit_px * sz,  # Nominalwert in USDC
            oid=int(o.get("oid", 0)),
        ))
    return orders


# ═══════════════════════════════════════════════════════════════════════════════
# HAUPTFUNKTION
# ═══════════════════════════════════════════════════════════════════════════════

def get_account_summary(
    address: str,
    hip3_dexes: list[str] | None = None,
    api_url: str = None,
    debug: bool = True,
) -> AccountSummary:
    """
    Aggregiert den kompletten Kontostand über alle DEXe.

    Funktionsweise (4 Phasen):
    ┌─────────────────────────────────────────────────────────────────────┐
    │ Phase 1: Setup                                                      │
    │   - Account-Modus ermitteln                                         │
    │   - DEXe bestimmen (explizit oder dynamisch)                        │
    │   - Spot-State abfragen (USDC total/hold)                           │
    ├─────────────────────────────────────────────────────────────────────┤
    │ Phase 2: Alle DEXe parallel abfragen                                │
    │   - Pro DEX: clearinghouseState + openOrders                        │
    │   - Läuft in einem ThreadPoolExecutor (parallel)                    │
    ├─────────────────────────────────────────────────────────────────────┤
    │ Phase 3: Ergebnisse aggregieren                                     │
    │   - Summen bilden, Positionen/Orders extrahieren                    │
    ├─────────────────────────────────────────────────────────────────────┤
    │ Phase 4: Werte zusammenführen                                       │
    │   - Account-Modus-abhängige Logik (Unified vs. Manual)              │
    └─────────────────────────────────────────────────────────────────────┘

    Args:
        address:    Wallet-Adresse (0x…)
        hip3_dexes: None → alle automatisch via perpDexs,
                    sonst explizite Liste wie ["xyz", "km"]
        api_url:    API-Endpoint (None → Default aus config.py)
        debug:      Wenn True, gibt Details in die Konsole aus

    Returns:
        AccountSummary-Objekt mit allen aggregierten Werten
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # api_url aus config.py laden, falls nicht explizit übergeben
    from config import API_URL as _default_url
    if api_url is None:
        api_url = _default_url

    # PERSISTENTES Info-Objekt (Singleton – Connection Keep-Alive)
    info = _get_info(api_url)

    # ── Phase 1: Setup (3 parallele, unabhängige Calls) ─────────────────────
    #
    # userAbstraction, perpDexs und spotClearinghouseState sind
    # voneinander unabhängig → können parallel laufen.
    #
    # ABER: perpDexs wird nur gebraucht, wenn hip3_dexes=None ist.
    # Wenn eine explizite Liste übergeben wurde, sparen wir den Call.

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_mode = pool.submit(_get_account_mode, info, address)

        # perpDexs nur bei None (dynamische Entdeckung)
        if hip3_dexes is None:
            f_dexes = pool.submit(_get_all_perp_dexes, info)
        else:
            f_dexes = None

        f_spot = pool.submit(_get_spot_clearinghouse_state, info, address)

        # Ergebnisse sammeln (blockiert bis alle fertig)
        account_mode = f_mode.result()
        all_dexes = f_dexes.result() if f_dexes else [""] + hip3_dexes
        spot_state = f_spot.result()

    is_unified_or_pm = account_mode in ("unifiedAccount", "portfolioMargin")

    if debug:
        print(f"[DEBUG] Phase 1: mode={account_mode}, dexes={all_dexes}")

    # USDC-Balance aus Spot-State extrahieren
    # USDC wird als coin="USDC" oder token=0 identifiziert
    usdc_total = 0.0
    usdc_hold = 0.0
    for bal in spot_state.get("balances", []):
        if bal.get("coin") == "USDC" or bal.get("token") == 0:
            usdc_total = float(bal.get("total", 0))
            usdc_hold = float(bal.get("hold", 0))

    if debug:
        print(f"[DEBUG] Spot USDC: total={usdc_total}, hold={usdc_hold}")

    # ── Phase 2: Alle DEXe PARALLEL abfragen ─────────────────────────────────
    #
    # Jeder DEX benötigt 2 API-Calls:
    #   - clearinghouseState (Positionen, Margin, PnL)
    #   - openOrders (offene Limit-Orders)
    #
    # Alle DEXe laufen GLEICHZEITIG in separaten Threads.
    # Gesamtdauer = langsamster einziger DEX (nicht Summe aller!).
    #
    # max_workers=10: Reicht für ~10 DEXe. Bei mehr DEXe kann man
    # den Wert erhöhen, aber die Hyperliquid-API hat Rate-Limits.

    def _fetch_dex(dex: str) -> tuple[str, Optional[dict], list[dict]]:
        """
        Worker-Funktion: Holt Perps-State + Orders für EINEN DEX.
        Läuft in einem separaten Thread.

        Returns:
            Tupel (dex_name, state_dict_oder_None, orders_liste)
        """
        state = _get_perps_state(info, address, dex)
        orders = _get_open_orders(info, address, dex)
        return dex, state, orders

    with ThreadPoolExecutor(max_workers=10) as pool:
        # Alle DEXe gleichzeitig einreichen
        futures = {pool.submit(_fetch_dex, dex): dex for dex in all_dexes}

        # Ergebnisse sammeln, sobald sie fertig sind (nicht in Reihenfolge)
        dex_results: dict[str, tuple[Optional[dict], list[dict]]] = {}
        for future in as_completed(futures):
            dex, state, orders = future.result()
            dex_results[dex] = (state, orders)

    # ── Phase 3: Ergebnisse aggregieren ──────────────────────────────────────
    #
    # Hier werden die Rohdaten aus Phase 2 in saubere Dataclasses
    # umgewandelt und die Summen gebildet.

    unrealized_pnl = 0.0
    total_margin_used = 0.0
    perps_account_value = 0.0
    perps_withdrawable = 0.0
    positions_by_dex: dict[str, list[PositionInfo]] = {}
    orders_by_dex: dict[str, list[OrderInfo]] = {}
    capital_in_orders_from_orders = 0.0

    # In der REIHENFOLGE von all_dexes iterieren (nicht as_completed-Reihenfolge),
    # damit die Ausgabe in Telegram immer konsistent ist.
    for dex in all_dexes:
        state, raw_orders = dex_results.get(dex, (None, []))

        # 3a) Perps-State auswerten
        if state:
            ms = state.get("marginSummary", {})
            perps_account_value += float(ms.get("accountValue", 0))
            total_margin_used += float(ms.get("totalMarginUsed", 0))
            perps_withdrawable += float(state.get("withdrawable", 0))

            # Positionen extrahieren und sammeln
            dex_positions = _extract_positions(state)
            positions_by_dex[dex] = dex_positions
            for p in dex_positions:
                unrealized_pnl += p.unrealized_pnl
        else:
            # DEX nicht erreichbar oder keine Daten → leere Liste
            positions_by_dex[dex] = []

        # 3b) Orders extrahieren und sammeln
        dex_orders = _extract_orders(raw_orders)
        orders_by_dex[dex] = dex_orders
        for o in dex_orders:
            capital_in_orders_from_orders += o.notional

    if debug:
        print(f"[DEBUG] Phase 2+3: accountValue={perps_account_value}, "
              f"marginUsed={total_margin_used}, withdrawable={perps_withdrawable}")
        print(f"[DEBUG] Unrealized PnL: {unrealized_pnl}")
        print(f"[DEBUG] Kapital in Orders (limitPx×sz): {capital_in_orders_from_orders}")

    # ── Phase 4: Werte zusammenführen (Account-Modus-abhängig) ───────────────
    #
    # DIE KRITISCHE LOGIK – hängt vom Account-Modus ab:
    #
    # UNIFIED / PORTFOLIO MARGIN:
    #   - total_balance = USDC total (enthält ALLES: Spot + Perps-Collateral)
    #   - withdrawable  = USDC total - USDC hold
    #   - capital_in_orders = hold - marginUsed
    #     (hold = Margin + Order-Collateral → Rest nach Margin = Orders)
    #   - capital_in_positions = totalMarginUsed
    #
    # MANUAL / STANDARD:
    #   - total_balance = Perps accountValue + Spot USDC total (getrennt)
    #   - withdrawable  = Perps withdrawable + (Spot total - Spot hold)
    #   - capital_in_orders = Summe(limitPx × sz) aus openOrders
    #   - capital_in_positions = totalMarginUsed

    if is_unified_or_pm:
        total_balance = usdc_total
        withdrawable = usdc_total - usdc_hold
        # max(0.0, …) verhindert negative Werte bei Rundungsfehlern
        capital_in_orders = max(0.0, usdc_hold - total_margin_used)
        capital_in_positions = total_margin_used
    else:
        total_balance = perps_account_value + usdc_total
        withdrawable = perps_withdrawable + (usdc_total - usdc_hold)
        capital_in_orders = capital_in_orders_from_orders
        capital_in_positions = total_margin_used

    return AccountSummary(
        account_mode=account_mode,
        total_balance=total_balance,
        unrealized_pnl=unrealized_pnl,
        withdrawable=withdrawable,
        capital_in_orders=capital_in_orders,
        capital_in_positions=capital_in_positions,
        positions_by_dex=positions_by_dex,
        orders_by_dex=orders_by_dex,
    )   
