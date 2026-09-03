# CLAUDE.md - arr-service

Project memory for Claude Code. Symlinked to the repo root as `CLAUDE.md`.

## Project Overview

`arr-service` reads, analyzes, and rewrites Sonarr and Radarr quality profiles over
the v3 API. It exists to stop runaway upgrade churn — profiles that re-download the
same film or episode forever to chase a better custom-format score — and to fold
duplicate profiles together.

The tool is an operator's CLI, not a service. It runs on demand from a workstation
against instances on the LAN. There is no daemon, no scheduler, and no state on disk
except snapshots.

### Key Features

- Pull full instance state to a timestamped snapshot
- Analyze profiles offline and rank findings by severity
- Clamp a quality cutoff, a format cutoff, or a minimum score
- Merge a duplicate profile into another, and repoint every referrer first
- Rebuild a profile's quality list, ordered by resolution
- Route media between profiles by the resolution already on disk
- Restore profiles from any snapshot

### Project Context

The problem is a Radarr default. `cutoffFormatScore` ships at 10000, which no real
release reaches, so the cutoff is never satisfied. If the custom-format table also
has a score gradient, the app climbs it one rung at a time and re-downloads whole
files for an audio tag. `analyze.py` detects that state and grades it by the size of
the gradient.

## Technology Stack

- **Language**: Python 3.9+ (the floor, not the dev version)
- **Dependencies**: none — standard library only, by design
- **Lint**: ruff (config in `pyproject.toml`)
- **CI**: GitHub Actions (`.github/workflows/Review.yml`)
- **Target APIs**: Sonarr v3, Radarr v3

Keep the zero-dependency rule. It is why the tool runs on any box that has Python,
including a NAS or a container shell. Add a dependency only if the standard library
truly cannot do the job, and say why in the pull request.

## Project Structure

```
arr.py                  CLI: argument parsing, report rendering
arr_service/
  config.py             discovers instances from <NAME>_URL / <NAME>_API_KEY pairs
  client.py             ArrClient — v3 wrapper over urllib, paging helpers
  snapshot.py           pull() full state, write()/load() timestamped snapshots
  analyze.py            pure functions over snapshot dicts -> findings
  apply.py              write operations, each returning reviewable Action objects
  reshape.py            rebuild a profile's quality list, sort media by resolution
docs/                   all documentation except README.md
.github/workflows/      CI
snapshots/              gitignored — pulled state and rollback source
_resources/             gitignored — dev references, never committed
```

### Naming Convention

Python modules and files use `snake_case`. This overrides the PascalCase house rule,
because PascalCase file names fight imports, PEP 8, and Python tooling. The global
standard permits this: respect the ecosystem when the two conflict.

Constants and environment variables stay `UPPER_SNAKE_CASE`, as everywhere else.

## Claude Code Conventions

### Plan mode

Use plan mode for anything past a trivial edit. Get approval before you write code
for a multi-file change, a refactor, a new subcommand, or any change to CI. A change
to `apply.py` or `reshape.py` always needs a plan first, because those modules write
to a live media library.

### Subagents

Subagents are allowed and are useful for parallel work: research spikes, reading the
Sonarr or Radarr API docs, and multi-file audits. Keep the main thread as the
integrator. Do not let a subagent run a write command.

### Standing rules

- Use `git pull --rebase` for linear history
- `_resources/` never enters git
- All docs live in `docs/`, and `README.md` is the only root doc
- CI must be green before merge

## Safety Rules

These are the rules that protect a real media library. Do not relax them.

1. **Every write command is dry-run by default.** `--execute` is the only way to
   apply a change. Keep it that way for any new subcommand.
2. **A snapshot comes before every write.** Each `--execute` path calls
   `collect(label="pre-<op>")` first, so `restore` has a rollback point.
3. **Never commit `.env`.** It holds live API keys. It is gitignored. The example
   file is `.env.example`.
4. **Actions are data.** Every function in `apply.py` and `reshape.py` returns a list
   of `Action` objects and calls `.run()` only when `execute=True`. Dry-run and apply
   use the same code path, so the plan the operator reads is the plan that runs.
5. **Test against a snapshot, not a live instance.** `analyze` never touches the
   network. Use `./arr.py report --from snapshots/<stamp>`.

## Environment Variables

Instances are declared as matching pairs in `.env`. The prefix before the first
underscore selects the app type.

| Variable | Meaning |
|---|---|
| `SONARR_URL` | base URL of a Sonarr instance |
| `SONARR_API_KEY` | its API key, from Settings -> General -> Security |
| `RADARR_URL` | base URL of a Radarr instance |
| `RADARR_API_KEY` | its API key |

Any number of instances works. Suffix the name for more of either app, for example
`SONARR_4K_URL` and `SONARR_4K_API_KEY`. A real environment variable beats the
value in `.env`.

## Common Tasks

### Add a subcommand

1. Write the operation in `apply.py` or `reshape.py`. Return `Action` objects.
2. Add a `cmd_*` function in `arr.py`. Call `collect(..., label="pre-<op>")` when
   `args.execute` is true.
3. Register the subparser in `main()`. Add `--execute` as a flag.
4. Document the command in `README.md` under Use.

### Debug a failed write

Read the error text. `ArrClient` includes the app's own response body, which is
where Sonarr and Radarr explain a rejected profile. If a profile delete returns
HTTP 500, something still references the profile — check import lists, Radarr
collections, root folders, and autotagging.

### Re-run an analysis offline

```bash
./arr.py report --from snapshots/20260820-192231
```

## Known Gotchas

- **A nested quality cannot be a cutoff.** A quality inside a group needs the group
  id. `analyze.cutoff_target()` resolves a member name to its group and says so.
- **The app's own quality order is not monotonic in resolution.** Sonarr ranks
  HDTV-1080p below Bluray-720p. `reshape.set_shape()` re-orders by resolution first,
  so "cutoff at the bottom of the 1080p tier" behaves as an operator expects.
- **An unlisted quality ranks below every listed one.** Move a 2160p film to a
  1080p-only profile and the app replaces the file. `sort_by_resolution()` routes by
  the highest resolution a title holds, to prevent this.
- **`restore` does not repoint import lists.** If a merged profile had an import list
  attached, set it back by hand.
- **Cutoff Unmet reflects the quality cutoff only.** It does not show items held open
  by an unsatisfiable format score, so that count does not measure format churn.
- **`max_reachable_score()` is an upper bound.** It sums every positive format score,
  which no single release reaches. `file_scores()` measures what files on disk really
  scored, and catches a cutoff that sits between the two.

## Resources

- Sonarr API: https://sonarr.tv/docs/api/
- Radarr API: https://radarr.video/docs/api/
- TRaSH Guides (custom format scoring): https://trash-guides.info/
