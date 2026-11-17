#!/usr/bin/env python3
"""Etapa 3 (variante sem API): classificação por subagentes Haiku 5.5. EXIGE hooks/AUTORIZACAO_LLM.txt.

Modos:
  preparar [--tam 25]   gera <trabalho>/haiku/instrucoes.md e entrada_NNN.json (id + descrição de cada unidade pendente)
  coletar               lê saida_NNN.json, valida contra a taxonomia e acrescenta em llm_resultados.jsonl
Mesmo formato de saída de classificar_llm.py (a última linha por id vale).
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import classificar_llm as c  # reaproveita descrever/validar/pendentes/sistema

MODELO = "claude-haiku-5-5"


def main():
    c.exigir_autorizacao()
    ap = argparse.ArgumentParser()
    ap.add_argument("modo", choices=["preparar", "coletar"])
    ap.add_argument("--trabalho", required=True)
    ap.add_argument("--taxonomia", required=True)
    ap.add_argument("--tam", type=int, default=25)
    a = ap.parse_args()
    trab = Path(a.trabalho)
    tax = json.load(open(a.taxonomia, encoding="utf-8"))
    hd = trab / "haiku"
    hd.mkdir(exist_ok=True)
    us = c.pendentes(trab)

    if a.modo == "preparar":
        sis = c.sistema(tax)
        (hd / "instrucoes.md").write_text(
            sis + """

OUTPUT FORMAT. You receive a JSON file: a list of {"id", "descricao"}. For EACH item, produce one object with exactly these keys:
id (copied unchanged), tipo, area, subarea, titulo, autores (array of surnames), ano (integer or null), idioma, confianca (0-1), justificativa.
Write the full list as a JSON array to the output file you are told. Output must be valid JSON, one object per input item, same order. Use only values allowed by the taxonomy above (tipo may also be "Not a reference"; area may also be "Unsorted" with subarea "").
""", encoding="utf-8")
        for f in hd.glob("entrada_*.json"):
            f.unlink()
        for i in range(0, len(us), a.tam):
            lote = [{"id": u["id"], "descricao": c.descrever(u, trab)[0]} for u in us[i:i + a.tam]]
            json.dump(lote, open(hd / f"entrada_{i // a.tam:03d}.json", "w", encoding="utf-8"), ensure_ascii=False)
        print(f"{len(us)} unidades pendentes em {(len(us) + a.tam - 1) // a.tam} lotes")
        return
    por_id = {u["id"]: u for u in us}
    ok = faltam = 0
    with open(trab / "llm_resultados.jsonl", "a", encoding="utf-8") as out:
        for f in sorted(hd.glob("saida_*.json")):
            try:
                itens = json.load(open(f, encoding="utf-8"))
            except Exception as e:
                print("saida inválida:", f.name, e)
                continue
            for d in itens:
                u = por_id.get(d.get("id"))
                if not u:
                    continue
                uid = d.pop("id")
                try:
                    r = c.validar(u, d, trab, tax)
                except Exception as e:
                    print("falha ao validar", uid, e)
                    continue
                out.write(json.dumps({"id": uid, "modelo": MODELO, "resultado": r}, ensure_ascii=False) + "\n")
                ok += 1
    restantes = len(c.pendentes(trab))
    print(f"gravados {ok}; ainda pendentes {restantes}")


if __name__ == "__main__":
    main()
