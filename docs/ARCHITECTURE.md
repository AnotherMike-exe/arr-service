# Architecture

Stdlib-only Python. No dependencies, so it runs anywhere Python 3.9+ does.

```
arr.py                  CLI: argument parsing and report rendering
arr_service/
  config.py             discovers instances from <NAME>_URL / <NAME>_API_KEY pairs
  client.py             ArrClient — thin v3 wrapper over urllib, paging helpers
  snapshot.py           pull() full state; write()/load() timestamped snapshots
  analyze.py            pure functions over snapshot dicts -> findings
  apply.py              write operations, each returning reviewable Action objects
snapshots/              gitignored; pulled state and rollback source
```

## Design decisions

**Snapshot as the unit of exchange.** `snapshot.pull()` returns a plain dict.
`analyze` consumes that dict and never touches the network, so a report can be
re-run offline against a saved snapshot and the analysis is trivially testable.

**Actions are data, not side effects.** Every function in `apply.py` builds a list
of `Action` objects and only calls `.run()` when `execute=True`. Dry-run and apply
therefore exercise the same code path — the plan you review is the plan that runs.

**Snapshot before every write.** `--execute` calls `collect(label="pre-<op>")` first,
so `restore` always has a rollback point from immediately before the change.

**Instance naming drives app type.** `SONARR*` and `RADARR*` prefixes select the app,
which determines the media endpoint (`series` vs `movie`) and the bulk-editor id field
(`seriesIds` vs `movieIds`). Multiple instances of either app are supported by suffixing
the name, e.g. `SONARR_4K_URL`.

## Profile analysis

Sonarr/Radarr profile `items[]` is an ordered worst-to-best list mixing individual
qualities and groups; groups nest their members and carry their own ids. `quality_index()`
flattens both into an id -> name map so `cutoff` (which may reference either) resolves.
Order is preserved in `quality_names()`, which is what makes "cutoff is the top allowed
quality" detectable.

`max_reachable_score()` sums every positive custom format score. That is an upper bound
— it assumes all positive formats match one release at once, which real releases never
do. Exceeding it is therefore a hard guarantee that the format cutoff is unsatisfiable,
with no false positives.

`file_scores()` closes the gap left by that upper bound by reading the score every file
on disk actually earned (`moviefile` / `episodefile`, which carry `customFormatScore` where
the movie/series list endpoints do not). A cutoff below the theoretical maximum but above
everything real is caught only by this measurement.

Severity then depends on the **score gradient**, not just satisfiability. An impossible
cutoff over a flat scoring table is inert — nothing outranks what is on disk, so nothing is
re-grabbed. The same cutoff over a tiered table (a TRaSH audio ladder, say) re-downloads
forever, one rung at a time. `GRADIENT_FLOOR` separates a real ladder from a lone
tiebreaker format worth a handful of points.

Duplicate detection uses two signatures: a full behavioural one (qualities, cutoff,
upgrade flags, format scores, language) for exact matches, and a weaker shape signature
(qualities + cutoff only) for near matches worth reviewing by hand.

## Deleting profiles

Sonarr and Radarr refuse to delete a quality profile that is still referenced, and say only
"QualityProfile [n] is in use" — no hint as to what holds it. Media is the obvious referrer.
The ones that bite:

- **Import lists** carry a `qualityProfileId`.
- **Radarr collections** carry one too, and a library can have hundreds. They keep the
  reference even while unmonitored, so a collection nobody is using still blocks the delete.

`merge_profiles()` repoints every referrer it knows about before issuing the delete. If a
delete still 500s, something else holds the profile — check `rootfolder` and `autotagging`.

## Reshaping and sorting

`reshape.set_shape()` rebuilds a profile's `items` from `qualityprofile/schema` so every
known quality is present, then orders by resolution before the app's own rank. Both apps
ship orderings that are not monotonic in resolution — Sonarr ranks HDTV-1080p *below*
Bluray-720p — so a cutoff pinned to "the bottom of the 1080p tier" would otherwise sit
above 720p content and leave it satisfied. Groups are flattened away; with the cutoff at the
bottom of a tier, grouping adds nothing that explicit ranking does not.

A quality **not listed** in a profile ranks below every listed one, so the app treats any
allowed release as an upgrade over it. That makes a careless profile change actively
destructive: move a movie holding a 2160p file onto a 1080p-only profile and it will be
replaced by a 1080p release. `sort_by_resolution()` exists to prevent that — it routes by
the resolution already on disk, using the *highest* resolution a series holds rather than
its predominant one, so mixed series land on the profile that allows all of their files.

## CI/CD

`.github/workflows/Review.yml` runs on every pull request and on pushes to `main`.
There is no build or publish pipeline: the tool is a script that runs from a checkout,
not a container or a package on an index.

| Job | Trigger | What it does |
|---|---|---|
| `lint` | PR, push to main | `ruff check .` against the config in `pyproject.toml` |
| `test` | PR, push to main | imports every module on Python 3.9, then runs `tests/` if it exists |
| `claude-review` | PR only | automated code review through `anthropics/claude-code-action` |

The `test` job pins Python 3.9 because that is the floor in `pyproject.toml`. An import
of all six modules is a real smoke test here, since the package has no dependencies and
nothing to mock.

`claude-review` needs the repository secret **`ANTHROPIC_API_KEY`**. The job reads it
into a job-level `env`, and the step is skipped when that value is empty. A fork, or a
repository where the secret is not yet set, therefore passes rather than fails.

`ruff format` is not enforced. The existing style is consistent, and a reformat would
rewrite working code for no benefit.
