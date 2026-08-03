import asyncio
import subprocess

import pytest

from app import jobs as jobs_module


@pytest.fixture(autouse=True)
def _isolated_jobs_dir(tmp_path, monkeypatch):
    """Never touch the real project's data/jobs/, and never leak Job objects
    between tests (JOBS is a module-level dict)."""
    monkeypatch.setattr(jobs_module, "DATA_DIR", tmp_path)
    jobs_module.JOBS.clear()
    yield
    jobs_module.JOBS.clear()


@pytest.fixture
def fake_translate_many(monkeypatch):
    calls = []

    async def _fake(texts, on_progress=None):
        calls.append(list(texts))
        if on_progress:
            for i in range(1, len(texts) + 1):
                on_progress(i, len(texts))
        return [f"[PT] {t}" for t in texts]

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

    async def _boom(texts, on_progress=None):
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
