#!/usr/bin/env python3
"""Etapa 2 do workflow (sem LLM): monta as UNIDADES de classificação a partir do inventário.

Unidade = o que é classificado e copiado como um todo:
  whole    pasta copiada inteira (produção própria, cursos, idiomas, livros a verificar)
  book     pasta de livro em capítulos (prefixo Knovel, numeração ou ISBN comum)
  lecture  pasta de notas de aula (Aula/Lecture/Slide numerados)
  file     documento avulso
Também: deduplicação de pastas idênticas e de arquivos idênticos, regra de pasta -> área,
metadados online por identificador (Crossref, OpenAlex, OpenLibrary, arXiv), com cache.
Saída: <trabalho>/unidades.jsonl e <trabalho>/planejamento.md
"""
import argparse, json, re, sys, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from pathlib import PurePosixPath as P

DOC_EXT = {".pdf", ".djvu", ".epub", ".doc", ".docx", ".rtf", ".odt", ".ps", ".chm", ".mobi"}
AULA = re.compile(r"^(aula|lecture|lec|slide|notas de aula)", re.I)
UA = {"User-Agent": "library-organizer/0.2"}


# ---------------- online ----------------
class Online:
    def __init__(self, path):
        self.path = path
        try:
            self.cache = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    def save(self):
        json.dump(self.cache, open(self.path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)

    def _get(self, url):
        if url in self.cache:
            return self.cache[url]
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                data = r.read().decode("utf-8", "replace")
                j = json.loads(data) if data.lstrip()[:1] in "{[" else {"_xml": data}
        except Exception as e:
            j = {"_err": f"{type(e).__name__}: {str(e)[:80]}"}
        if "_err" not in j or "404" in j.get("_err", ""):
            self.cache[url] = j
        time.sleep(0.15)
        return j

    def crossref(self, doi):
        j = self._get("https://api.crossref.org/works/" + urllib.parse.quote(doi))
        m = j.get("message")
        if not m:
            return None
        autores = [a.get("family") or a.get("name") for a in m.get("author", []) if a.get("family") or a.get("name")]
        ano = None
        for k in ("published-print", "published-online", "issued", "created"):
            dp = (m.get(k) or {}).get("date-parts")
            if dp and dp[0] and dp[0][0]:
                ano = dp[0][0]; break
        return {"fonte": "crossref", "titulo": (m.get("title") or [None])[0], "autores": autores, "ano": ano,
                "tipo_crossref": m.get("type"), "veiculo": (m.get("container-title") or [None])[0],
                "isbn": (m.get("ISBN") or [None])[0]}

    def openalex(self, doi):
        j = self._get("https://api.openalex.org/works/doi:" + urllib.parse.quote(doi))
        pt = j.get("primary_topic")
        if not pt:
            return None
        return {"topic": pt.get("display_name"), "subfield": (pt.get("subfield") or {}).get("display_name"),
                "field": (pt.get("field") or {}).get("display_name")}

    def openlibrary(self, isbn):
        j = self._get(f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data")
        b = j.get(f"ISBN:{isbn}") if isinstance(j, dict) else None
        if not b:
            return None
        autores = [a.get("name") for a in b.get("authors", []) if a.get("name")]
        ano = re.search(r"(1[5-9]\d\d|20\d\d)", b.get("publish_date") or "")
        return {"fonte": "openlibrary", "titulo": b.get("title"), "subtitulo": b.get("subtitle"), "autores": autores,
                "ano": int(ano.group(1)) if ano else None, "assuntos": [s.get("name") for s in b.get("subjects", [])][:8]}

    def arxiv(self, aid):
        j = self._get("http://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(aid))
        x = j.get("_xml", "")
        ent = x.split("<entry>", 1)[1] if "<entry>" in x else ""
        t = re.search(r"<title>(.*?)</title>", ent, re.S)
        if not t:
            return None
        return {"fonte": "arxiv", "titulo": re.sub(r"\s+", " ", t.group(1)).strip(),
                "autores": [re.sub(r"\s+", " ", a).strip().split(" ")[-1] for a in re.findall(r"<name>(.*?)</name>", ent)],
                "ano": int(re.search(r"<published>(\d{4})", ent).group(1)) if "<published>" in ent else None,
                "categoria": (re.search(r'term="([^"]+)"', ent) or [None, None])[1]}


# ---------------- regras ----------------
def melhor_prefixo(rel, regras):
    best = None
    for r in regras:
        p = r["prefixo"]
        if rel == p or rel.startswith(p + "/"):
            if best is None or len(p) > len(best["prefixo"]):
                best = r
    return best


def tipo_pasta(nomes, isbns):
    """nomes: nomes (sem extensão) dos PDFs diretos de uma pasta."""
    if len(nomes) < 3:
        return None
    g = Counter(re.sub(r"\d+", "#", n) for n in nomes)
    st, n = g.most_common(1)[0]
    pre = Counter(re.split(r"[_\-\s]", x)[0] for x in nomes)
    p, pn = pre.most_common(1)[0]
    if AULA.match(st) or AULA.match(p):
        return "lecture"
    if re.fullmatch(r"\d{4,6}", p) and pn / len(nomes) >= 0.6:
        return "book"
    if "#" in st and n >= 3 and n / len(nomes) >= 0.6:
        return "book"
    if len(isbns) >= 3 and Counter(isbns).most_common(1)[0][1] >= 0.6 * len(isbns) and len(isbns) >= 0.3 * len(nomes):
        return "book"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trabalho", required=True)
    ap.add_argument("--taxonomia", required=True)
    ap.add_argument("--sem-online", action="store_true")
    a = ap.parse_args()
    trab = P(a.trabalho)
    tax = json.load(open(a.taxonomia, encoding="utf-8"))
    inv = [json.loads(l) for l in open(trab / "inventario.jsonl", encoding="utf-8")]
    online = Online(str(trab / "cache_online.json"))

    unidades = []
    resto = []
    # 1) cópias inteiras
    whole = defaultdict(list)
    for r in inv:
        w = melhor_prefixo(r["rel"], tax["copia_inteira"])
        if w:
            whole[w["prefixo"]].append(r)
        else:
            resto.append(r)
    for w in tax["copia_inteira"]:
        rs = whole.get(w["prefixo"], [])
        if rs:
            unidades.append({"id": f"W:{w['prefixo']}", "kind": "whole", "pasta": w["prefixo"], "destino_base": w["destino"],
                             "motivo": w["motivo"], "membros": [x["rel"] for x in rs],
                             "tamanho": sum(x["tamanho"] for x in rs)})

    # 2) obras por pasta (só documentos fora das cópias inteiras)
    por_pasta = defaultdict(list)
    for r in resto:
        por_pasta[str(P(r["rel"]).parent)].append(r)
    kind_pasta = {}
    for d, rs in por_pasta.items():
        pdfs = [x for x in rs if x["ext"] == ".pdf"]
        kind_pasta[d] = tipo_pasta([P(x["rel"]).stem for x in pdfs], [x["isbn"] for x in pdfs if x.get("isbn")])

    # 3) deduplicação de pastas-obra idênticas (mesmo conjunto de SHA-256)
    assinatura = {}
    dup_pasta = {}
    for d in sorted((d for d, k in kind_pasta.items() if k), key=lambda d: (len(d), d)):
        sig = frozenset(x["sha256"] for x in por_pasta[d])
        if sig in assinatura:
            dup_pasta[d] = assinatura[sig]
        else:
            assinatura[sig] = d

    sha_vistos = {}
    nao_doc = []
    for d, rs in sorted(por_pasta.items()):
        k = kind_pasta[d]
        regra = melhor_prefixo(d, tax["regras_pasta"]) or {}
        if k:
            u = {"id": f"{k[0].upper()}:{d}", "kind": k, "pasta": d, "membros": [x["rel"] for x in rs],
                 "tamanho": sum(x["tamanho"] for x in rs), "regra": regra, "dup_de": None}
            if d in dup_pasta:
                u["dup_de"] = f"{k[0].upper()}:{dup_pasta[d]}"
            else:
                pdfs = [x for x in rs if x["ext"] == ".pdf"]
                isb = Counter(x["isbn"] for x in pdfs if x.get("isbn")).most_common(1)
                u["isbn"] = isb[0][0] if isb else None
                com_texto = sorted((x for x in pdfs if x.get("chars", 0) > 200), key=lambda x: x["rel"])
                u["texto_sha"] = com_texto[0]["sha256"] if com_texto else None
                u["arquivos_exemplo"] = [P(x["rel"]).name for x in sorted(rs, key=lambda x: x["rel"])[:15]]
                if u["isbn"] and not a.sem_online:
                    u["meta"] = online.openlibrary(u["isbn"])
            unidades.append(u)
            continue
        for x in rs:
            if x["ext"] not in DOC_EXT:
                nao_doc.append(x)
                continue
            u = {"id": f"F:{x['rel']}", "kind": "file", "pasta": d, "membros": [x["rel"]], "tamanho": x["tamanho"],
                 "ext": x["ext"], "sha256": x["sha256"], "regra": regra, "paginas": x.get("paginas"),
                 "chars": x.get("chars", 0), "doi": x.get("doi"), "isbn": x.get("isbn"), "arxiv": x.get("arxiv"),
                 "meta_titulo": x.get("meta_titulo"), "meta_autor": x.get("meta_autor"), "dup_de": None}
            if x["sha256"] in sha_vistos:
                u["dup_de"] = sha_vistos[x["sha256"]]
            else:
                sha_vistos[x["sha256"]] = u["id"]
                if not a.sem_online:
                    if u["doi"]:
                        u["meta"] = online.crossref(u["doi"])
                        u["openalex"] = online.openalex(u["doi"])
                    elif u["arxiv"]:
                        u["meta"] = online.arxiv(u["arxiv"])
                    elif u["isbn"]:
                        u["meta"] = online.openlibrary(u["isbn"])
            unidades.append(u)
        online.save()
    online.save()

    with open(trab / "unidades.jsonl", "w", encoding="utf-8") as f:
        for u in unidades:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")
    with open(trab / "nao_documentos.jsonl", "w", encoding="utf-8") as f:
        for x in nao_doc:
            f.write(json.dumps({"rel": x["rel"], "ext": x["ext"], "tamanho": x["tamanho"]}, ensure_ascii=False) + "\n")

    # resumo
    c = Counter(); arq = Counter(); dup = Counter()
    for u in unidades:
        c[u["kind"]] += 1; arq[u["kind"]] += len(u["membros"])
        if u.get("dup_de"):
            dup[u["kind"]] += 1
    ids = sum(1 for u in unidades if u["kind"] == "file" and not u["dup_de"] and (u.get("doi") or u.get("isbn") or u.get("arxiv")))
    meta = sum(1 for u in unidades if u["kind"] == "file" and not u["dup_de"] and u.get("meta"))
    linhas = ["# Planejamento (etapa sem LLM)", "", f"Arquivos inventariados: {len(inv)}", "",
              "| Unidade | Unidades | Arquivos | Duplicadas |", "|---|---|---|---|"]
    for k in ("whole", "book", "lecture", "file"):
        linhas.append(f"| {k} | {c[k]} | {arq[k]} | {dup[k]} |")
    linhas += ["", f"Avulsos únicos com identificador: {ids}; com metadados online: {meta}",
               f"Não documentos fora das cópias inteiras (não copiados): {len(nao_doc)}"]
    open(trab / "planejamento.md", "w", encoding="utf-8").write("\n".join(linhas) + "\n")
    print("\n".join(linhas))


if __name__ == "__main__":
    sys.exit(main())
