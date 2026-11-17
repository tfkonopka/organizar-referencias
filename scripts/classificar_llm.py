#!/usr/bin/env python3
"""Etapa 3 do workflow: classificação por LLM (Claude API). EXIGE hooks/AUTORIZACAO_LLM.txt.

Para cada unidade book/lecture/file (não duplicada), envia à API:
  caminho da pasta, regra de pasta (dica), nome do arquivo, metadados online já obtidos
  e até ~3.500 caracteres do texto das primeiras páginas.
Recebe JSON validado por schema: tipo, área, subárea, título, autores, ano, idioma, confiança.

Modos:
  piloto N          amostra estratificada de N unidades, chamadas síncronas (com fallback no servidor)
  lote-enviar       envia todas as unidades pendentes pela Batches API (50% do custo)
  lote-coletar ID   baixa os resultados do lote; recusas são refeitas de forma síncrona
Saída: <trabalho>/llm_resultados.jsonl (uma linha por unidade; a última vale)
"""
import argparse, hashlib, json, random, re, sys, unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic

MODEL = "claude-opus-5-5"
AQUI = Path(__file__).resolve().parent.parent
AUT = AQUI / "hooks" / "AUTORIZACAO_LLM.txt"


def carregar_chave():
    """Lê ANTHROPIC_API_KEY de ~/.config/anthropic_api_key.env se não estiver no ambiente. Nunca imprime a chave."""
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    arq = Path.home() / ".config" / "anthropic_api_key.env"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            if linha.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = linha.split("=", 1)[1].strip().strip('"')
                return
    sys.exit(f"Chave da API não encontrada: defina ANTHROPIC_API_KEY ou crie {arq}.")


def exigir_autorizacao():
    if not (AUT.exists() and "AUTORIZO LLM" in AUT.read_text(encoding="utf-8")):
        sys.exit(f"Sem autorização: crie {AUT} contendo 'AUTORIZO LLM' e a data.")


def schema(tax):
    return {
        "type": "object",
        "properties": {
            "tipo": {"type": "string", "enum": tax["tipos"] + ["Not a reference"]},
            "area": {"type": "string", "enum": list(tax["areas"]) + ["Unsorted"]},
            "subarea": {"type": "string"},
            "titulo": {"type": "string"},
            "autores": {"type": "array", "items": {"type": "string"}},
            "ano": {"type": ["integer", "null"]},
            "idioma": {"type": "string"},
            "confianca": {"type": "number"},
            "justificativa": {"type": "string"},
        },
        "required": ["tipo", "area", "subarea", "titulo", "autores", "ano", "idioma", "confianca", "justificativa"],
        "additionalProperties": False,
    }


def sistema(tax):
    areas = "\n".join(f"- {a}: {', '.join(s)}" for a, s in tax["areas"].items())
    return f"""You classify documents from a personal engineering reference library (mechanical engineering, fluid mechanics, porous media, numerical methods). Each request describes ONE unit: a single document, or a folder holding a book split into chapter files, or a folder of lecture notes.

Return:
- tipo: one of {', '.join(tax['tipos'])}. Use "Not a reference" for personal documents, forms, receipts, exams or anything that is not reference material.
- area and subarea: pick from this taxonomy. subarea must be one of the listed names for that area. If no area fits, use area "Unsorted" and subarea "".
{areas}
- titulo: the document's real title as printed on it (for a book folder, the book title). Copy it from the text when it appears there; do not translate it. Drop edition markers, DOIs and file-name noise.
- autores: surnames of the authors in order (for a book, its authors or editors). Empty list if unknown.
- ano: publication year if stated or given in the metadata, else null.
- idioma: ISO code of the document language (en, pt, es, fr, de...).
- confianca: 0 to 1, your confidence in area, subarea and tipo together.
- justificativa: one short sentence.

The folder the owner stored the file in is a hint, often a good one; when marked "generic", it says little. Metadata from Crossref, OpenLibrary or arXiv is authoritative for title, authors and year. Never invent a title or author that is not supported by the text, the file name or the metadata: if you cannot tell, return what the file name supports and lower confianca."""


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def descrever(u, trab):
    regra = u.get("regra") or {}
    dica = "generic (mixed folder)" if regra.get("generica") or not regra.get("area") else f"{regra.get('area')} / {regra.get('subarea') or '?'}"
    partes = [f"Unit kind: {u['kind']}", f"Folder: {u['pasta']}", f"Folder hint: {dica}"]
    if u["kind"] == "file":
        partes += [f"File name: {Path(u['membros'][0]).name}", f"Pages: {u.get('paginas')}"]
        if u.get("meta_titulo") or u.get("meta_autor"):
            partes.append(f"Embedded PDF metadata: title={u.get('meta_titulo')!r} author={u.get('meta_autor')!r}")
        sha = u.get("sha256")
    else:
        partes.append(f"Files in folder ({len(u['membros'])}): " + "; ".join(u.get("arquivos_exemplo", [])))
        sha = u.get("texto_sha")
    for k in ("doi", "isbn", "arxiv"):
        if u.get(k):
            partes.append(f"{k.upper()}: {u[k]}")
    if u.get("meta"):
        partes.append("Online metadata: " + json.dumps(u["meta"], ensure_ascii=False))
    if u.get("openalex"):
        partes.append("OpenAlex topic: " + json.dumps(u["openalex"], ensure_ascii=False))
    texto = ""
    if sha:
        tp = trab / "texto" / f"{sha}.txt"
        if tp.exists():
            texto = re.sub(r"[ \t]+", " ", tp.read_text(encoding="utf-8"))[:3500]
    partes.append("Text of the first pages:\n" + (texto or "(no extractable text - scanned or empty)"))
    return "\n".join(partes), texto


def params(u, trab, tax, sis):
    desc, _ = descrever(u, trab)
    return {
        "model": MODEL,
        "max_tokens": 4000,
        "system": sis,
        "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": schema(tax)}},
        "messages": [{"role": "user", "content": desc}],
    }


def validar(u, dados, trab, tax):
    _, texto = descrever(u, trab)
    sub = dados.get("subarea")
    if dados.get("area") in tax["areas"] and sub not in tax["areas"][dados["area"]]:
        dados["subarea_invalida"] = sub
        dados["subarea"] = None
    t, fonte = norm(dados.get("titulo")), norm(texto + " " + " ".join(u.get("arquivos_exemplo", [])) + " " + Path(u["pasta"]).name + " " + Path(u["membros"][0]).stem + " " + json.dumps(u.get("meta") or {}))
    dados["titulo_verificado"] = bool(t) and t[:60] in fonte
    return dados


def cid(uid):
    return hashlib.sha1(uid.encode()).hexdigest()[:40]


def chamar_sync(client, u, trab, tax, sis):
    p = params(u, trab, tax, sis)
    r = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **p)
    if r.stop_reason == "refusal":
        return {"id": u["id"], "erro": "refusal", "modelo": r.model}
    txt = next((b.text for b in r.content if b.type == "text"), "")
    dados = validar(u, json.loads(txt), trab, tax)
    return {"id": u["id"], "modelo": r.model, "uso": {"in": r.usage.input_tokens, "out": r.usage.output_tokens}, "resultado": dados}


def pendentes(trab):
    feitos = {}
    rp = trab / "llm_resultados.jsonl"
    if rp.exists():
        for l in open(rp, encoding="utf-8"):
            j = json.loads(l)
            if "resultado" in j:
                feitos[j["id"]] = j
    us = [json.loads(l) for l in open(trab / "unidades.jsonl", encoding="utf-8")]
    return [u for u in us if u["kind"] in ("book", "lecture", "file") and not u.get("dup_de") and u["id"] not in feitos]


def main():
    exigir_autorizacao()
    ap = argparse.ArgumentParser()
    ap.add_argument("modo", choices=["piloto", "lote-enviar", "lote-coletar"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--trabalho", required=True)
    ap.add_argument("--taxonomia", required=True)
    a = ap.parse_args()
    trab = Path(a.trabalho)
    tax = json.load(open(a.taxonomia, encoding="utf-8"))
    sis = sistema(tax)
    carregar_chave()
    client = anthropic.Anthropic()
    out = open(trab / "llm_resultados.jsonl", "a", encoding="utf-8")
    us = pendentes(trab)

    if a.modo == "piloto":
        n = int(a.arg or 50)
        random.seed(42)
        grupos = {}
        for u in us:
            g = (u["kind"], bool(u.get("doi") or u.get("isbn")), (u.get("chars") or 0) > 200 if u["kind"] == "file" else True)
            grupos.setdefault(g, []).append(u)
        amostra = []
        while len(amostra) < n and any(grupos.values()):
            for g in list(grupos):
                if grupos[g] and len(amostra) < n:
                    amostra.append(grupos[g].pop(random.randrange(len(grupos[g]))))
        with ThreadPoolExecutor(4) as ex:
            for res in ex.map(lambda u: chamar_sync(client, u, trab, tax, sis), amostra):
                out.write(json.dumps(res, ensure_ascii=False) + "\n"); out.flush()
        print(f"piloto: {len(amostra)} unidades")
    elif a.modo == "lote-enviar":
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request
        mapa = {cid(u["id"]): u["id"] for u in us}
        b = client.messages.batches.create(requests=[
            Request(custom_id=cid(u["id"]), params=MessageCreateParamsNonStreaming(**params(u, trab, tax, sis))) for u in us])
        json.dump(mapa, open(trab / f"lote_{b.id}.json", "w", encoding="utf-8"))
        print(f"lote {b.id}: {len(us)} pedidos, status {b.processing_status}")
    else:
        bid = a.arg
        b = client.messages.batches.retrieve(bid)
        if b.processing_status != "ended":
            sys.exit(f"lote ainda em processamento: {b.request_counts}")
        mapa = json.load(open(trab / f"lote_{bid}.json", encoding="utf-8"))
        por_id = {u["id"]: u for u in us}
        refazer = []
        for res in client.messages.batches.results(bid):
            uid = mapa[res.custom_id]
            u = por_id.get(uid)
            if u is None:
                continue
            if res.result.type == "succeeded" and res.result.message.stop_reason != "refusal":
                m = res.result.message
                txt = next((x.text for x in m.content if x.type == "text"), "")
                out.write(json.dumps({"id": uid, "modelo": m.model, "uso": {"in": m.usage.input_tokens, "out": m.usage.output_tokens},
                                      "resultado": validar(u, json.loads(txt), trab, tax)}, ensure_ascii=False) + "\n")
            else:
                refazer.append(u)
        for u in refazer:
            out.write(json.dumps(chamar_sync(client, u, trab, tax, sis), ensure_ascii=False) + "\n")
        print(f"coletado; refeitos de forma síncrona: {len(refazer)}")


if __name__ == "__main__":
    sys.exit(main())
