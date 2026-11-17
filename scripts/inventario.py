#!/usr/bin/env python3
"""Etapa 1 do workflow: inventário SOMENTE LEITURA da origem.

Para cada arquivo: caminho, tamanho, mtime, SHA-256.
Para cada PDF: páginas, texto das 3 primeiras páginas (cache em <trabalho>/texto/<sha>.txt),
DOI, ISBN (dígito verificador conferido), arXiv, metadados embutidos.
Saída: <trabalho>/inventario.jsonl. Retomável (pula caminhos já inventariados).
"""
import argparse, hashlib, json, logging, os, re, sys, time
from pathlib import Path

import pypdf

logging.getLogger("pypdf").setLevel(logging.ERROR)
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
ARX = re.compile(r"arXiv:\s?(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})", re.I)
ISBNC = re.compile(r"(?:ISBN(?:-1[03])?:?\s*)?((?:97[89][\s\-]?)?\d[\d\s\-]{8,15}[\dXx])")


def isbn_ok(s):
    s = re.sub(r"[^0-9Xx]", "", s).upper()
    if len(s) == 10 and re.fullmatch(r"\d{9}[\dX]", s):
        return s if sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s)) % 11 == 0 else None
    if len(s) == 13 and s.isdigit() and s[:3] in ("978", "979"):
        t = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s[:12]))
        return s if (10 - t % 10) % 10 == int(s[12]) else None
    return None


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def meta_str(v):
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return str(v).strip() or None


def ler_pdf(p, n_pag):
    r = {"paginas": None, "chars": 0, "doi": None, "isbn": None, "arxiv": None,
         "meta_titulo": None, "meta_autor": None, "erro": None}
    txt = ""
    try:
        rd = pypdf.PdfReader(str(p), strict=False)
        r["paginas"] = len(rd.pages)
        for i in range(min(n_pag, r["paginas"])):
            try:
                txt += (rd.pages[i].extract_text() or "") + "\n"
            except Exception:
                pass
        md = rd.metadata or {}
        r["meta_titulo"] = meta_str(md.get("/Title"))
        r["meta_autor"] = meta_str(md.get("/Author"))
    except Exception as e:
        r["erro"] = type(e).__name__
    r["chars"] = len(txt.strip())
    m = DOI.search(txt)
    r["doi"] = m.group(1).rstrip(".,;)]").lower() if m else None
    m = ARX.search(txt)
    r["arxiv"] = m.group(1) if m else None
    for m in ISBNC.finditer(txt):
        v = isbn_ok(m.group(1))
        if v:
            r["isbn"] = v
            break
    return r, txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raiz-origem", required=True, help="pasta Estudos (base dos caminhos relativos)")
    ap.add_argument("--pasta", action="append", required=True, help="subpasta a inventariar")
    ap.add_argument("--trabalho", required=True)
    ap.add_argument("--paginas", type=int, default=3)
    a = ap.parse_args()
    base, trab = Path(a.raiz_origem), Path(a.trabalho)
    (trab / "texto").mkdir(parents=True, exist_ok=True)
    saida = trab / "inventario.jsonl"
    feitos = set()
    if saida.exists():
        for l in open(saida, encoding="utf-8"):
            feitos.add(json.loads(l)["rel"])
    n = 0
    t0 = time.time()
    with open(saida, "a", encoding="utf-8") as out:
        for pasta in a.pasta:
            for d, ds, fs in os.walk(base / pasta):
                ds.sort()
                for f in sorted(fs):
                    p = Path(d) / f
                    rel = str(p.relative_to(base))
                    if rel in feitos:
                        continue
                    st = p.stat()
                    rec = {"rel": rel, "ext": p.suffix.lower(), "tamanho": st.st_size,
                           "mtime_ns": st.st_mtime_ns, "sha256": sha256(p)}
                    if rec["ext"] == ".pdf":
                        info, txt = ler_pdf(p, a.paginas)
                        rec.update(info)
                        if txt.strip():
                            tp = trab / "texto" / f"{rec['sha256']}.txt"
                            if not tp.exists():
                                tp.write_text(txt, encoding="utf-8")
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
                    if n % 500 == 0:
                        out.flush()
                        print(f"{n} arquivos, {time.time() - t0:.0f}s", flush=True)
    print(f"fim: {n} novos arquivos inventariados")


if __name__ == "__main__":
    sys.exit(main())
