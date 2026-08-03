from dataclasses import dataclass, field
from pathlib import Path

# Split out from jobs.py so db.py can construct/type Job instances without a
# circular import (jobs.py -> db.py -> jobs.py).


@dataclass
class Job:
    id: str
    original_filename: str
    input_format: str
    output_format: str
    status: str = "pending"  # pending, translating, rebuilding, converting, done, error, cancelled
    progress_done: int = 0
    progress_total: int = 0
    error_message: str | None = None
    warning_message: str | None = None
    output_path: Path | None = None
    dir: Path = field(default=None)
    created_at: str = ""
    updated_at: str = ""
    resume_attempts: int = 0
