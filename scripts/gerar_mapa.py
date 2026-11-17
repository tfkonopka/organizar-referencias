#!/usr/bin/env python3
"""Gera o mapa de->para (origem -> destino) com verificação SHA-256. Origem somente leitura.

Saídas em <destino>/_meta/:
  mapa_de_para.csv   todo arquivo de documento da origem e onde ele está agora
  nao_copiados.csv   arquivos da origem que NÃO têm cópia (outros formatos, ocultos etc.)
"""
import csv, hashlib, os, sys
from pathlib import Path


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(origem, destino):
    origem, meta = Path(origem), Path(destino) / "_meta"
    copia = {}
    for r in csv.DictReader(open(meta / "manifesto_copia.csv", encoding="utf-8")):
        if r["grupo"] != "dup":
            copia[r["origem"]] = r["destino"]
    dup = {r["origem"]: r["motivos"].removeprefix("duplicado de ")
           for r in csv.DictReader(open(meta / "manifesto.csv", encoding="utf-8"))
           if r["acao"].startswith("duplicado")}

    cache = {}
    def sha_origem(p):
        if p not in cache:
            cache[p] = sha(p)
        return cache[p]

    def destino_final(o):
        """Segue a cadeia de duplicatas até um arquivo que foi copiado."""
        vistos = set()
        while o not in copia and o in dup and o not in vistos:
            vistos.add(o)
            o = dup[o]
        return copia.get(o)

    ok = Counter = 0
    falhas = []
    with open(meta / "mapa_de_para.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["de", "para", "situacao", "sha256", "verificado"])
        for o in sorted(set(copia) | set(dup)):
            if o in copia:
                sit, d = "copiado", copia[o]
            else:
                sit, d = "duplicata idêntica (não copiada; o conteúdo está em 'para')", destino_final(o)
            if not d or not os.path.exists(d):
                w.writerow([o, d or "", sit, "", "FALHA: sem destino"]); falhas.append(o); continue
            so, sd = sha_origem(o), sha(d)
            v = "ok" if so == sd else "FALHA: hash diferente"
            if v != "ok":
                falhas.append(o)
            ok += v == "ok"
            w.writerow([o, d, sit, so, v])
    mapeados = set(copia) | set(dup)
    n_nc = 0
    with open(meta / "nao_copiados.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["arquivo", "tamanho_bytes"])
        for raiz, _, nomes in os.walk(origem):
            for n in nomes:
                p = os.path.join(raiz, n)
                if p not in mapeados:
                    w.writerow([p, os.stat(p).st_size]); n_nc += 1
    print(f"verificados ok: {ok} | falhas: {len(falhas)} | origem sem cópia: {n_nc}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
