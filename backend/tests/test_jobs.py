import asyncio
import subprocess

import pytest

from app import db
from app import jobs as jobs_module


@pytest.fixture(autouse=True)
def _isolated_jobs_dir(tmp_path, monkeypatch):
    """Never touch the real project's data/jobs/, and never leak Job/task
    state between tests (JOBS and RUNNING_TASKS are module-level dicts)."""
    monkeypatch.setattr(jobs_module, "DATA_DIR", tmp_path)
    jobs_module.JOBS.clear()
    jobs_module.RUNNING_TASKS.clear()
    yield
    jobs_module.JOBS.clear()
    jobs_module.RUNNING_TASKS.clear()


@pytest.fixture
def fake_translate_many(monkeypatch):
    """Stands in for llm.translate_many with the same on_item_done/
    on_progress contract, so run_job's resume/cache bookkeeping (which reads
    results back out through on_item_done, not the return value) works the
    same as it does against the real implementation."""
    calls = []

    async def _fake(texts, on_progress=None, on_item_done=None):
        calls.append(list(texts))
        results = []
        for i, t in enumerate(texts):
            translated = f"[PT] {t}"
            results.append(translated)
            if on_item_done:
                maybe_awaitable = on_item_done(i, translated)
                if asyncio.iscoroutine(maybe_awaitable):
                    await maybe_awaitable
            if on_progress:
                on_progress(i + 1, len(texts))
        return results

    monkeypatch.setattr(jobs_module, "translate_many", _fake)
    return calls


# ---------------------------------------------------------------------------
# _detect_format / create_job
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename, expected",
    [("book.txt", "txt"), ("book.epub", "epub"), ("book.pdf", "pdf"), ("BOOK.PDF", "pdf")],
)
def test_detect_format_supported(filename, expected):
    assert jobs_module._detect_format(filename) == expected


def test_detect_format_unsupported_raises():
    with pytest.raises(ValueError, match="não suportado"):
        jobs_module._detect_format("book.docx")


def test_create_job_writes_input_file_and_defaults_output_format():
    job = jobs_module.create_job("book.txt", b"Hello world.", None)

    assert job.input_format == "txt"
    assert job.output_format == "txt"
    assert job.status == "pending"
    assert (job.dir / "input.txt").read_bytes() == b"Hello world."
    assert jobs_module.get_job(job.id) is job


def test_create_job_with_explicit_different_output_format():
    job = jobs_module.create_job("book.epub", b"fake epub bytes", "pdf")
    assert job.input_format == "epub"
    assert job.output_format == "pdf"


def test_create_job_rejects_unsupported_input_format():
    with pytest.raises(ValueError):
        jobs_module.create_job("book.docx", b"data", None)


def test_create_job_rejects_unsupported_output_format():
    with pytest.raises(ValueError, match="saída"):
        jobs_module.create_job("book.txt", b"data", "docx")


def test_get_job_returns_none_for_unknown_id():
    assert jobs_module.get_job("does-not-exist") is None


# ---------------------------------------------------------------------------
# _convert_with_calibre
# ---------------------------------------------------------------------------


def test_convert_with_calibre_returns_false_when_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs_module.shutil, "which", lambda _name: None)
    ok = jobs_module._convert_with_calibre(tmp_path / "in.epub", tmp_path / "out.pdf")
    assert ok is False


def test_convert_with_calibre_success(monkeypatch, tmp_path):
    dst = tmp_path / "out.pdf"

    def _fake_run(cmd, check, capture_output, timeout):
        dst.write_bytes(b"fake pdf output")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(jobs_module.shutil, "which", lambda _name: "/usr/bin/ebook-convert")
    monkeypatch.setattr(jobs_module.subprocess, "run", _fake_run)

    ok = jobs_module._convert_with_calibre(tmp_path / "in.epub", dst)

    assert ok is True
    assert dst.exists()


def test_convert_with_calibre_false_when_process_fails(monkeypatch, tmp_path):
    def _fake_run(cmd, check, capture_output, timeout):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(jobs_module.shutil, "which", lambda _name: "/usr/bin/ebook-convert")
    monkeypatch.setattr(jobs_module.subprocess, "run", _fake_run)

    ok = jobs_module._convert_with_calibre(tmp_path / "in.epub", tmp_path / "out.pdf")

    assert ok is False


def test_convert_with_calibre_false_on_timeout(monkeypatch, tmp_path):
    def _fake_run(cmd, check, capture_output, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(jobs_module.shutil, "which", lambda _name: "/usr/bin/ebook-convert")
    monkeypatch.setattr(jobs_module.subprocess, "run", _fake_run)

    ok = jobs_module._convert_with_calibre(tmp_path / "in.epub", tmp_path / "out.pdf")

    assert ok is False


# ---------------------------------------------------------------------------
# run_job
# ---------------------------------------------------------------------------


async def test_run_job_same_format_marks_done_and_sets_output(fake_translate_many):
    job = jobs_module.create_job("book.txt", b"First.\n\nSecond.", None)

    await jobs_module.run_job(job.id)

    assert job.status == "done"
    assert job.error_message is None
    assert job.output_path == job.dir / "translated.txt"
    assert job.output_path.read_text(encoding="utf-8") == "[PT] First.\n\n[PT] Second."
    assert job.progress_total == 2
    assert job.progress_done == 2


async def test_run_job_progress_updates_during_translation(fake_translate_many):
    job = jobs_module.create_job("book.txt", b"A.\n\nB.\n\nC.", None)

    await jobs_module.run_job(job.id)

    assert job.progress_total == 3
    assert job.progress_done == 3


async def test_run_job_output_format_txt_from_different_input(fake_translate_many, make_epub):
    epub_path = make_epub()
    with open(epub_path, "rb") as f:
        epub_bytes = f.read()
    job = jobs_module.create_job("book.epub", epub_bytes, "txt")

    await jobs_module.run_job(job.id)

    assert job.status == "done"
    assert job.output_path == job.dir / "translated.txt"
    content = job.output_path.read_text(encoding="utf-8")
    assert content.startswith("[PT] ")
    # the native (epub) rebuild still happens first, even though it's unused
    assert (job.dir / "translated.epub").exists()


async def test_run_job_converts_with_calibre_when_available(fake_translate_many, monkeypatch):
    job = jobs_module.create_job("book.txt", b"Hello.", "epub")

    def _fake_convert(src_path, dst_path):
        dst_path.write_bytes(b"fake epub")
        return True

    monkeypatch.setattr(jobs_module, "_convert_with_calibre", _fake_convert)

    await jobs_module.run_job(job.id)

    assert job.status == "done"
    assert job.warning_message is None
    assert job.output_path == job.dir / "translated.epub"
    assert job.output_path.read_bytes() == b"fake epub"


async def test_run_job_falls_back_with_warning_when_calibre_unavailable(fake_translate_many, monkeypatch):
    job = jobs_module.create_job("book.txt", b"Hello.", "epub")

    monkeypatch.setattr(jobs_module, "_convert_with_calibre", lambda src, dst: False)

    await jobs_module.run_job(job.id)

    assert job.status == "done"
    assert job.warning_message is not None
    assert "Calibre" in job.warning_message
    assert job.output_path == job.dir / "translated.txt"


async def test_run_job_sets_error_status_on_exception(monkeypatch):
    job = jobs_module.create_job("book.txt", b"Hello.", None)

    async def _boom(texts, on_progress=None, on_item_done=None):
        raise RuntimeError("llama-server unreachable")

    monkeypatch.setattr(jobs_module, "translate_many", _boom)

    await jobs_module.run_job(job.id)

    assert job.status == "error"
    assert "llama-server unreachable" in job.error_message
    assert job.output_path is None


async def test_run_job_error_when_input_file_missing(fake_translate_many):
    job = jobs_module.create_job("book.txt", b"Hello.", None)
    (job.dir / "input.txt").unlink()

    await jobs_module.run_job(job.id)

    assert job.status == "error"
    assert job.error_message


# ---------------------------------------------------------------------------
# start_job
# ---------------------------------------------------------------------------


async def test_start_job_schedules_run_job_as_background_task(monkeypatch):
    job = jobs_module.create_job("book.txt", b"Hello.", None)

    ran_with = {}

    async def _fake_run_job(job_id):
        ran_with["id"] = job_id

    monkeypatch.setattr(jobs_module, "run_job", _fake_run_job)

    jobs_module.start_job(job.id)
    await asyncio.sleep(0)  # yield control so the scheduled task actually runs

    assert ran_with["id"] == job.id


async def test_start_job_does_not_schedule_a_second_task_for_the_same_job(monkeypatch):
    job = jobs_module.create_job("book.txt", b"Hello.", None)
    call_count = 0

    async def _hang(job_id):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(3600)

    monkeypatch.setattr(jobs_module, "run_job", _hang)

    jobs_module.start_job(job.id)
    await asyncio.sleep(0)
    first_task = jobs_module.RUNNING_TASKS[job.id]

    jobs_module.start_job(job.id)  # must be a no-op: a task is already running
    await asyncio.sleep(0)

    assert jobs_module.RUNNING_TASKS[job.id] is first_task
    assert call_count == 1

    first_task.cancel()
    await asyncio.sleep(0)


async def test_start_job_schedules_a_new_task_once_the_previous_one_finished(monkeypatch):
    job = jobs_module.create_job("book.txt", b"Hello.", None)
    call_count = 0

    async def _instant(job_id):
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr(jobs_module, "run_job", _instant)

    jobs_module.start_job(job.id)
    # done_callback fires via call_soon on a later loop iteration, not
    # necessarily within a single sleep(0) - give it a couple of ticks.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert job.id not in jobs_module.RUNNING_TASKS

    jobs_module.start_job(job.id)
    await asyncio.sleep(0)

    assert call_count == 2


# ---------------------------------------------------------------------------
# Resume after a crash: unfinished job_units are reused, not retranslated
# ---------------------------------------------------------------------------


async def test_run_job_resumes_partial_translation_without_recalling_llm(fake_translate_many):
    job = jobs_module.create_job("book.txt", b"First.\n\nSecond.\n\nThird.", None)
    # Simulate a previous run that died after translating only the first unit.
    db.save_job_units(job.id, ["First.", "Second.", "Third."])
    db.save_unit_translation(job.id, 0, "[PT] First.")

    await jobs_module.run_job(job.id)

    assert job.status == "done"
    assert fake_translate_many == [["Second.", "Third."]]  # unit 0 never resent
    assert job.output_path.read_text(encoding="utf-8") == "[PT] First.\n\n[PT] Second.\n\n[PT] Third."


async def test_run_job_progress_accounts_for_already_translated_units(fake_translate_many):
    job = jobs_module.create_job("book.txt", b"First.\n\nSecond.", None)
    db.save_job_units(job.id, ["First.", "Second."])
    db.save_unit_translation(job.id, 0, "[PT] First.")

    await jobs_module.run_job(job.id)

    assert job.progress_done == 2
    assert job.progress_total == 2


# ---------------------------------------------------------------------------
# Global translation cache
# ---------------------------------------------------------------------------


async def test_run_job_uses_cached_translation_instead_of_calling_llm(fake_translate_many):
    db.save_cached_translation(
        db.hash_text("Cached paragraph."), "Cached paragraph.", "[CACHE] Parágrafo em cache."
    )

    job = jobs_module.create_job("book.txt", b"Cached paragraph.\n\nNew paragraph.", None)
    await jobs_module.run_job(job.id)

    assert job.status == "done"
    assert fake_translate_many == [["New paragraph."]]
    assert (
        job.output_path.read_text(encoding="utf-8")
        == "[CACHE] Parágrafo em cache.\n\n[PT] New paragraph."
    )


async def test_run_job_writes_new_translations_into_the_cache(fake_translate_many):
    job = jobs_module.create_job("book.txt", b"Brand new text.", None)

    await jobs_module.run_job(job.id)

    cached = db.get_cached_translation(db.hash_text("Brand new text."))
    assert cached == "[PT] Brand new text."


async def test_run_job_cache_is_shared_across_different_jobs(fake_translate_many):
    job1 = jobs_module.create_job("book1.txt", b"Shared header text.", None)
    await jobs_module.run_job(job1.id)
    assert fake_translate_many == [["Shared header text."]]

    job2 = jobs_module.create_job("book2.txt", b"Shared header text.", None)
    await jobs_module.run_job(job2.id)

    # job2 must not have triggered a second LLM call for identical text
    assert fake_translate_many == [["Shared header text."]]
    assert job2.output_path.read_text(encoding="utf-8") == "[PT] Shared header text."


# ---------------------------------------------------------------------------
# cancel_job
# ---------------------------------------------------------------------------


async def test_cancel_job_marks_status_cancelled_and_stops_the_task(monkeypatch):
    job = jobs_module.create_job("book.txt", b"Hello.", None)
    started = asyncio.Event()

    async def _hang(texts, on_progress=None, on_item_done=None):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(jobs_module, "translate_many", _hang)

    jobs_module.start_job(job.id)
    await started.wait()

    assert jobs_module.cancel_job(job.id) is True
    await asyncio.sleep(0.05)  # let the cancelled task run its except-clause

    assert job.status == "cancelled"
    assert job.id not in jobs_module.RUNNING_TASKS


def test_cancel_job_returns_false_for_unknown_job():
    assert jobs_module.cancel_job("does-not-exist") is False


async def test_cancel_job_returns_false_once_job_already_finished(fake_translate_many):
    job = jobs_module.create_job("book.txt", b"Hello.", None)
    await jobs_module.run_job(job.id)  # ran directly, never entered RUNNING_TASKS

    assert jobs_module.cancel_job(job.id) is False


# ---------------------------------------------------------------------------
# list_jobs
# ---------------------------------------------------------------------------


def test_list_jobs_sorted_newest_first():
    job1 = jobs_module.create_job("a.txt", b"1", None)
    job1.created_at = "2026-01-01T00:00:00+00:00"
    job2 = jobs_module.create_job("b.txt", b"2", None)
    job2.created_at = "2026-01-02T00:00:00+00:00"

    result = jobs_module.list_jobs()

    assert [j.id for j in result] == [job2.id, job1.id]


def test_list_jobs_empty():
    assert jobs_module.list_jobs() == []


# ---------------------------------------------------------------------------
# load_jobs_from_db: startup auto-resume
# ---------------------------------------------------------------------------


async def test_load_jobs_from_db_auto_resumes_non_terminal_job(fake_translate_many):
    job = jobs_module.create_job("book.txt", b"Hello.", None)
    job.status = "translating"
    db.save_job(job)

    jobs_module.JOBS.clear()
    jobs_module.RUNNING_TASKS.clear()

    jobs_module.load_jobs_from_db()

    assert jobs_module.JOBS[job.id].resume_attempts == 1
    await asyncio.sleep(0.05)  # let the auto-resumed task actually finish
    assert jobs_module.JOBS[job.id].status == "done"


def test_load_jobs_from_db_does_not_resume_terminal_jobs(monkeypatch):
    job = jobs_module.create_job("book.txt", b"Hello.", None)
    job.status = "done"
    db.save_job(job)

    jobs_module.JOBS.clear()
    jobs_module.RUNNING_TASKS.clear()
    resumed = []
    monkeypatch.setattr(jobs_module, "start_job", lambda job_id: resumed.append(job_id))

    jobs_module.load_jobs_from_db()

    assert resumed == []
    assert jobs_module.JOBS[job.id].status == "done"
    assert jobs_module.JOBS[job.id].resume_attempts == 0


def test_load_jobs_from_db_gives_up_after_max_resume_attempts(monkeypatch):
    job = jobs_module.create_job("book.txt", b"Hello.", None)
    job.status = "translating"
    job.resume_attempts = jobs_module.MAX_RESUME_ATTEMPTS
    db.save_job(job)

    jobs_module.JOBS.clear()
    jobs_module.RUNNING_TASKS.clear()
    resumed = []
    monkeypatch.setattr(jobs_module, "start_job", lambda job_id: resumed.append(job_id))

    jobs_module.load_jobs_from_db()

    assert resumed == []
    assert jobs_module.JOBS[job.id].status == "error"
    assert "tentativas de retomada" in jobs_module.JOBS[job.id].error_message
