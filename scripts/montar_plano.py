#!/usr/bin/env python3
"""Etapa 4 do workflow: monta o plano de cópia (SIMULAÇÃO, nada é copiado).

Combina unidades.jsonl + llm_resultados.jsonl e decide destino e nome de cada arquivo:
  artigos, teses:   <Área>/<Subárea>/<Tipo>/Ano - Autor - Título.ext
  livros avulsos:   <Área>/<Subárea>/Books/Autor - Título.ext
  livro em pastas:  <Área>/<Subárea>/Books/Autor - Título/<arquivos originais>
  notas de aula:    <Área>/<Subárea>/Lecture Notes/<pasta original>/<arquivos originais>
  cópias inteiras:  <destino da regra>/<caminho original abaixo do prefixo>
  baixa confiança com área válida: <Área>/<Subárea>/_To Verify/<caminho original>
  sem área / não é referência:     _Unsorted/<caminho original>
Sem LLM para a unidade, ela vai para _Unsorted (o plano diz o motivo).
Saídas: <trabalho>/plano.csv e <trabalho>/relatorio_simulacao.md
"""
import argparse, csv, json, re, sys, unicodedata
from collections import Counter, defaultdict
from pathlib import PurePosixPath as P

try:
    from ftfy import fix_text
except ImportError:
    def fix_text(s):
        return s

LIMIAR = 0.7
INVALIDOS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def limpar(s, maxlen=120):
    s = fix_text(s or "")
    s = re.sub(r"<[^>]+>", "", s)                      # tags HTML
    s = re.sub(r"\$([^$]*)\$", r"\1", s)                # $...$ do LaTeX
    s = s.replace("\\", "")
    s = INVALIDOS.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" .-_")
    if len(s) > maxlen:
        s = s[:maxlen].rsplit(" ", 1)[0]
    return unicodedata.normalize("NFC", s)


def autor_txt(autores):
    autores = [limpar(a, 40) for a in (autores or []) if a and limpar(a, 40)]
    if not autores:
        return None
    return autores[0] + (" et al." if len(autores) > 1 else "")


def ano_txt(ano):
    return str(ano) if ano else "s.d."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trabalho", required=True)
    ap.add_argument("--taxonomia", required=True)
    ap.add_argument("--biblioteca", required=True, help="pasta de destino final (ex.: .../Library)")
    a = ap.parse_args()
    from pathlib import Path
    trab, lib = Path(a.trabalho), P(a.biblioteca)
    tax = json.load(open(a.taxonomia, encoding="utf-8"))
    unidades = [json.loads(l) for l in open(trab / "unidades.jsonl", encoding="utf-8")]
    inv = {json.loads(l)["rel"]: json.loads(l) for l in open(trab / "inventario.jsonl", encoding="utf-8")}
    llm = {}
    rp = trab / "llm_resultados.jsonl"
    if rp.exists():
        for l in open(rp, encoding="utf-8"):
            j = json.loads(l)
            if "resultado" in j:
                llm[j["id"]] = j["resultado"]

    linhas = []          # (origem_rel, destino, acao, unidade, kind, tipo, area, subarea, fonte, confianca, obs)
    destino_de_unidade = {}
    ocupado = {}         # destino -> sha
    conflitos = []

    def reservar(dest, sha):
        d = P(dest)
        i = 1
        while True:
            c = d if i == 1 else d.with_name(f"{d.stem} ({i}){d.suffix}")
            k = str(c).lower()  # sem distinção de maiúsculas (compatível com NTFS/exFAT)
            if k not in ocupado or ocupado[k] == sha:
                ocupado[k] = sha
                return str(c), i > 1
            i += 1

    def decidir(u):
        """Retorna (tipo, area, subarea, titulo, autores, ano, fonte, conf, motivo_unsorted)."""
        r = llm.get(u["id"])
        meta = u.get("meta") or {}
        if not r:
            return None, None, None, None, None, None, "sem LLM", None, "não classificado pela LLM"
        tipo, area, sub, conf = r["tipo"], r["area"], r.get("subarea"), r.get("confianca", 0)
        if tipo == "Manuals":
            tipo = "Standards and Manuals"
        titulo, autores, ano, fonte = r.get("titulo"), r.get("autores"), r.get("ano"), "llm"
        if meta.get("titulo"):
            titulo, fonte = meta["titulo"], meta.get("fonte", "online")
            autores = meta.get("autores") or autores
            ano = meta.get("ano") or ano
        motivo = None
        if tipo == "Not a reference":
            motivo = "não é referência"
        elif area == "Unsorted" or not sub:
            motivo = "área/subárea indefinida"
        elif conf < LIMIAR:
            motivo = f"confiança {conf:.2f} < {LIMIAR}"
        elif fonte == "llm" and not r.get("titulo_verificado"):
            motivo = "título da LLM não encontrado no texto"
        return tipo, area, sub, titulo, autores, ano, fonte, conf, motivo

    # cópias inteiras primeiro (têm prioridade de caminho)
    ja_copiado = set()
    for u in (x for x in unidades if x["kind"] == "whole"):
        for rel in sorted(u["membros"]):
            sub_rel = rel[len(u["pasta"]):].lstrip("/")
            dest, renomeado = reservar(str(lib / u["destino_base"] / sub_rel), inv[rel]["sha256"])
            obs = u["motivo"]
            if renomeado:
                obs += "; conflito de nome na unificação (sufixo adicionado)"
                conflitos.append((rel, dest))
            acao = "copiar"
            if dest in ja_copiado:
                acao, obs = "duplicata", obs + "; arquivo idêntico já está no mesmo destino"
            ja_copiado.add(dest)
            linhas.append((rel, dest, acao, u["id"], "whole", "", "", "", "regra de pasta", "", obs))

    for u in (x for x in unidades if x["kind"] != "whole"):
        if u.get("dup_de"):
            continue
        tipo, area, sub, titulo, autores, ano, fonte, conf, motivo = decidir(u)
        if motivo:
            # confiança baixa / título não verificado, mas com área válida: <Área>/<Subárea>/_To Verify/<caminho original>
            if area and area != "Unsorted" and sub and (motivo.startswith("confiança") or motivo.startswith("título")):
                base = lib / area / sub / "_To Verify"
            else:
                base = lib / "_Unsorted"
            for rel in u["membros"]:
                dest, _ = reservar(str(base / rel), inv[rel]["sha256"])
                linhas.append((rel, dest, "copiar", u["id"], u["kind"], tipo or "", area or "", sub or "", fonte, conf or "", motivo))
            destino_de_unidade[u["id"]] = str(base / u["membros"][0])
            continue
        raiz = lib / area / sub
        aut = autor_txt(autores)
        tit = limpar(titulo) or P(u["membros"][0]).stem
        if u["kind"] == "book":
            pasta = limpar(f"{aut} - {tit}" if aut else tit, 150)
            for rel in u["membros"]:
                dest, _ = reservar(str(raiz / "Books" / pasta / P(rel).name), inv[rel]["sha256"])
                linhas.append((rel, dest, "copiar", u["id"], "book", "Books", area, sub, fonte, conf, ""))
            destino_de_unidade[u["id"]] = str(raiz / "Books" / pasta)
        elif u["kind"] == "lecture":
            for rel in u["membros"]:
                dest, _ = reservar(str(raiz / "Lecture Notes" / P(u["pasta"]).name / P(rel).name), inv[rel]["sha256"])
                linhas.append((rel, dest, "copiar", u["id"], "lecture", "Lecture Notes", area, sub, fonte, conf, ""))
            destino_de_unidade[u["id"]] = str(raiz / "Lecture Notes" / P(u["pasta"]).name)
        else:
            ext = P(u["membros"][0]).suffix.lower()
            if tipo == "Books":
                nome = f"{aut} - {tit}" if aut else tit
            else:
                nome = f"{ano_txt(ano)} - {aut or 'Unknown'} - {tit}"
            dest, renomeado = reservar(str(raiz / tipo / (limpar(nome, 180) + ext)), u["sha256"])
            linhas.append((u["membros"][0], dest, "copiar", u["id"], "file", tipo, area, sub, fonte, conf,
                           "nome repetido: sufixo adicionado" if renomeado else ""))
            destino_de_unidade[u["id"]] = dest

    # duplicatas: apontam para o destino da unidade original
    for u in (x for x in unidades if x.get("dup_de")):
        alvo = destino_de_unidade.get(u["dup_de"], "")
        for rel in u["membros"]:
            linhas.append((rel, alvo, "duplicata", u["id"], u["kind"], "", "", "", "", "", f"idêntico a {u['dup_de']}"))

    # não documentos (zip, imagens, áudio/vídeo, ppt, código...) fora das cópias inteiras: também são copiados e
    # mantêm a correspondência com a pasta de origem:
    #  1) irmãos copiados todos no mesmo destino -> junto deles;
    #  2) pasta com regra de área -> <Área>/<Subárea>/_Related Files/<caminho original>;
    #  3) sem regra -> _Related Files/<caminho original>
    nd = trab / "nao_documentos.jsonl"
    if nd.exists():
        irmaos = defaultdict(set)
        for l in linhas:
            if l[2] == "copiar":
                irmaos[str(P(l[0]).parent)].add(str(P(l[1]).parent))
        regras = tax["regras_pasta"]
        vistos = set(l[1] for l in linhas if l[2] == "copiar")
        for l in open(nd, encoding="utf-8"):
            j = json.loads(l)
            rel = j["rel"]
            pasta = str(P(rel).parent)
            if len(irmaos.get(pasta, ())) == 1:
                base, regra_txt = P(next(iter(irmaos[pasta]))), "junto dos arquivos da mesma pasta"
                dest, _ = reservar(str(base / P(rel).name), inv[rel]["sha256"])
            else:
                rg = max((r for r in regras if rel.startswith(r["prefixo"] + "/")), key=lambda r: len(r["prefixo"]), default=None)
                sub_rel = "/".join(P(rel).parts[1:])
                if rg and rg.get("area") and not rg.get("generica"):
                    base = lib / rg["area"] / (rg.get("subarea") or "General") / "_Related Files"
                    regra_txt = "área pela regra de pasta"
                else:
                    base, regra_txt = lib / "_Related Files", "sem regra de área"
                dest, _ = reservar(str(base / sub_rel), inv[rel]["sha256"])
            acao = "duplicata" if dest in vistos else "copiar"
            vistos.add(dest)
            linhas.append((rel, dest, acao, "", "nao_documento", "", "", "", "", "", f"não-documento ({j['ext']}): {regra_txt}"))

    with open(trab / "plano.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["origem_rel", "destino", "acao", "unidade", "kind", "tipo", "area", "subarea", "fonte", "confianca", "obs"])
        w.writerows(linhas)

    # relatório
    c = Counter(l[2] for l in linhas)
    por_area = Counter((l[6], l[7], l[5]) for l in linhas if l[2] == "copiar" and l[6])
    unsorted = Counter(l[10] for l in linhas if "/_Unsorted/" in l[1])
    nd_c = Counter(l[10].split(": ")[-1] for l in linhas if l[4] == "nao_documento")
    coberto = {l[0] for l in linhas}
    faltando = [r for r in inv if r not in coberto]
    R = ["# Relatório da simulação", "", "Nada foi copiado.", "",
         f"Arquivos no inventário: {len(inv)}; no plano: {len(coberto)}; sem decisão: {len(faltando)}", "",
         "| Ação | Arquivos |", "|---|---|"] + [f"| {k} | {v} |" for k, v in c.most_common()]
    R += ["", "## Classificados por área / subárea / tipo", "", "| Área | Subárea | Tipo | Arquivos |", "|---|---|---|---|"]
    R += [f"| {a_} | {s} | {t} | {n} |" for (a_, s, t), n in sorted(por_area.items())]
    tv = Counter(l[10].split(" ")[0] for l in linhas if "/_To Verify/" in l[1])
    R += ["", "## _To Verify (área válida, nomes originais mantidos)", ""] + [f"- {k}: {v}" for k, v in tv.most_common()]
    R += ["", "## Não-documentos (copiados com a correspondência de pasta)", ""] + [f"- {k}: {v}" for k, v in nd_c.most_common()]
    R += ["", "## _Unsorted (motivos)", ""] + [f"- {k}: {v}" for k, v in unsorted.most_common()]
    R += ["", f"## Conflitos de nome na unificação: {len(conflitos)}", ""] + [f"- `{o}` → `{d}`" for o, d in conflitos[:50]]
    (trab / "relatorio_simulacao.md").write_text("\n".join(R) + "\n", encoding="utf-8")
    print("\n".join(R[:12]))


if __name__ == "__main__":
    sys.exit(main())
