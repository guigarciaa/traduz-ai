# Limitações Conhecidas e Ideias de Evolução

## Testes automatizados: cobrem lógica, não pixels

A suíte (`backend/tests/`, ver [testing.md](./testing.md)) tem 100% de cobertura de linha e
testa diretamente cada um dos 5 bugs documentados em [pdf-extraction.md](./pdf-extraction.md)
(sanitização de caracteres, fusão de fragmentos, limites de `max_y`, desenho único sem duplicar,
perda de texto) via asserções sobre bbox/contagem de spans/conteúdo de texto extraído — não há
comparação de pixels/imagem renderizada. Isso pega regressões de **lógica** (ex.: alguém reverte
o fix do `insert_textbox` duplicado, ou afrouxa o limiar de merge de blocos) mas não pega
regressões puramente **visuais** que não mudam a estrutura de texto/bbox (ex.: uma mudança na
lib PyMuPDF que altere métricas de fonte sutilmente). Para esse tipo de regressão, validação
visual manual (renderizar páginas como PNG e inspecionar) continua sendo o método mais confiável
— é assim que os 5 bugs originais de PDF **e** um bug real de CSS no frontend (o atributo
`hidden` sendo ignorado por elementos com `display` próprio — ver architecture.md) foram
encontrados, nenhum deles gerava exceção.

## Performance da tradução local

- ~7-9 segundos por bloco de texto observados com `gemma-4-E2B-it` Q8_0 rodando localmente via
  llama-server. Um livro de 875 páginas (~7200 blocos de texto no PDF) levaria ~15-16h traduzindo
  sequencialmente **na primeira vez** — retomada e cache (ver abaixo) reduzem isso em execuções
  seguintes ou após uma interrupção, mas não mudam a velocidade bruta de uma tradução 100% nova.
- Aumentar `TRADUZ_TRANSLATION_CONCURRENCY` **não** acelerou na prática (testado com 3) — o
  llama-server local processa um request por vez (single slot de inferência). Concorrência só
  ajudaria contra um backend de inferência que de fato serve múltiplos slots em paralelo.
- ~~Não há mecanismo de retomar uma tradução parcial~~ **Resolvido**: `job_units` no SQLite
  (`db.py`) persiste cada tradução conforme ela chega; `load_jobs_from_db()` retoma
  automaticamente qualquer job não-terminal ao reiniciar o backend, pulando o que já foi
  traduzido. Ver [architecture.md](./architecture.md) e [translation-pipeline.md](./translation-pipeline.md).
- ~~Não há cache de tradução~~ **Resolvido**: `translation_cache` (SQLite, chave = hash SHA-256 do
  texto original) é global entre jobs. Texto repetido (cabeçalhos/rodapés, ou o mesmo trecho
  aparecendo em livros diferentes) só é enviado ao LLM uma vez.
- **Novo, decorrente da correção acima**: o cache **nunca expira nem é invalidado
  automaticamente**. Trocar de modelo no llama-server não invalida traduções já cacheadas para o
  texto antigo — se isso for indesejado, é preciso apagar `data/traduz.db` ou implementar alguma
  política de expiração/versionamento de cache por modelo (não feito).

## Persistência de jobs (SQLite)

~~`jobs.JOBS` é um `dict` Python. Reiniciar o processo do backend perde todo o estado~~
**Resolvido**: todo job é espelhado em `data/traduz.db` a cada transição de status, e
`load_jobs_from_db()` repopula `JOBS` e retoma jobs não-terminais automaticamente no startup. O
que continua sendo uma limitação real:

- **SQLite via conexões curtas, sem pool** — cada chamada de `db.py` abre/fecha sua própria
  conexão. Adequado para o volume de escrita de um único usuário local (uma escrita por chunk
  traduzido, a cada poucos segundos); não foi testado sob carga de múltiplos usuários/jobs
  simultâneos em volume alto.
- **Guarda contra job "envenenado", mas não contra bug determinístico**: `resume_attempts` evita
  reiniciar para sempre um job após `MAX_RESUME_ATTEMPTS` (3) restarts do processo, mas se o job
  falha por um bug determinístico (ex: um arquivo de entrada que sempre quebra o extractor), ele
  ainda vai consumir as 3 tentativas antes de desistir, gerando 3 ciclos de log de erro.
- **Sem migração de schema.** `db.init_db()` só faz `CREATE TABLE IF NOT EXISTS` — não há
  versionamento nem migração se o schema precisar mudar no futuro (ex: adicionar uma coluna).
  Para um projeto pessoal isso é aceitável agora; deletar `data/traduz.db` reseta tudo.

## PDF: limitações estruturais do pipeline atual

- **Assume layout de coluna única.** `_compute_max_y_bounds()` decide "o que está abaixo de mim"
  usando só sobreposição horizontal de bbox — não foi testado contra PDFs de múltiplas colunas
  (comum em artigos científicos, jornais). Pode escolher o vizinho errado como limite de
  crescimento nesse cenário.
- **Fontes substitutas, não as originais.** O texto novo sempre usa uma das 4 variantes Helvetica
  base-14 do PyMuPDF (`helv`/`hebo`/`heit`/`hebi`), nunca a fonte original embutida no PDF (que
  pode ser serifada, ex: Computer Modern/Times, como no livro de teste). Resultado: o texto
  traduzido tem uma aparência tipográfica visivelmente diferente do original na mesma página.
  Melhorar isso exigiria detectar/embutir uma fonte TrueType Unicode real (resolveria também a
  limitação de charset do Latin-1, ver pdf-extraction.md bug 1) — trabalho não-trivial com
  PyMuPDF (`page.insert_font()` + reaproveitar métricas corretas).
- ~~Preenchimento de redação sempre branco~~ **Resolvido**: `_sample_background_color()` amostra
  a cor real por trás de cada bloco (moda quantizada dos pixels renderizados) antes de redigir.
  Limitação residual: título grande/negrito ocupando a maioria da área amostrada pode fazer a
  moda pegar a cor do texto em vez do fundo — ver pdf-extraction.md.
- **Sem OCR.** Texto dentro de imagens (comum em tabelas/diagramas escaneados ou renderizados como
  raster, como as tabelas do livro de teste) nunca é traduzido — permanece no idioma original,
  por design ("imagens permanecem originais" era requisito explícito do usuário), mas vale deixar
  claro que isso também cobre texto-dentro-de-imagem que o usuário talvez quisesse traduzido.

## EPUB: formatação inline é perdida

Negrito/itálico/links **no meio de uma frase** são achatados para texto plano ao traduzir (ver
[epub-extraction.md](./epub-extraction.md)). Estrutura de blocos, CSS e imagens são preservados.

## Conversão entre formatos depende de Calibre externo

Pedir um `output_format` diferente do formato de entrada (ex: enviar `.epub`, baixar `.pdf`) só
funciona se `ebook-convert` (Calibre) estiver instalado na máquina — o projeto não implementa
conversão de formato própria (renderizar EPUB como PDF ou vice-versa é um projeto à parte). Sem
Calibre, o job completa com um aviso e entrega o arquivo no formato nativo.

## Sem autenticação / superfície de API aberta

`main.py` não tem nenhuma camada de autenticação — qualquer processo na máquina (ou na rede, se o
uvicorn for exposto além de `localhost`) pode fazer upload, listar, cancelar e ler qualquer job
pelo `job_id` (que é um UUID, não adivinhável, mas não há controle de acesso). Aceitável para uso
local; **não** exponha esse backend diretamente à internet sem adicionar autenticação.

## Cancelamento não tem confirmação nem desfazer

`POST /api/jobs/{id}/cancel` age imediatamente (chama `task.cancel()`), sem pedir confirmação no
frontend antes do clique, e não há como "retomar" um job cancelado deliberadamente — ele fica
`cancelled` (terminal) para sempre; a única forma de continuar seria enviar o arquivo de novo (o
que reaproveitaria qualquer trecho já traduzido via cache/`job_units`, mas criaria um `job_id`
novo).

## Ideias de evolução (não implementadas)

- Suporte a fontes TrueType embutidas no PDF, para preservar tipografia original e ampliar cobertura
  de caracteres além do Latin-1.
- Testes de regressão visual automatizados (comparação de pixels) para o pipeline de PDF, além dos
  testes lógicos que já existem.
- Suporte a PDFs multi-coluna no cálculo de `_compute_max_y_bounds()`.
- Autenticação básica na API, caso o backend precise ser exposto além de `localhost`.
- Política de expiração/invalidação para `translation_cache` (hoje permanente e não versionado por
  modelo do LLM).
- Migração de schema para `data/traduz.db` (hoje só `CREATE TABLE IF NOT EXISTS`).
- Tela de histórico/listagem de jobs no frontend (a API `GET /api/jobs` já existe; não há UI para
  ela — só o job atual é mostrado na página).
