"""Tests for the image enhancement pass.

The fixtures draw synthetic staves at a known interline, so the measurement can
be checked against a number we chose rather than one we eyeballed.
"""

import numpy as np
import pytest
from PIL import Image

from transposer.preprocess import (
    MAX_SCALE,
    PreprocessReport,
    _otsu,
    _run_lengths,
    _sauvola,
    enhance,
    enhance_pages,
    estimate_skew,
    measure_staff_geometry,
)


def draw_staves(
    interline: int = 7,
    thickness: int = 1,
    systems: int = 4,
    width: int = 600,
    margin: int = 40,
) -> np.ndarray:
    """A white page with evenly spaced five-line staves drawn on it."""
    system_height = interline * 5
    height = margin * 2 + systems * (system_height + interline * 6)
    page = np.full((height, width), 255, dtype=np.uint8)

    y = margin
    for _ in range(systems):
        for line in range(5):
            top = y + line * interline
            page[top : top + thickness, margin : width - margin] = 0
        y += system_height + interline * 6
    return page


def save(array: np.ndarray, path) -> str:
    Image.fromarray(array).save(path)
    return str(path)


@pytest.mark.parametrize("interline", [7, 10, 16, 20])
def test_interline_is_measured_correctly(interline):
    measured, thickness = measure_staff_geometry(draw_staves(interline=interline))
    assert measured == interline
    assert thickness == 1


def test_line_thickness_is_measured():
    measured, thickness = measure_staff_geometry(draw_staves(interline=20, thickness=3))
    assert thickness == 3
    assert measured == 20  # line centre to line centre


def test_a_blank_page_has_no_staff_geometry():
    blank = np.full((400, 400), 255, dtype=np.uint8)
    assert measure_staff_geometry(blank) == (None, None)


def test_a_tiny_image_has_no_staff_geometry():
    assert measure_staff_geometry(np.zeros((5, 5), dtype=np.uint8)) == (None, None)


def test_a_level_page_reports_no_skew():
    assert abs(estimate_skew(draw_staves(interline=12))) < 0.5


def test_skew_is_detected_and_signed_correctly():
    """The returned angle is the *correction*: rotating by it levels the page."""
    page = Image.fromarray(draw_staves(interline=12, width=800))
    tilted = np.asarray(page.rotate(-1.5, resample=Image.BILINEAR, fillcolor=255))
    estimated = estimate_skew(tilted)
    assert estimated == pytest.approx(1.5, abs=0.5)

    levelled = np.asarray(
        Image.fromarray(tilted).rotate(estimated, resample=Image.BICUBIC, fillcolor=255)
    )
    assert measure_staff_geometry(levelled)[0] == 12


def test_enhance_scales_to_the_target_interline(tmp_path):
    source = save(draw_staves(interline=7), tmp_path / "in.png")
    report = enhance(source, tmp_path / "out.png", target_interline=20)

    assert report.measured_interline == 7
    assert report.scale == pytest.approx(20 / 7, rel=0.01)
    assert report.output_size[0] > report.source_size[0]

    result = np.asarray(Image.open(tmp_path / "out.png").convert("L"))
    measured, _ = measure_staff_geometry(result)
    assert measured == pytest.approx(20, abs=2)


def test_enhance_never_shrinks_a_page_that_is_already_big_enough(tmp_path):
    source = save(draw_staves(interline=30), tmp_path / "in.png")
    report = enhance(source, tmp_path / "out.png", target_interline=20)
    assert report.scale == 1.0
    assert report.output_size == report.source_size


def test_the_scale_factor_is_capped(tmp_path):
    """A very small staff must not be blown up without limit."""
    source = save(draw_staves(interline=3, width=400), tmp_path / "in.png")
    report = enhance(source, tmp_path / "out.png", target_interline=40)
    assert report.scale <= MAX_SCALE


def test_a_page_with_no_staves_is_passed_through(tmp_path):
    blank = np.full((300, 300), 255, dtype=np.uint8)
    source = save(blank, tmp_path / "in.png")
    report = enhance(source, tmp_path / "out.png")
    assert report.scale == 1.0
    assert any("could not find staff lines" in note for note in report.notes)
    assert (tmp_path / "out.png").is_file()


def test_sharpening_can_be_turned_off(tmp_path):
    source = save(draw_staves(interline=7), tmp_path / "in.png")
    sharp = enhance(source, tmp_path / "a.png", target_interline=20, sharpen=True)
    soft = enhance(source, tmp_path / "b.png", target_interline=20, sharpen=False)
    assert any("unsharp" in step for step in sharp.steps)
    assert not any("unsharp" in step for step in soft.steps)


def test_binarisation_produces_two_tone_output(tmp_path):
    source = save(draw_staves(interline=10), tmp_path / "in.png")
    enhance(source, tmp_path / "out.png", target_interline=20, binarize=True)
    result = np.asarray(Image.open(tmp_path / "out.png").convert("L"))
    assert set(np.unique(result)).issubset({0, 255})


def test_rgba_input_is_flattened_onto_white(tmp_path):
    rgba = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    source = tmp_path / "in.png"
    rgba.save(source)
    report = enhance(source, tmp_path / "out.png")
    assert report.output_size == (200, 200)


def test_enhance_pages_handles_a_list(tmp_path):
    sources = [
        save(draw_staves(interline=7), tmp_path / "p1.png"),
        save(draw_staves(interline=7), tmp_path / "p2.png"),
    ]
    written, reports = enhance_pages(
        [tmp_path / "p1.png", tmp_path / "p2.png"],
        tmp_path / "out",
        target_interline=20,
    )
    assert len(written) == len(reports) == 2
    assert all(path.is_file() for path in written)
    del sources


def test_the_report_summary_mentions_the_measurement(tmp_path):
    source = save(draw_staves(interline=7), tmp_path / "in.png")
    report = enhance(source, tmp_path / "out.png", target_interline=20)
    summary = report.summary()
    assert "interline 7px" in summary
    assert "2.86x" in summary


def test_an_empty_report_summarises_without_crashing():
    assert PreprocessReport().summary()


# -- numeric helpers ----------------------------------------------------


def test_run_lengths():
    column = np.array([False, False, True, True, True, False])
    assert _run_lengths(column) == [(False, 2), (True, 3), (False, 1)]


def test_run_lengths_of_an_empty_column():
    assert _run_lengths(np.array([], dtype=bool)) == []


def test_otsu_splits_a_bimodal_image():
    """The threshold is the highest value still in the dark class, so callers
    must compare with ``<=``."""
    image = np.concatenate(
        [np.full(500, 20, dtype=np.uint8), np.full(500, 230, dtype=np.uint8)]
    ).reshape(20, 50)
    threshold = _otsu(image)
    assert 20 <= threshold < 230
    dark = image <= threshold
    assert dark.sum() == 500


def test_otsu_of_a_flat_image_is_safe():
    assert 0 <= _otsu(np.full((10, 10), 42, dtype=np.uint8)) <= 255


def test_sauvola_keeps_ink_dark_and_paper_white():
    page = draw_staves(interline=10, width=300)
    binary = _sauvola(page)
    assert binary.shape == page.shape
    assert set(np.unique(binary)).issubset({0, 255})
    # The staff lines must survive as black pixels.
    assert (binary == 0).sum() > 0
    # And most of the page must stay white.
    assert (binary == 255).mean() > 0.8
