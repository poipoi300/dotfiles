# dev

Python is uv-only: every project has a `pyproject.toml` and its own venv (`uv sync`, `uv run`).
`~/.config/uv/.python-version` pins 3.15 as the default; install it once per machine with
`uv python install 3.15`. No other system Python packages beyond what other packages pull in.

Rust comes from rustup (`rustup default stable`).
