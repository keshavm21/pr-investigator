# Werkzeug fixture

An unmodified subset of [Werkzeug](https://github.com/pallets/werkzeug) 3.1.9, used as the base project for evaluation cases. Werkzeug is BSD-3-Clause licensed; its licence is kept in `LICENSE.txt`.

- **Source:** the PyPI source distribution `werkzeug-3.1.9.tar.gz`, SHA-256 `55ca7c70a75689be937aa27f8ff4b018f06ff4838fc73045560bf0f5a1291060`.
- **Kept:** `src/werkzeug/` (all Python modules and `py.typed`), the Python test modules in `tests/`, `README.md`, `pyproject.toml` and `LICENSE.txt`.
- **Left out:** documentation, examples, `CHANGES.rst`, `uv.lock`, and the binary and HTTP sample files under `tests/` (reviewers never see binaries, and the cases don't need them).

The fixture is data. It is never installed, imported or executed by PR Investigator or its tests; eval cases copy it into a temporary git repository and apply edits to it.
