# Pipeline de Tradução

## Fluxo ponta a ponta

```mermaid
sequenceDiagram
    actor U as Usuário
    participant FE as Frontend (app.js)
    participant API as main.py
    participant J as jobs.run_job()
    participant DB as db.py (SQLite)
    participant Ex as Extractor (txt/epub/pdf)
    participant LLM as llm.translate_many()
    participant LS as llama-server
    participant Cal as ebook-convert (opcional)

    U->>FE: seleciona arquivo + formato de saída
    FE->>API: POST /api/jobs (multipart: file, output_format)
    API->>J: jobs.create_job() — grava input.* em disco
    J->>DB: save_job() (persiste antes mesmo de começar a traduzir)
    API->>J: jobs.start_job() — asyncio.create_task(run_job)
    API-->>FE: 200 { id, status: "pending", ... }

    loop polling a cada 2s
        FE->>API: GET /api/jobs/{id}
        API-->>FE: { status, progress_done, progress_total }
        opt usuário clica "Cancelar"
            FE->>API: POST /api/jobs/{id}/cancel
            API->>J: jobs.cancel_job() — task.cancel()
        end
    end

    Note over J: run_job() começa a rodar em background
    J->>Ex: Extractor.load(input_path)
    Ex-->>J: doc.units (lista de strings)
    J->>J: status = "translating"
    J->>DB: save_job() + save_job_units() (idempotente)

    J->>DB: load_job_units() — o que já foi traduzido antes? (retomada)
    loop para cada unidade ainda sem tradução
        J->>DB: get_cached_translation(hash) — algum job já traduziu esse texto exato?
    end
    Note over J: só as unidades que sobraram (nem retomadas, nem em cache) vão pro LLM

    par tradução (concorrência limitada por semáforo)
        LLM->>LS: POST /v1/chat/completions (chunk 1)
        LLM->>LS: POST /v1/chat/completions (chunk 2..N)
    end
    LS-->>LLM: tradução de cada chunk
    LLM->>J: on_item_done(index, texto) por chunk concluído
    J->>DB: save_unit_translation() + save_cached_translation() por chunk

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
    J->>DB: save_job()
    FE->>API: GET /api/jobs/{id} (status == "done")
    FE->>U: mostra link de download
    U->>API: GET /api/jobs/{id}/download
    API-->>U: FileResponse(output_path)
```

## Retomada automática após um crash/restart

```mermaid
sequenceDiagram
    participant API as main.py (lifespan)
    participant J as jobs.py
    participant DB as db.py (SQLite)

    Note over API: processo do backend reinicia (crash anterior no meio de um job)
    API->>DB: init_db()
    API->>J: load_jobs_from_db()
    J->>DB: load_all_jobs() — progress recalculado a partir de job_units
    loop para cada job com status não-terminal
        alt resume_attempts < MAX_RESUME_ATTEMPTS (3)
            J->>J: resume_attempts += 1
            J->>DB: save_job()
            J->>J: start_job() — reinicia run_job() do zero,<br/>que vai pular unidades já traduzidas
        else excedeu o limite
            J->>J: status = "error" (job "envenenado", desiste de retomar)
            J->>DB: save_job()
        end
    end
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
    translating --> cancelled: cancel_job() -> CancelledError
    rebuilding --> cancelled: cancel_job() -> CancelledError
    converting --> cancelled: cancel_job() -> CancelledError
    done --> [*]
    error --> [*]
    cancelled --> [*]
```

Estados possíveis de `Job.status` (`jobs.py`): `pending`, `translating`, `rebuilding`,
`converting`, `done`, `error`, `cancelled`. O frontend (`app.js: STATUS_LABELS`) traduz cada um
para uma label em português. `error`, `done` e `cancelled` são estados terminais
(`jobs.TERMINAL_STATUSES`) — o polling do frontend para (`clearInterval`) ao atingir qualquer um
dos três, e é esse mesmo conjunto que `load_jobs_from_db()` usa para decidir quais jobs precisam
ser retomados no startup (todo o resto).

## Pontos de atenção para quem for mexer aqui

- **`run_job` roda como `asyncio.Task`, mas agora persiste estado suficiente em SQLite para
  sobreviver a um crash.** Diferente da versão anterior (só em memória), um restart do backend não
  perde progresso: `load_jobs_from_db()` retoma automaticamente qualquer job não-terminal, e
  `run_job()` pula unidades que `job_units` já mostra como traduzidas.
- **Toda exceção dentro de `run_job` vira `status="error"` com a mensagem crua da exceção**
  (`str(exc)`) exposta no frontend. Isso é intencional para uma ferramenta local de uso pessoal
  (facilita debugar), mas seria um problema de segurança/UX se isso virasse multi-usuário.
- **`progress_total` só é conhecido depois do `.load()`** (é `len(doc.units)`), então entre
  `status="translating"` e o primeiro `on_progress` callback, o frontend recebe
  `progress_total > 0` mas `progress_done == 0` — isso é esperado, não travado.
- **`progress_done` já parte de um valor maior que zero quando há retomada/cache.** `run_job`
  calcula `already_done = len(doc.units) - len(pending_indices)` antes mesmo de chamar
  `translate_many()` — se metade das unidades já estava traduzida (retomada) ou em cache, a barra
  de progresso já começa em 50%, não em 0%.
- **A tradução usa a MESMA lista `translations` em rebuild() independente do formato de saída.**
  Ou seja, o texto que sai no `.txt` de fallback (quando `output_format == "txt"`) é o texto puro
  traduzido, sem qualquer estrutura do documento original — é literalmente
  `"\n\n".join(translations)`.
- **O cache de tradução é global e permanente** (tabela `translation_cache`, chave = hash SHA-256
  do texto original). Isso significa que um texto idêntico em dois livros diferentes é traduzido
  uma vez só — bom para velocidade, mas também significa que **o cache nunca expira nem é
  invalidado automaticamente**: se o modelo do llama-server mudar (ex: trocar de `gemma-4-E2B` para
  outro modelo) e você quiser uma tradução nova para o mesmo texto, vai precisar limpar a tabela
  `translation_cache` manualmente (ou apagar `data/traduz.db`).
- **Cancelar não interrompe uma escrita SQLite em andamento.** `task.cancel()` propaga
  `asyncio.CancelledError` na próxima vez que a coroutine dá `await`; um `save_unit_translation()`
  já disparado via `asyncio.to_thread` roda até o fim (é uma operação atômica no SQLite) antes do
  cancelamento ser observado — nunca fica uma escrita pela metade.
