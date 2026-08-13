"""Tests for the Audiveris backend's environment and availability check.

Both of these come from the same incident: a container reported Audiveris as
usable, and every run failed. The engine was found, so the check passed; the JVM
then died in a static initialiser before recognising anything.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from transposer.omr.audiveris import AudiverisEngine


def fake_launcher(tmp_path: Path, body: str, name: str = "audiveris") -> Path:
    """A stand-in launcher script, so the tests never need a JVM."""
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


# -- the environment handed to the JVM ------------------------------------


def test_hidpi_detection_is_turned_off():
    """Audiveris probes the monitor scaling through GTK, on Linux, always.

    It does that from a static initialiser, wrapped in ``catch (Exception)`` --
    and a missing native library raises ``UnsatisfiedLinkError``, which is an
    Error, not an Exception. So on any headless Linux box without GTK the
    process dies before it reads a single note. Setting the scale explicitly
    makes it return before it goes looking.
    """
    engine = AudiverisEngine(launcher="/nonexistent/audiveris")
    env = engine.build_env()
    assert "-Dsun.java2d.uiScale=1" in env["JAVA_OPTS"]
    assert env["GDK_SCALE"] == "1"


def test_headless_and_heap_are_still_set():
    env = AudiverisEngine(launcher="/nonexistent/audiveris").build_env()
    assert "-Djava.awt.headless=true" in env["JAVA_OPTS"]
    assert "-Xmx" in env["JAVA_OPTS"]


def test_an_existing_java_opts_is_kept(monkeypatch):
    """Ours are added to the user's, not instead of them."""
    monkeypatch.setenv("JAVA_OPTS", "-XX:+UseZGC")
    env = AudiverisEngine(launcher="/nonexistent/audiveris").build_env()
    assert "-XX:+UseZGC" in env["JAVA_OPTS"]
    assert "-Dsun.java2d.uiScale=1" in env["JAVA_OPTS"]


def test_a_heap_the_user_chose_is_not_overridden(monkeypatch):
    monkeypatch.setenv("JAVA_OPTS", "-Xmx12g")
    env = AudiverisEngine(launcher="/nonexistent/audiveris").build_env()
    assert "-Xmx12g" in env["JAVA_OPTS"]
    assert "-Xmx4g" not in env["JAVA_OPTS"]


def test_an_explicit_gdk_scale_is_kept(monkeypatch):
    monkeypatch.setenv("GDK_SCALE", "2")
    env = AudiverisEngine(launcher="/nonexistent/audiveris").build_env()
    assert env["GDK_SCALE"] == "2"


# -- availability ----------------------------------------------------------


def test_a_missing_launcher_is_unavailable():
    engine = AudiverisEngine(launcher="/nonexistent/audiveris")
    status = engine.availability()
    assert not status.ok


def test_a_launcher_that_cannot_start_is_not_available(tmp_path):
    """The whole point. A launcher that exists but dies is not a usable engine.

    The Docker image shipped for months reporting Audiveris [ok] while every
    recognition failed, because the check only asked whether the file was there.
    """
    launcher = fake_launcher(
        tmp_path,
        "echo 'Exception in thread \"main\" java.lang.UnsatisfiedLinkError: "
        "Unable to load library gtk-3' >&2\nexit 1",
    )
    status = AudiverisEngine(launcher=launcher).availability()

    assert not status.ok
    assert "UnsatisfiedLinkError" in status.reason or "gtk" in status.reason.lower()


def test_a_launcher_that_starts_is_available(tmp_path):
    launcher = fake_launcher(tmp_path, "echo 'Audiveris 5.11.0'\nexit 0")
    status = AudiverisEngine(launcher=launcher).availability()
    assert status.ok, status.reason


def test_the_probe_runs_once_per_engine(tmp_path):
    """`transposer engines` and /healthz both ask; only one JVM should start."""
    counter = tmp_path / "runs"
    launcher = fake_launcher(tmp_path, f"echo x >> {counter}\nexit 0")

    engine = AudiverisEngine(launcher=launcher)
    for _ in range(4):
        assert engine.availability().ok

    assert counter.read_text().count("x") == 1


def test_a_launcher_that_hangs_is_not_available(tmp_path):
    launcher = fake_launcher(tmp_path, "sleep 30")
    engine = AudiverisEngine(launcher=launcher, probe_timeout=1)
    status = engine.availability()
    assert not status.ok
    assert "did not respond" in status.reason


def test_the_probe_does_not_need_a_display(tmp_path):
    """It must not be the GUI that answers: no DISPLAY in the probe's env."""
    seen = tmp_path / "env"
    launcher = fake_launcher(tmp_path, f'echo "$JAVA_OPTS" > {seen}\nexit 0')

    AudiverisEngine(launcher=launcher).availability()
    assert "-Djava.awt.headless=true" in seen.read_text()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell fixtures")
def test_recognise_refuses_when_the_engine_cannot_start(tmp_path):
    from transposer.errors import EngineUnavailableError
    from transposer.ingest import IngestedInput

    launcher = fake_launcher(tmp_path, "exit 1")
    engine = AudiverisEngine(launcher=launcher)
    source = IngestedInput(path=tmp_path / "x.pdf", kind="pdf", workdir=tmp_path)

    with pytest.raises(EngineUnavailableError):
        engine.recognize(source, tmp_path)
