import asyncio
import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .extractors.epub import EpubDocument
from .extractors.pdf import PdfDocument
from .extractors.txt import TxtDocument
from .llm import translate_many

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


@dataclass
class Job:
    id: str
    original_filename: str
    input_format: str
    output_format: str
    status: str = "pending"  # pending, translating, rebuilding, converting, done, error
    progress_done: int = 0
    progress_total: int = 0
    error_message: str | None = None
    warning_message: str | None = None
    output_path: Path | None = None
    dir: Path = field(default=None)


JOBS: dict[str, Job] = {}


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

    job = Job(
        id=job_id,
        original_filename=filename,
        input_format=input_format,
        output_format=out_format,
        dir=job_dir,
    )
    JOBS[job_id] = job
    return job


def get_job(job_id: str) -> Job | None:
    return JOBS.get(job_id)


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
        job.progress_done = 0
        job.status = "translating"

        def on_progress(done: int, total: int) -> None:
            job.progress_done = done
            job.progress_total = total

        translations = await translate_many(doc.units, on_progress=on_progress)

        job.status = "rebuilding"
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
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s falhou", job_id)
        job.status = "error"
        job.error_message = str(exc)


def start_job(job_id: str) -> None:
    asyncio.create_task(run_job(job_id))
