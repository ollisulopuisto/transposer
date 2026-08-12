"""FastAPI application: upload a scan, pick a key, download a PDF."""

from __future__ import annotations

import os
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

#: Uploads larger than this are rejected outright. A 20-page scan at 400 dpi is
#: comfortably under it; anything larger is a mistake or an attack.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

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

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        engines = [
            {"name": engine.name, "description": engine.description, "ok": status.ok, "reason": status.reason}
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
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "version": __version__,
                "engines": engines,
                "renderers": renderers,
                "instruments": sorted(INSTRUMENT_TRANSPOSITIONS),
                "default_dpi": DEFAULT_DPI,
                "jobs": [job.as_dict() for job in store.list()[:10]],
            },
        )

    @app.get("/healthz")
    def healthz() -> dict:
        usable = [engine.name for engine, status in available_engines() if status.ok]
        return {"status": "ok", "version": __version__, "engines": usable}

    @app.post("/jobs")
    async def create_job(
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
    ) -> JSONResponse:
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
        )

        job = store.submit(file.filename or "upload", data, options)
        return JSONResponse(job.as_dict(), status_code=202)

    @app.get("/jobs")
    def list_jobs() -> JSONResponse:
        return JSONResponse([job.as_dict() for job in store.list()])

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
