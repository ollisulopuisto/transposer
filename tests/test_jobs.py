"""Tests for job ownership, retention and disk hygiene.

Two of these are about a public instance not leaking: an anonymous service that
lists every upload tells each visitor what everyone else is transposing, and
hands them the ids to download it with. The rest are about not keeping other
people's sheet music on disk any longer than it takes to hand it back.
"""

from __future__ import annotations

import time

import pytest

from transposer.pipeline import PipelineOptions
from transposer.web.jobs import JobStore


@pytest.fixture
def store(tmp_path):
    made = JobStore(tmp_path, workers=1)
    yield made
    made.shutdown()


def submit(store, name="scan.pdf", owner="owner-a", data=b"%PDF-1.4 not really"):
    return store.submit(name, data, PipelineOptions(), owner=owner)


# -- ids as capabilities ---------------------------------------------------


def test_a_job_id_is_long_enough_to_be_a_secret():
    """With the listing gone, the id is the only thing standing between a
    stranger and someone else's upload."""
    from transposer.web.jobs import new_job_id

    ids = {new_job_id() for _ in range(500)}
    assert len(ids) == 500
    assert all(len(job_id) >= 22 for job_id in ids)


# -- ownership -------------------------------------------------------------


def test_a_listing_shows_only_your_own_jobs(store):
    mine = submit(store, owner="me")
    submit(store, owner="someone-else")

    listed = store.list(owner="me")
    assert [job.id for job in listed] == [mine.id]


def test_a_listing_without_an_owner_is_empty(store):
    """No owner, no listing. There is deliberately no way to ask for all of
    them over HTTP."""
    submit(store, owner="me")
    assert store.list(owner=None) == []
    assert store.list(owner="") == []


def test_a_job_can_be_fetched_by_id_regardless_of_owner(store):
    """The id is the capability: a shared link has to keep working."""
    job = submit(store, owner="me")
    assert store.get(job.id) is not None


# -- retention -------------------------------------------------------------


def test_the_upload_is_deleted_once_the_job_finishes(store):
    """The scan is only needed while it is being recognised. Keeping it
    afterwards means an open instance accumulates other people's sheet music.
    """
    job = submit(store)
    _wait_for(job)

    assert not job.source.exists()


def test_finished_artifacts_are_reaped_after_their_time(tmp_path):
    made = JobStore(tmp_path, workers=1, retain=0.05)
    try:
        job = submit(made)
        _wait_for(job)
        assert job.workdir.exists()

        time.sleep(0.12)
        assert made.reap() >= 1
        assert not job.workdir.exists()
        assert made.get(job.id) is None
    finally:
        made.shutdown()


def test_a_running_job_is_never_reaped(tmp_path):
    made = JobStore(tmp_path, workers=1, retain=0.0)
    try:
        job = submit(made)
        job.status = "running"
        job.finished_at = None
        made.reap()
        assert made.get(job.id) is not None
    finally:
        made.shutdown()


def test_reaping_is_idempotent(tmp_path):
    made = JobStore(tmp_path, workers=1, retain=0.0)
    try:
        job = submit(made)
        _wait_for(job)
        made.reap()
        assert made.reap() == 0
        assert made.get(job.id) is None
    finally:
        made.shutdown()


def test_the_store_stays_bounded(tmp_path):
    made = JobStore(tmp_path, workers=1, keep=3)
    try:
        jobs = [submit(made, name=f"s{index}.pdf") for index in range(6)]
        for job in jobs:
            _wait_for(job)
        assert len(made.list(owner="owner-a")) <= 3
        # The evicted ones took their directories with them.
        assert not jobs[0].workdir.exists()
    finally:
        made.shutdown()


def test_shutdown_removes_everything_it_wrote(tmp_path):
    made = JobStore(tmp_path, workers=1)
    job = submit(made)
    _wait_for(job)
    made.shutdown(purge=True)
    assert not job.workdir.exists()


# -- helpers ---------------------------------------------------------------


def _wait_for(job, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job.status in {"done", "failed"}:
            return
        time.sleep(0.02)
    raise AssertionError(f"job stayed {job.status}")


def test_no_copy_of_the_upload_survives_the_run(tmp_path):
    """Deleting the file the request wrote is not enough.

    Ingestion copies the upload into the pipeline's own working directory
    before touching it, so removing only the original leaves the user's music
    sitting in work/input under a name we chose. Verified against a real run
    on the live instance, where exactly that happened.
    """
    import music21 as m21
    from music21.converter.subConverters import ConverterMusicXML  # noqa: F401
    from music21.musicxml.m21ToXml import GeneralObjectExporter

    part = m21.stream.Part()
    part.append(m21.note.Note("c4", quarterLength=4.0))
    score = m21.stream.Score()
    score.append(part)
    data = GeneralObjectExporter().parse(score)

    made = JobStore(tmp_path, workers=1)
    try:
        job = made.submit("private.musicxml", data, PipelineOptions(), owner="me")
        _wait_for(job, timeout=90)
        assert job.status == "done", job.error

        # The uploaded file itself, under any of the names it was copied to.
        # The output PDF is named after it ("private-C.pdf") and is the thing
        # the user came for, so it is not a leftover.
        leftovers = [
            path
            for path in job.workdir.rglob("*")
            if path.is_file() and path.name == "private.musicxml"
        ]
        assert leftovers == [], leftovers

        # What the endpoints serve is still there.
        assert job.result is not None
        assert job.result.pdf.exists()
        assert job.result.musicxml.exists()
    finally:
        made.shutdown()


def test_rasterised_pages_do_not_outlive_the_run(tmp_path):
    """They are the bulk of the disk and nothing serves them."""
    made = JobStore(tmp_path, workers=1)
    try:
        job = submit(made)
        _wait_for(job)
        assert not (job.workdir / "work" / "input").exists()
    finally:
        made.shutdown()
