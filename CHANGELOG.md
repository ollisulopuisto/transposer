# Changelog

What each build of modulaattori contains, newest first. CI names a build `YY.MM.DD.N`, where N is the commit count (`git rev-list --count HEAD`, .github/workflows/pipeline.yml), so a heading names the build that commit produced. Sections before 2026-09-28 were written from git history, one per day, named after that day's last build. Add each change's entry here in the same commit, under the version its build will get.

## [v26.09.28.23] - 2026-09-28

### Changed
- **Merging to main now deploys to modulaattori.** The deploy job had never deployed: it had no credentials, so it skipped and still showed green. It now logs in with a key that can only operate modulaattori, checks the server's host key, waits for the arm64 image, and fails loudly if anything is missing.

## [v26.09.28.22] - 2026-09-28

### Fixed
- **A job no longer says "done" while the upload is still on disk.** The job set its status first and deleted the upload's working copy afterwards, so anything polling for "done" could still find the user's music in `work/input` for a moment. The inputs are now deleted before the outcome is published. This race also failed CI on 2026-09-28, which blocked the v26.09.28.21 release.

## [v26.09.28.21] - 2026-09-28

### Added
- **A CHANGELOG, and the image carries it** (`/app/CHANGELOG.md`). The ops dashboard reads it from the running container and shows a version's entries when you hover it.

## [v26.08.21.20] - 2026-08-21

- arm64: buildx needs setup-buildx-action for a gha cache (build 20)
- build arm64 on a hosted ARM runner: public repos get them free (build 19)
- probe: does a public repo get a hosted ARM runner (build 18)
- Merge the OMR/transpose/engrave pipeline into main (build 17)

## [v26.08.13.16] - 2026-08-13

- Delete the intermediates too, not just the file the request wrote (build 16)
- Limit what an anonymous upload can cost, and stop leaking other people's (build 15)
- Name the bars that do not add up (build 14)
- Place chords by engraved width, and put the words back on the notes (build 13)
- Convert flat figures before music21 sees them, not after (build 12)
- Make the chord-band pass work on a real engraved page (build 11)
- Make Audiveris actually run, and stop claiming it does when it cannot (build 10)
- Document the modulaattori deployment (build 9)
- Log in to GHCR whenever the build will push (build 8)
- Make the end-to-end chord test assert placement, not yield (build 7)
- Declare the HTTP client starlette's TestClient needs (build 6)
- Drop Mozart from the image, and let a branch build reach staging (build 5)
- Add strict linting and a CI/CD pipeline (build 4)
- Re-read the chord band with Tesseract's LSTM engine (build 3)
- Enhance scans before recognition, and repair OCR'd chord symbols (build 2)

## [v26.08.12.1] - 2026-08-12

- Add self-hosted OMR, transposition and PDF engraving pipeline
