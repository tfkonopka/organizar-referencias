#!/usr/bin/env python3
"""Hook PreToolUse do Claude Code: protege o HD de origem e exige autorização expressa para LLM.

Bloqueia (exit 2) quando:
  1. Write, Edit, MultiEdit ou NotebookEdit tentam gravar dentro da origem;
  2. Bash tenta alterar algo dentro da origem: rm, mv, redirecionamento (>),
     sed -i, chmod, touch, mkdir, ou cp/rsync com a origem como destino;
  3. Bash invoca processamento com LLM sem o arquivo de autorização expressa.

Limitação: é uma barreira de software e cobre os comandos mais comuns.
A garantia forte é montar o HD como somente leitura (ver SKILL.md).
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
ORIGEM_TXT = CONFIG["origem"]
ORIGEM = os.path.realpath(os.path.expanduser(ORIGEM_TXT))
ARQ_AUTORIZACAO = HERE / CONFIG.get("arquivo_autorizacao_llm", "AUTORIZACAO_LLM.txt")
TOKEN_AUTORIZACAO = "AUTORIZO LLM"

FERRAMENTAS_ESCRITA = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
COMANDOS_ESCRITA = {"rm", "rmdir", "mv", "touch", "mkdir", "truncate", "shred",
                    "unlink", "chmod", "chown", "chattr", "dd", "tee", "ln"}
COMANDOS_COPIA = {"cp", "rsync", "scp", "install"}
PADRAO_LLM = re.compile(r"classificar_llm|claude\s+-p|anthropic|openai|--llm\b", re.IGNORECASE)


def dentro_da_origem(caminho):
    if not caminho:
        return False
    real = os.path.realpath(os.path.expanduser(caminho))
    return real == ORIGEM or real.startswith(ORIGEM + os.sep)


def bloquear(motivo):
    print(f"BLOQUEADO pelo hook guard_hd: {motivo}", file=sys.stderr)
    sys.exit(2)


def llm_autorizado():
    try:
        return ARQ_AUTORIZACAO.exists() and TOKEN_AUTORIZACAO in ARQ_AUTORIZACAO.read_text(encoding="utf-8")
    except OSError:
        return False


def checar_bash(cmd):
    if not cmd:
        return

    if PADRAO_LLM.search(cmd) and not llm_autorizado():
        bloquear("processamento com LLM sem autorização expressa. "
                 f"Crie {ARQ_AUTORIZACAO.name} contendo '{TOKEN_AUTORIZACAO}' para liberar.")

    if ORIGEM_TXT not in cmd and ORIGEM not in cmd:
        return

    if ">" in cmd:
        bloquear("redirecionamento (>) em comando que mexe na origem do HD.")

    for segmento in re.split(r"&&|\|\||;|\|", cmd):
        try:
            tokens = shlex.split(segmento, posix=True)
        except ValueError:
            tokens = segmento.split()
        if not tokens:
            continue
        cmd_base = os.path.basename(tokens[0])
        if cmd_base in COMANDOS_ESCRITA:
            bloquear(f"comando '{cmd_base}' em caminho da origem do HD.")
        if cmd_base == "sed" and "-i" in tokens:
            bloquear("sed -i altera arquivos da origem do HD.")
        if cmd_base in COMANDOS_COPIA and len(tokens) > 1 and dentro_da_origem(tokens[-1]):
            bloquear(f"'{cmd_base}' com a origem do HD como destino.")


def main():
    try:
        entrada = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        bloquear("entrada do hook inválida (fail-closed).")

    ferramenta = entrada.get("tool_name", "")
    ti = entrada.get("tool_input") or {}

    if ferramenta in FERRAMENTAS_ESCRITA:
        alvo = ti.get("file_path") or ti.get("notebook_path")
        if dentro_da_origem(alvo):
            bloquear(f"gravação dentro da origem do HD: {alvo}")
    elif ferramenta == "Bash":
        checar_bash(ti.get("command", ""))

    sys.exit(0)


if __name__ == "__main__":
    main()
