from pathlib import Path

from app import db
from app.models import Job

# The _isolated_db autouse fixture in conftest.py already points db.DB_PATH
# at a fresh tmp_path file and calls init_db() before every test.


def _make_job(job_id="job-1", status="pending", resume_attempts=0) -> Job:
    return Job(
        id=job_id,
        original_filename="book.txt",
        input_format="txt",
        output_format="txt",
        status=status,
        dir=Path("/tmp/does-not-matter"),
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        resume_attempts=resume_attempts,
    )


def test_save_and_load_job_roundtrip():
    job = _make_job()
    db.save_job(job)

    loaded = db.load_all_jobs()

    assert len(loaded) == 1
    assert loaded[0].id == job.id
    assert loaded[0].status == "pending"
    assert loaded[0].progress_done == 0
    assert loaded[0].progress_total == 0


def test_save_job_upserts_on_conflict():
    job = _make_job(status="pending")
    db.save_job(job)

    job.status = "done"
    job.error_message = None
    db.save_job(job)

    loaded = db.load_all_jobs()
    assert len(loaded) == 1
    assert loaded[0].status == "done"


def test_load_all_jobs_recomputes_progress_from_units():
    job = _make_job()
    db.save_job(job)
    db.save_job_units(job.id, ["unit0", "unit1", "unit2"])
    db.save_unit_translation(job.id, 0, "traduzido0")
    db.save_unit_translation(job.id, 1, "traduzido1")

    loaded = db.load_all_jobs()[0]

    assert loaded.progress_done == 2
    assert loaded.progress_total == 3


def test_load_all_jobs_empty_db_returns_empty_list():
    assert db.load_all_jobs() == []


def test_save_job_units_is_idempotent():
    job = _make_job()
    db.save_job(job)
    db.save_job_units(job.id, ["a", "b"])
    db.save_unit_translation(job.id, 0, "traduzido-a")

    # calling again (as happens on resume) must not clobber the existing
    # translation for index 0
    db.save_job_units(job.id, ["a", "b"])

    units = db.load_job_units(job.id)
    assert units == {0: "traduzido-a", 1: None}


def test_load_job_units_for_unknown_job_is_empty():
    assert db.load_job_units("does-not-exist") == {}


def test_save_unit_translation_updates_only_target_index():
    job = _make_job()
    db.save_job(job)
    db.save_job_units(job.id, ["a", "b", "c"])

    db.save_unit_translation(job.id, 1, "traduzido-b")

    units = db.load_job_units(job.id)
    assert units == {0: None, 1: "traduzido-b", 2: None}


def test_hash_text_is_deterministic_and_sensitive_to_content():
    h1 = db.hash_text("Hello world.")
    h2 = db.hash_text("Hello world.")
    h3 = db.hash_text("Hello World.")  # different case
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64  # sha256 hex digest


def test_translation_cache_miss_then_hit():
    text_hash = db.hash_text("Repeated header text")
    assert db.get_cached_translation(text_hash) is None

    db.save_cached_translation(text_hash, "Repeated header text", "Texto de cabeçalho repetido")

    assert db.get_cached_translation(text_hash) == "Texto de cabeçalho repetido"


def test_translation_cache_is_global_across_jobs():
    # Nothing in the schema scopes translation_cache to a job_id - two
    # different jobs sharing identical source text should both see the hit.
    text_hash = db.hash_text("Shared running header")
    db.save_cached_translation(text_hash, "Shared running header", "Cabeçalho compartilhado")

    job_a = _make_job(job_id="job-a")
    job_b = _make_job(job_id="job-b")
    db.save_job(job_a)
    db.save_job(job_b)

    assert db.get_cached_translation(text_hash) == "Cabeçalho compartilhado"


def test_save_cached_translation_overwrites_existing_entry():
    text_hash = db.hash_text("text")
    db.save_cached_translation(text_hash, "text", "primeira tradução")
    db.save_cached_translation(text_hash, "text", "segunda tradução")

    assert db.get_cached_translation(text_hash) == "segunda tradução"


def test_save_job_persists_none_output_path():
    job = _make_job()
    job.output_path = None
    db.save_job(job)

    loaded = db.load_all_jobs()[0]
    assert loaded.output_path is None


def test_save_job_persists_output_path_and_messages():
    job = _make_job(status="done")
    job.output_path = Path("/tmp/does-not-matter/translated.txt")
    job.warning_message = "algum aviso"
    job.error_message = None
    db.save_job(job)

    loaded = db.load_all_jobs()[0]
    assert loaded.output_path == Path("/tmp/does-not-matter/translated.txt")
    assert loaded.warning_message == "algum aviso"


def test_resume_attempts_persisted():
    job = _make_job(resume_attempts=2)
    db.save_job(job)

    loaded = db.load_all_jobs()[0]
    assert loaded.resume_attempts == 2
