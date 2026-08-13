# Deploying modulaattori

`transposer` runs on `dev.sulopuis.to` as the service **modulaattori**, blue/green
like the rest of that host. Infrastructure is declared in the `infra` repo
(`registry.json`) — never edited on the server.

| | |
|---|---|
| Production | https://modulaattori.sulopuis.to |
| Staging | https://modulaattori.s.sulopuis.to |
| Image | `ghcr.io/ollisulopuisto/modulaattori` |
| Ports | blue 10160, green 10161 → container 8000 |
| Health | `/healthz` |
| Data | `/opt/stacks/modulaattori/data` → `/data` |

## The pipeline

Push to `main` → lint and tests → image built and pushed to GHCR under CalVer
→ staging slot updated. Promotion is manual, always.

A branch can be put on staging before its PR is merged:

```bash
gh workflow run pipeline.yml --ref <branch>
```

A manual run pushes the image but does **not** move `:latest`, which only ever
means `main`.

## Commands

Name the version in every command; the version is the CalVer string without the
`v`.

```bash
# Put a version on staging
ssh dst@dev.sulopuis.to "/opt/stacks/scripts/deploy.sh modulaattori deploy 26.08.13.8"

# Make the version now on staging the live one
ssh dst@dev.sulopuis.to "/opt/stacks/scripts/deploy.sh modulaattori promote 26.08.13.8"

# Back to the version that preceded the last promotion
ssh dst@dev.sulopuis.to "/opt/stacks/scripts/deploy.sh modulaattori rollback"

# What is actually running
curl -s https://modulaattori.sulopuis.to/healthz
curl -s https://modulaattori.s.sulopuis.to/healthz
```

`/healthz` reports the version and which OMR engines the container can use. If
`engines` does not contain `audiveris`, the image is broken — that is the whole
reason the container is 966 MB.

## What this service needs that the others do not

* **A JVM.** Audiveris is a Java application, built from source in the image's
  first stage. The runtime keeps only the JRE.
* **The `tesseract` binary.** Not just the language data: the chord-band pass
  shells out to it with `--oem 1`. Audiveris reaches Tesseract through its own
  JNI bindings and needs the traineddata file, which is a separate thing. Both
  are in the image; if `tesseract` goes missing the pass skips with a note in
  the job warnings rather than failing, so watch for that rather than an error.
* **Time.** Recognising a page takes minutes, not milliseconds. The web UI runs
  jobs in a background worker and the browser polls, so no request is long-lived
  — but do not put this behind anything with a short upstream timeout.
* **Body size.** Caddy allows 64 MB, matching what the app accepts. A 20-page
  scan at 400 dpi fits.

## Adding it to a new host

Restarting Caddy is not optional. The service's routes directory is a new volume
on the Caddy container, and a running container does not pick up a new mount from
a `daemon-reload` — the route symlink dangles inside it and config validation
refuses the reload with a confusing "no such file" for a symlink that plainly
exists on the host. Restart Caddy after `sync_to_host.sh --apply`, before the
first deploy. It is briefly visible on every site the host serves.

DNS must exist before the first reload, or Caddy burns ACME attempts on
NXDOMAIN and then backs off. Both names need an A record:

```
modulaattori.sulopuis.to      A   65.108.211.17
modulaattori.s.sulopuis.to    A   65.108.211.17
```
