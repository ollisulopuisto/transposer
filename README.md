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
transposed : E- major → C major (m-3, -3 semitones); source key signature; 108 notes; 12 chord symbols
pdf        : rainbow-in-C.pdf
musicxml   : /tmp/transposer-xyz/transposed.musicxml
```

There is also a web UI (`transposer serve`) and a Docker image.

## What it does

* **Reads** PDFs, images, MusicXML, MIDI and ABC.
* **Recognises** the music with a pluggable OMR backend — Audiveris, oemer or
  Mozart, whichever is installed.
* **Repairs** the mistakes OMR reliably makes, and tells you about each one.
* **Re-reads the chord band** with Tesseract's LSTM engine, which finds the
  symbols the OMR engine never proposed at all — on a chart, most of them.
* **Puts the words back on the notes**, so lyrics an engine handed back as
  free-floating text lay out as verses instead of piling up.
* **Measures** the staff-line spacing and scales the page to what the engine
  wants, instead of guessing at a dpi number.
* **Transposes** notes, key signatures, chord symbols, and chord names that OMR
  left as plain text — including ones it mangled into `Fm?` or `CT`. Target a
  key (`C`, `F#m`, `Bb major`, `C-duuri`) or an interval (`-M3`, `+5`).
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
For scans you need an OMR engine — see below — and, for the chord-band pass,
the `tesseract` binary (`brew install tesseract` or `apt install tesseract-ocr`).
Then:

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

# A chord chart: two recognition passes, chords merged from the sharper one
transposer transpose chart.pdf --to C --chord-pass

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

## Resolution, and what preprocessing can and cannot do

Recognition quality is governed less by a scan's nominal dpi than by one
number: **interline**, the staff-line spacing in pixels. Audiveris' filters are
written in fractions of interline and assume roughly 20; Tesseract wants text
around 30 pixels tall. A page that is geometrically fine at 144 dpi can still
have an interline of 7, and at that size everything downstream works near its
rounding limits.

So `transposer` does not resample to a dpi figure. It *measures* the interline
and scales to hit a target — the same decision a person makes when they zoom a
scan until the staff looks right. It also reads a scanned PDF at the resolution
the scan actually holds, because rendering a 144 dpi image at 300 dpi
interpolates once and then the enhancement pass interpolates again.

```
enhanced page 1: interline 7px → 20px; scale 2.86x; 833×1079 → 2380×3083;
                 steps: autocontrast, upscale 2.86x (Lanczos), unsharp mask
```

Be clear about the ceiling: **upscaling invents nothing.** A 144 dpi scan holds
144 dpi of detail whatever you do to it. What preprocessing buys is putting the
detail that *is* there onto a grid the downstream code handles well, and undoing
the blur that resampling introduces. On a real 144 dpi lead-sheet scan
(interline 7px, 33 chord symbols), measured against Audiveris:

| Input | Notes | Chord symbols recovered |
|---|---|---|
| 144 dpi as scanned | recognition failed outright | — |
| blind `--dpi 400` upscale | 177 | 2 |
| measured scale to interline 20 | 189 | 7 |
| the same, binarised | 134 | 10 |
| **+ chord repair, two passes** | **108 notes in one part** | **11, all correct** |

The relevant flags:

| Flag | Effect |
|---|---|
| `--target-interline N` | staff spacing to scale to (default 20); matters far more than dpi |
| `--binarize` | sharper text, softer noteheads: better chords, worse notes |
| `--chord-pass` | run recognition twice and take chords from the binarised pass |
| `--no-chord-ocr` | skip the LSTM re-read of the band above each staff |
| `--keep-text-floating` | leave recognised words as free text instead of attaching them as lyrics |
| `--no-preprocess` | hand the scan to the engine untouched |
| `--no-deskew`, `--no-sharpen` | turn off individual steps |

## Chord symbols

On a lead sheet the chord symbols are the point, and they are what OCR handles
worst. Audiveris drives Tesseract's *legacy* engine, which on chart fonts
confuses a small, stable set of glyphs:

```
Fm7 -> Fm?      C7 -> CT       Am7 -> Am?
Bb7 -> BW       Eb -> E'P      Gm7 -> Gm"!
```

What matters is what is *right* in that list: the root letter survives and the
symbol is in the correct place. Only the quality suffix is scrambled. So
`transposer` re-spells rather than re-detects — it matches the suffix against
the qualities that appear on real charts, under an edit distance where known
OCR confusions are cheap and everything else is expensive.

Roots are never guessed. Misreading a root transposes to the wrong chord, which
is worse than no chord at all, so a symbol whose first character is not a
literal A–G is left as text. Text that repairs confidently is promoted to a real
MusicXML `<harmony>` element, which means it transposes as harmony — root, bass
and quality together — and engraves in chord-symbol style.

On the scan above this took chord recovery from 2 symbols to 11, with no false
positives: 30 lines of surrounding prose (`Somewhere`, `poco rit.`,
`Harold Arlen`, `D.C. al Fine`) were all correctly rejected.

`--no-chord-repair` turns it off.

### Re-reading the chord band

Repair can only fix a symbol the engine proposed. On a 33-chord chart Audiveris
proposed 11 — the other 22 were never detected at all, and nothing downstream
can recover a symbol that is not in the MusicXML.

That is a limitation of *which* Tesseract engine Audiveris uses, not of
Tesseract. It initialises the pre-4.0 **legacy** classifier; Tesseract 4 and 5
also ship an **LSTM** line recogniser that is markedly better on short tokens in
display faces. So `transposer` runs its own pass with `--oem 1`, over just the
strip of page where chord symbols live:

```
find the staves ──▶ crop the band above each ──▶ OCR it (LSTM)
    ──▶ keep what the chord grammar accepts ──▶ place it by x position
```

Cropping to the band is what makes this work: no noteheads, no beams, no lyrics,
so the recogniser is not fighting the score. Both of Tesseract's relevant page
segmentation modes are run and the readings merged by position, because neither
wins everywhere.

Placement is by systems, not by a global bar count: the staves found on the page
must divide evenly into the systems the score was broken into, and within a
system the detected barlines are used when there are as many as the score says
and an even division otherwise. Positions are quantised to the beat. If the page
and the score disagree about the systems, **nothing is placed** and the run says
so — a chord in the wrong bar is worse than a chord that was never read.

Every candidate still goes through the same chord grammar as the repair pass, so
prose is rejected the same way.

Where a chord lands is decided by the engraved measure widths MusicXML records,
not by an even division of the system and not by barlines re-detected from the
pixels. Those widths are what the engine measured off this very page, and they
are the only source that knows the first bar of a system is half again as wide
as its neighbours because it carries the clef, the key signature and a repeat.
An even division puts a chord written over bar 1 into bar 2. Detected barlines
are the fallback: on a busy page there are three times as many candidates as
there are bars, because stems, repeat signs and double bars all read as one.

This needs the `tesseract` binary on `PATH` (`brew install tesseract`,
`apt install tesseract-ocr`; the Docker image has it). Without it the pass is
skipped with a note. `--no-chord-ocr` turns it off, and
`--chord-ocr-confidence` sets how sure the recogniser must be before a word is
offered to the grammar.

Two bugs this pass exposed, both now fixed and both affecting the older paths
too: the LSTM engine renders a `7` as `/` on chart fonts about as reliably as
the legacy engine renders it as `?`, and music21 will not build a chord from the
figure `Bb` — it reads the `b` as a quality abbreviation and raises — so every
bare flat triad on a chart was being read, repaired, and then silently dropped.
Slash bass notes (`C/G`, `Dm7/F`) now survive as well, instead of failing the
grammar and being left as text.

## Running it where other people can reach it

The upload endpoint is anonymous and answers each request by starting a JVM that
runs for minutes with several gigabytes of heap. That is an expensive thing to
offer the internet, so an open instance limits it:

| Limit | Default | Environment variable |
|---|---|---|
| Recognition jobs per client per hour | 12 | `TRANSPOSER_JOBS_PER_HOUR` |
| Unfinished jobs per client at once | 2 | `TRANSPOSER_JOBS_IN_FLIGHT` |
| Requests per client per minute | 240 | `TRANSPOSER_REQUESTS_PER_MINUTE` |
| Upload size | 64 MB | — |
| Pages per upload | 40 | — |
| Pixels per page | 80 M | — |
| How long results are kept | 1 hour | — |

The in-flight limit is the one that matters most: an hourly quota still lets one
client queue a hundred scans and deny the service to everyone else while they
run.

**Uploads are deleted as soon as recognition finishes**, and everything else a
job produced is deleted an hour later, whichever comes first — plus a disk
budget that evicts the oldest finished jobs early if scans are large. A job id
is a 128-bit secret and is the only thing needed to download that job's result,
so treat the link as private.

`GET /jobs` lists **only the caller's own** jobs, keyed by a cookie. It is not
authentication: it stops an anonymous instance showing every visitor what
everyone else is transposing, which is what it did before. If the music matters,
put the whole thing behind a password at the proxy.

Behind a reverse proxy, set `TRANSPOSER_TRUST_FORWARDED=1` so the limits count
real clients rather than lumping the internet together as one. The Docker image
sets it, because it is always behind Caddy. Do **not** set it on a directly
exposed instance: any client can send that header and mint itself a fresh quota.

## Reading the output critically

OMR is not a solved problem, and the honest workflow is *recognise, then check*.
This project is built around that: every run reports what it did, and keeps the
MusicXML so you can correct it.

The cleanup pass fixes the mistakes that recur on almost every scan:

| Mistake | What happens without a fix | Flag |
|---|---|---|
| Chord symbols mangled by OCR | chart transposes with the wrong chords | `--no-chord-repair` |
| A plain treble clef read as treble-8vb | the whole part sounds an octave low | `--keep-clefs` to disable |
| A one- or two-bar "modulation" from a smudge | transposition propagates a wrong key | `--key-changes keep\|drop\|auto` |
| Lyrics that failed to attach to notes | garbled text piles up over the staff | `--drop-text` to delete it |
| Stray OCR promoted to page credits | fragments stacked above the first system | `--keep-credits` to disable |
| An unreadable composer name | nonsense printed under the title | part of the cleanup pass |

What it does **not** fix, because no heuristic can: wrong notes. If the engine
misread a bar, the transposition faithfully moves the wrong notes. Watch for
`Audiveris flagged N measure(s) whose rhythm did not add up` — those bars are the
ones to check first.

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
  preprocess.py      staff measurement, deskew, scaling, sharpening
  transpose.py       key detection, transposition, reporting
  chordtext.py       chord symbols that arrived as plain text
  chordocr.py        re-spelling chord symbols OCR mangled
  chordband.py       re-reading the band above each staff with Tesseract LSTM
  lyrics.py          putting recognised words back on the notes they belong to
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
