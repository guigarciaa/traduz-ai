# Testes Automatizados

Suíte em `backend/tests/`, usando `pytest` + `pytest-asyncio` + `pytest-cov` + `respx` (mock de
HTTP para o `llama-server`, sem precisar de um modelo real rodando). **102 testes, 100% de
cobertura de linha** em `backend/app/` no momento em que isto foi escrito.

## Como rodar

```bash
cd backend
source .venv/bin/activate
pip install -r requirements-dev.txt   # instala pytest/pytest-asyncio/pytest-cov/respx além do requirements.txt
pytest                                 # roda tudo, com coverage e o gate de 90%
```

`pytest` sozinho já aplica coverage e o portão mínimo — configurado em `backend/pyproject.toml`
(`addopts = "--cov=app --cov-report=term-missing --cov-fail-under=90"`). **O comando falha
(exit code != 0) se a cobertura cair abaixo de 90%** — isso é o mecanismo de "não deixar
regressão de cobertura passar despercebida", não precisa de CI externo configurado para valer
localmente.

Para rodar sem o gate de cobertura (mais rápido, útil durante iteração): `pytest --no-cov`.
Para ver linhas específicas não cobertas: `pytest --cov-report=html` gera `htmlcov/index.html`.

## Mapa de cobertura por módulo

| Módulo | Arquivo de teste | O que é validado |
|---|---|---|
| `extractors/txt.py` | `test_txt.py` | Split de parágrafos por linha em branco, rebuild, edge cases de whitespace. |
| `extractors/epub.py` | `test_epub.py` | Extração de blocos (p/h1/li/td...), skip de `nav.xhtml`, skip de blocos com `img`/`table`, blocos aninhados, achatamento de formatação inline, rebuild preservando estrutura/imagens/nav. |
| `extractors/pdf.py` | `test_pdf.py` | O mais extenso (40+ casos) — ver seção dedicada abaixo. |
| `llm.py` | `test_llm.py` | Sucesso, retry com backoff, esgotamento de tentativas, resposta malformada, ordem preservada sob concorrência, callback de progresso, skip de texto vazio. Todo HTTP é mockado via `respx` contra `settings.llama_server_url` — nenhum teste depende de um `llama-server` real rodando. |
| `jobs.py` | `test_jobs.py` | Detecção de formato, `create_job`, todos os ramos de `run_job` (mesmo formato / saída txt / conversão via Calibre disponível/indisponível / exceção → status error), `_convert_with_calibre` (sucesso/falha/timeout/ausente), `start_job` agendando a task em background. |
| `main.py` | `test_main.py` | As 3 rotas via `fastapi.testclient.TestClient`, incluindo códigos de erro (400/404/409) e o frontend estático sendo servido. |
| `config.py` | (coberto indiretamente) | `Settings` é exercitado pelos testes de `llm.py`/`jobs.py` que leem seus valores; não tem lógica própria que justifique testes dedicados. |

## Testando o pipeline de PDF (o mais delicado)

`pdf-extraction.md` documenta 5 bugs reais já corrigidos no algoritmo de extração/reinserção.
`test_pdf.py` foi escrito para que **cada um desses bugs, se reintroduzido, quebre um teste
específico**, em vez de só validar "roda sem erro":

- **Sanitização de caracteres** (`test_sanitize_*`): testa a função pura `_sanitize_for_pdf_font`
  diretamente contra pontuação tipográfica, símbolos matemáticos/gregos, acentos em português
  (que não devem ser tocados) e o catch-all para caracteres não mapeados (`*`).
- **Fusão de fragmentos** (`test_should_merge_*`, `test_merge_touching_blocks_*`): testa
  `_should_merge`/`_merge_touching_blocks` com dicts de bloco construídos à mão (bbox controlada
  por teste), cobrindo o limiar exato (`-2.0` a `0.5`), gaps reais de parágrafo (não devem
  fundir), e colunas diferentes (não devem fundir mesmo com gap pequeno).
- **Limites de `max_y`** (`test_max_y_*`): testa `_compute_max_y_bounds` isoladamente, sem
  precisar carregar um PDF de verdade — via `PdfDocument.__new__(PdfDocument)` (bypassa
  `__init__`) com `self._blocks`/`self._obstacles` definidos manualmente. Cobre bloco de texto
  abaixo, obstáculo de imagem abaixo, coluna diferente (deve ignorar), e página sem nada abaixo
  (cai no limite da página).
- **Desenho único, sem duplicar** (`test_insert_with_shrink_*`, `test_measure_spare_*`): o bug
  mais sutil (`insert_textbox` desenha imediatamente e cumulativamente — ver pdf-extraction.md).
  `test_insert_with_shrink_draws_content_exactly_once` cria uma página real, chama
  `_insert_with_shrink` e verifica que o texto aparece **exatamente uma vez** no resultado — se
  alguém reintroduzir uma chamada extra de `insert_textbox` na página real durante tentativas de
  ajuste de fonte, esse teste falha imediatamente.
- **Nunca perder texto** (`test_insert_with_shrink_never_loses_text_in_a_tiny_box`): caixa
  propositalmente pequena demais para o texto; verifica que ainda assim algo é desenhado.
- **Fim a fim** (`test_rebuild_produces_translated_pdf`,
  `test_load_and_rebuild_preserve_image_obstacles`): constrói um PDF de verdade via `fitz`,
  roda `PdfDocument.load()` → `rebuild()` → reabre o resultado e verifica o texto final, incluindo
  o caso de uma legenda com uma imagem real (raster, via `page.insert_image`) logo abaixo.

### Padrões de teste usados (para manter ao adicionar novos)

- **Instâncias "nuas" via `PdfDocument.__new__(PdfDocument)`**: para testar métodos que só
  precisam de um subconjunto de atributos (`self.doc`, `self._blocks`, `self._obstacles`,
  `self._scratch_doc`), pula-se `__init__` (que exige um arquivo PDF real em disco) e define-se
  só o necessário. Isso deixa os testes de geometria/algoritmo independentes de qualquer arquivo
  fixture.
- **PDFs construídos via `fitz` diretamente nos testes**, não fixtures de arquivo versionadas —
  cada teste monta exatamente a geometria que precisa (`page.insert_text`, `page.insert_image`).
  Evita depender de um PDF de exemplo versionado no repo (que seria grande e opaco).
- **Imagem real via `page.insert_image(rect, pixmap=...)`**, não `page.draw_rect(...)`: vetores
  desenhados (retângulos/linhas) **não aparecem** em `page.get_text("dict")["blocks"]` — só texto
  (`type=0`) e imagens raster (`type=1`) aparecem. Um teste de "obstáculo" que use `draw_rect`
  passaria silenciosamente sem testar nada (o `_obstacles` ficaria vazio). Ver
  `test_load_and_rebuild_preserve_image_obstacles`.

## Isolamento de estado (`jobs.py` e `main.py`)

`jobs.JOBS` é um dict global em memória e `jobs.DATA_DIR` aponta pra pasta real do projeto — sem
cuidado, os testes escreveriam em `data/jobs/` de verdade e vazariam `Job`s entre testes. Os dois
arquivos de teste que tocam `jobs.py` (`test_jobs.py`, `test_main.py`) têm uma fixture
`autouse=True` que faz `monkeypatch.setattr(jobs_module, "DATA_DIR", tmp_path)` e limpa
`jobs_module.JOBS` antes/depois de cada teste. Qualquer teste novo que chame `create_job`/`run_job`
deve estar em um arquivo com essa fixture (ou repetir o padrão).

`test_jobs.py` e `test_main.py` também nunca deixam `run_job`/`translate_many` fazer uma chamada
HTTP de verdade: sempre monkeypatcham `jobs_module.translate_many` (ou, nos testes de API,
`jobs_module.start_job` inteiro) por uma função fake determinística.

## O que não está coberto por design

- **Não há teste contra um `llama-server` real** — só contra o mock via `respx`. Isso é
  intencional (testes precisam rodar sem infraestrutura externa), mas significa que mudanças no
  formato de resposta real do llama-server (ex.: uma atualização do llama.cpp que mude o schema
  JSON) não seriam pegas automaticamente. Validação manual contra o servidor real continua
  necessária ao trocar de modelo/versão do llama.cpp.
- **Não há teste do fluxo de conversão via Calibre contra um `ebook-convert` real** — só mockado
  (`subprocess.run`/`shutil.which`). Combina com `known-limitations.md`: a integração com Calibre
  em si não tem cobertura de integração real.
- **Não há teste de carga/performance** (tempo de tradução de um livro inteiro, uso de memória em
  PDFs de centenas de páginas).
