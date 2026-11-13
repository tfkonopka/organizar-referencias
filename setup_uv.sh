#!/usr/bin/env bash
# Cria um ambiente virtual com uv e instala as dependências do organizar.py.
# Uso (a partir da pasta organizar-referencias/):
#   bash setup_uv.sh
# Depois ative com:  source .venv/bin/activate
# ou rode sem ativar: uv run python scripts/organizar.py --help

set -euo pipefail

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv não encontrado. Instale com: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

# Ambiente virtual em .venv, com Python gerenciado pelo uv
uv venv .venv --python 3.11 2>/dev/null || uv venv .venv

uv pip install --python .venv/bin/python -r requirements.txt

# Verifica dependências de sistema, usadas só com --ocr
for bin in pdftoppm tesseract; do
  if command -v "$bin" >/dev/null 2>&1; then
    echo "OK: $bin encontrado"
  else
    echo "AVISO: $bin não encontrado. Só é necessário para --ocr." >&2
  fi
done

if command -v tesseract >/dev/null 2>&1 && ! tesseract --list-langs 2>/dev/null | grep -q '^por$'; then
  echo "AVISO: idioma 'por' do tesseract não instalado (pacote tesseract-ocr-por)." >&2
fi

echo "Ambiente pronto em .venv"
