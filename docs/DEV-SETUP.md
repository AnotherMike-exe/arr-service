# Dev Setup

## Requirements

Python 3.9 or newer. Nothing else. The package imports the standard library only,
so there is no virtualenv to build and no lockfile to sync.

Optional, for development:

```bash
pipx install ruff      # lint, matches CI
```

## Get running

```bash
git clone https://github.com/AnotherMike-exe/arr-service.git
cd arr-service
cp .env.example .env
$EDITOR .env           # add the URL and API key for each instance
./arr.py check         # make sure every instance answers
```

Each API key is in the app under **Settings -> General -> Security -> API Key**.
`.env` is gitignored and must stay that way. It holds live credentials.

## Git

```bash
git config pull.rebase true    # linear history, the house standard
```

## Lint

```bash
ruff check .
ruff format .          # use --check to match CI without writing
```

CI runs both. Format the tree before you open a pull request, or the `lint` job fails.
Line length is 110, set in `pyproject.toml`.

## Tests

There is no test suite yet. `analyze.py` is the natural place to start, because every
function in it is pure and takes a snapshot dict. A snapshot under `snapshots/` is a
ready-made fixture.

Integration tests need a live Sonarr or Radarr instance, so they cannot run in CI.

## Work safely against a real library

The tool writes to a live media library. Four habits keep that safe.

1. Read first. `./arr.py report` changes nothing.
2. Run every write command without `--execute` and read the printed plan.
3. Add `--execute` only after the plan looks right. A snapshot is taken first.
4. If a change is wrong, restore it:

```bash
./arr.py restore snapshots/20260820-183853-pre-set-cutoff --execute
```

`restore` does not repoint import lists. If a merged profile had one attached, set it
back by hand.

## Develop offline

`analyze` never touches the network, so a saved snapshot is a full test bed:

```bash
./arr.py pull                                  # once, against the live instances
./arr.py report --from snapshots/20260820-192231
```

Use this for any change to `analyze.py`. It is faster than a live pull, and it cannot
damage anything.

## Add a subcommand

1. Write the operation in `apply.py` or `reshape.py`. Return `Action` objects, and
   call `.run()` only when `execute=True`.
2. Add a `cmd_*` function in `arr.py`. Call `collect(..., label="pre-<op>")` when
   `args.execute` is true.
3. Register the subparser in `main()`, with an `--execute` flag.
4. Document the command in `README.md`.

## More

- [ARCHITECTURE.md](ARCHITECTURE.md) — system design and the reasoning behind it
- [CLAUDE.md](CLAUDE.md) — project memory for Claude Code
