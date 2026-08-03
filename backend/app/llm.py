import asyncio
import logging

import httpx

from .config import settings

logger = logging.getLogger("traduz.llm")

SYSTEM_PROMPT = (
    "You are a professional literary translator. Translate the user's text from "
    "{source_lang} to {target_lang}. Preserve the original meaning, tone and register. "
    "Do not summarize, explain, or add any commentary. Do not add quotation marks that "
    "were not in the original. Return ONLY the translated text, nothing else."
).format(source_lang=settings.source_lang, target_lang=settings.target_lang)


class TranslationError(RuntimeError):
    pass


async def translate_text(client: httpx.AsyncClient, text: str) -> str:
    """Translate a single chunk of text using the local llama-server."""
    if not text or not text.strip():
        return text

    payload = {
        "model": settings.llama_model_name,
        "temperature": settings.llm_temperature,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }

    last_error: Exception | None = None
    for attempt in range(1, settings.llm_max_retries + 1):
        try:
            response = await client.post(
                settings.llama_server_url,
                json=payload,
                timeout=settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            translated = data["choices"][0]["message"]["content"]
            return translated.strip()
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            last_error = exc
            logger.warning(
                "Translation attempt %s/%s failed: %s",
                attempt,
                settings.llm_max_retries,
                exc,
            )
            if attempt < settings.llm_max_retries:
                await asyncio.sleep(1.5 * attempt)

    raise TranslationError(
        f"Falha ao traduzir texto após {settings.llm_max_retries} tentativas: {last_error}"
    )


async def translate_many(texts: list[str], on_progress=None, on_item_done=None) -> list[str]:
    """Translate a list of text chunks respecting configured concurrency.

    Order of results matches order of input. `on_progress(done, total)` is
    called after each chunk finishes. `on_item_done(index, translated_text)`,
    if given, is called right when that item's result is known - sync or
    async (awaited if it returns a coroutine). This module has no idea what
    the caller does with it (jobs.py uses it to persist progress and feed a
    translation cache); llm.py stays free of DB/cache concerns.
    """
    results: list[str | None] = [None] * len(texts)
    total = len(texts)
    done = 0
    semaphore = asyncio.Semaphore(max(1, settings.translation_concurrency))

    async with httpx.AsyncClient() as client:

        async def worker(index: int, chunk: str):
            nonlocal done
            async with semaphore:
                translated = await translate_text(client, chunk)
            results[index] = translated
            done += 1
            if on_item_done:
                maybe_awaitable = on_item_done(index, translated)
                if asyncio.iscoroutine(maybe_awaitable):
                    await maybe_awaitable
            if on_progress:
                on_progress(done, total)

        await asyncio.gather(*(worker(i, t) for i, t in enumerate(texts)))

    return [r if r is not None else "" for r in results]
