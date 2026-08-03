import pytest
from fastapi.testclient import TestClient

from app import jobs as jobs_module
from app.main import app


@pytest.fixture(autouse=True)
def _isolated_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "DATA_DIR", tmp_path)
    jobs_module.JOBS.clear()
    jobs_module.RUNNING_TASKS.clear()
    yield
    jobs_module.JOBS.clear()
    jobs_module.RUNNING_TASKS.clear()


@pytest.fixture(autouse=True)
def _no_background_translation(monkeypatch):
    """API tests exercise HTTP plumbing only - the actual translation
    pipeline is covered in test_jobs.py."""
    monkeypatch.setattr(jobs_module, "start_job", lambda job_id: None)


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /api/jobs
# ---------------------------------------------------------------------------


def test_create_job_returns_pending_job(client):
    response = client.post(
        "/api/jobs",
        files={"file": ("book.txt", b"Hello world.", "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["input_format"] == "txt"
    assert body["output_format"] == "txt"
    assert body["status"] == "pending"
    assert body["progress_done"] == 0
    assert body["error_message"] is None
    assert body["warning_message"] is None
    assert "id" in body


def test_create_job_with_explicit_output_format(client):
    response = client.post(
        "/api/jobs",
        files={"file": ("book.epub", b"fake epub bytes", "application/epub+zip")},
        data={"output_format": "pdf"},
    )

    assert response.status_code == 200
    assert response.json()["output_format"] == "pdf"


def test_create_job_rejects_empty_file(client):
    response = client.post("/api/jobs", files={"file": ("book.txt", b"", "text/plain")})

    assert response.status_code == 400
    assert "vazio" in response.json()["detail"]


def test_create_job_rejects_unsupported_extension(client):
    response = client.post(
        "/api/jobs", files={"file": ("book.docx", b"some bytes", "application/octet-stream")}
    )

    assert response.status_code == 400


def test_create_job_actually_schedules_translation(client, monkeypatch):
    started = {}
    monkeypatch.setattr(jobs_module, "start_job", lambda job_id: started.setdefault("id", job_id))

    response = client.post("/api/jobs", files={"file": ("book.txt", b"Hi.", "text/plain")})

    assert started["id"] == response.json()["id"]


# ---------------------------------------------------------------------------
# GET /api/jobs/{id}
# ---------------------------------------------------------------------------


def test_get_job_returns_current_state(client):
    created = client.post("/api/jobs", files={"file": ("book.txt", b"Hi.", "text/plain")}).json()

    response = client.get(f"/api/jobs/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_job_404_for_unknown_id(client):
    response = client.get("/api/jobs/does-not-exist")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/jobs (list)
# ---------------------------------------------------------------------------


def test_list_jobs_empty(client):
    response = client.get("/api/jobs")
    assert response.status_code == 200
    assert response.json() == []


def test_list_jobs_returns_created_jobs(client):
    created1 = client.post("/api/jobs", files={"file": ("a.txt", b"1", "text/plain")}).json()
    created2 = client.post("/api/jobs", files={"file": ("b.txt", b"2", "text/plain")}).json()

    response = client.get("/api/jobs")

    assert response.status_code == 200
    ids = {job["id"] for job in response.json()}
    assert ids == {created1["id"], created2["id"]}


# ---------------------------------------------------------------------------
# POST /api/jobs/{id}/cancel
# ---------------------------------------------------------------------------


def test_cancel_job_404_for_unknown_id(client):
    response = client.post("/api/jobs/does-not-exist/cancel")
    assert response.status_code == 404


def test_cancel_job_409_when_already_terminal(client):
    created = client.post("/api/jobs", files={"file": ("book.txt", b"Hi.", "text/plain")}).json()
    job = jobs_module.get_job(created["id"])
    job.status = "done"

    response = client.post(f"/api/jobs/{created['id']}/cancel")

    assert response.status_code == 409


def test_cancel_job_200_and_delegates_to_jobs_cancel_job(client, monkeypatch):
    created = client.post("/api/jobs", files={"file": ("book.txt", b"Hi.", "text/plain")}).json()
    cancelled = []
    monkeypatch.setattr(jobs_module, "cancel_job", lambda job_id: cancelled.append(job_id) or True)

    response = client.post(f"/api/jobs/{created['id']}/cancel")

    assert response.status_code == 200
    assert cancelled == [created["id"]]


# ---------------------------------------------------------------------------
# GET /api/jobs/{id}/download
# ---------------------------------------------------------------------------


def test_download_404_for_unknown_id(client):
    response = client.get("/api/jobs/does-not-exist/download")
    assert response.status_code == 404


def test_download_409_when_not_done(client):
    created = client.post("/api/jobs", files={"file": ("book.txt", b"Hi.", "text/plain")}).json()

    response = client.get(f"/api/jobs/{created['id']}/download")

    assert response.status_code == 409


def test_download_returns_file_when_done(client, tmp_path):
    created = client.post("/api/jobs", files={"file": ("meu-livro.txt", b"Hi.", "text/plain")}).json()

    job = jobs_module.get_job(created["id"])
    output_file = job.dir / "translated.txt"
    output_file.write_text("Olá.", encoding="utf-8")
    job.status = "done"
    job.output_path = output_file

    response = client.get(f"/api/jobs/{created['id']}/download")

    assert response.status_code == 200
    assert response.content == "Olá.".encode("utf-8")
    assert "meu-livro_pt-br.txt" in response.headers["content-disposition"]


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------


def test_frontend_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Traduz AI" in response.text


# ---------------------------------------------------------------------------
# Startup lifespan
# ---------------------------------------------------------------------------


def test_lifespan_initializes_db_and_resumes_jobs(monkeypatch):
    init_called = []
    resume_called = []
    monkeypatch.setattr("app.main.db.init_db", lambda: init_called.append(True))
    monkeypatch.setattr("app.main.jobs.load_jobs_from_db", lambda: resume_called.append(True))

    with TestClient(app):
        pass  # entering/exiting the context triggers the lifespan startup/shutdown

    assert init_called == [True]
    assert resume_called == [True]
