import asyncio
import logging
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .extractors.epub import EpubDocument
from .extractors.pdf import PdfDocument
from .extractors.txt import TxtDocument
from .llm import translate_many
from .models import Job

logger = logging.getLogger("traduz.jobs")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data" / "jobs"
DATA_DIR.mkdir(parents=True, exist_ok=True)

EXTRACTORS = {
    "txt": TxtDocument,
    "epub": EpubDocument,
    "pdf": PdfDocument,
}

SUPPORTED_FORMATS = set(EXTRACTORS.keys())

TERMINAL_STATUSES = {"done", "error", "cancelled"}
MAX_RESUME_ATTEMPTS = 3

JOBS: dict[str, Job] = {}
RUNNING_TASKS: dict[str, asyncio.Task] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_format(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in SUPPORTED_FORMATS:
        raise ValueError(f"Formato não suportado: .{ext}")
    return ext


def create_job(filename: str, file_bytes: bytes, output_format: str | None) -> Job:
    input_format = _detect_format(filename)
    out_format = (output_format or input_format).lower()
    if out_format not in SUPPORTED_FORMATS:
        raise ValueError(f"Formato de saída não suportado: {out_format}")

    job_id = uuid.uuid4().hex
    job_dir = DATA_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    input_path = job_dir / f"input.{input_format}"
    input_path.write_bytes(file_bytes)

    now = _now()
    job = Job(
        id=job_id,
        original_filename=filename,
        input_format=input_format,
        output_format=out_format,
        dir=job_dir,
        created_at=now,
        updated_at=now,
    )
    JOBS[job_id] = job
    # Persisted synchronously (not asyncio.to_thread): a single small INSERT
    # on a local file, and this runs outside run_job's long-lived task - we
    # want the job to survive a crash even if it dies before run_job starts.
    db.save_job(job)
    return job


def get_job(job_id: str) -> Job | None:
    return JOBS.get(job_id)


def list_jobs() -> list[Job]:
    return sorted(JOBS.values(), key=lambda j: j.created_at, reverse=True)


def _convert_with_calibre(src_path: Path, dst_path: Path) -> bool:
    if shutil.which("ebook-convert") is None:
        return False
    try:
        subprocess.run(
            ["ebook-convert", str(src_path), str(dst_path)],
            check=True,
            capture_output=True,
            timeout=600,
        )
        return dst_path.exists()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning("Falha na conversão via Calibre: %s", exc)
        return False


async def run_job(job_id: str) -> None:
    job = JOBS[job_id]
    try:
        input_path = job.dir / f"input.{job.input_format}"

        extractor_cls = EXTRACTORS[job.input_format]
        doc = extractor_cls.load(str(input_path))

        job.progress_total = len(doc.units)
        job.status = "translating"
        job.updated_at = _now()
        await asyncio.to_thread(db.save_job, job)
        await asyncio.to_thread(db.save_job_units, job_id, doc.units)

        # Resolve what's already known before calling the LLM for anything:
        # units this exact job already translated (resume after a crash) and
        # units whose exact text was already translated by *any* job (the
        # global cache) both skip the network call entirely.
        existing = await asyncio.to_thread(db.load_job_units, job_id)
        translations: list[str | None] = [None] * len(doc.units)
        pending_indices: list[int] = []
        pending_texts: list[str] = []
        for i, unit in enumerate(doc.units):
            prior = existing.get(i)
            if prior is not None:
                translations[i] = prior
                continue
            cached = await asyncio.to_thread(db.get_cached_translation, db.hash_text(unit))
            if cached is not None:
                translations[i] = cached
                await asyncio.to_thread(db.save_unit_translation, job_id, i, cached)
            else:
                pending_indices.append(i)
                pending_texts.append(unit)

        already_done = len(doc.units) - len(pending_indices)
        job.progress_done = already_done

        def on_progress(done: int, total: int) -> None:
            job.progress_done = already_done + done
            job.progress_total = len(doc.units)

        async def on_item_done(local_index: int, text: str) -> None:
            real_index = pending_indices[local_index]
            translations[real_index] = text
            source_text = pending_texts[local_index]
            await asyncio.to_thread(db.save_unit_translation, job_id, real_index, text)
            await asyncio.to_thread(
                db.save_cached_translation, db.hash_text(source_text), source_text, text
            )

        if pending_texts:
            await translate_many(pending_texts, on_progress=on_progress, on_item_done=on_item_done)

        job.status = "rebuilding"
        job.updated_at = _now()
        await asyncio.to_thread(db.save_job, job)
        native_output_path = job.dir / f"translated.{job.input_format}"
        doc.rebuild(translations, str(native_output_path))

        if job.output_format == job.input_format:
            job.output_path = native_output_path
        elif job.output_format == "txt":
            txt_path = job.dir / "translated.txt"
            txt_path.write_text("\n\n".join(translations), encoding="utf-8")
            job.output_path = txt_path
        else:
            job.status = "converting"
            job.updated_at = _now()
            await asyncio.to_thread(db.save_job, job)
            final_path = job.dir / f"translated.{job.output_format}"
            ok = await asyncio.to_thread(_convert_with_calibre, native_output_path, final_path)
            if ok:
                job.output_path = final_path
            else:
                job.output_path = native_output_path
                job.warning_message = (
                    f"Não foi possível converter para .{job.output_format} "
                    "(Calibre/ebook-convert não encontrado ou falhou). "
                    f"Entregando o arquivo traduzido em .{job.input_format}."
                )

        job.status = "done"
        job.updated_at = _now()
        await asyncio.to_thread(db.save_job, job)
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.updated_at = _now()
        await asyncio.to_thread(db.save_job, job)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s falhou", job_id)
        job.status = "error"
        job.error_message = str(exc)
        job.updated_at = _now()
        await asyncio.to_thread(db.save_job, job)


def start_job(job_id: str) -> None:
    existing_task = RUNNING_TASKS.get(job_id)
    if existing_task is not None and not existing_task.done():
        return  # already running - never schedule a second task for the same job

    task = asyncio.create_task(run_job(job_id))
    RUNNING_TASKS[job_id] = task
    task.add_done_callback(lambda _t: RUNNING_TASKS.pop(job_id, None))


def cancel_job(job_id: str) -> bool:
    task = RUNNING_TASKS.get(job_id)
    if task is not None and not task.done():
        task.cancel()
        return True
    return False


def load_jobs_from_db() -> None:
    """Called once at startup: repopulates JOBS from disk and automatically
    resumes any job that was left mid-flight by a previous process exit."""
    for job in db.load_all_jobs():
        JOBS[job.id] = job
        if job.status in TERMINAL_STATUSES:
            continue

        if job.resume_attempts >= MAX_RESUME_ATTEMPTS:
            job.status = "error"
            job.error_message = (
                f"Job abandonado após {job.resume_attempts} tentativas de retomada "
                "malsucedidas (o processo reiniciou repetidamente com este job em "
                "andamento)."
            )
            job.updated_at = _now()
            db.save_job(job)
            continue

        job.resume_attempts += 1
        job.updated_at = _now()
        db.save_job(job)
        start_job(job.id)
