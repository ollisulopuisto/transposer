"""A small in-process job queue.

OMR takes minutes, which is far longer than a browser will wait on a POST, so
uploads become jobs: the request hands work to a background thread and returns
an id the page polls. Everything lives in memory and on local disk -- this is a
single-container, self-hosted tool, not a cluster -- but the interface is small
enough to swap for a real queue if that ever changes.
"""

from __future__ import annotations

import contextlib
import secrets
import shutil
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..pipeline import PipelineOptions, PipelineResult
from ..pipeline import run as run_pipeline

Status = Literal["queued", "running", "done", "failed"]

#: How long a finished job's artifacts stay on disk, in seconds.
#:
#: An open instance is handed other people's sheet music, and the only version
#: of "we do not keep it" anyone should believe is the one where it is deleted.
#: An hour is long enough to download a PDF and short enough that the disk is
#: not an archive of everything the internet has uploaded.
DEFAULT_RETAIN = 3600.0

#: Bytes of job artifacts to keep before evicting the oldest finished jobs,
#: whatever their age. A recognition run leaves rasterised pages behind, so this
#: fills faster than the job count suggests.
DEFAULT_DISK_BUDGET = 2 * 1024**3


def new_job_id() -> str:
    """An unguessable job id.

    With the cross-user listing gone the id *is* the capability: whoever holds
    it can download the result. A truncated uuid4 was 48 bits, which is a
    reasonable thing to guess at if the prize is somebody else's upload.
    """
    return secrets.token_urlsafe(16)


@dataclass
class Job:
    """One upload's worth of work."""

    id: str
    filename: str
    options: PipelineOptions
    source: Path
    workdir: Path
    owner: str = ""
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

    def __init__(
        self,
        data_dir: Path,
        workers: int = 2,
        keep: int = 50,
        retain: float = DEFAULT_RETAIN,
        disk_budget: int = DEFAULT_DISK_BUDGET,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.keep = keep
        self.retain = float(retain)
        self.disk_budget = int(disk_budget)
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(workers)
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None
        if self.retain > 0:
            self._reaper = threading.Thread(target=self._reap_forever, daemon=True)
            self._reaper.start()

    def submit(
        self,
        filename: str,
        data: bytes,
        options: PipelineOptions,
        owner: str = "",
    ) -> Job:
        job_id = new_job_id()
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
            owner=owner,
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

    def list(self, owner: str | None) -> list[Job]:
        """The jobs belonging to one visitor, newest first.

        An owner is required and there is deliberately no way to ask for all of
        them: an anonymous service that lists every upload tells each visitor
        what everyone else is transposing, and hands over the ids to download it
        with.
        """
        if not owner:
            return []
        with self._lock:
            return [
                self._jobs[i]
                for i in reversed(self._order)
                if i in self._jobs and self._jobs[i].owner == owner
            ]

    def reap(self) -> int:
        """Delete the artifacts of jobs that have outlived their retention.

        Returns how many were removed. Jobs still queued or running are never
        touched, whatever the clock says.
        """
        now = time.time()
        doomed: list[Job] = []

        with self._lock:
            for job_id in list(self._order):
                job = self._jobs.get(job_id)
                if job is None:
                    self._order.remove(job_id)
                    continue
                if job.finished_at is None or job.status not in {"done", "failed"}:
                    continue
                if now - job.finished_at < self.retain:
                    continue
                doomed.append(job)
                self._jobs.pop(job_id, None)
                self._order.remove(job_id)

            self._evict_for_space_locked()

        for job in doomed:
            shutil.rmtree(job.workdir, ignore_errors=True)
        return len(doomed)

    def shutdown(self, purge: bool = False) -> None:
        """Stop the reaper, and optionally remove everything written."""
        self._stop.set()
        if self._reaper is not None:
            self._reaper.join(timeout=2)
            self._reaper = None
        if purge:
            with self._lock:
                jobs = list(self._jobs.values())
                self._jobs.clear()
                self._order.clear()
            for job in jobs:
                shutil.rmtree(job.workdir, ignore_errors=True)

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
            except Exception as exc:
                job.status = "failed"
                job.stage = "failed"
                job.message = str(exc) or exc.__class__.__name__
                job.error = "".join(
                    traceback.format_exception_only(type(exc), exc)
                ).strip()
            finally:
                job.finished_at = time.time()
                _discard_inputs(job)

    def _reap_forever(self) -> None:
        """Sweep expired jobs in the background.

        Checked often enough that an hour's retention means roughly an hour,
        and rarely enough to cost nothing.
        """
        interval = max(5.0, min(60.0, self.retain / 10))
        while not self._stop.wait(interval):
            # A sweep must never take the process down with it.
            with contextlib.suppress(Exception):
                self.reap()

    def _evict_for_space_locked(self) -> None:
        """Drop the oldest finished jobs while the artifacts exceed the budget.

        Recognition leaves rasterised pages behind, so a handful of large scans
        fills a disk long before the job count looks alarming.
        """
        if self.disk_budget <= 0:
            return
        while self._order and self._disk_used_locked() > self.disk_budget:
            for job_id in list(self._order):
                job = self._jobs.get(job_id)
                if job is not None and job.status in {"done", "failed"}:
                    self._jobs.pop(job_id, None)
                    self._order.remove(job_id)
                    shutil.rmtree(job.workdir, ignore_errors=True)
                    break
            else:
                return

    def _disk_used_locked(self) -> int:
        total = 0
        for job in self._jobs.values():
            for path in job.workdir.rglob("*"):
                if path.is_file():
                    # Racing the reaper is expected; a vanished file is zero.
                    with contextlib.suppress(OSError):
                        total += path.stat().st_size
        return total

    def _evict_locked(self) -> None:
        """Drop the oldest finished jobs once the store grows past ``keep``."""
        while len(self._order) > self.keep:
            oldest = self._order.pop(0)
            job = self._jobs.pop(oldest, None)
            if job is not None:
                shutil.rmtree(job.workdir, ignore_errors=True)


#: What a finished job still has to be able to serve. Everything else it wrote
#: is an input or an intermediate, and both are copies of the user's music.
_KEEP_AFTER_RUN = ("transposed.musicxml", "render")


def _discard_inputs(job: Job) -> None:
    """Delete the upload and the intermediates as soon as the run is over.

    Deleting the file the request wrote is not enough, and assuming otherwise
    is how a live instance ended up holding somebody's scan: ingestion *copies*
    the upload into the pipeline's working directory before touching it, and
    rasterising a PDF leaves a full-page PNG per page next to it. Those pages
    are also the bulk of the disk a job occupies.

    What the endpoints serve -- the PDF, the transposed MusicXML and the SVG
    previews -- stays until the retention sweep takes it.
    """
    with contextlib.suppress(OSError):
        job.source.unlink(missing_ok=True)

    work = job.workdir / "work"
    if not work.is_dir():
        return

    for entry in work.iterdir():
        if entry.name in _KEEP_AFTER_RUN:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                entry.unlink()


def _safe_name(name: str) -> str:
    cleaned = Path(name).name.replace("\x00", "").strip()
    return cleaned or "upload"


def _slug(target: str) -> str:
    return "".join(ch for ch in target if ch.isalnum() or ch in "+-#") or "transposed"
