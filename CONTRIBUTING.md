# Contributing

Thank you for considering a contribution to Proxy2VPN!

## Changelog fragments

We use [Towncrier](https://towncrier.readthedocs.io/) to maintain the changelog.

1. For every pull request, create a news fragment in the `news/` directory.
2. Name the file `<PR_NUMBER>.<type>.md`, for example `123.feature.md`.
3. Keep the file to a single-line description.
4. Supported types:
   - `feature`
   - `bugfix`
   - `doc`
   - `removal`
   - `misc`
5. Run `make changelog-draft` to see how the next release notes will look.

Fragments are removed automatically when `make changelog VERSION=x.y.z` is run during a release.
