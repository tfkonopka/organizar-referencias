#!/usr/bin/env python3
"""Organiza referências bibliográficas (livros e artigos) por tipo e área.

Uso:
  # 1) simulação (padrão): não copia nada, só gera o manifesto e o relatório
  python3 organizar.py --origem "/run/media/konopka/FreeAgent GoFlex Drive/Estudos" \
                       --destino "/home/konopka/Documents/OrganizadorArquivos/Biblioteca"

  # 2) cópia de fato
  python3 organizar.py ... --executar

  # opcionais
  --ocr      OCR nas primeiras páginas de PDFs sem texto (exige pdftoppm e tesseract)
  --online   consulta Crossref (DOI) e Open Library (ISBN) quando faltam metadados

A origem é somente leitura. O script nunca move, altera nem apaga nada na origem.
Duplicatas exatas (mesmo SHA-256) não são copiadas.
Arquivos não identificados vão para uma pasta separada (<destino>_nao_identificados,
ou --revisao), com o motivo registrado no manifesto.
Ao final, compara o estado da origem com o do início; se algo mudou, sai com erro.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_AREAS = SCRIPT_DIR.parent / "references" / "areas.json"

DOC_EXT = {".pdf", ".djvu", ".epub", ".doc", ".docx", ".odt", ".rtf", ".txt"}
GENERIC_TITLE = re.compile(r"^(untitled|scan\w*|\d+|microsoft word.*|document\d*)$", re.I)
NAME_RE = re.compile(r"^(?P<autor>[^-]{2,80}?)\s+-\s+(?P<ano>(?:19|20)\d{2})\s+-\s+(?P<titulo>.+)$")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)")
ISBN_CAND = re.compile(r"97[89][\d\- ]{10,16}\d|\b\d{9}[\dXx]\b")
UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information"}

MANIFEST_COLS = [
    "origem", "destino", "acao", "tipo", "area", "confianca",
    "autor", "ano", "titulo", "doi", "isbn", "paginas", "ocr", "motivos",
    "sha256", "tamanho_bytes",
]


# ---------- identificadores ----------

def isbn_valido(s):
    """Valida ISBN-10 ou ISBN-13 pelo dígito verificador. Retorna o ISBN limpo ou None."""
    s = re.sub(r"[^0-9Xx]", "", s).upper()
    if len(s) == 10 and re.fullmatch(r"\d{9}[\dX]", s):
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
        return s if total % 11 == 0 else None
    if len(s) == 13 and s.isdigit() and s[:3] in ("978", "979"):
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s[:12]))
        return s if (10 - total % 10) % 10 == int(s[12]) else None
    return None


def ids_do_texto(texto):
    doi = None
    m = DOI_RE.search(texto)
    if m:
        doi = m.group(1).rstrip(".,;)]")
    isbn = None
    for m in ISBN_CAND.finditer(texto):
        v = isbn_valido(m.group(0))
        if v:
            isbn = v
            break
    return doi, isbn


# ---------- leitura de conteúdo ----------

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def ocr_paginas(path, n):
    """OCR só das primeiras n páginas, para classificação. Não altera o arquivo."""
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(
            ["pdftoppm", "-r", "200", "-f", "1", "-l", str(n), "-png", str(path), os.path.join(td, "p")],
            check=True, capture_output=True,
        )
        partes = []
        for img in sorted(Path(td).glob("*.png")):
            r = subprocess.run(["tesseract", str(img), "-", "-l", "por+eng"], capture_output=True, text=True)
            partes.append(r.stdout)
        return "\n".join(partes)


def _meta_texto(v):
    """Metadado do PDF como str. O pypdf pode devolver bytes em arquivos malformados."""
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", errors="replace")
    return str(v).strip() or None


def ler_pdf(path, n_pag, ocr):
    info = {"paginas": None, "texto": "", "titulo": None, "autor": None, "ocr": False}
    try:
        from pypdf import PdfReader
        r = PdfReader(str(path))
        info["paginas"] = len(r.pages)
        partes = []
        for p in r.pages[:n_pag]:
            try:
                partes.append(p.extract_text() or "")
            except Exception:
                pass
        info["texto"] = "\n".join(partes)
        meta = r.metadata or {}
        info["titulo"] = _meta_texto(meta.get("/Title"))
        info["autor"] = _meta_texto(meta.get("/Author"))
    except Exception:
        pass
    if ocr and len(info["texto"].strip()) < 200:
        try:
            info["texto"] = ocr_paginas(path, n_pag)
            info["ocr"] = True
        except Exception:
            pass
    return info


# ---------- classificação ----------

def tipo_do_documento(info, doi, isbn, nome):
    """Pontua sinais de livro e de artigo. Retorna 'livro', 'artigo' ou None (ambíguo)."""
    livro, artigo = 0, 0
    if isbn:
        livro += 3
    if doi:
        artigo += 3
    p = info["paginas"]
    if p:
        if p >= 100:
            livro += 2
        elif p <= 30:
            artigo += 1
    t = info["texto"].lower()
    if re.search(r"sum[áa]rio|contents|cap[íi]tulo|chapter|[íi]ndice", t):
        livro += 1
    if re.search(r"\bedition\b|\bedi[çc][ãa]o\b|\bpublisher\b|\beditora\b", t):
        livro += 1
    if re.search(r"\babstract\b|\bresumo\b", t):
        artigo += 1
    if re.search(r"references|refer[êe]ncias|bibliography|bibliografia", t):
        artigo += 1
    if re.search(r"livro|book|handbook|manual|textbook", nome.lower()):
        livro += 1
    if livro >= 2 and livro - artigo >= 2:
        return "livro"
    if artigo >= 2 and artigo - livro >= 2:
        return "artigo"
    return None


def area_do_documento(titulo, texto, areas):
    """Conta palavras-chave por área. Retorna a área só com evidência clara; senão None."""
    corpo = (titulo + " " + texto[:20000]).lower()
    pont = sorted(
        ((a, sum(corpo.count(k) for k in kws)) for a, kws in areas.items()),
        key=lambda x: -x[1],
    )
    (a1, s1), (_, s2) = pont[0], pont[1]
    if s1 >= 3 and s1 >= 1.5 * max(s2, 1):
        return a1
    return None


def consultar_online(doi, isbn, cache):
    chave = doi or isbn
    if not chave:
        return None
    if chave in cache:
        return cache[chave]
    headers = {"User-Agent": "organizar-referencias/1.0 (uso pessoal)"}
    res = None
    try:
        if doi:
            url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
                m = json.load(r)["message"]
            autor = (m.get("author") or [{}])[0].get("family")
            partes = ((m.get("issued") or {}).get("date-parts") or [[None]])[0]
            res = {"titulo": (m.get("title") or [None])[0], "autor": autor,
                   "ano": str(partes[0] or "")}
        else:
            url = f"https://openlibrary.org/isbn/{isbn}.json"
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
                m = json.load(r)
            ano = re.search(r"(?:19|20)\d{2}", m.get("publish_date") or "")
            res = {"titulo": m.get("title"), "autor": None, "ano": ano.group(0) if ano else ""}
    except Exception:
        res = None
    cache[chave] = res
    return res


# ---------- nomes e caminhos ----------

def limpar(s):
    s = UNSAFE.sub(" ", s or "")
    return re.sub(r"\s+", " ", s).strip(" .")


def montar_nome(autor, ano, titulo, ext):
    base = " - ".join([
        limpar(autor)[:80] or "Autor desconhecido",
        limpar(ano) or "s.d.",
        limpar(titulo)[:150] or "Sem título",
    ])
    return limpar(base)[:180] + ext


def caminho_livre(caminho):
    if not caminho.exists():
        return caminho
    n = 2
    while True:
        cand = caminho.with_name(f"{caminho.stem} ({n}){caminho.suffix}")
        if not cand.exists():
            return cand
        n += 1


# ---------- varredura ----------

def snapshot(origem):
    """Tamanho e mtime de todos os arquivos da origem (só stat, nada é lido)."""
    snap = {}
    for raiz, dirs, nomes in os.walk(origem):
        for n in nomes:
            p = os.path.join(raiz, n)
            try:
                st = os.stat(p, follow_symlinks=False)
                snap[p] = (st.st_size, st.st_mtime_ns)
            except OSError:
                snap[p] = None
    return snap


def varrer(origem):
    excluidos = Counter()
    arquivos = []
    for raiz, dirs, nomes in os.walk(origem):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for n in nomes:
            if n.startswith("."):
                continue
            p = Path(raiz) / n
            ext = p.suffix.lower()
            if ext not in DOC_EXT:
                excluidos[ext or "(sem extensão)"] += 1
                continue
            try:
                arquivos.append((p, p.stat().st_size))
            except OSError:
                excluidos["erro de leitura"] += 1
    return sorted(arquivos), excluidos


# ---------- principal ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--origem", required=True)
    ap.add_argument("--destino", required=True)
    ap.add_argument("--executar", action="store_true", help="copia de fato (sem isso, só simula)")
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--online", action="store_true")
    ap.add_argument("--paginas", type=int, default=3)
    ap.add_argument("--areas", default=str(DEFAULT_AREAS))
    ap.add_argument("--revisao", default=None,
                    help="pasta separada para arquivos não identificados (padrão: <destino>_nao_identificados)")
    args = ap.parse_args()

    origem = Path(args.origem)
    destino = Path(args.destino)
    revisao_dir = Path(args.revisao) if args.revisao else destino.with_name(destino.name + "_nao_identificados")
    if not origem.is_dir():
        raise SystemExit(f"Origem não encontrada: {origem}")
    for alvo_saida in (destino, revisao_dir):
        if alvo_saida.resolve().is_relative_to(origem.resolve()):
            raise SystemExit(f"A saída {alvo_saida} não pode estar dentro da origem.")

    snap_antes = snapshot(origem)

    areas = json.loads(Path(args.areas).read_text(encoding="utf-8"))
    meta_dir = destino / "_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    cache_path = meta_dir / "cache_online.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    arquivos, excluidos = varrer(origem)
    print(f"Documentos candidatos: {len(arquivos)}")

    # Duplicatas exatas: SHA-256 só para arquivos com tamanho repetido
    por_tamanho = defaultdict(list)
    for p, tam in arquivos:
        por_tamanho[tam].append(p)
    hash_de = {}
    for tam, grupo in por_tamanho.items():
        if len(grupo) > 1:
            for p in grupo:
                hash_de[p] = sha256(p)

    vistos = {}
    linhas = []
    resumo = Counter()

    for p, tam in arquivos:
        ext = p.suffix.lower()
        h = hash_de.get(p)
        if h and h in vistos:
            linhas.append({"origem": str(p), "destino": "", "acao": "duplicado (não copiado)",
                           "motivos": f"duplicado de {vistos[h]}", "sha256": h, "tamanho_bytes": tam})
            resumo["duplicado"] += 1
            continue
        if h:
            vistos[h] = str(p)

        info = ler_pdf(p, args.paginas, args.ocr) if ext == ".pdf" else \
            {"paginas": None, "texto": "", "titulo": None, "autor": None, "ocr": False}
        doi, isbn = ids_do_texto(info["texto"])
        nome_m = NAME_RE.match(p.stem)

        autor = nome_m.group("autor").strip() if nome_m else None
        ano = nome_m.group("ano") if nome_m else None
        titulo = nome_m.group("titulo").strip() if nome_m else None
        if not titulo and info["titulo"] and not GENERIC_TITLE.match(info["titulo"]):
            titulo = info["titulo"]
        autor = autor or info["autor"]

        if args.online and (doi or isbn) and (not titulo or not autor or not ano):
            on = consultar_online(doi, isbn, cache)
            if on:
                titulo = titulo or on.get("titulo")
                autor = autor or on.get("autor")
                ano = ano or on.get("ano")
        titulo = titulo or re.sub(r"_+", " ", p.stem)

        tipo = tipo_do_documento(info, doi, isbn, p.name)
        area = area_do_documento(titulo, info["texto"], areas) if tipo else None

        motivos = []
        if ext != ".pdf":
            motivos.append(f"formato {ext} sem extração de texto")
        elif info["paginas"] is None:
            motivos.append("PDF ilegível")
        elif not info["texto"].strip():
            motivos.append("PDF sem texto (escaneado; OCR não aplicado ou falhou)")
        if not tipo:
            motivos.append("tipo indefinido")
        if tipo and not area:
            motivos.append("área indefinida")
        if not (doi or isbn):
            motivos.append("sem DOI/ISBN")

        pode_classificar = ext == ".pdf" and bool(info["texto"].strip()) and tipo and area
        if pode_classificar:
            confianca = "alta" if (doi or isbn) else "média"
            pasta_tipo = "Livros" if tipo == "livro" else "Artigos"
            subpasta = Path(pasta_tipo) / area
        else:
            # Não identificado: vai para uma pasta separada, fora da biblioteca classificada
            confianca = "baixa"
            raiz = revisao_dir
            subpasta = Path("Livros" if tipo == "livro" else "Artigos" if tipo == "artigo" else "indefinido")
            area = area or ""

        if pode_classificar:
            raiz = destino

        nome_destino = montar_nome(autor, ano, titulo, ext)
        alvo = raiz / subpasta / nome_destino
        if alvo.exists() and alvo.stat().st_size == tam:
            acao = "já existe (pulado)"
        elif args.executar:
            alvo = caminho_livre(alvo)
            alvo.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, alvo)
            acao = "copiado"
        else:
            alvo = caminho_livre(alvo)
            acao = "simulado"

        resumo[f"{confianca} / {tipo or 'indefinido'}"] += 1
        linhas.append({
            "origem": str(p), "destino": str(alvo),
            "acao": acao, "tipo": tipo or "", "area": area, "confianca": confianca,
            "autor": autor or "", "ano": ano or "", "titulo": titulo, "doi": doi or "", "isbn": isbn or "",
            "paginas": info["paginas"] or "", "ocr": "sim" if info["ocr"] else "não",
            "motivos": "; ".join(motivos), "sha256": h or "", "tamanho_bytes": tam,
        })

    # Garantia de integridade: a origem não pode ter mudado durante a execução
    snap_depois = snapshot(origem)
    comuns = set(snap_antes) & set(snap_depois)
    alterados = sorted(
        {k for k in comuns if snap_antes[k] != snap_depois[k]}
        | (set(snap_antes) ^ set(snap_depois))
    )

    with open(meta_dir / "manifesto.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(linhas)

    if args.online:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    rel = [
        "# Relatório de organização",
        "",
        f"Modo: {'cópia executada' if args.executar else 'simulação (nada copiado)'}",
        f"Origem: {origem}",
        f"Destino (classificados): {destino}",
        f"Destino (não identificados): {revisao_dir}",
        "",
        "## Integridade da origem",
        f"- Arquivos da origem verificados: {len(snap_antes)}",
        f"- Alterados durante a execução: {len(alterados)}" + ("  <-- ALERTA" if alterados else " (nenhum)"),
        "",
        f"Documentos candidatos: {len(arquivos)}",
        f"Duplicatas exatas (não copiadas): {resumo['duplicado']}",
        "",
        "## Resultado por confiança e tipo",
    ]
    for k, v in sorted(resumo.items()):
        if k != "duplicado":
            rel.append(f"- {k}: {v}")
    rel += ["", "## Excluídos da biblioteca (não são documentos)"]
    for k, v in excluidos.most_common():
        rel.append(f"- {k}: {v}")
    (meta_dir / "relatorio.md").write_text("\n".join(rel) + "\n", encoding="utf-8")

    print("\n".join(rel))
    print(f"\nManifesto: {meta_dir / 'manifesto.csv'}")

    if alterados:
        print("\nERRO: a origem foi alterada durante a execução. Arquivos:", file=sys.stderr)
        for a in alterados[:50]:
            print(f"  {a}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
