# arr-service

> Stop Sonarr and Radarr from re-downloading the same file forever.

`arr-service` pulls, analyzes, and rewrites Sonarr/Radarr quality profile settings over
the v3 API. It was built to find the cause of runaway upgrade churn — a format cutoff no
release can satisfy, over a scoring table the app then climbs one rung at a time — and to
fold duplicate profiles together. It is an operator's CLI for a home media stack: no
daemon, no scheduler, and nothing on disk except snapshots you can restore from.

## Usage

```bash
cp .env.example .env
$EDITOR .env          # add each instance URL + API key
./arr.py check        # verify connectivity
```

API keys live in each app under **Settings → General → Security → API Key**.
`.env` is gitignored.

```bash
./arr.py report                  # pull + analyze, read-only
./arr.py pull                    # snapshot state to snapshots/<stamp>/
./arr.py report --from snapshots/20260820-180835   # re-analyze without hitting the API
```

Write commands are **dry-run by default** and print exactly what they would do.
Add `--execute` to apply. Every `--execute` takes a snapshot first.

```bash
# Stop a profile upgrading past WEBDL-1080p, and clamp the format cutoff
./arr.py set-cutoff radarr 2 --quality WEBDL-1080p --format-score 0
./arr.py set-cutoff radarr 2 --quality WEBDL-1080p --format-score 0 --execute

# Unblock a profile whose minimum score rejects everything
./arr.py set-cutoff radarr 12 --min-score 0 --format-score 0 --execute

# Never upgrade at all: grab the first acceptable release and stop
./arr.py set-cutoff sonarr 3 --disable-upgrades --execute

# Fold profile 5 into profile 2 (reassigns media + import lists, then deletes 5)
./arr.py merge radarr 5 2
./arr.py merge radarr 5 2 --execute

# Rebuild a profile's allowed qualities, ordered by resolution
./arr.py reshape sonarr 4 --allow WEBDL-1080p,Bluray-1080p --cutoff WEBDL-1080p

# Route media by the resolution already on disk
./arr.py sort radarr --uhd 7 --hd 2 --source 5:hd --source 6:uhd

# Instance-wide settings
./arr.py config --propers doNotPrefer --execute        # both instances
./arr.py config radarr --recycle-bin /media/recyclebin/movies --execute

# Roll back
./arr.py restore snapshots/20260820-180853-pre-set-cutoff --execute
```

Use `--instance NAME` (repeatable) on `check`/`pull`/`report` to limit scope.

`--quality` accepts either a standalone quality or a quality *group*. Qualities nested in
a group (e.g. `WEBDL-2160p` inside `WEB 2160p`) cannot be a cutoff on their own — the app
requires the group id — so naming a member resolves to its group and says so. An invalid
name lists the valid cutoffs for that profile.

## What the report flags

- **Unsatisfiable format cutoff, with a ladder to climb** (critical) — `cutoffFormatScore`
  is higher than any release can score, *and* the profile has a spread of positive custom
  format scores. Radarr/Sonarr keep accepting every better-scoring release forever, walking
  up the ladder (e.g. DD 750 → DTS 1250 → DTS-HD MA 2500 → TrueHD ATMOS 5000). This is the
  expensive one: it re-downloads whole files to chase an audio tag.
- **Unsatisfiable format cutoff, no ladder** (warning) — same broken setting, but nothing
  scores higher than what is already on disk, so it costs little in practice. Worth fixing,
  not urgent. Distinguishing these two is the point of the gradient check.
- **Unsatisfiable minimum score** (critical) — `minFormatScore` exceeds what any release can
  reach, so *every* release is rejected and the profile grabs nothing at all. Newer Radarr
  refuses to save a profile in this state, which makes it hard to fix in the UI.
- **Cutoff at top quality** — the profile hunts until it finds the single best release.
- **Propers/repacks set to "Prefer and Upgrade"** (critical) — an instance-wide setting that
  replaces files which have already met their cutoff, bypassing every profile cutoff below
  it. Score repacks with a custom format and leave this on "Do Not Prefer".
- **No Recycle Bin** (warning) — a file replaced by an upgrade is deleted outright.
- **Exact duplicates** — profiles whose behaviour is byte-identical apart from name.
- **Near duplicates** — same allowed qualities and cutoff, differing only in formats.
- **Unused profiles** — nothing references them; safe to delete.

## Other usage details

**Requirements.** Python 3.9+. No third-party dependencies.

**Configuration.** Instances are `<NAME>_URL` / `<NAME>_API_KEY` pairs in `.env`. The prefix
selects the app type, so any number of instances works — suffix the name for extras, e.g.
`SONARR_4K_URL`. A real environment variable overrides the `.env` value.

**Where data lives.** `snapshots/<timestamp>[-label]/<instance>.json` holds a full pull and
is the rollback source for `restore`. Both `snapshots/` and `.env` are gitignored.

**Gotchas.**

Neither app has a native "unmonitor at cutoff". The quality profile *is* the control:
once the quality cutoff and `cutoffFormatScore` are both satisfied, the item stops being
considered for upgrades. Unmonitoring instead would also stop missing-episode grabs.

Radarr's **Cutoff Unmet** list and `wanted/cutoff` endpoint reflect the *quality* cutoff
only — they do not show items held open by an unsatisfiable format score. So that count
is not a measure of format-driven churn, and a fix to `cutoffFormatScore` will not move it.

`restore` does not re-point import lists; if a merged profile had one attached, set it back
by hand.

A quality **not listed** in a profile ranks below every listed one, so moving media to a
narrower profile can trigger a replacement download. Use `sort` rather than a bulk edit.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design and the reasoning behind it
- [docs/DEV-SETUP.md](docs/DEV-SETUP.md) — environment setup and safe working habits
- [docs/CLAUDE.md](docs/CLAUDE.md) — project memory for Claude Code

## Attributions

Built against the [Sonarr](https://sonarr.tv/) and [Radarr](https://radarr.video/) v3 APIs.
Custom format scoring conventions follow the [TRaSH Guides](https://trash-guides.info/).
No third-party code is vendored — the tool is standard library only.

Licensed under the MIT License. See [LICENSE](LICENSE).

## TODO

- No test suite. `analyze.py` is pure and takes snapshot dicts, so it is the place to
  start; a saved snapshot works as a fixture. CI runs an import smoke test until then.
- Integration tests need a live instance and cannot run in CI.
- `restore` does not repoint import lists after a `merge`.
- The repository secret `ANTHROPIC_API_KEY` is not set yet, so the CI review job is
  skipped. Add it under **Settings → Secrets and variables → Actions** to enable it.
- `sort` covers 2160p and everything below. Finer resolution tiers are not supported.
