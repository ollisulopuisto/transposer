"""A small in-process job queue.

OMR takes minutes, which is far longer than a browser will wait on a POST, so
uploads become jobs: the request hands work to a background thread and returns
an id the page polls. Everything lives in memory and on local disk -- this is a
single-container, self-hosted tool, not a cluster -- but the interface is small
enough to swap for a real queue if that ever changes.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..pipeline import PipelineOptions, PipelineResult
from ..pipeline import run as run_pipeline

Status = Literal["queued", "running", "done", "failed"]


@dataclass
class Job:
    """One upload's worth of work."""

    id: str
    filename: str
    options: PipelineOptions
    source: Path
    workdir: Path
    status: Status = "queued"
    stage: str = "queued"
    message: str = "waiting to start"
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: PipelineResult | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        payload = {
            "id": self.id,
            "filename": self.filename,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "target": self.options.target,
            "elapsed": round((self.finished_at or time.time()) - self.created_at, 1),
        }
        if self.result is not None:
            payload |= {
                "engine": self.result.engine,
                "renderer": self.result.renderer,
                "pages": self.result.render.page_count,
                "summary": self.result.transposition.summary(),
                "source_key": (
                    self.result.transposition.source_key.name
                    if self.result.transposition.source_key
                    else None
                ),
                "written_key": (
                    self.result.transposition.written_key.name
                    if self.result.transposition.written_key
                    else None
                ),
                "warnings": self.result.warnings,
                "pdf_url": f"/jobs/{self.id}/pdf",
                "musicxml_url": f"/jobs/{self.id}/musicxml",
                "preview_url": f"/jobs/{self.id}/preview/1",
            }
        if self.error:
            payload["error"] = self.error
        return payload


class JobStore:
    """Thread-safe job registry with a bounded worker pool."""

    def __init__(self, data_dir: Path, workers: int = 2, keep: int = 50) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.keep = keep
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(workers)

    def submit(self, filename: str, data: bytes, options: PipelineOptions) -> Job:
        job_id = uuid.uuid4().hex[:12]
        workdir = self.data_dir / job_id
        workdir.mkdir(parents=True, exist_ok=True)

        source = workdir / _safe_name(filename)
        source.write_bytes(data)

        job = Job(
            id=job_id,
            filename=source.name,
            options=options,
            source=source,
            workdir=workdir,
        )

        with self._lock:
            self._jobs[job_id] = job
            self._order.append(job_id)
            self._evict_locked()

        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return [self._jobs[i] for i in reversed(self._order) if i in self._jobs]

    # -- internals ---------------------------------------------------------

    def _run(self, job: Job) -> None:
        with self._slots:
            job.status = "running"

            def progress(stage: str, message: str) -> None:
                job.stage = stage
                job.message = message

            try:
                output = job.workdir / f"{Path(job.filename).stem}-{_slug(job.options.target)}.pdf"
                job.result = run_pipeline(
                    job.source,
                    output,
                    job.options,
                    workdir=job.workdir / "work",
                    progress=progress,
                )
                job.status = "done"
                job.stage = "done"
                job.message = "finished"
            except Exception as exc:  # noqa: BLE001 - the message goes to the UI
                job.status = "failed"
                job.stage = "failed"
                job.message = str(exc) or exc.__class__.__name__
                job.error = "".join(
                    traceback.format_exception_only(type(exc), exc)
                ).strip()
            finally:
                job.finished_at = time.time()

    def _evict_locked(self) -> None:
        """Drop the oldest finished jobs once the store grows past ``keep``."""
        import shutil

        while len(self._order) > self.keep:
            oldest = self._order.pop(0)
            job = self._jobs.pop(oldest, None)
            if job is not None:
                shutil.rmtree(job.workdir, ignore_errors=True)


def _safe_name(name: str) -> str:
    cleaned = Path(name).name.replace("\x00", "").strip()
    return cleaned or "upload"


def _slug(target: str) -> str:
    return "".join(ch for ch in target if ch.isalnum() or ch in "+-#") or "transposed"
