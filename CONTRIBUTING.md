# Contributing

Thanks for helping. This repository holds `hyperodds`, the Python client for the public Hyperodds
API.

- A bug in the client, a DataFrame that comes out wrong, or a gap in these docs: open an issue or
  a pull request here.
- Data the API does not offer yet, or a field that looks wrong at the source: open a
  [feature request](https://github.com/usehyperodds/hyperodds-python/issues/new?template=feature_request.yml).
  Those changes ship on the API, and the client picks up new fields as model extras until a
  release names them.

## Set up

You need [uv](https://docs.astral.sh/uv/) 0.13 or later.

```sh
git clone https://github.com/usehyperodds/hyperodds-python.git
cd hyperodds-python
uv sync
uv run pytest
```

`uv run ruff check` and `uv run ruff format --check` check lint and formatting (`uv run ruff
format` fixes it), and `uv run mypy` runs the type checker in strict mode. CI runs all of them on
Python 3.10 to 3.14 on Linux, on 3.13 on macOS and Windows, and once on the oldest versions of
httpx, pydantic and pandas the package accepts.

The tests replay API answers recorded in `tests/fixtures`. When the API gains a field the tests
should cover, record them again and read the diff:

```sh
uv run python scripts/record_fixtures.py
```

`uv run pytest -m live` runs the smoke tests against production. They read every live market once
and stay well inside the API's limits.

`uv.lock` only resolves releases that have been public for a week (`exclude-newer` in
`pyproject.toml`).

## Pull requests

- Keep each pull request to one change, with a test that fails without it.
- Keep the runtime dependencies to httpx and pydantic; pandas stays optional.
- Write commit messages in the imperative, short and lowercase: `read the cluster anchor`.
- Describe how you tested it, including the Python version if it matters.

By contributing, you agree to release your work under the [MIT license](LICENSE) and to follow the
[code of conduct](CODE_OF_CONDUCT.md).

## Releases

Maintainers release from `main`: bump `__version__` in `src/hyperodds/_version.py`, date the entry
in [CHANGELOG.md](CHANGELOG.md) and push a `vX.Y.Z` tag. The release workflow checks the tag
against the version, then builds the sdist and wheel and publishes them to PyPI through trusted
publishing. Each file carries an attestation linking it to the commit and workflow that built it.
With the files on PyPI, the workflow opens the GitHub release from the changelog entry.
