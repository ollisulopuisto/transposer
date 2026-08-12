import time

import music21 as m21
import pytest
from music21.musicxml.m21ToXml import GeneralObjectExporter

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from transposer.web.app import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path / "data"))


@pytest.fixture
def musicxml_bytes():
    part = m21.stream.Part()
    part.append(m21.key.Key("E-", "major"))
    part.append(m21.meter.TimeSignature("4/4"))
    for name in ["e-4", "f4", "g4", "a-4"]:
        note = m21.note.Note(name)
        note.quarterLength = 1.0
        part.append(note)
    score = m21.stream.Score()
    score.append(part)
    return GeneralObjectExporter().parse(score)


def wait_for(client, job_id, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/jobs/{job_id}").json()
        if payload["status"] in {"done", "failed"}:
            return payload
        time.sleep(0.25)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_index_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "transposer" in response.text
    assert "Target key" in response.text


def test_healthz_lists_usable_engines(client):
    payload = client.get("/healthz").json()
    assert payload["status"] == "ok"
    assert "passthrough" in payload["engines"]


def test_a_job_runs_to_completion(client, musicxml_bytes):
    response = client.post(
        "/jobs",
        files={"file": ("demo.musicxml", musicxml_bytes, "application/xml")},
        data={"target": "C"},
    )
    assert response.status_code == 202

    job = wait_for(client, response.json()["id"])
    assert job["status"] == "done", job.get("error")
    assert "E- major" in job["summary"]
    assert job["written_key"] == "C major"

    pdf = client.get(job["pdf_url"])
    assert pdf.status_code == 200
    assert pdf.content[:5] == b"%PDF-"

    assert client.get(job["musicxml_url"]).status_code == 200

    preview = client.get(job["preview_url"])
    assert preview.status_code == 200
    assert preview.text.lstrip().startswith("<svg")


def test_an_empty_upload_is_rejected(client):
    response = client.post("/jobs", files={"file": ("empty.musicxml", b"", "application/xml")})
    assert response.status_code == 400


def test_a_nonsense_key_is_rejected_before_any_work(client, musicxml_bytes):
    response = client.post(
        "/jobs",
        files={"file": ("demo.musicxml", musicxml_bytes, "application/xml")},
        data={"target": "purple"},
    )
    assert response.status_code == 400
    assert "could not parse target" in response.json()["detail"]


def test_a_failing_job_reports_the_reason(client):
    response = client.post(
        "/jobs",
        files={"file": ("broken.musicxml", b"this is not xml", "application/xml")},
        data={"target": "C"},
    )
    job = wait_for(client, response.json()["id"])
    assert job["status"] == "failed"
    assert job["message"]


def test_unknown_job_is_a_404(client):
    assert client.get("/jobs/deadbeef").status_code == 404
    assert client.get("/jobs/deadbeef/pdf").status_code == 404


def test_downloading_before_the_job_finishes_is_a_409(client, musicxml_bytes):
    response = client.post(
        "/jobs",
        files={"file": ("demo.musicxml", musicxml_bytes, "application/xml")},
        data={"target": "C"},
    )
    job_id = response.json()["id"]
    early = client.get(f"/jobs/{job_id}/pdf")
    assert early.status_code in {200, 409}
    wait_for(client, job_id)
