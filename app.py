"""
app.py – Web-Dashboard (FastAPI + Tailwind).

Phase 1: Testseite mit Mock-Daten (keine echte API-Kommunikation).
Phase 2: Echte Daten via hl_api.py + SSE für Live-Updates.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request

from config import WEB_HOST, WEB_PORT

from types import SimpleNamespace

app = FastAPI(title="Hyperliquid Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Testseite mit Mock-Daten."""
    from types import SimpleNamespace

    mock_positions = [
        SimpleNamespace(coin="BTC", dex="", side="LONG", leverage=5,
                        size=0.013, entry_px=67200.00, mark_px=69120.00,
                        unrealized_pnl=25.00, roe=0.0287),
        SimpleNamespace(coin="ETH", dex="", side="SHORT", leverage=3,
                        size=1.200, entry_px=3520.00, mark_px=3485.00,
                        unrealized_pnl=42.00, roe=0.0298),
        SimpleNamespace(coin="SOL", dex="", side="LONG", leverage=10,
                        size=45.0, entry_px=142.50, mark_px=138.20,
                        unrealized_pnl=-193.50, roe=-0.0302),
    ]
    mock_orders = [
        SimpleNamespace(coin="BTC", dex="", side="B", limit_px=68500.00,
                        size=0.010, notional=685.00, oid=100234),
        SimpleNamespace(coin="ETH", dex="", side="S", limit_px=3550.00,
                        size=0.800, notional=2840.00, oid=100235),
        SimpleNamespace(coin="SOL", dex="", side="B", limit_px=135.00,
                        size=60.0, notional=8100.00, oid=100236),
    ]

    summary = SimpleNamespace(
        account_mode="unifiedAccount",
        total_balance=12_450.87,
        unrealized_pnl=234.56,
        withdrawable=8_200.00,
        capital_in_orders=1_500.00,
        capital_in_positions=2_750.87,
        positions=mock_positions,
        orders=mock_orders,
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "summary": summary,
            "trading_enabled": True,
        },
    )


if __name__ == "__main__":
    import sys
    from config import WEB_ENABLED

    if not WEB_ENABLED:
        print("ℹ️  Web-Dashboard deaktiviert (WEB_ENABLED=False) – nicht gestartet.")
        sys.exit(0)

    import uvicorn
    print(f"🌐 Dashboard: http://{WEB_HOST}:{WEB_PORT}")
    print("   (Testseite mit Mock-Daten)\n")
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)
