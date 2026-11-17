#!/usr/bin/env python3
"""Fase 1 da cópia da biblioteca. A origem é SOMENTE LEITURA.

Grupos:
  A  confiança alta no manifesto           -> <destino>/Livros|Artigos/<Área>/...  (caminho do manifesto)
  B  pastas de livros em capítulos          -> <destino>/Livros_em_capitulos/<pasta>/  (todos os PDFs)
  C  demais documentos (não duplicados)     -> <destino>_para_inspecao/<caminho relativo original>
Duplicatas exatas apontadas pelo manifesto não são copiadas (só registradas).
Retomável: arquivo já existente com o mesmo tamanho é pulado.
"""
import argparse, csv, os, re, shutil, sys
from collections import Counter, defaultdict
from pathlib import Path

AULA = re.compile(r"(aula|lecture|lec|slide)", re.I)


def pastas_de_livros(origem):
    """Pastas com >=3 PDFs, >=60% seguindo o mesmo padrão numerado, exceto estilo Aula/Lecture."""
    res = {}
    for d, _, fs in os.walk(origem):
        if Path(d) == Path(origem):
            continue
        pdfs = sorted(f for f in fs if f.lower().endswith(".pdf"))
        if len(pdfs) < 3:
            continue
        g = Counter(re.sub(r"\d+", "#", f[:-4]) for f in pdfs)
        stem, n = g.most_common(1)[0]
        if "#" in stem and n >= 3 and n / len(pdfs) >= 0.6 and not AULA.match(stem):
            res[d] = pdfs
    return res


def sha(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def snapshot(origem):
    snap = {}
    for raiz, _, nomes in os.walk(origem):
        for n in nomes:
            p = os.path.join(raiz, n)
            try:
                st = os.stat(p, follow_symlinks=False)
                snap[p] = (st.st_size, st.st_mtime_ns)
            except OSError:
                snap[p] = None
    return snap


def livre(dest, origem, usados):
    """Escolhe um destino livre. Só considera 'já copiado' se o conteúdo (SHA-256) for igual;
    destino ocupado nesta execução por outro arquivo recebe sufixo ' (n)'."""
    i = 1
    while True:
        c = dest if i == 1 else dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        if c not in usados:
            if not c.exists() or sha(origem) == sha(c):
                usados.add(c)
                return c
        i += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--origem", required=True)
    ap.add_argument("--destino", required=True)  # .../Biblioteca
    ap.add_argument("--executar", action="store_true")
    a = ap.parse_args()
    origem, destino = Path(a.origem), Path(a.destino)
    insp = destino.parent / "Biblioteca_para_inspecao"
    cap_dir = destino / "Livros_em_capitulos"
    manifesto = destino / "_meta" / "manifesto.csv"
    rows = list(csv.DictReader(open(manifesto, encoding="utf-8")))

    livros = pastas_de_livros(origem)
    # nome de destino por pasta de livro (desambigua homônimos pelo caminho pai)
    nomes = defaultdict(list)
    for d in livros:
        nomes[Path(d).name].append(d)
    dest_livro = {}
    for nome, ds in nomes.items():
        for i, d in enumerate(sorted(ds)):
            dest_livro[d] = cap_dir / (nome if i == 0 else f"{nome} ({i + 1})")

    plano = []  # (grupo, origem, destino)
    em_livro = set()
    for d, pdfs in livros.items():
        for f in pdfs:
            o = Path(d) / f
            em_livro.add(str(o))
            plano.append(("B", o, dest_livro[d] / f))
    log_dup = []
    for r in rows:
        o = r["origem"]
        if o in em_livro:
            continue
        if r["acao"].startswith("duplicado"):
            log_dup.append(o)
            continue
        if r["confianca"] == "alta":
            plano.append(("A", Path(o), Path(r["destino"])))
        else:
            plano.append(("C", Path(o), insp / Path(o).relative_to(origem)))

    tam = Counter(); n = Counter()
    for g, o, d in plano:
        n[g] += 1
        tam[g] += o.stat().st_size
    for g in "ABC":
        print(f"Grupo {g}: {n[g]} arquivos, {tam[g] / 1e9:.2f} GB")
    print(f"Duplicatas não copiadas: {len(log_dup)} | pastas de livros: {len(livros)}")
    if not a.executar:
        print("Simulação: nada copiado.")
        return 0

    antes = snapshot(origem)
    out = destino / "_meta" / "manifesto_copia.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    stat = Counter()
    usados = set()
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["grupo", "origem", "destino", "resultado"])
        for i, (g, o, d) in enumerate(plano, 1):
            try:
                t = o.stat().st_size
                d = livre(d, o, usados)
                if d.exists():
                    res = "já existia"
                else:
                    d.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(o, d)
                    res = "copiado"
            except OSError as e:
                res = f"ERRO: {e}"
            stat[res.split(":")[0]] += 1
            w.writerow([g, str(o), str(d), res])
            if i % 200 == 0:
                print(f"{i}/{len(plano)}", dict(stat), flush=True)
        for o in log_dup:
            w.writerow(["dup", o, "", "duplicata exata, não copiada"])
    depois = snapshot(origem)
    alt = [p for p in set(antes) | set(depois) if antes.get(p) != depois.get(p)]
    print("Resultado:", dict(stat))
    print(f"Integridade da origem: {len(alt)} arquivos alterados")
    return 2 if alt else 0


if __name__ == "__main__":
    sys.exit(main())
