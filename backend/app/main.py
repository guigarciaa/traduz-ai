import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import jobs

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Traduz AI")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"


@app.post("/api/jobs")
async def create_job(file: UploadFile = File(...), output_format: str | None = Form(None)):
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")

    try:
        job = jobs.create_job(file.filename, file_bytes, output_format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    jobs.start_job(job.id)
    return _job_to_dict(job)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    return _job_to_dict(job)


@app.get("/api/jobs/{job_id}/download")
async def download_job(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado.")
    if job.status != "done" or job.output_path is None:
        raise HTTPException(status_code=409, detail="Tradução ainda não concluída.")

    stem = Path(job.original_filename).stem
    download_name = f"{stem}_pt-br.{job.output_format}"
    return FileResponse(job.output_path, filename=download_name)


def _job_to_dict(job: jobs.Job) -> dict:
    return {
        "id": job.id,
        "original_filename": job.original_filename,
        "input_format": job.input_format,
        "output_format": job.output_format,
        "status": job.status,
        "progress_done": job.progress_done,
        "progress_total": job.progress_total,
        "error_message": job.error_message,
        "warning_message": job.warning_message,
    }


# Serve the static frontend last so it doesn't shadow the /api routes above.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
