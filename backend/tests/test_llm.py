import httpx
import pytest
import respx

from app.config import settings
from app.llm import TranslationError, translate_many, translate_text


def _chat_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Retries in llm.py sleep for real between attempts; make that instant
    so retry-path tests don't slow down the suite."""

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.llm.asyncio.sleep", _no_sleep)


async def test_translate_text_returns_input_unchanged_for_empty_string():
    async with httpx.AsyncClient() as client:
        result = await translate_text(client, "")
    assert result == ""


async def test_translate_text_returns_input_unchanged_for_whitespace_only():
    async with httpx.AsyncClient() as client:
        result = await translate_text(client, "   \n\t  ")
    assert result == "   \n\t  "


@respx.mock
async def test_translate_text_success_strips_whitespace_from_response():
    route = respx.post(settings.llama_server_url).mock(
        return_value=httpx.Response(200, json=_chat_response("  Olá mundo.  "))
    )

    async with httpx.AsyncClient() as client:
        result = await translate_text(client, "Hello world.")

    assert result == "Olá mundo."
    assert route.called


@respx.mock
async def test_translate_text_sends_system_and_user_messages():
    route = respx.post(settings.llama_server_url).mock(
        return_value=httpx.Response(200, json=_chat_response("traduzido"))
    )

    async with httpx.AsyncClient() as client:
        await translate_text(client, "original text")

    sent_body = route.calls.last.request.content
    import json

    payload = json.loads(sent_body)
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1] == {"role": "user", "content": "original text"}
    assert payload["model"] == settings.llama_model_name


@respx.mock
async def test_translate_text_retries_on_failure_then_succeeds():
    route = respx.post(settings.llama_server_url).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json=_chat_response("sucesso na segunda tentativa")),
        ]
    )

    async with httpx.AsyncClient() as client:
        result = await translate_text(client, "text")

    assert result == "sucesso na segunda tentativa"
    assert route.call_count == 2


@respx.mock
async def test_translate_text_raises_translation_error_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_retries", 2)
    route = respx.post(settings.llama_server_url).mock(return_value=httpx.Response(500))

    async with httpx.AsyncClient() as client:
        with pytest.raises(TranslationError):
            await translate_text(client, "text")

    assert route.call_count == 2


@respx.mock
async def test_translate_text_raises_on_malformed_response_shape(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_retries", 1)
    respx.post(settings.llama_server_url).mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(TranslationError):
            await translate_text(client, "text")


@respx.mock
async def test_translate_many_preserves_order_despite_concurrency(monkeypatch):
    monkeypatch.setattr(settings, "translation_concurrency", 4)

    def _responder(request):
        import json

        sent = json.loads(request.content)
        original = sent["messages"][1]["content"]
        return httpx.Response(200, json=_chat_response(f"[PT] {original}"))

    respx.post(settings.llama_server_url).mock(side_effect=_responder)

    texts = [f"sentence {i}" for i in range(10)]
    results = await translate_many(texts)

    assert results == [f"[PT] sentence {i}" for i in range(10)]


@respx.mock
async def test_translate_many_calls_on_progress_for_every_item():
    respx.post(settings.llama_server_url).mock(
        return_value=httpx.Response(200, json=_chat_response("ok"))
    )

    progress_calls = []
    await translate_many(["a", "b", "c"], on_progress=lambda done, total: progress_calls.append((done, total)))

    assert len(progress_calls) == 3
    assert progress_calls[-1] == (3, 3)
    assert all(total == 3 for _, total in progress_calls)


async def test_translate_many_empty_list_returns_empty_list():
    assert await translate_many([]) == []


@respx.mock
async def test_translate_many_skips_http_call_for_blank_entries():
    route = respx.post(settings.llama_server_url).mock(
        return_value=httpx.Response(200, json=_chat_response("traduzido"))
    )

    results = await translate_many(["", "real text", "   "])

    assert results == ["", "traduzido", "   "]
    assert route.call_count == 1
