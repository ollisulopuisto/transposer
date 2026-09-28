"""The repo keeps a CHANGELOG and the image carries it (ops dashboard, 2026-09-28).

The host reads /app/CHANGELOG.md from the running modulaattori container and
shows the build's own section when you hover its version.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_changelog_names_builds_by_calver():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert re.search(r"^## \[v\d{2}\.\d{2}\.\d{2}\.\d+\] - \d{4}-\d{2}-\d{2}$", text, re.M)


def test_the_image_carries_its_changelog():
    dockerfile = (ROOT / "Dockerfile").read_text()
    final_stage = dockerfile.split("\nFROM ")[-1]
    assert re.search(r"^COPY CHANGELOG\.md /app/CHANGELOG\.md$", final_stage, re.M)
