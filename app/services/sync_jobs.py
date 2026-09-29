"""
Truthful synchronization progress.

Bootstrap must be able to say "history is still downloading" instead of
optimistically reporting READY while chunks are still in flight.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

JOB_QUEUED = "QUEUED"
JOB_RUNNING = "RUNNING"
JOB_PARTIAL = "PARTIAL"
JOB_COMPLETE = "COMPLETE"
JOB_FAILED = "FAILED"


@dataclass
class SyncJob:
    job_id: str
    status: str = JOB_QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total_series: int = 0
    completed_series: int = 0
    series: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status in {JOB_QUEUED, JOB_RUNNING}

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "total_series": self.total_series,
            "completed_series": self.completed_series,
            "series": self.series,
            "error": self.error,
        }


_jobs: dict[int, SyncJob] = {}
_lock = threading.Lock()


def start_job(user_id: int, *, total_series: int) -> SyncJob:
    job = SyncJob(
        job_id=uuid.uuid4().hex[:12],
        status=JOB_RUNNING,
        started_at=datetime.now(timezone.utc),
        total_series=total_series,
    )
    with _lock:
        _jobs[user_id] = job
    return job


def record_series(user_id: int, result: dict[str, Any]) -> None:
    with _lock:
        job = _jobs.get(user_id)
        if job is None:
            return
        job.series.append(result)
        job.completed_series = len(job.series)


def finish_job(user_id: int, *, status: str, error: str | None = None) -> SyncJob | None:
    with _lock:
        job = _jobs.get(user_id)
        if job is None:
            return None
        job.status = status
        job.error = error
        job.finished_at = datetime.now(timezone.utc)
        return job


def get_job(user_id: int) -> SyncJob | None:
    with _lock:
        return _jobs.get(user_id)


def any_active() -> bool:
    with _lock:
        return any(job.is_active for job in _jobs.values())
