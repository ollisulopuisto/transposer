"""FastAPI application: upload a scan, pick a key, download a PDF."""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..ingest import DEFAULT_DPI
from ..keys import parse_target
from ..omr import available_engines
from ..pipeline import PipelineOptions
from ..render import RENDERERS, get_renderer
from ..transpose import INSTRUMENT_TRANSPOSITIONS
from .jobs import JobStore
from .limits import RateLimiter, client_key

#: Uploads larger than this are rejected outright. A 20-page scan at 400 dpi is
#: comfortably under it; anything larger is a mistake or an attack.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

#: How many recognition jobs one client may start per hour, and how many it may
#: have unfinished at once.
#:
#: A job is minutes of CPU and gigabytes of JVM heap, handed to whoever asks.
#: The queue limit is the one that matters: without it a single client fills
#: both worker slots and everyone else waits behind an unbounded backlog.
JOBS_PER_HOUR = int(os.environ.get("TRANSPOSER_JOBS_PER_HOUR", "12"))
JOBS_IN_FLIGHT = int(os.environ.get("TRANSPOSER_JOBS_IN_FLIGHT", "2"))

#: Polling and page loads are cheap, but not free.
REQUESTS_PER_MINUTE = int(os.environ.get("TRANSPOSER_REQUESTS_PER_MINUTE", "240"))

#: Honour X-Forwarded-For. Behind Caddy every request arrives from localhost, so
#: without this the whole internet shares one quota -- and with it on a directly
#: exposed instance, any client can forge a fresh quota per request. The Docker
#: image sets it because it is always behind the proxy.
TRUST_FORWARDED = os.environ.get("TRANSPOSER_TRUST_FORWARDED", "0") == "1"

#: The cookie that decides whose jobs a visitor sees listed. It is not
#: authentication -- it separates one browser's uploads from another's, so the
#: front page stops showing strangers what everyone else is transposing.
OWNER_COOKIE = "transposer_owner"
OWNER_COOKIE_MAX_AGE = 7 * 24 * 3600

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(data_dir: Path | str | None = None, workers: int = 2) -> FastAPI:
    resolved = Path(
        data_dir
        or os.environ.get("TRANSPOSER_DATA_DIR")
        or tempfile.mkdtemp(prefix="transposer-web-")
    )
    store = JobStore(resolved, workers=workers)

    app = FastAPI(title="transposer", version=__version__)
    app.state.store = store
    app.state.data_dir = resolved
    app.state.submissions = RateLimiter(limit=JOBS_PER_HOUR, window=3600)
    app.state.requests = RateLimiter(limit=REQUESTS_PER_MINUTE, window=60)

    def owner_of(request: Request) -> str:
        return request.cookies.get(OWNER_COOKIE, "")

    @app.middleware("http")
    async def throttle(request: Request, call_next):
        """A ceiling on plain request volume, above the per-job limit.

        Downloads and status polling are cheap individually and not free in
        aggregate; this stops one client turning the page's poll loop into a
        flood. Health checks are exempt so that throttling never takes the
        service out of its own monitoring.
        """
        if request.url.path != "/healthz":
            verdict = app.state.requests.check(client_key(request, TRUST_FORWARDED))
            if not verdict.allowed:
                return JSONResponse(
                    {"detail": "too many requests"},
                    status_code=429,
                    headers={"Retry-After": str(int(verdict.retry_after) + 1)},
                )
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        owner = owner_of(request) or secrets.token_urlsafe(16)
        engines = [
            {
                "name": engine.name,
                "description": engine.description,
                "ok": status.ok,
                "reason": status.reason,
            }
            for engine, status in available_engines()
            if engine.name != "passthrough"
        ]
        renderers = []
        for name in sorted(RENDERERS):
            renderer = get_renderer(name)
            status = renderer.availability()
            renderers.append(
                {"name": name, "description": renderer.description, "ok": status.ok}
            )
        page = TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "version": __version__,
                "engines": engines,
                "renderers": renderers,
                "instruments": sorted(INSTRUMENT_TRANSPOSITIONS),
                "default_dpi": DEFAULT_DPI,
                "jobs": [job.as_dict() for job in store.list(owner)[:10]],
            },
        )
        page.set_cookie(
            OWNER_COOKIE,
            owner,
            max_age=OWNER_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return page

    @app.get("/healthz")
    def healthz() -> dict:
        usable = [engine.name for engine, status in available_engines() if status.ok]
        return {"status": "ok", "version": __version__, "engines": usable}

    @app.post("/jobs")
    async def create_job(
        request: Request,
        response: Response,
        file: UploadFile,
        target: str = Form("C"),
        engine: str = Form("auto"),
        renderer: str = Form("auto"),
        direction: str = Form("auto"),
        octave_shift: int = Form(0),
        instrument: str = Form(""),
        paper: str = Form("a4"),
        landscape: bool = Form(False),
        scale: int = Form(40),
        dpi: int = Form(DEFAULT_DPI),
        drop_text: bool = Form(False),
        key_changes: str = Form("auto"),
        binarize: bool = Form(False),
        chord_pass: bool = Form(False),
        chord_ocr: bool = Form(True),
        attach_text_lyrics: bool = Form(True),
    ) -> JSONResponse:
        who = client_key(request, TRUST_FORWARDED)

        # Unfinished work first: one client queueing a hundred scans denies the
        # service to everyone else long before it exhausts an hourly quota.
        owner = owner_of(request) or secrets.token_urlsafe(16)
        in_flight = sum(
            1
            for job in store.list(owner)
            if job.status in {"queued", "running"}
        )
        if in_flight >= JOBS_IN_FLIGHT:
            raise HTTPException(
                429,
                f"you already have {in_flight} job(s) running; wait for one to "
                "finish before starting another",
            )

        verdict = app.state.submissions.check(who)
        if not verdict.allowed:
            raise HTTPException(
                429,
                "too many recognition jobs from this address; try again in "
                f"{int(verdict.retry_after // 60) + 1} minute(s)",
                headers={"Retry-After": str(int(verdict.retry_after) + 1)},
            )

        data = await file.read()
        if not data:
            raise HTTPException(400, "the uploaded file is empty")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413, f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"
            )

        try:
            parse_target(target)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        options = PipelineOptions(
            target=target,
            engine=engine,
            renderer=renderer,
            direction=direction if direction in {"auto", "up", "down"} else "auto",
            octave_shift=max(-3, min(3, octave_shift)),
            instrument=instrument or None,
            paper=paper,
            landscape=landscape,
            scale=max(10, min(120, scale)),
            dpi=max(72, min(600, dpi)),
            drop_text=drop_text,
            key_changes=key_changes if key_changes in {"auto", "keep", "drop"} else "auto",
            binarize=binarize,
            chord_pass=chord_pass,
            chord_ocr=chord_ocr,
            attach_text_lyrics=attach_text_lyrics,
        )

        job = store.submit(file.filename or "upload", data, options, owner=owner)
        payload = JSONResponse(job.as_dict(), status_code=202)
        payload.set_cookie(
            OWNER_COOKIE,
            owner,
            max_age=OWNER_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        del response
        return payload

    @app.get("/jobs")
    def list_jobs(request: Request) -> JSONResponse:
        """Only the caller's own jobs.

        There is deliberately no way to list everyone's: this endpoint used to
        return every upload on the instance, filenames and download links
        included.
        """
        return JSONResponse([job.as_dict() for job in store.list(owner_of(request))])

    @app.get("/jobs/{job_id}")
    def read_job(job_id: str) -> JSONResponse:
        return JSONResponse(_require(store, job_id).as_dict())

    @app.get("/jobs/{job_id}/pdf")
    def download_pdf(job_id: str) -> FileResponse:
        job = _require_done(store, job_id)
        assert job.result is not None
        return FileResponse(
            job.result.pdf,
            media_type="application/pdf",
            filename=job.result.pdf.name,
        )

    @app.get("/jobs/{job_id}/musicxml")
    def download_musicxml(job_id: str) -> FileResponse:
        job = _require_done(store, job_id)
        assert job.result is not None
        return FileResponse(
            job.result.musicxml,
            media_type="application/vnd.recordare.musicxml+xml",
            filename=job.result.musicxml.name,
        )

    @app.get("/jobs/{job_id}/preview/{page}")
    def preview(job_id: str, page: int) -> Response:
        job = _require_done(store, job_id)
        assert job.result is not None
        pages = job.result.render.svg_pages
        if not pages:
            raise HTTPException(
                404, "this renderer produced no SVG preview; download the PDF instead"
            )
        if page < 1 or page > len(pages):
            raise HTTPException(404, f"page {page} does not exist")
        return Response(
            pages[page - 1].read_text(encoding="utf-8"),
            media_type="image/svg+xml",
        )

    return app


def _require(store: JobStore, job_id: str):
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, f"no job {job_id}")
    return job


def _require_done(store: JobStore, job_id: str):
    job = _require(store, job_id)
    if job.status != "done" or job.result is None:
        raise HTTPException(409, f"job {job_id} is {job.status}, not finished")
    return job


#: Module-level app so ``uvicorn transposer.web.app:app --reload`` works.
app = create_app()
