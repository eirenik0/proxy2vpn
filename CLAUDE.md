# Development Guidelines

This project uses [Towncrier](https://towncrier.readthedocs.io/) to manage the changelog.

## News fragments

- For every pull request, add a file under `news/` named `<PR_NUMBER>.<type>.md`.
- Supported fragment types:
  - `feature` – new features
  - `bugfix` – bug fixes
  - `doc` – documentation updates
  - `removal` – deprecated feature removals
  - `misc` – other changes
- Each fragment must contain a one-line description.

## Changelog

- Run `make changelog-draft` to preview upcoming release notes.
- Run `make changelog VERSION=x.y.z` to finalize the changelog for a release.
- Fragments are removed automatically when the changelog is built.
