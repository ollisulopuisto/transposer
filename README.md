# transposer

Read sheet music with optical music recognition, transpose it to any key, and
engrave a new PDF. Self-hosted: no accounts, no uploads to anyone else's server.

```
scan.pdf ──▶ OMR ──▶ MusicXML ──▶ cleanup ──▶ transpose ──▶ engrave ──▶ out.pdf
```

```bash
transposer transpose rainbow.pdf --to C -o rainbow-in-C.pdf
```

```
engine     : audiveris
renderer   : verovio (1 page(s))
transposed : E- major → C major (m-3, -3 semitones); source key signature; 95 notes; 2 chord symbols
pdf        : rainbow-in-C.pdf
musicxml   : /tmp/transposer-xyz/transposed.musicxml
```

There is also a web UI (`transposer serve`) and a Docker image.

## What it does

* **Reads** PDFs, images, MusicXML, MIDI and ABC.
* **Recognises** the music with a pluggable OMR backend — Audiveris, oemer or
  Mozart, whichever is installed.
* **Repairs** the mistakes OMR reliably makes, and tells you about each one.
* **Transposes** notes, key signatures, chord symbols, and chord names that OMR
  left as plain text. Target a key (`C`, `F#m`, `Bb major`, `C-duuri`) or an
  interval (`-M3`, `+5`).
* **Writes for transposing instruments** — ask for concert C and get D for a
  B♭ trumpet.
* **Engraves** a fresh PDF with Verovio, or MuseScore if you have it.
* **Keeps the MusicXML**, so you can fix a misread note in a notation editor and
  re-render rather than starting over.

## Install

```bash
git clone https://github.com/ollisulopuisto/transposer
cd transposer
python -m venv .venv && source .venv/bin/activate
pip install -e '.[web]'
```

That gives you a working pipeline for MusicXML and MIDI input plus PDF output.
For scans you need an OMR engine — see below — then:

```bash
transposer engines     # what is installed and what is missing
transposer renderers
```

### Docker (everything included)

```bash
docker compose up --build      # http://127.0.0.1:8000
```

The image builds Audiveris from source, fetches Tesseract language data and
Mozart, and starts the web UI. First build takes several minutes.

## OMR engines

`transposer` does not implement optical music recognition; it orchestrates
engines that do, and normalises their output. Pick with `--engine`, or leave it
on `auto` to take the best available.

| Engine | Good at | Needs | Reads PDFs |
|---|---|---|---|
| `audiveris` | real scans: grand staves, chord symbols, lyrics, repeats | JDK + a source build | yes |
| `oemer` | piano scores, no Java required | `pip install 'transposer[oemer]'`, ~200 MB of weights on first run | no |
| `mozart` | clean printed single-staff treble-clef music | a checkout + `pip install 'transposer[mozart]'` | no |
| `passthrough` | MusicXML/MIDI/ABC input | nothing | n/a |

### Audiveris (recommended)

```bash
scripts/install_audiveris.sh
export TRANSPOSER_AUDIVERIS=".../app/build/install/app/bin/Audiveris"
```

Two things are easy to get wrong:

* **Chord names are off by default.** This project turns the switch on for you.
* **Lyrics and chord names need Tesseract data that includes the *legacy*
  engine.** Audiveris initialises Tesseract in legacy mode, but most distributions
  ship the `tessdata_fast` files, which are LSTM-only — you get
  `Tesseract couldn't load any languages`. Fetch the full file:

  ```bash
  mkdir -p third_party/tessdata
  curl -Lo third_party/tessdata/eng.traineddata \
    https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/eng.traineddata
  export TESSDATA_PREFIX="$PWD/third_party/tessdata"
  ```

### Mozart

[aashrafh/Mozart](https://github.com/aashrafh/Mozart) binarises the page,
strips the staff lines and classifies each glyph with an HOG + MLP model. It is
small, fast and completely offline.

```bash
scripts/fetch_mozart.sh
pip install -e '.[mozart]'
export TRANSPOSER_MOZART_DIR="$PWD/third_party/mozart"
transposer transpose scale.png --to D --engine mozart -o scale-in-D.pdf
```

Know what it is for. Mozart reads **one treble-clef staff of single-voice
printed music**. It has no concept of key signatures, grand staves, lyrics,
chord symbols, repeats or multiple voices, and it emits a bracket notation
rather than MusicXML. This project ships a parser for that notation
(`transposer.omr.mozart_notation`) and works around two things upstream does
not handle on a current stack: four-channel input images, and scikit-image
having tightened `rgb2gray` since Mozart was written. Point it at a lead sheet
and you will get nonsense; point it at printed monophonic music and it works.

## Using it

### Command line

```bash
# A key
transposer transpose scan.pdf --to C
transposer transpose scan.pdf --to F#m
transposer transpose scan.pdf --to "Bb major"
transposer transpose scan.pdf --to C-duuri      # Finnish
transposer transpose scan.pdf --to a-molli

# An interval
transposer transpose scan.pdf --to -M3          # down a major third
transposer transpose scan.pdf --to +5           # up five semitones
transposer transpose scan.pdf --to "down 2"

# Which way to move, when a key allows both
transposer transpose scan.pdf --to C --direction up
transposer transpose scan.pdf --to C --octave -1

# Written for a transposing instrument: sounds in C, reads in D
transposer transpose scan.pdf --to C --for-instrument bb-trumpet

# Page and engraving
transposer transpose scan.pdf --to C --paper letter --scale 45 --landscape

# Keep the intermediates to inspect or edit
transposer transpose scan.pdf --to C --workdir ./work --musicxml out.musicxml
```

`transposer transpose --help` lists everything.

### Web UI

```bash
transposer serve --port 8000 --data-dir ./data
```

Upload a scan, pick a key, watch the job run, then download the PDF or the
MusicXML. Recognition takes minutes, so uploads become background jobs the page
polls; two run at a time.

The API underneath is small enough to script against:

| Route | Purpose |
|---|---|
| `POST /jobs` | multipart upload, returns a job id |
| `GET /jobs/{id}` | status, warnings, result links |
| `GET /jobs/{id}/pdf` | the transposed PDF |
| `GET /jobs/{id}/musicxml` | the transposed MusicXML |
| `GET /jobs/{id}/preview/{page}` | an SVG page for in-browser preview |
| `GET /healthz` | liveness plus the list of usable engines |

### Python

```python
from transposer import PipelineOptions, run

result = run("scan.pdf", "out.pdf", PipelineOptions(target="C"))
print(result.transposition.summary())
for warning in result.warnings:
    print("!", warning)
```

## Reading the output critically

OMR is not a solved problem, and the honest workflow is *recognise, then check*.
This project is built around that: every run reports what it did, and keeps the
MusicXML so you can correct it.

The cleanup pass fixes the mistakes that recur on almost every scan:

| Mistake | What happens without a fix | Flag |
|---|---|---|
| A plain treble clef read as treble-8vb | the whole part sounds an octave low | `--keep-clefs` to disable |
| A one- or two-bar "modulation" from a smudge | transposition propagates a wrong key | `--key-changes keep\|drop\|auto` |
| Lyrics that failed to attach to notes | garbled text piles up over the staff | `--drop-text` to delete it |
| Stray OCR promoted to page credits | fragments stacked above the first system | `--keep-credits` to disable |
| An unreadable composer name | nonsense printed under the title | part of the cleanup pass |

What it does **not** fix, because no heuristic can: wrong notes. If the engine
misread a bar, the transposition faithfully moves the wrong notes. Watch for
`Audiveris flagged N measure(s) whose rhythm did not add up` — those bars are the
ones to check first.

Text recognition is the weakest link, and it is resolution-bound. A scan whose
embedded image is around 100 dpi will produce readable noteheads and unreadable
lyrics; `Fm7` becomes `Fm?` and `C7` becomes `CT`. Chord names that survive as
text are still transposed correctly (`Bb7` → `G7`) — but a chord name the OCR got
wrong stays wrong. Use `--dpi 400` on low-resolution input, and expect to fix
chord symbols by hand on anything below about 200 dpi.

## Transposition details

**Direction.** E♭ to C is either down a minor third or up a major sixth.
`--direction auto` (the default) takes the shorter move, with ties going down,
which is usually what a singer wants. `--octave` stacks whole octaves on top.

**Spelling.** Everything stays diatonic. Transposing keeps the interval's
spelling, so G♭ moves to E♭ rather than D♯, and destination keys with an easier
enharmonic twin get respelled — a request that lands in G♯ major produces A♭
major. `--keep-spelling` turns that off.

**Chord symbols** move whether or not the OMR engine parsed them. Real
`<harmony>` elements are transposed and their printed figure regenerated; text
that merely *looks* like a chord symbol has its root — and the bass after a
slash — rewritten while the quality suffix is left verbatim, since `m7b5` and
`sus4` mean the same thing in every key. Roots nobody writes (`E#`, `Cb`) are
respelled. Prose is left alone: `Somewhere` is not a chord.

**Transposing instruments** compose with the key target. `--to C
--for-instrument bb-trumpet` means "make it sound in C", so the part is written
in D.

**Key names** follow English convention: `B` is B natural and B flat is `Bb`.
`H` is accepted as a synonym for B. Quality words in English, German and Finnish
all work: `major`/`dur`/`duuri`, `minor`/`moll`/`molli`. With no quality word,
upper case is major and lower case is minor, so `c` is C minor.

## Development

```bash
pip install -e '.[web,dev]'
pytest
```

The suite covers key parsing, transposition, chord-symbol text, the cleanup
passes, the Mozart notation parser, the full pipeline and the web API — all
without needing an OMR engine installed, because MusicXML input exercises
everything after recognition.

Layout:

```
src/transposer/
  keys.py            target parsing, interval resolution
  transpose.py       key detection, transposition, reporting
  chordtext.py       chord symbols that arrived as plain text
  cleanup.py         repairs for common OMR mistakes
  ingest.py          input normalisation, PDF rasterising, PDF merging
  pipeline.py        the end-to-end job
  omr/               recognition backends + the Mozart notation parser
  render/            engraving backends
  web/               FastAPI app and job queue
```

Adding an OMR engine means subclassing `OmrEngine`, returning a path to
MusicXML from `recognize()`, and adding it to the registry in `omr/__init__.py`.

## Copyright

Sheet music is usually under copyright. This tool runs on your own machine and
sends nothing anywhere, but transposing a copyrighted work and sharing the
result is still distribution — that is between you and the rights holder.
