# Pipeline de Tradução

## Fluxo ponta a ponta

```mermaid
sequenceDiagram
    actor U as Usuário
    participant FE as Frontend (app.js)
    participant API as main.py
    participant J as jobs.run_job()
    participant Ex as Extractor (txt/epub/pdf)
    participant LLM as llm.translate_many()
    participant LS as llama-server
    participant Cal as ebook-convert (opcional)

    U->>FE: seleciona arquivo + formato de saída
    FE->>API: POST /api/jobs (multipart: file, output_format)
    API->>J: jobs.create_job() — grava input.* em disco
    API->>J: jobs.start_job() — asyncio.create_task(run_job)
    API-->>FE: 200 { id, status: "pending", ... }

    loop polling a cada 2s
        FE->>API: GET /api/jobs/{id}
        API-->>FE: { status, progress_done, progress_total }
    end

    Note over J: run_job() começa a rodar em background
    J->>Ex: Extractor.load(input_path)
    Ex-->>J: doc.units (lista de strings)
    J->>J: status = "translating"

    par tradução (concorrência limitada por semáforo)
        LLM->>LS: POST /v1/chat/completions (chunk 1)
        LLM->>LS: POST /v1/chat/completions (chunk 2..N)
    end
    LS-->>LLM: traduções
    LLM-->>J: translations[] (mesma ordem de units)

    J->>J: status = "rebuilding"
    J->>Ex: doc.rebuild(translations, native_output_path)
    Ex-->>J: arquivo traduzido no formato nativo

    alt output_format == input_format
        J->>J: output_path = native_output_path
    else output_format == "txt"
        J->>J: escreve translations join("\n\n") direto
    else output_format diferente (ex: epub -> pdf)
        J->>J: status = "converting"
        J->>Cal: ebook-convert native_output_path final_path
        alt Calibre disponível e sucesso
            Cal-->>J: arquivo convertido
        else Calibre ausente/falhou
            J->>J: output_path = native_output_path + warning_message
        end
    end

    J->>J: status = "done"
    FE->>API: GET /api/jobs/{id} (status == "done")
    FE->>U: mostra link de download
    U->>API: GET /api/jobs/{id}/download
    API-->>U: FileResponse(output_path)
```

## Máquina de estados do Job

```mermaid
stateDiagram-v2
    [*] --> pending: create_job()
    pending --> translating: run_job() inicia
    translating --> rebuilding: translate_many() completo
    rebuilding --> converting: output_format != input_format e != "txt"
    rebuilding --> done: output_format == input_format ou == "txt"
    converting --> done: sempre (com ou sem warning_message)
    translating --> error: exceção não tratada
    rebuilding --> error: exceção não tratada
    converting --> error: exceção não tratada
    done --> [*]
    error --> [*]
```

Estados possíveis de `Job.status` (`jobs.py`): `pending`, `translating`, `rebuilding`,
`converting`, `done`, `error`. O frontend (`app.js: STATUS_LABELS`) traduz cada um para uma label
em português. `error` e `done` são estados terminais — o polling do frontend para (`clearInterval`)
ao atingir qualquer um dos dois.

## Pontos de atenção para quem for mexer aqui

- **`run_job` é fire-and-forget** (`asyncio.create_task`, não aguardado por nada). Se o processo
  do backend morrer no meio, o job fica "translating" para sempre do ponto de vista do
  frontend (que vai continuar fazendo polling em um job_id que não existe mais depois de um
  restart, já que `JOBS` é só em memória) — o usuário vê a barra de progresso travada, sem erro
  explícito. Não há reconciliação/retomada de jobs após um restart.
- **Toda exceção dentro de `run_job` vira `status="error"` com a mensagem crua da exceção**
  (`str(exc)`) exposta no frontend. Isso é intencional para uma ferramenta local de uso pessoal
  (facilita debugar), mas seria um problema de segurança/UX se isso virasse multi-usuário.
- **`progress_total` só é conhecido depois do `.load()`** (é `len(doc.units)`), então entre
  `status="translating"` e o primeiro `on_progress` callback, o frontend recebe
  `progress_total > 0` mas `progress_done == 0` — isso é esperado, não travado.
- **A tradução usa a MESMA lista `translations` em rebuild() independente do formato de saída.**
  Ou seja, o texto que sai no `.txt` de fallback (quando `output_format == "txt"`) é o texto puro
  traduzido, sem qualquer estrutura do documento original — é literalmente
  `"\n\n".join(translations)`.
