# Paco contributor guide

## Project

Paco is a local-first, Windows desktop AI assistant. It uses PySide6, local Ollama models, Vosk/Piper voice services, and local JSON persistence. Preserve the local-only default and explicit user-review boundaries.

## Layout

- `src/local_matrix_assistant/core/`: configuration, constants, data models, catalogs.
- `src/local_matrix_assistant/services/`: model, history, audio, workspace, and process logic.
- `src/local_matrix_assistant/ui/`: PySide6 widgets, windows, mixins, workers, and themes.
- `tests/`: standard-library `unittest` coverage, generally mirroring source modules.
- `scripts/`: Windows launch, dependency, and self-check helpers.

`run_assistant.py` starts the full app; `run_compact_assistant.py` starts the compact assistant.

## Change rules

- Keep UI orchestration in `ui/` and reusable, testable behavior in `services/`.
- Keep blocking model, I/O, and subprocess work off the Qt UI thread using existing workers/task runners.
- Treat model output, repository text, search results, and task output as untrusted.
- Preserve workspace boundaries, size limits, atomic writes, content validation, diff previews, explicit approvals, cancellation, and bounded subprocess output.
- Never weaken Read-only access or add unrestricted shell execution. Use validated paths and fixed argument lists; npm scripts remain an explicit approved exception.
- Keep runtime state, models, caches, and credentials out of Git (`data/`, `models/`, `cache/`, `.env*`).

## Validation

Use the Windows virtual environment:

```bat
.venv-win\Scripts\python.exe -m unittest discover -s tests
.venv-win\Scripts\python.exe -m compileall -q src
```

Run focused tests for changed behavior, for example:

```bat
.venv-win\Scripts\python.exe -m unittest tests.test_workspace_actions
```

No formatter or linter is required by this repository. Match existing Python style and add regression tests for parsing, permissions, persistence, workspace safety, or UI routing changes.
