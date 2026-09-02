# ═══════════════════════════════════════════════════════════════════════════════
# Hyperliquid Telegram Bot – Makefile
#
# Verfügbare Befehle:
#   make setup     – Venv anlegen + Abhängigkeiten installieren (einmalig)
#   make config    – config.example.py → config.py kopieren (einmalig)
#   make run       – Telegram-Bot starten
#   make dry-run   – Telegram-Bot im Paper-Trading-Modus starten
#   make web       – Web-Dashboard starten (FastAPI)
#   make bot       – Nur Telegram-Bot starten (Alias für run)
#   make all       – Web + Bot gleichzeitig starten
#   make clean     – Venv und Caches entfernen
# ═══════════════════════════════════════════════════════════════════════════════

VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip

.DEFAULT_GOAL := help

.PHONY: help setup config run dry-run web bot all clean

help:
	@echo "Hyperliquid Telegram Bot – verfügbare Befehle:"
	@echo ""
	@echo "  make setup     – Venv anlegen + Abhängigkeiten installieren (einmalig)"
	@echo "  make config    – config.example.py → config.py kopieren (einmalig)"
	@echo "  make run       – Telegram-Bot starten"
	@echo "  make dry-run   – Telegram-Bot im Paper-Trading-Modus starten"
	@echo "  make web       – Web-Dashboard starten (FastAPI, Port aus config.py)"
	@echo "  make bot       – Nur Telegram-Bot starten (Alias für run)"
	@echo "  make all       – Web-Dashboard + Telegram-Bot gleichzeitig starten"
	@echo "  make clean     – Venv und Caches entfernen"
	@echo ""
	@echo "Erster Start:"
	@echo "  make setup && make config"
	@echo "  # config.py mit deinen Werten ausfüllen"
	@echo "  make all"

# ─── Einmalige Einrichtung ─────────────────────────────────────────────────────

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "✅ Setup fertig. Führe 'make config' aus und trage deine Werte ein."

config:
	@if [ -f config.py ]; then \
		echo "⚠️  config.py existiert bereits – überspringe."; \
	else \
		cp config.example.py config.py; \
		echo "✅ config.py erstellt. Bitte Werte eintragen."; \
	fi

# ─── Start ─────────────────────────────────────────────────────────────────────

run:
	$(PYTHON) bot.py

dry-run:
	$(PYTHON) bot.py --dry-run

web:
	$(PYTHON) app.py

bot:
	$(PYTHON) bot.py

all:
	@echo "🌐 Web-Dashboard + 🤖 Telegram-Bot starten…"
	@$(PYTHON) app.py & \
	$(PYTHON) bot.py & \
	wait

# ─── Aufräumen ─────────────────────────────────────────────────────────────────

clean:
	rm -rf $(VENV)
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	@echo "✅ Venv und Caches entfernt."
