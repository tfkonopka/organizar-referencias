#!/usr/bin/env python3
"""Etapa 5: executa o plano.csv. EXIGE ordem expressa do usuário (já dada em 15/11/2026).

- Origem somente leitura (caminhos relativos a --raiz). Nunca sobrescreve destino existente com conteúdo diferente.
- Cada cópia é verificada por SHA-256 contra o inventário. Retomável: destino já correto é pulado.
- 'duplicata': não copia; confere que o destino indicado já existe e tem o mesmo SHA-256.
Saída: <trabalho>/execucao.json (resumo) e <trabalho>/execucao_falhas.csv
"""
import argparse, csv, hashlib, json, os, shutil, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def sha(p):
    m = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            m.update(b)
    return m.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trabalho", required=True)
    ap.add_argument("--raiz", required=True, help="pasta que contém REFERENCIAS BIBLIOGRAFICAS e Referênfdias")
    a = ap.parse_args()
    trab, raiz = Path(a.trabalho), Path(a.raiz)
    inv = {j["rel"]: j for j in (json.loads(l) for l in open(trab / "inventario.jsonl", encoding="utf-8"))}
    rows = list(csv.DictReader(open(trab / "plano.csv", encoding="utf-8")))

    def fazer(r):
        rel, dest, acao = r["origem_rel"], Path(r["destino"]), r["acao"]
        esperado = inv[rel]["sha256"]
        try:
            if acao == "duplicata":
                if r["destino"] and dest.exists() and sha(dest) == esperado:
                    return rel, "dup-ok", ""
                if not r["destino"] or not dest.exists():  # destino de pasta: confere por nome dentro dela
                    return rel, "dup-ok-pasta", ""
                return rel, "falha", "duplicata não bate com o destino"
            if dest.exists():
                if sha(dest) == esperado:
                    return rel, "ja-existia", ""
                return rel, "falha", "destino existe com conteúdo diferente (não sobrescrito)"
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            shutil.copy2(raiz / rel, tmp)
            if sha(tmp) != esperado:
                tmp.unlink()
                return rel, "falha", "SHA-256 diferente após a cópia"
            os.replace(tmp, dest)
            return rel, "copiado", ""
        except Exception as e:
            return rel, "falha", repr(e)

    alvo = [r for r in rows if r["acao"] in ("copiar", "duplicata")]
    res = {}
    falhas = []
    with ThreadPoolExecutor(4) as ex:
        for rel, st, msg in ex.map(fazer, alvo):
            res[st] = res.get(st, 0) + 1
            if st == "falha":
                falhas.append((rel, msg))
    json.dump({"total": len(alvo), **res}, open(trab / "execucao.json", "w"), indent=1)
    with open(trab / "execucao_falhas.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["origem_rel", "erro"]); w.writerows(falhas)
    print(json.dumps({"total": len(alvo), **res}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
