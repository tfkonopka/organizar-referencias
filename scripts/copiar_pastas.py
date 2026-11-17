#!/usr/bin/env python3
"""Copia pastas inteiras (todos os tipos de arquivo), mantendo a estrutura original.
Origem SOMENTE LEITURA. Retomável: arquivo já presente com o mesmo SHA-256 é pulado.
Conflito (mesmo caminho, conteúdo diferente) NÃO é sobrescrito: é registrado como ERRO.
Registro: <raiz_destino>/_meta_copia_pastas.csv
"""
import argparse, csv, hashlib, os, shutil, sys
from collections import Counter
from pathlib import Path


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def snapshot(origem):
    s = {}
    for r, _, ns in os.walk(origem):
        for n in ns:
            p = os.path.join(r, n)
            try:
                st = os.stat(p, follow_symlinks=False)
                s[p] = (st.st_size, st.st_mtime_ns)
            except OSError:
                s[p] = None
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origem", required=True)
    ap.add_argument("--pasta", action="append", required=True, help="relativa à origem")
    ap.add_argument("--destino-raiz", required=True)
    ap.add_argument("--excluir", action="append", default=[], help="pasta de topo a nunca copiar")
    a = ap.parse_args()
    origem, raiz = Path(a.origem), Path(a.destino_raiz)
    for p in a.pasta:
        if Path(p).parts[0] in a.excluir:
            sys.exit(f"recusado: {p} está em pasta excluída")
    antes = snapshot(origem)
    st = Counter()
    with open(raiz / "_meta_copia_pastas.csv", "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["origem", "destino", "resultado"])
        for pasta in a.pasta:
            for r, ds, ns in os.walk(origem / pasta):
                ds.sort()
                for n in sorted(ns):
                    o = Path(r) / n
                    d = raiz / o.relative_to(origem)
                    try:
                        if d.exists():
                            res = "já existia (idêntico)" if sha(o) == sha(d) else "ERRO: destino existe com conteúdo diferente"
                        else:
                            d.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(o, d)
                            res = "copiado"
                    except OSError as e:
                        res = f"ERRO: {e}"
                    st[res.split(":")[0]] += 1
                    w.writerow([str(o), str(d), res])
                    if sum(st.values()) % 300 == 0:
                        print(dict(st), flush=True)
    depois = snapshot(origem)
    alt = [p for p in set(antes) | set(depois) if antes.get(p) != depois.get(p)]
    print("Resultado:", dict(st))
    print(f"Integridade da origem: {len(alt)} arquivos alterados")
    return 2 if alt else 0


if __name__ == "__main__":
    sys.exit(main())
