"""Launcher for Mozart's ``main.py``, run as a separate process.

Mozart was written against scikit-image 0.17, where ``rgb2gray`` quietly passed
a two-dimensional array straight through and accepted a four-channel one. Modern
scikit-image raises instead, and Mozart's ``main.py`` calls ``rgb2gray`` on an
already-greyscale image whenever it decides a page needs deskewing -- so on a
current stack it dies on exactly the inputs it was built for.

Rather than patch the checkout (which would fight every ``git pull``), this
module restores the old, permissive behaviour in-process and then hands control
to Mozart unchanged.

Usage::

    python _mozart_runner.py <mozart-src-dir> <input-dir> <output-dir>
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def install_skimage_compatibility() -> None:
    """Make ``skimage.color.rgb2gray`` tolerant again."""
    import numpy as np
    import skimage.color as color

    original = color.rgb2gray

    def rgb2gray(image, *args, **kwargs):
        array = np.asarray(image)
        if array.ndim == 2:
            return array
        if array.ndim == 3 and array.shape[-1] == 4:
            array = color.rgba2rgb(array)
        return original(array, *args, **kwargs)

    color.rgb2gray = rgb2gray


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2

    mozart_src, input_dir, output_dir = argv[1], argv[2], argv[3]

    install_skimage_compatibility()

    # Mozart's modules import each other by bare name and load its classifier by
    # a path relative to src/, so it has to run from there.
    sys.path.insert(0, str(Path(mozart_src).resolve()))
    sys.argv = ["main.py", input_dir, output_dir]
    runpy.run_path(str(Path(mozart_src) / "main.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
