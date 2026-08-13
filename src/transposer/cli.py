"""Command line interface.

    transposer transpose scan.pdf --to C -o out.pdf
    transposer engines
    transposer serve --port 8000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .chordband import MIN_CONFIDENCE
from .errors import TransposerError
from .ingest import DEFAULT_DPI
from .omr import describe_engines
from .pipeline import PipelineOptions, run
from .preprocess import TARGET_INTERLINE
from .render import describe_renderers
from .transpose import INSTRUMENT_TRANSPOSITIONS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="transposer",
        description=(
            "Read sheet music with OMR, transpose it to any key, and engrave a "
            "new PDF."
        ),
    )
    parser.add_argument("--version", action="version", version=f"transposer {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    transpose = subparsers.add_parser(
        "transpose",
        help="run the full pipeline on a PDF, image or score file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "target keys and intervals:\n"
            "  C, Bb, F#m, 'Eb major', a-molli, C-duuri   -- a key\n"
            "  -M3, +P5, m6                               -- a named interval\n"
            "  -4, +7, 'down 3'                           -- semitones\n"
        ),
    )
    transpose.add_argument("input", type=Path, help="PDF, image, MusicXML, MIDI or ABC file")
    transpose.add_argument(
        "-o",
        "--output",
        type=Path,
        help="where to write the PDF (default: <input>-<key>.pdf next to the input)",
    )
    transpose.add_argument(
        "-t",
        "--to",
        dest="target",
        default="C",
        help="target key or interval (default: C)",
    )
    transpose.add_argument(
        "-e",
        "--engine",
        default="auto",
        help="OMR engine: auto, audiveris, oemer, mozart, passthrough (default: auto)",
    )
    transpose.add_argument(
        "-r",
        "--renderer",
        default="auto",
        help="engraver: auto, musescore, verovio (default: auto)",
    )
    transpose.add_argument(
        "-d",
        "--direction",
        choices=("auto", "up", "down"),
        default="auto",
        help="which way to move when a key target allows both (default: auto)",
    )
    transpose.add_argument(
        "--octave",
        dest="octave_shift",
        type=int,
        default=0,
        help="extra whole octaves to add on top (e.g. -1 to drop an octave)",
    )
    transpose.add_argument(
        "--for-instrument",
        dest="instrument",
        choices=sorted(INSTRUMENT_TRANSPOSITIONS),
        help="write the part for a transposing instrument, on top of --to",
    )
    transpose.add_argument(
        "--paper",
        default="a4",
        help="page size: a3, a4, a5, letter, legal, tabloid (default: a4)",
    )
    transpose.add_argument("--landscape", action="store_true", help="rotate the page")
    transpose.add_argument(
        "--scale",
        type=int,
        default=40,
        help="Verovio staff scaling percentage; bigger is bigger (default: 40)",
    )
    transpose.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help=f"resolution used when rasterising a PDF for OMR (default: {DEFAULT_DPI})",
    )
    transpose.add_argument(
        "--keep-spelling",
        dest="simplify_enharmonics",
        action="store_false",
        help="do not respell a destination key that has an easier enharmonic twin",
    )
    transpose.add_argument(
        "--no-text-chords",
        dest="transpose_text_chords",
        action="store_false",
        help="leave chord names that OMR could not parse as untouched text",
    )
    transpose.add_argument(
        "--keep-clefs",
        dest="plain_clefs",
        action="store_false",
        help="keep octave-displaced clefs instead of flattening OMR's usual misread",
    )
    transpose.add_argument(
        "--key-changes",
        choices=("auto", "keep", "drop"),
        default="auto",
        help=(
            "what to do with mid-score key signature changes: auto drops ones "
            "that revert within a few bars, drop keeps only the first, keep "
            "leaves them alone (default: auto)"
        ),
    )
    transpose.add_argument(
        "--no-preprocess",
        dest="preprocess",
        action="store_false",
        help="skip image enhancement and hand the scan to the engine as-is",
    )
    transpose.add_argument(
        "--target-interline",
        type=int,
        default=TARGET_INTERLINE,
        help=(
            "staff-line spacing in pixels to scale the scan to before "
            f"recognition (default: {TARGET_INTERLINE}); this matters far more "
            "than dpi"
        ),
    )
    transpose.add_argument(
        "--binarize",
        action="store_true",
        help="binarise the page: better chord and lyric OCR, worse notehead detection",
    )
    transpose.add_argument(
        "--no-deskew",
        dest="deskew",
        action="store_false",
        help="do not straighten the page before recognition",
    )
    transpose.add_argument(
        "--no-sharpen",
        dest="sharpen",
        action="store_false",
        help="do not sharpen after upscaling",
    )
    transpose.add_argument(
        "--chord-pass",
        action="store_true",
        help=(
            "run recognition twice -- once for notes, once binarised for chord "
            "symbols -- and merge the chords. Doubles the recognition time and "
            "is worth it on a chord chart."
        ),
    )
    transpose.add_argument(
        "--no-chord-ocr",
        dest="chord_ocr",
        action="store_false",
        help=(
            "do not re-read the band above each staff with Tesseract's LSTM "
            "engine. That pass finds chord symbols the OMR engine never "
            "proposed at all, which on a chart is most of them."
        ),
    )
    transpose.add_argument(
        "--chord-ocr-confidence",
        type=float,
        default=MIN_CONFIDENCE,
        metavar="PERCENT",
        help=(
            "how sure the LSTM pass must be before a word is offered to the "
            f"chord grammar (default: {MIN_CONFIDENCE:g})"
        ),
    )
    transpose.add_argument(
        "--no-chord-repair",
        dest="repair_chords",
        action="store_false",
        help="do not re-spell chord symbols the text recogniser mangled",
    )
    transpose.add_argument(
        "--drop-text",
        action="store_true",
        help="delete floating text that OMR could not read (garbled lyrics)",
    )
    transpose.add_argument(
        "--keep-credits",
        dest="strip_credits",
        action="store_false",
        help="keep the page credits OMR produced instead of redrawing the title block",
    )
    transpose.add_argument(
        "--title-suffix",
        help="text appended to the title, e.g. '(in C)'",
    )
    transpose.add_argument(
        "--workdir",
        type=Path,
        help="directory for intermediates (default: a temporary directory)",
    )
    transpose.add_argument(
        "--musicxml",
        type=Path,
        help="also write the transposed MusicXML here",
    )
    transpose.add_argument("-q", "--quiet", action="store_true", help="only print the PDF path")

    subparsers.add_parser("engines", help="list OMR engines and whether they are usable")
    subparsers.add_parser("renderers", help="list engraving backends")

    serve = subparsers.add_parser("serve", help="run the web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--data-dir",
        type=Path,
        help="where uploads and results are stored (default: a temporary directory)",
    )
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "transpose":
            return _cmd_transpose(args)
        if args.command == "engines":
            for line in describe_engines():
                print(line)
            return 0
        if args.command == "renderers":
            for line in describe_renderers():
                print(line)
            return 0
        if args.command == "serve":
            return _cmd_serve(args)
    except TransposerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        print("interrupted", file=sys.stderr)
        return 130

    parser.error(f"unknown command {args.command!r}")
    return 2


def _cmd_transpose(args: argparse.Namespace) -> int:
    output = args.output or _default_output(args.input, args.target)

    options = PipelineOptions(
        target=args.target,
        engine=args.engine,
        renderer=args.renderer,
        direction=args.direction,
        octave_shift=args.octave_shift,
        instrument=args.instrument,
        simplify_enharmonics=args.simplify_enharmonics,
        transpose_text_chords=args.transpose_text_chords,
        plain_clefs=args.plain_clefs,
        key_changes=args.key_changes,
        drop_text=args.drop_text,
        strip_credits=args.strip_credits,
        repair_chords=args.repair_chords,
        chord_pass=args.chord_pass,
        chord_ocr=args.chord_ocr,
        chord_ocr_confidence=args.chord_ocr_confidence,
        preprocess=args.preprocess,
        target_interline=args.target_interline,
        deskew=args.deskew,
        sharpen=args.sharpen,
        binarize=args.binarize,
        paper=args.paper,
        landscape=args.landscape,
        scale=args.scale,
        dpi=args.dpi,
        title_suffix=args.title_suffix,
    )

    def progress(stage: str, message: str) -> None:
        if not args.quiet:
            print(f"[{stage}] {message}", file=sys.stderr)

    result = run(args.input, output, options, workdir=args.workdir, progress=progress)

    if args.musicxml:
        args.musicxml.parent.mkdir(parents=True, exist_ok=True)
        args.musicxml.write_bytes(result.musicxml.read_bytes())

    if args.quiet:
        print(result.pdf)
    else:
        print()
        print(result.summary())
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "error: the web UI needs extra packages: pip install 'transposer[web]'",
            file=sys.stderr,
        )
        return 2

    from .web.app import create_app

    if args.reload:
        # uvicorn's reloader needs an import string rather than an app object.
        import os

        if args.data_dir:
            os.environ["TRANSPOSER_DATA_DIR"] = str(args.data_dir)
        uvicorn.run(
            "transposer.web.app:app",
            host=args.host,
            port=args.port,
            reload=True,
        )
        return 0

    uvicorn.run(create_app(data_dir=args.data_dir), host=args.host, port=args.port)
    return 0


def _default_output(source: Path, target: str) -> Path:
    slug = "".join(ch for ch in target if ch.isalnum() or ch in "+-#") or "transposed"
    return source.with_name(f"{source.stem}-{slug}.pdf")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
