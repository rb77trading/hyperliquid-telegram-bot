VENV   := .venv
PYTHON := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip

.DEFAULT_GOAL := help

.PHONY: help setup config run dry-run clean

help:
	@echo "Befehle:"
	@echo "  make setup    – Venv + Abhängigkeiten installieren"
	@echo "  make config   – config.example.py → config.py kopieren"
	@echo "  make run      – Bot starten"
	@echo "  make dry-run  – Bot im Paper-Trading-Modus starten"
	@echo "  make clean    – Venv und Caches entfernen"

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "✅ Setup fertig."

config:
	@if [ -f config.py ]; then \
		echo "⚠️  config.py existiert bereits – überspringe."; \
	else \
		cp config.example.py config.py; \
		echo "✅ config.py erstellt. Bitte Werte eintragen."; \
	fi

run: config
	$(PYTHON) main.py

dry-run: config
	$(PYTHON) main.py --dry-run

clean:
	rm -rf $(VENV)
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
