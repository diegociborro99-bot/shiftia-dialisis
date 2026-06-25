#!/bin/bash
# Doble clic en Finder para abrir el panel de Shiftia · Diálisis.
cd "$(dirname "$0")"
echo "Preparando Shiftia · Diálisis…"
python3 -m pip install --quiet ortools pdfplumber >/dev/null 2>&1
python3 app.py
