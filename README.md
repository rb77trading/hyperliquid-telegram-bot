# Hyperliquid Telegram Trading Bot

🤖 Ein Telegram-Bot für Hyperliquid mit Echtzeit-Notifications, Dashboard-Ansichten und Trading-Funktionalität.

## ⚠️ Haftungsausschluss (Disclaimer)

> **WICHTIG**: Dieses Projekt dient ausschließlich zu **Bildungszwecken** und stellt **keine Finanzberatung** dar. Kryptowährungen und Derivate sind hochvolatil – du kannst dein gesamtes investiertes Kapital verlieren.
>
> - Der Autor übernimmt **keine Haftung** für finanzielle Verluste oder Schäden, die durch die Nutzung dieses Bots entstehen.
> - Teste alle Funktionen immer zuerst mit kleinen Beträgen oder im Testnet.
> - Die Nutzung erfolgt auf **eigenes Risiko**.
> - **DYOR – Do Your Own Research.**
>
> Dieser Bot ist ein Werkzeug zur Informationsdarstellung und -ausführung, keine automatisierte Trading-Strategie.

## ✨ Funktionsumfang

### 📊 Echtzeit-Notifications (WebSocket)
- **Orderausführungen (Fills)**: Limit, Market, Liquidation, StopLoss, TakeProfit
- **Order-Lebenszyklus**: Plaziert, Geändert, Storniert, Trigger ausgelöst, Abgelehnt (optional)
- **Ein- und Auszahlungen**: Deposits, Withdrawals, interne Transfers
- **Push-Benachrichtigungen**: Jede Notification löst eine Push-Nachricht auf deinem Smartphone aus

### 📈 Dashboard-Ansichten
- **Kontostand**: Gesamt-Guthaben, unrealisierter PnL, abhebbarer Betrag, gebundenes Kapital
- **Positionen**: Alle offenen Positionen mit Hebel, Ein-/Ausstiegspreise, PnL, ROE
- **Offene Orders**: Alle aktiven Limit-Orders mit Nominalwerten

### 🔄 Multi-DEX Support
- **Nativ** + **HIP-3 Builder-DEXe** (z.B. xyz, km, flx)
- Automatische DEX-Entdeckung oder explizite Konfiguration
- Aggregierte Kontodaten über alle DEXe

### 💹 Trading-Funktionalität (optional)
- **Order stornieren**: Direkt über Telegram-Buttons
- **Order bearbeiten**: Preis und Größe ändern
- **Sicherheitsabfragen**: Bestätigungs-Dialoge vor kritischen Aktionen

### 🌐 Web-Dashboard (optional)
- **FastAPI-basiertes Web-Interface** mit Tailwind CSS
- **Alternative zum Telegram-Dashboard**
- **Live-Daten** (Mock-Daten in Phase 1, echte API in Phase 2)

## 🚀 Installation

### Voraussetzungen
- Python 3.9+
- pip
- Virtual Environment (empfohlen)

### Schritt 1: Repository klonen
```bash
git clone https://github.com/dein-username/hyperliquid-telegram-bot.git
cd hyperliquid-telegram-bot
```

### Schritt 2: Virtual Environment erstellen
```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# oder
.venv\Scripts\activate  # Windows
```

### Schritt 3: Abhängigkeiten installieren
```bash
pip install -r requirements.txt
```

### Schritt 4: Konfiguration einrichten
```bash
cp .env.example .env
# .env mit deinen Werten bearbeiten
```

### Schritt 5: Bot starten
```bash
python bot.py
```

## ⚙️ Konfiguration

### .env-Datei erstellen

Kopiere `.env.example` zu `.env` und konfiguriere die folgenden Werte:

```env
# Telegram Configuration
BOT_TOKEN=dein_bot_token_hier          # Von @BotFather
TELEGRAM_CHAT_ID=deine_chat_id_hier    # Deine Telegram Chat-ID

# Hyperliquid Configuration
WALLET_ADDRESS=deine_wallet_adresse_hier
HIP3_DEXES=None                        # None = auto, oder ["xyz", "km"]
API_URL=https://api.hyperliquid.xyz    # Mainnet oder Testnet

# WebSocket Notifications
NOTIFY_ORDER_UPDATES=True              # Order-Lebenszyklus-Notifications
EDIT_WINDOW=3                          # Edit-Erkennungs-Fenster (Sekunden)

# Trading Configuration
TRADING_ENABLED=False                  # Trading-Buttons aktivieren?
AGENT_PRIVATE_KEY=dein_agent_key_hier  # Nur wenn TRADING_ENABLED=True

# Web Dashboard
WEB_ENABLED=False                      # Web-Dashboard aktivieren?
WEB_HOST=0.0.0.0
WEB_PORT=8000
WEB_URL=http://192.168.178.11:8000     # Externe URL für Telegram-Button
```

### Telegram Bot einrichten

1. **Bot erstellen**: Chat mit [@BotFather](https://t.me/BotFather) starten, `/newbot` ausführen
2. **Token erhalten**: BotFather gibt dir ein Token wie `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`
3. **Chat-ID finden**:
   - `/start` an deinen Bot senden
   - `https://api.telegram.org/bot<TOKEN>/getUpdates` im Browser öffnen
   - Unter `result[0].message.chat.id` steht deine ID (z.B. `123456789`)
   - Für Gruppen: ID ist negativ (z.B. `-1001234567890`)

### Hyperliquid Wallet einrichten

1. **Main-Wallet**: Die Wallet, deren Kontodaten angezeigt werden
2. **Agent-Wallet** (optional, für Trading): Separate Wallet mit begrenztem USDC-Bestand
   - Empfohlen für Sicherheit: Agent-Wallet mit nur z.B. 100 USDC
   - Private Key der Agent-Wallet in `AGENT_PRIVATE_KEY`

## 📱 Nutzung

### Telegram-Bedienung

#### Starten
```
/start oder /menu
```
Zeigt das Hauptmenü mit Dashboard-Buttons.

#### Dashboard-Buttons
- **📊 Kontostand**: Gesamtübersicht, PnL, gebundenes Kapital
- **📈 Positionen**: Alle offenen Positionen mit Details
- **⏳ Offene Orders**: Alle aktiven Limit-Orders
- **🌐 Web-Dashboard**: Link zum Web-Interface (falls aktiviert)

#### Trading-Buttons (wenn TRADING_ENABLED=True)
- **❌**: Order stornieren (mit Sicherheitsabfrage)
- **✏️**: Order bearbeiten (Preis/Größe ändern)

### Web-Dashboard (optional)

```bash
# Web-Dashboard separat starten
python app.py
```

Besuche `http://localhost:8000` im Browser.

## 🏗️ Architektur

### Projektstruktur
```
hype-telegram-bot/
├── bot.py              # Telegram-Bot Entry-Point
├── ws.py               # WebSocket-Listener (Echtzeit-Events)
├── hl_api.py           # Hyperliquid API-Kommunikation
├── trader.py           # Order-Aktionen (Cancel, Modify, Place)
├── formatters.py       # Telegram-HTML-Formatierung
├── models.py           # Datenstrukturen (Dataclasses)
├── app.py              # Web-Dashboard (FastAPI)
├── config.py           # Konfiguration (lädt .env)
├── .env                # Sensitive Konfiguration (nicht in Git)
├── .env.example        # Template für .env
├── requirements.txt    # Python-Abhängigkeiten
├── templates/          # HTML-Templates für Web-Dashboard
│   └── dashboard.html  # Haupt-Dashboard Template
├── images/             # Bilder für Telegram-Dashboard
│   ├── menu.png        # Hauptmenü-Bild
│   ├── balance.png     # Kontostand-Bild
│   ├── positions.png   # Positionen-Bild
│   └── orders.png      # Orders-Bild
├── .venv/              # Virtual Environment (nicht in Git)
├── .gitignore          # Git-Ignore-Regeln
├── LICENSE             # Apache 2.0 Lizenz
└── README.md           # Diese Datei
```

### Technische Highlights

- **Thread-Safe WebSocket**: WebSocket in separatem Thread, Kommunikation via `asyncio.run_coroutine_threadsafe()`
- **Connection Keep-Alive**: Persistente HTTP-Sessions für API-Calls (~150ms Ersparnis)
- **Multi-DEX Parallelisierung**: ThreadPoolExecutor für gleichzeitige API-Calls
- **Bidirektionale Edit-Erkennung**: Korreliert Cancel+Open Events zu Order-Edits
- **Market-Order Filter**: 3-Strategien-Filterung (IOC, Market-Type, OID-Korrelation)
- **Robustheit**: Exponential Backoff bei Reconnects, Fallback-Werte bei API-Fehlern

## 🔧 Troubleshooting

### Bot startet nicht
- **Fehler**: `Benötigte Umgebungsvariable 'BOT_TOKEN' nicht gesetzt`
- **Lösung**: `.env` Datei erstellen und konfigurieren

### Keine Telegram-Nachrichten
- **Fehler**: WebSocket verbunden, aber keine Notifications
- **Lösung**: Chat-ID prüfen, `/start` an Bot senden

### Trading-Buttons fehlen
- **Fehler**: Orders-Ansicht zeigt keine Stornieren/Bearbeiten Buttons
- **Lösung**: `TRADING_ENABLED=True` in `.env` setzen und `AGENT_PRIVATE_KEY` konfigurieren

### Market-Order Doppel-Nachrichten
- **Fehler**: Bekanntes Problem bei HIP-3 Coins
- **Lösung**: Ist durch OID-Korrelation in `ws.py` behoben

### API-Fehler
- **Fehler**: `⚠️ perpDexs-Fehler` oder ähnliche API-Warnungen
- **Lösung**: Netzwerkverbindung prüfen, API-URL korrekt?

## 🛡️ Sicherheit

### Best Practices
- ✅ **.env-Datei**: Sensitive Daten niemals in Git committen
- ✅ **Agent-Wallet**: Separate Wallet mit begrenztem Kapital für Trading
- ✅ **Read-Only Standard**: `TRADING_ENABLED=False` bis du sicher bist
- ✅ **Testnet zuerst**: Immer im Testnet vor Mainnet testen
- ✅ **Kleine Beträge**: Mit kleinen Beträgen anfangen

### .env-Verschlüsselung (optional)
Für zusätzliche Sicherheit kann die `.env`-Datei verschlüsselt werden. Dies ist für fortgeschrittene Nutzer gedacht und kann bei Bedarf implementiert werden.

## 📄 Lizenz

Apache License 2.0 – siehe LICENSE-Datei für Details.

## 🤝 Beiträge

Beiträge sind willkommen! Bitte erst ein Issue öffnen vor größeren Änderungen.

## 📞 Support

Bei Problemen oder Fragen:
- GitHub Issues öffnen
- Dokumentation lesen
- Discord/Community (falls vorhanden)

## 🙏 Danksagung

- [Hyperliquid](https://hyperliquid.xyz/) für die hervorragende DEX-Infrastruktur
- [hyperliquid-python-sdk](https://github.com/hyperliquid-dex/hyperliquid-python-sdk) für die Python-Bibliothek
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) für das Telegram-Framework

---

**Disclaimer**: Dieser Bot ist ein Werkzeug zur Informationsdarstellung und -ausführung. Keine Finanzberatung. Handle auf eigenes Risiko.
