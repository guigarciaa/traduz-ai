import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import Job

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "traduz.db"


@contextmanager
def _connection():
    """One short-lived connection per call, not a shared/global connection.

    Safer under asyncio.to_thread than a connection reused across threads,
    and cheap enough (local file, WAL mode) that per-call overhead is
    irrelevant next to the 7-9s LLM latency each unit's translation costs.
    WAL + busy_timeout are set here (not just once in init_db()) because
    busy_timeout is a per-connection pragma - without it here, a second
    concurrent writer would raise "database is locked" immediately instead
    of waiting and retrying.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                input_format TEXT NOT NULL,
                output_format TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                warning_message TEXT,
                output_path TEXT,
                dir TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resume_attempts INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_units (
                job_id TEXT NOT NULL REFERENCES jobs(id),
                unit_index INTEGER NOT NULL,
                source_text TEXT NOT NULL,
                translated_text TEXT,
                PRIMARY KEY (job_id, unit_index)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translation_cache (
                text_hash TEXT PRIMARY KEY,
                source_text TEXT NOT NULL,
                translated_text TEXT NOT NULL
            )
            """
        )


def save_job(job: Job) -> None:
    with _connection() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, original_filename, input_format, output_format, status,
                               error_message, warning_message, output_path, dir,
                               created_at, updated_at, resume_attempts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                error_message = excluded.error_message,
                warning_message = excluded.warning_message,
                output_path = excluded.output_path,
                updated_at = excluded.updated_at,
                resume_attempts = excluded.resume_attempts
            """,
            (
                job.id,
                job.original_filename,
                job.input_format,
                job.output_format,
                job.status,
                job.error_message,
                job.warning_message,
                str(job.output_path) if job.output_path else None,
                str(job.dir),
                job.created_at,
                job.updated_at,
                job.resume_attempts,
            ),
        )


def _row_to_job(row: sqlite3.Row, progress_done: int, progress_total: int) -> Job:
    return Job(
        id=row["id"],
        original_filename=row["original_filename"],
        input_format=row["input_format"],
        output_format=row["output_format"],
        status=row["status"],
        progress_done=progress_done,
        progress_total=progress_total,
        error_message=row["error_message"],
        warning_message=row["warning_message"],
        output_path=Path(row["output_path"]) if row["output_path"] else None,
        dir=Path(row["dir"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        resume_attempts=row["resume_attempts"],
    )


def load_all_jobs() -> list[Job]:
    """Progress is recomputed from job_units on every load rather than
    trusted from a stored column, so it's always accurate even if the
    process died mid-job without a final progress write."""
    with _connection() as conn:
        job_rows = conn.execute("SELECT * FROM jobs").fetchall()
        jobs = []
        for row in job_rows:
            counts = conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN translated_text IS NOT NULL THEN 1 ELSE 0 END) AS done "
                "FROM job_units WHERE job_id = ?",
                (row["id"],),
            ).fetchone()
            total = counts["total"] or 0
            done = counts["done"] or 0
            jobs.append(_row_to_job(row, done, total))
        return jobs


def save_job_units(job_id: str, units: list[str]) -> None:
    """INSERT OR IGNORE: safe to call again on resume without clobbering
    already-translated rows."""
    with _connection() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO job_units (job_id, unit_index, source_text) VALUES (?, ?, ?)",
            [(job_id, i, unit) for i, unit in enumerate(units)],
        )


def load_job_units(job_id: str) -> dict[int, str | None]:
    with _connection() as conn:
        rows = conn.execute(
            "SELECT unit_index, translated_text FROM job_units WHERE job_id = ? "
            "ORDER BY unit_index",
            (job_id,),
        ).fetchall()
        return {row["unit_index"]: row["translated_text"] for row in rows}


def save_unit_translation(job_id: str, index: int, translated_text: str) -> None:
    with _connection() as conn:
        conn.execute(
            "UPDATE job_units SET translated_text = ? WHERE job_id = ? AND unit_index = ?",
            (translated_text, job_id, index),
        )


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_cached_translation(text_hash: str) -> str | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT translated_text FROM translation_cache WHERE text_hash = ?",
            (text_hash,),
        ).fetchone()
        return row["translated_text"] if row else None


def save_cached_translation(text_hash: str, source_text: str, translated_text: str) -> None:
    with _connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO translation_cache (text_hash, source_text, translated_text) "
            "VALUES (?, ?, ?)",
            (text_hash, source_text, translated_text),
        )
