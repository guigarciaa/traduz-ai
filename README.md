# Traduz AI

Tradutor de livros (EN → PT-BR) que recebe `.txt`, `.epub` ou `.pdf`, traduz
apenas o texto usando um modelo local via `llama-server`, e devolve o arquivo
no mesmo formato preservando imagens, espaçamento e diagramação.

## Como funciona

- **TXT**: divide em parágrafos, traduz cada um, remonta com as mesmas quebras.
- **EPUB**: percorre cada capítulo (XHTML), traduz por bloco (`p`, `li`, `h1`-`h6`,
  `blockquote`, células de tabela, etc.), preservando tags, CSS e imagens.
  O documento de navegação (sumário do e-reader) não é tocado, para não quebrar
  os links. Formatação inline (negrito/itálico no meio da frase) é simplificada
  para texto plano ao traduzir.
- **PDF**: extrai blocos de texto por página (posição, fonte, cor) com
  PyMuPDF, apaga o texto original (redação) e insere a tradução na mesma
  caixa, reduzindo a fonte automaticamente se o texto em português não couber.
  Imagens e elementos vetoriais não são tocados. Layouts complexos (múltiplas
  colunas, fontes incomuns) podem não ficar perfeitos — é uma limitação
  inerente à técnica.

Trocar o **formato de saída** (ex.: enviar `.epub` e baixar `.pdf`) usa o
`ebook-convert` do [Calibre](https://calibre-ebook.com/) se estiver instalado
no sistema. Sem o Calibre, o job termina com um aviso e entrega o arquivo
traduzido no formato original.

## Requisitos

- Python 3.11+ **com suporte a SSL** (o Python do pyenv neste ambiente foi
  compilado sem OpenSSL; use `/opt/homebrew/bin/python3.12` ou outro Python
  com SSL para criar o venv).
- Um `llama-server` (llama.cpp) rodando localmente com API compatível OpenAI,
  servindo o modelo `ggml-org/gemma-4-E2B-it-GGUF:Q8_0` (ou outro de sua
  escolha).
- Opcional: [Calibre](https://calibre-ebook.com/) instalado (para conversão
  entre formatos diferentes do original).

## Setup

```bash
cd backend
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuração

Variáveis de ambiente (todas opcionais, prefixo `TRADUZ_`):

| Variável                        | Padrão                                          | Descrição                                  |
|----------------------------------|--------------------------------------------------|---------------------------------------------|
| `TRADUZ_LLAMA_SERVER_URL`        | `http://localhost:8080/v1/chat/completions`       | Endpoint do llama-server                    |
| `TRADUZ_LLAMA_MODEL_NAME`        | `ggml-org/gemma-4-E2B-it-GGUF:Q8_0`               | Nome/alias do modelo                         |
| `TRADUZ_TRANSLATION_CONCURRENCY` | `1`                                                | Traduções simultâneas (aumente com cautela)  |
| `TRADUZ_LLM_TIMEOUT_SECONDS`     | `300`                                              | Timeout por requisição de tradução           |
| `TRADUZ_LLM_MAX_RETRIES`         | `3`                                                | Tentativas em caso de falha                  |

## Rodando

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8899
```

Abra `http://localhost:8899` no navegador, envie o arquivo, escolha o formato
de saída (ou deixe "mesmo formato do original") e acompanhe o progresso na
barra. Quando terminar, o link de download aparece na página.

## Limitações conhecidas

- PDFs com layout complexo (colunas múltiplas, texto rotacionado, fontes
  incomuns) podem sair com pequenas imperfeições — o texto é sempre
  redesenhado na mesma caixa do original, então textos em português mais
  longos reduzem a fonte automaticamente até caberem.
- Links/hiperlinks dentro do corpo do texto (não no sumário) são convertidos
  em texto plano ao traduzir o bloco que os contém.
- Tradução é feita localmente, parágrafo por parágrafo — livros grandes podem
  levar bastante tempo dependendo do hardware e do modelo usado.
