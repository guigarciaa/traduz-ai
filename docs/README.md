# Documentação Técnica — Traduz AI

Esta pasta é o contexto técnico do projeto para uso em evolução futura (por humanos ou por
um agente de IA retomando o trabalho). Ela documenta **como o sistema é construído hoje**,
**por que** as decisões não-óbvias foram tomadas, e **o que falta/pode quebrar**.

## Índice

1. [architecture.md](./architecture.md) — visão geral do sistema, componentes, stack, persistência SQLite (`db.py`), layout de diretórios.
2. [translation-pipeline.md](./translation-pipeline.md) — fluxo ponta a ponta (upload → extração → tradução → reconstrução → download), retomada automática após crash, diagrama de sequência e máquina de estados do job.
3. [pdf-extraction.md](./pdf-extraction.md) — o algoritmo de extração/tradução/reinserção de PDF em detalhe. É a parte mais complexa do sistema; documenta 5 bugs não-óbvios do PyMuPDF já descobertos e corrigidos, para não serem reintroduzidos.
4. [epub-extraction.md](./epub-extraction.md) — abordagem de extração/reinserção de EPUB.
5. [testing.md](./testing.md) — estratégia de testes automatizados, como rodar, mapa de cobertura por módulo.
6. [known-limitations.md](./known-limitations.md) — limitações conhecidas, débitos técnicos e ideias para evolução.

## Como usar esta documentação

- Antes de alterar `backend/app/extractors/pdf.py`, leia **pdf-extraction.md** por completo — várias
  decisões ali parecem "estranhas" ou "excessivamente cautelosas" à primeira vista, mas cada uma
  existe por causa de um bug real e visualmente confirmado (sobreposição de texto, texto sumindo
  silenciosamente, texto duplicado). O comentário `# insert_textbox draws immediately and
  cumulatively` no código é o resumo de uma tarde inteira de debugging.
- O projeto tem suíte de testes automatizados (`backend/tests/`, 100% de cobertura, gate mínimo de
  90% configurado em `backend/pyproject.toml`) — rode `pytest` antes de abrir um PR. Ver
  **testing.md** para o mapa completo e os padrões usados para testar o pipeline de PDF sem
  depender de arquivos fixture grandes. Mesmo com a suíte passando, mudanças no pipeline de PDF
  continuam se beneficiando de validação visual (renderizar páginas como PNG e inspecionar) para
  qualquer caso fora do que os testes já cobrem — os bugs documentados em pdf-extraction.md não
  geram exceptions, geram PDFs corrompidos silenciosamente.
