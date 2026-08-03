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
— é assim que os 5 bugs originais foram encontrados, nenhum deles gerava exceção.

## Performance da tradução local

- ~7-9 segundos por bloco de texto observados com `gemma-4-E2B-it` Q8_0 rodando localmente via
  llama-server. Um livro de 875 páginas (~7200 blocos de texto no PDF) levaria ~15-16h traduzindo
  sequencialmente.
- Aumentar `TRADUZ_TRANSLATION_CONCURRENCY` **não** acelerou na prática (testado com 3) — o
  llama-server local processa um request por vez (single slot de inferência). Concorrência só
  ajudaria contra um backend de inferência que de fato serve múltiplos slots em paralelo.
- Não há mecanismo de **retomar** uma tradução parcial. Se o job falhar ou o processo cair no meio
  de um livro de 15h, tem que começar do zero. Seria razoável persistir `translations[]`
  incrementalmente (ex: em um arquivo JSON no `job.dir`) para permitir retomar.
- Não há cache de tradução. Se o mesmo parágrafo aparecer duas vezes (comum em cabeçalhos/rodapés
  repetidos por página), ele é traduzido de novo a cada ocorrência.

## Estado em memória, sem persistência de jobs

`jobs.JOBS` é um `dict` Python. Reiniciar o processo do backend perde todo o estado de jobs em
andamento ou concluídos (os arquivos em `data/jobs/` sobrevivem em disco, mas ficam órfãos — não
há como redescobrir um `job_id` depois de um restart). Para uso local single-user isso é aceitável;
não seria para multi-usuário ou uso de longa duração sem supervisão.

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
- **Preenchimento de redação sempre branco.** Se a região atrás do texto original não for branca
  (fundo colorido/texturizado), a redação (`fill=(1,1,1)`) vai destoar visualmente. Não há
  amostragem de cor de fundo.
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
uvicorn for exposto além de `localhost`) pode fazer upload e ler qualquer job pelo `job_id` (que é
um UUID, não adivinhável, mas não há controle de acesso). Aceitável para uso local; **não** exponha
esse backend diretamente à internet sem adicionar autenticação.

## Ideias de evolução (não implementadas)

- Fila de jobs persistente (SQLite ou arquivo) em vez de dict em memória, com retomada após restart.
- Progresso e resultado de tradução persistidos incrementalmente, para permitir retomar um livro
  grande sem recomeçar do zero.
- Cache de tradução por hash do texto original (evita retraduzir cabeçalhos/rodapés repetidos).
- Suporte a fontes TrueType embutidas no PDF, para preservar tipografia original e ampliar cobertura
  de caracteres além do Latin-1.
- Testes de regressão visual automatizados para o pipeline de PDF.
- Suporte a PDFs multi-coluna no cálculo de `_compute_max_y_bounds()`.
- Endpoint para listar/cancelar jobs (hoje só existe criar/consultar/baixar).
