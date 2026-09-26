# Changelog

All notable changes to this project are recorded here.

The format is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-25

First release. Other projects can now install `arr-service` from this tag and use
`ArrClient` and `config.discover()`.

### Added
- `pull`: save the full state of each Sonarr and Radarr instance to a timestamped snapshot
- `report`: analyze quality profiles offline and rank the findings by severity
- `set-cutoff`: clamp a quality cutoff, a format cutoff or a minimum score
- `merge`: fold a duplicate profile into another, and repoint every referrer first
- `reshape`: rebuild a profile's quality list, ordered by resolution
- `sort`: move media between profiles by the resolution already on disk
- `restore`: push profiles back from any snapshot
- Every write command is a dry-run unless you add `--execute`, and takes a snapshot first

[Unreleased]: https://github.com/AnotherMike-exe/arr-service/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AnotherMike-exe/arr-service/releases/tag/v0.1.0
