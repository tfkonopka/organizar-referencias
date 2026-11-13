---
name: organizar-referencias
description: Organiza uma biblioteca de referências bibliográficas (livros e artigos em PDF, DJVU e similares) lida de um HD externo e copiada para uma pasta local, separando em Livros e Artigos e agrupando por área de conhecimento (aproximação da tabela CNPq). Use sempre que o usuário mencionar organizar biblioteca, referências, livros ou artigos científicos, duplicatas de PDFs, classificar por área, ou copiar uma biblioteca de um HD externo, mesmo sem pedir explicitamente uma skill.
---

# Organizar referências bibliográficas

Script: `scripts/organizar.py`. Áreas: `references/areas.json` (editável). Proteções: `hooks/guard_hd.py`.

## Regras que não mudam

- A origem é somente leitura. Nada é movido, alterado ou apagado no HD externo. O script verifica isso ao final e sai com erro se algo mudou.
- Duplicatas exatas (mesmo SHA-256) não são copiadas.
- Arquivos não identificados vão para uma pasta separada, `<destino>_nao_identificados/`, com o motivo no manifesto. Nada é descartado.
- A primeira execução é sempre simulação. A cópia só ocorre com `--executar`, depois de aprovação do usuário.
- A LLM só é usada com autorização expressa do usuário (ver "Autorização para LLM").

## Fluxo

1. **Simular.** Rode sem `--executar`. O script gera `_meta/manifesto.csv` e `_meta/relatorio.md`.
2. **Conferir o relatório com o usuário.** Mostre totais por confiança e tipo, duplicatas, o status de integridade da origem e o que foi excluído. Não cole o CSV inteiro no chat.
3. **Ajustar áreas se necessário.** Se muitos arquivos caírem em "área indefinida", o usuário pode editar `references/areas.json` e simular de novo.
4. **Copiar.** Com aprovação, rode com `--executar`. A execução é retomável: arquivos já copiados com o mesmo tamanho são pulados.
5. **Revisar a pasta de não identificados.** O usuário decide manualmente. Se quiser uma etapa com LLM, ela só roda após a autorização expressa.

## Comandos

```bash
# simulação
python3 scripts/organizar.py --origem "ORIGEM" --destino "DESTINO"

# com OCR e consulta online (opcionais)
python3 scripts/organizar.py --origem "ORIGEM" --destino "DESTINO" --ocr --online

# cópia
python3 scripts/organizar.py --origem "ORIGEM" --destino "DESTINO" --executar
```

Dependências e ambiente: `bash setup_uv.sh` cria o `.venv` com uv a partir de `requirements.txt`.

## Proteções

**1. Hook de proteção (Claude Code).** `.claude/settings.json` registra um hook `PreToolUse` que roda `hooks/guard_hd.py` antes de cada Write, Edit, MultiEdit, NotebookEdit e Bash. O hook bloqueia:

- gravações dentro da origem, definida em `hooks/config.json`;
- comandos Bash que alteram a origem: `rm`, `mv`, `>`, `sed -i`, `chmod`, `touch`, `mkdir`, e `cp` ou `rsync` com a origem como destino;
- invocação de LLM sem autorização.

Leitura e cópia da origem para fora continuam permitidas. Se a entrada do hook não for JSON válido, o hook bloqueia por segurança (fail-closed).

Limitação: o hook é uma barreira de software e cobre os comandos mais comuns. Não substitui a proteção do próprio disco.

**2. Montagem somente leitura (recomendado).** Para garantia no sistema operacional, monte o HD como somente leitura antes de rodar, por exemplo com `sudo mount -o remount,ro "/run/media/konopka/FreeAgent GoFlex Drive"`. Com isso, nenhum processo consegue gravar nele. Depois da cópia, desmonte com `umount`.

**3. Verificação de integridade.** O script compara tamanho e data de modificação de todos os arquivos da origem antes e depois da execução. Qualquer diferença aparece no relatório e encerra o processo com código 2.

## Autorização para LLM

A LLM não é usada por padrão. Para liberar uma etapa com LLM, o próprio usuário cria o arquivo `hooks/AUTORIZACAO_LLM.txt` contendo a frase `AUTORIZO LLM` e a data. Para revogar, apague o arquivo. O hook recusa comandos com `classificar_llm`, `claude -p`, `anthropic`, `openai` ou `--llm` enquanto a autorização não existir.

## Critérios de classificação

- **Tipo:** ISBN válido (dígito verificador conferido) ou DOI indicam livro ou artigo. Sem identificador, usa número de páginas, seções ("Resumo", "Referências", "Capítulo") e palavras no nome. Sinais fracos ou empatados viram "indefinido".
- **Área:** conta palavras-chave do título e do início do texto. Só classifica quando a área vence com clareza. Caso contrário, fica indefinida.
- **Confiança:**
  - alta: tipo, área e identificador (DOI ou ISBN);
  - média: tipo e área, sem identificador. Vai para a pasta da área, com o motivo registrado;
  - baixa: qualquer outro caso, incluindo formatos sem texto e PDFs escaneados sem OCR. Vai para a pasta de não identificados.

## Estrutura de saída

```
Biblioteca/                          # classificados
├── Livros/<Área>/Autor - Ano - Título.pdf
├── Artigos/<Área>/Autor - Ano - Título.pdf
└── _meta/manifesto.csv, relatorio.md, cache_online.json

Biblioteca_nao_identificados/        # separada, para revisão manual
└── {Livros|Artigos|indefinido}/...
```

## Escopo

Entram apenas documentos (PDF, DJVU, EPUB, DOC, DOCX, ODT, RTF, TXT). Código, dados, imagens, vídeos e arquivos compactados são excluídos e listados no relatório.
