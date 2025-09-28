# Repository Guidelines

## Project Structure & Module Organization
- `main.py`: Entry point; starts the Discord bot.
- `src/`: Application code.
  - `bot.py`: Slash command registration and startup.
  - `commands/`: Feature handlers (e.g., `music.py`, `draw.py`, `tts.py`). Each exposes `handle_*` functions used by slash commands.
  - `art/`: Image/video/3D generation utilities.
  - `ui/`: View components and helpers for interactive UIs.
  - `queue_manager.py`: `@enqueue` decorator and task queue for rate‑safe command execution.
  - `error_handler.py`: Centralized error mapping and interaction-safe responses.
- `tests/`: Lightweight checks (e.g., `test_aspect_ratios.py`).
- `.env.example`: Env var template; copy to `.env` and fill keys.
- `requirements.txt`: Python dependencies.

## Build, Test, and Development Commands
- Install runtime deps: `pip install -r requirements.txt`
- Install dev tools: `pip install -r dev-requirements.txt`
- Run bot (dev): `python main.py` (Windows: `start.bat`)
- Run tests: `pytest` (configured via `pytest.ini`)
- Lint: `ruff check .` (config in `pyproject.toml`)

## Coding Style & Naming Conventions
- Python 3.10+; follow PEP 8; 4‑space indentation.
- Files/modules: `snake_case.py`; functions: `snake_case`; classes: `PascalCase`.
- Command handlers live in `src/commands/` and expose `handle_*` functions. Register the slash command in `src/bot.py` and wrap with `@enqueue`.
- Avoid blocking calls in command paths; prefer `async`/`await` and background tasks for long jobs.

## Testing Guidelines
- Keep small, focused tests in `tests/`. Name files `test_*.py` and functions `test_*`.
- Run ad‑hoc checks with `python tests/<file>.py`. If using pytest locally, prefer `pytest -q`.
- Add minimal fixtures/mocks for networked calls; do not hit external APIs in tests.

## Commit & Pull Request Guidelines
- Commits: imperative mood, concise scope (e.g., `fix: handle voice connect timeout`, `feat: add /video command params`).
- PRs: include summary, motivation, screenshots/logs for UX or runtime changes, and steps to verify (commands to run).
- Link related issues and note any env or migration changes.

## Security & Configuration Tips
- Never commit secrets. Use `.env` based on `.env.example` (Discord/OpenAI/Anthropic/etc.).
- Validate required keys at startup; prefer graceful error messages via `error_handler.py`.
- Be cautious with file writes; use temp paths for generated media.
