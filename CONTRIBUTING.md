# Contributing

## Development setup

1. Clone the repository.
2. Create a virtual environment: `python3 -m venv .venv` then `source .venv/bin/activate` (on Windows: `.venv\Scripts\activate`).
3. Install in editable mode with tests: `pip install -e ".[dev]"`.
4. Run the test suite: `pytest`.

For local runs without sudo elevation prompts, you can use `GETKERNEL_NO_ELEVATE=1` (development only; see `utils/helpers.py`).

## Pull requests

- Run `pytest` before opening a PR.
- Keep changes focused and describe them clearly in the PR description.
