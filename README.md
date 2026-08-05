# Jarvis — Local Matrix Assistant

Jarvis is a local-first Windows desktop AI assistant with a custom PySide6 interface. It combines private Ollama inference, conversational workspace automation, reviewed code generation and editing, bounded command execution, offline speech recognition, offline speech synthesis, optional source-backed web search, and persistent local history in a black-and-green Matrix-style UI.

The application is designed around one rule: conversational convenience must not silently bypass review boundaries. Read-only investigation may run directly, generated or edited code is shown as a diff before writing, and generated Python runs only after the user approves `Create & Run`.

## Contents

- [Quick Start](#quick-start)
- [Stack](#stack)
- [Features](#features)
- [Project Layout](#project-layout)
- [Windows Setup](#windows-setup)
- [Voice Tab](#voice-tab)
- [Chat File Attachments](#chat-file-attachments)
- [Chat History](#chat-history)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Web Search](#web-search)
- [Model Routing](#model-routing)
- [Agent and Desktop Actions](#agent-and-desktop-actions)
- [Architecture](#architecture)
- [Local Data and Privacy](#local-data-and-privacy)
- [Development](#development)
- [Validation](#validation)
- [Troubleshooting](#troubleshooting)

## Quick Start

Prerequisites: Windows 10/11, Python 3.12+, and [Ollama](https://ollama.com/) running locally.

```bat
git clone <repository-url> jarvis
cd jarvis
python -m venv .venv-win
.venv-win\Scripts\python.exe -m pip install --upgrade pip
.venv-win\Scripts\python.exe -m pip install -r requirements.txt
.venv-win\Scripts\python.exe scripts\download_models.py
scripts\run_local.bat
```

Ollama must have at least one supported model installed. The recommended local set is:

```bat
ollama pull llama3.2:3b
ollama pull qwen3.5:4b
ollama pull qwen2.5-coder:7b
```

Run the automated test suite before making changes:

```bat
.venv-win\Scripts\python.exe -m unittest discover -s tests
```

## Stack

- `Python 3.12`
- `PySide6` for the desktop UI
- `Ollama` for local LLM inference over `http://127.0.0.1:11434`
- `Vosk` for offline speech-to-text
- `Piper` for offline text-to-speech
- `miniaudio` plus Windows default playback fallback for local microphone and speaker handling
- `pypdf` for bounded local PDF text extraction
- `Bing RSS` as the optional web-search provider

## Features

- Matrix-style desktop chat UI
- Persistent multi-conversation chat history with local autosave
- Fast long-chat reopening with the newest 40 messages rendered first and anchored older-message pages
- Searchable history sidebar with open, new, rename, and delete actions
- Indexed, non-blocking full-text history search with local loading and recovery states
- Responsive 820px-wide compact layout with a persistent navigation/history toggle
- Edit-and-resend for user messages and regeneration for the latest assistant response
- Local text chat against Ollama
- Coalesced low-jank token streaming with explicit retryable local-model error cards
- Phase-aware elapsed reply status with distinct model-loading, streaming, and stalled-token guidance
- Persisted per-reply local performance metrics from Ollama, including load time, first-token latency, token counts, and generation speed
- Syntax-highlighted fenced code blocks with one-click copy and flicker-free plain-text streaming
- Safe Markdown links that permit web URLs while blocking local files, scripts, and embedded resources
- Local text, code, PDF, Word-document, file-picker image, drag-and-drop, and clipboard-image attachments in Chat
- Automatic local-model routing with Fast, Balanced, Coding, Reasoning, and Manual profiles
- In-app installation of curated Ollama models with streamed progress and cancellation
- Toggleable web search with visible source links on responses
- Automatic web-search activation for typed or spoken commands such as `search the web for ...`
- Cancelable web-search and long-chat memory preparation before local generation begins
- Typed or spoken commands to open Windows apps and create local files, including real Word documents
- A minimal working-folder chooser directly in the Agent tab
- Per-workspace Standard or Read-only Agent access with fail-closed task enforcement
- Dedicated Chat plus a conversational Agent that understands follow-ups and routes requests into bounded tools
- Structured Agent task timeline with full raw execution details on demand
- Current-workspace or all-workspace Agent timeline filtering with protected history clearing
- Selectable per-command Agent execution output plus a complete All tasks log
- Direct task-card Details, exact output copying, and atomic workspace-scoped output export
- Live Agent task progress with current phase, elapsed time, safe cancellation, and explicit completion states
- Restart-safe Agent task history with bounded local execution logs and explicit clearing
- Workspace-scoped `Use Again` recall that never executes or crosses folders implicitly
- Actionable file results with validated `Open File` and `Open Folder` controls
- Reviewed model-generated code, configuration, and text files with exclusive no-overwrite creation
- Reviewed Python creation-and-run with fixed interpreter arguments, captured output, cancellation, and timeout
- Source-grounded workspace analysis with relevant file selection and line-number citations
- Evidence-grounded workspace fixes with staged reasoning, diff review, and optional test execution
- Cancelable allowlisted test, build, lint, and formatting-check workflows for Python, Node, and Rust
- Reviewed formatter staging that runs on a temporary copy before showing an approval diff
- Highlighted multi-file diff review with file navigation, statistics, and copy support
- Dedicated Voice tab
- Multiple local Piper voices with male and female options
- Voice preview/test playback
- Adjustable voice rate and volume
- Voice engine visibility in the UI
- Local microphone capture, STT, Ollama reply, and spoken response playback
- Low-latency sentence-chunked Piper playback with next-segment prefetching
- Local adaptive end-of-speech detection with live microphone-level visualization
- Generation-safe transcription and speech callbacks that cannot outlive newer voice actions
- Explicit unavailable-device entries with automatic microphone and speaker fallback
- Independent microphone-frame watchdog that recovers from silent device disconnects
- Inline Voice Only device-recovery guidance and privacy-safe capture cancellation when leaving the view
- Exception-safe speaker shutdown that cannot strand the UI in Speaking after output loss
- Elapsed local STT/TTS stage feedback with stale-result-safe transcription and synthesis timeouts
- Persistent microphone mute controls in Voice and Voice Only views
- Optional persisted hands-free mode that resumes listening after a complete spoken reply
- Voice barge-in that cancels a streaming reply before listening
- Cancel-safe TTS so stopped synthesis cannot begin playing later
- Clear local-processing privacy guidance in Voice controls
- Status badges for Ollama, model, microphone, and voice readiness
- Actionable system notices with retry, settings, and voice recovery controls
- Graceful handling for missing models, missing devices, and missing services

## Project Layout

- `run_assistant.py`
- `src/local_matrix_assistant/app.py`
- `src/local_matrix_assistant/ui/main_window.py`
- `src/local_matrix_assistant/ui/chat_panel.py`
- `src/local_matrix_assistant/ui/agent_panel.py`
- `src/local_matrix_assistant/ui/agent_timeline.py`
- `src/local_matrix_assistant/ui/diff_review.py`
- `src/local_matrix_assistant/ui/voice_panel.py`
- `src/local_matrix_assistant/services/ollama.py`
- `src/local_matrix_assistant/services/stt.py`
- `src/local_matrix_assistant/services/tts.py`
- `src/local_matrix_assistant/services/audio.py`
- `src/local_matrix_assistant/services/web_search.py`
- `src/local_matrix_assistant/services/history.py`
- `src/local_matrix_assistant/services/agent_history.py`
- `src/local_matrix_assistant/services/agent_intent.py`
- `src/local_matrix_assistant/services/project_tasks.py`
- `src/local_matrix_assistant/services/workspace_actions.py`
- `src/local_matrix_assistant/services/workspace_analysis.py`
- `src/local_matrix_assistant/services/workspace_change.py`
- `src/local_matrix_assistant/services/workspace_creation.py`
- `src/local_matrix_assistant/services/workspace_fix.py`
- `src/local_matrix_assistant/services/workspace_task.py`
- `scripts/download_models.py`
- `scripts/self_check.py`

## Windows Setup

1. Install Python 3.12 or newer.
2. Install Ollama for Windows and confirm `ollama.exe` is on your Windows PATH.
3. Create and populate the Windows virtual environment if you are not using the launcher:

```bat
python -m venv .venv-win
.venv-win\Scripts\python.exe -m pip install --upgrade pip
.venv-win\Scripts\python.exe -m pip install -r requirements.txt
```

4. Download the local speech models and bundled voice set:

```bat
.venv-win\Scripts\python.exe scripts\download_models.py
```

By default this downloads:

- STT: `vosk-model-small-en-us-0.15`
- Voices: `en_US-lessac-low`, `en_US-amy-medium`, `en_GB-alan-medium`, `en_US-ryan-high`

You can also download specific voices only:

```bat
.venv-win\Scripts\python.exe scripts\download_models.py --voices en_US-lessac-low en_GB-alan-medium
```

5. Launch the app:

```bat
scripts\run_local.bat
```

or

```bat
.venv-win\Scripts\python.exe run_assistant.py
```

## Voice Tab

The Voice tab lets you:

- enable or disable spoken assistant responses
- enable or disable auto-play for replies
- enable hands-free continuation after spoken replies
- choose among installed local Piper voices
- preview the selected voice with custom text
- inspect the active TTS engine
- adjust voice rate and volume
- choose the microphone input and speaker output device

Voice Only listens for sustained speech and sends after roughly 0.9 seconds of trailing silence. A second tap still sends immediately, and a 30-second local safety limit prevents abandoned captures from recording indefinitely. The waveform responds to the real microphone signal; voice activity detection, transcription, and synthesis all remain on-device.

A separate four-second frame watchdog protects against USB, Bluetooth, or virtual microphone loss where the audio backend remains open but stops delivering samples. Jarvis cancels that capture, invalidates stale transcription callbacks, refreshes device status, and shows recovery guidance directly in Voice Only. Leaving Voice Only always cancels active capture immediately.

Voice Only reports elapsed on-device transcription and first-audio synthesis time. A 30-second transcription limit or 20-second per-segment synthesis limit detaches a stalled worker, restores an actionable UI, and increments the existing generation id so any late text or audio is discarded. If a prefetched later speech segment stalls, the segment already playing may finish while the remaining response stays available in chat.

Turn on `Hands-Free` in Voice Only to resume microphone capture after the full spoken reply finishes. A short delay prevents speaker-tail echo. Muting, stopping voice output, leaving Voice Only, previews, and synthesis errors cancel automatic resume.

On Windows, automatic playback now avoids fake/virtual output devices when possible and falls back to the system default output path for actual audible playback when no explicit output device is chosen.

Speaker cleanup is exception-safe. If an output disappears during playback, queued speech and hands-free resume are stopped, the audio state returns to Idle, and Voice Only shows how to reconnect or select another output instead of remaining stuck in Speaking.

## Chat File Attachments

Use `+ File`, drop files onto the Chat composer, or copy an image and press `Ctrl+V` in the composer. Clipboard images appear immediately in the local attachment tray and do not require a temporary file. Jarvis reads text, source code, configuration, CSV, `.docx`, and text-based PDF files locally. JPEG, PNG, WebP, BMP, GIF, and clipboard images are resized and encoded locally before being sent to Ollama. Attachment extraction runs in the worker pool so large documents cannot freeze the interface.

Up to five files and three images can be attached at once. Text files are limited to 2 MB; PDFs, Word documents, and images are limited to 12 MB. Extracted text, PDF pages, image dimensions, and encoded image size are capped to keep local models responsive. Scanned PDFs without selectable text require OCR and are rejected with guidance.

The composer and sent messages show local image thumbnails plus file names and sizes without exposing document snapshot contents. Snapshots persist with the local conversation so follow-up questions retain context, while absolute source paths are not stored or sent to Ollama. Attached filenames and extracted document text are included in chat-history search.

Use `Edit` on any user message to restore its text and saved attachment snapshots in the composer. Sending the revision replaces all later messages and resets stale conversation memory; canceling restores the unsent draft. Use `Regenerate` on the latest assistant message, or press `Ctrl+Shift+R`, to retry the same user request without duplicating it.

Canceled replies and local-model failures are saved with the conversation, including any partial response. Their stopped or error state survives restart. Every Ollama request records an in-flight reply before generation starts; an unexpected exit recovers that marker as a distinct retryable interruption, while a normal shutdown checkpoints partial text first. Successful completion atomically replaces the marker instead of adding a duplicate message. The latest failed or interrupted reply exposes `Retry`, which removes the failed record only after the replacement request is safely prepared; canceled replies expose the normal `Regenerate` action.

While a reply is active, its compact status row reports elapsed local-model loading and streaming time. A delayed first token or an eight-second token pause changes to an amber stalled state with explicit Stop guidance. Status-only ticks do not rebuild rendered Markdown or code blocks, and all timing state is cleared on success, error, cancellation, or shutdown.

Completed replies show a compact local-response duration and generation rate when enough tokens were produced for the rate to be meaningful. Hover the status row for total workflow time, first-token latency, Ollama load time, prompt and generated token counts, and generation speed. These bounded metrics come from Ollama's final local stream event, remain in the conversation record, and never leave the machine.

Conversation records use flushed atomic replacement and remove abandoned temporary files after write failures. If a user message cannot be saved, it remains in the composer and Ollama never starts. If the pending marker cannot be saved, the request stays retryable without invoking the model. If a completed reply cannot be saved, the full answer remains visible in a distinct `History Save Failed` state; `Retry Save` restores normal history state, while sending, switching, renaming, or deleting chats stays disabled to prevent accidental loss. Failed voice-message saves restore the transcript to the composer.

## Chat History

Chat history is stored locally on disk under:

- `data/chats/*.json`

Each conversation is stored as its own JSON file with:

- a generated conversation id
- a derived title based on the first user message
- created and updated timestamps
- the full message list and metadata

Use the search field above the history list to filter conversations by title or message content. Rename the selected conversation with the `Rename` button or `F2`; custom titles persist in the same local JSON record.

The last opened conversation returns after restart. Unsent text drafts are saved locally per conversation after a short debounce and marked `[Draft]` in history. Sending or deleting the chat clears its draft. Attachments and temporary message-edit content are never included in draft persistence.

Deleting a chat requires a second confirmation click within five seconds. Press `Escape`, switch chats, or wait for the confirmation to expire to cancel deletion.

Conversation summaries are cached in a small corruption-safe local index, so routine sidebar refreshes do not reread every chat file. Full-text searches run in the worker pool and discard stale results when the query changes. Search content remains local; the index stores only summary metadata and rebuilds automatically if it is missing or malformed.

Long conversations reopen at the latest message without constructing every historical message widget. Jarvis renders the newest 40 messages first; use `Load earlier messages` at the top of the conversation to prepend older messages in anchored batches without losing the current reading position. Streaming follows the newest token only while the reader remains near the bottom; scrolling upward preserves the reading position and reveals `Jump to latest`. All messages remain stored locally and available for editing, search, and model context.

  The navigation and history sidebar can be hidden with `Ctrl+B`. Its wide-window preference persists between launches. Below 1060px, Jarvis automatically uses a compact layout: navigation opens as a full-width screen and composer controls wrap without clipping.

Jarvis also restores the last active tab and validated window geometry. Geometry that is malformed or no longer intersects an available monitor is discarded, and the app opens maximized instead of becoming inaccessible off-screen.

The older legacy single-file history at `data/conversation_history.json` is migrated into the new format when needed.

Settings are written through an atomic replacement and mirrored to `data/settings.json.bak`. If the primary file is incomplete or corrupt, Jarvis restores the last valid backup. Rapid voice rate and volume changes are debounced to keep slider interaction responsive.

## Keyboard Shortcuts

- `Ctrl+N`: new chat
- `Ctrl+K`: search chats
- `Ctrl+B`: show or hide navigation and chat history
- `Ctrl+L`: focus the chat composer
- `Ctrl+O`: attach local files
- `Ctrl+Shift+R`: regenerate the latest assistant response
- `Ctrl+Shift+Space`: start, send, or interrupt voice capture
- `Ctrl+Shift+M`: mute or unmute the microphone
- `Ctrl+Shift+X`: stop spoken output
- `F2`: rename the selected chat
- `Alt+1` / `Alt+2` / `Alt+3` / `Alt+4`: open Chat, Agent, Voice, or Settings
- `Ctrl+,`: open Settings
- `Ctrl+/`: show the shortcut reference
- `Escape`: cancel message editing, pending deletion, script approval, or the active rename/search/voice-only state

The Voice Only visualizer is keyboard focusable. Press `Enter` or `Space` while it is
focused to start or send voice capture. Voice controls expose their current state to
screen readers.

## Web Search

Web search is optional and local-first in the sense that the main assistant runtime, STT, TTS, and model inference stay local. When enabled, the app performs a live search against Bing's RSS search feed, injects the result summaries into the model prompt, and shows the sources used in the assistant response bubble.

Important behavior:

- Web search is off by default unless enabled in the UI.
- An explicit `search the web for ...`, `search online for ...`, or `web search ...` command turns it on automatically.
- Responses that used search are labeled in the chat UI.
- Source titles, URLs, and snippets are shown under the response.
- If web search fails, the app continues with a local-only model reply instead of breaking the chat flow.

## Model Routing

The Chat composer includes model profiles:

- `Auto` classifies each prompt and chooses an installed model suited to the task.
- `Fast` prioritizes a responsive small model for short general requests.
- `Balanced` uses a stronger general model for normal conversation and drafting.
- `Coding` prioritizes an installed coder model and adds coding-specific response guidance.
- `Reasoning` prioritizes a stronger reasoning model for analysis and trade-offs.
- `Manual` always uses the model selected in Settings.

Routing is tuned for an 8 GB-class GPU. On the current RTX 2070 setup, Auto prefers `llama3.2:3b` for fast requests, `qwen3.5:4b` for balanced/reasoning and image work, and `qwen2.5-coder:7b` for coding. Image prompts cannot be sent to text-only models; they route to an installed vision-capable model or remain safely unsent with clear guidance. Oversized models such as `mistral-nemo` are excluded from automatic routing when safer installed models are available. Each response shows the selected profile and model.

Settings includes a curated local-model installer for the RTX 2070 profile. Choose Fast, Balanced + Vision, or Coding, then select `Install`. Jarvis streams Ollama's download status, allows cancellation, refreshes the installed-model list on success, and selects the new model. Ollama manages the model storage; cancellation may leave reusable partial download data.

Each profile also reserves model-specific space for the response. Chat context is selected by a conservative token estimate instead of a fixed message count. Jarvis retains complete recent user/assistant turns, limits web-search material, and only shortens the middle of the newest prompt when that prompt cannot fit. The composer reports when context was adjusted.

Long chats retain continuity through bounded conversation memory. When complete older turns no longer fit, the active local model compresses requirements, preferences, decisions, outcomes, and unresolved work into the chat's JSON record before answering. Memory updates stay local, cannot execute transcript commands, and fall back to safe extractive notes if Ollama cannot summarize. Conversation records are written atomically to reduce corruption risk.

The normal Stop control remains available while web sources are loading or older context is being summarized. Canceling either preparation phase stops before the answer request begins, keeps the sent user message in history, and leaves the conversation ready for regeneration or a follow-up.

## Agent and Desktop Actions

Use the `Agent` tab for natural conversation plus file, workspace, project, and app actions. It interprets follow-ups from recent Agent context and keeps writes reviewable. The `Chat` tab remains a dedicated conversation history. Spoken action commands are routed to Agent. Examples:

- `list files`
- `analyze workspace for startup errors`
- `where are authentication tokens validated and what happens when one is missing?`
- `add validation so login rejects empty tokens`
- `make the sidebar responsive on small windows`
- `explain the project architecture`
- `investigate why login tests fail`
- `fix workspace issue: login crashes when the token is empty`
- `fix the bug where subtraction adds values`
- `run tests`
- `run tests in tests/test_agent.py`
- `build project`
- `run lint`
- `check formatting`
- `format project`
- `list project scripts`
- `run project script typecheck`
- `list files in src`
- `read file src/app.py`
- `search files for "TODO" in src`
- `replace in file src/app.py text "old" with "new"`
- `replace all in file src/app.py text "old" with "new"`
- `edit file src/app.py to add input validation`
- `edit files config.py, src/app.py to rename a shared setting`
- `open Notepad`
- `launch Calculator`
- `open Spotify` (using its Windows Start menu shortcut)
- `create a file called notes.txt with content Buy milk`
- `create a file called reports/summary.md with content # Summary`
- `create a Python file src/health.py that exposes a health check`
- `build a Python file that prints the first five squares then run it`
- `run src/health.py`
- `create a JSON file config/defaults.json that defines development defaults`
- `create a Word document outlining open source models`
- `create a Word document named model-guide.docx about open source models`
- `create Word file` (creates a blank `.docx`)

Agent results appear as command, result, and error cards in a bounded task timeline. Command cards show the workspace in which they were issued. The timeline can show `Current workspace` or `All workspaces`; legacy unscoped cards remain available in the all-workspaces view. Each command shows its live or final state and duration: running, review, approval, success, error, canceled, blocked, discarded, or interrupted after a restart. `Execution` offers an `All tasks` log and isolated output for each recorded command, labeled with state, duration, time, workspace, and command. Streaming output, proposal review, apply results, test output, rejection, cancellation, and errors stay grouped with the initiating command. `Details` on a command card opens its exact persisted record. The selected output can be copied exactly or saved as `.txt`, `.log`, or `.md` through a non-modal dialog. Exports use atomic replacement, must remain inside an allowed Agent folder, and are disabled under Read-only access. Legacy history without task records remains available through All tasks. `Use Again` restores the exact text to the composer for editing, never runs automatically, and refuses to restore a scoped command when a different Agent folder is active. Legacy unscoped events remain usable. Recall is unavailable during active work or a pending script approval. Long model and test tasks show a live phase, elapsed time, a task-local Stop control, and completed, failed, or stopped state. Created, edited, and exported files include validated `Open File` and `Open Folder` controls that persist across restarts. Missing and out-of-scope paths fail safely. Source and text artifacts use Windows' edit action, while executable, installer, and shortcut artifacts expose only `Open Folder` so generated results cannot launch them directly. Test runs open Execution automatically, while the timeline remains available after completion. The latest 80 events, 40 per-task records, 200,000 characters of combined per-task output, and 200,000 characters of All tasks output persist locally in `data/agent_history.json`; malformed history is ignored safely. `Clear All` requires a second click within five seconds, always covers hidden workspace cards and all output records, and can be canceled with `Escape`.

Choose the working folder directly in the `Agent` tab. It receives relative file commands; without one, files use `Documents/Jarvis Files`. Absolute paths must stay inside the chosen folder or the default Jarvis folder. Natural Word requests use the selected local Ollama model to draft structured content and save a valid `.docx`; if drafting is unavailable, the Agent still creates an editable outline. Explicit filenames are never overwritten, while automatic document names receive a numeric suffix when needed. App launches use known Windows apps, Start menu shortcuts, or explicit `.exe`/`.lnk` paths without invoking a command shell.

The `Access` control is saved separately for each Agent workspace. `Standard access` permits explicit file creation, reviewed model edits, and the existing approved project workflows. `Read-only` permits bounded list, read, search, analysis, and planned investigation commands, while blocking direct and model-generated file creation, replacements, edits, fixes, formatting, tests, builds, lint, and project scripts before model or tool execution. App launching remains available because it is outside workspace access. Switching to Read-only discards a pending edit and cancels pending script approval. Modes persist in bounded, atomic `data/agent_permissions.json`; malformed or unreadable permission data fails closed to Read-only until the user explicitly selects a mode.

Workspace inspection is recursive but bounded, skips dependency/cache folders, rejects path escapes and binary or oversized files, and caps displayed output. Exact replacements refuse ambiguous multiple matches unless `replace all` is explicit. Natural-language `edit file` commands route to the coding model and produce a diff without changing the file. The user must select `Apply Edit`; Agent then revalidates Python, JSON, or TOML, detects concurrent file changes, stores a backup under `Documents/Jarvis Files/.jarvis-backups`, and replaces the file atomically. `Discard` leaves the file unchanged.

`analyze workspace`, `explain project`, and `investigate` commands build a local, source-grounded view of the selected folder. Agent ranks filenames and file contents against the question, sends at most six bounded line-numbered excerpts to the stronger reasoning model, lists every source reviewed, and reports when scan limits were reached. Dependency/cache directories, binary files, oversized files, `.env` variants, credential files, and private-key formats are excluded. Analysis is read-only and treats repository text as untrusted evidence rather than instructions.

Natural workspace questions such as `where are authentication tokens validated?` no longer require a command prefix. Agent creates a strict plan with at most four read-only steps, limited to validated file reads and literal searches across eligible non-sensitive text files. It then synthesizes a cited answer from the original excerpts and bounded tool results. Model-authored writes, commands, network access, unreviewed paths, duplicate steps, and oversized plans are rejected before execution. `Stop Agent` cancels planning, searching, or synthesis without changing files.

Natural implementation requests such as `add validation so login rejects empty tokens` and `make the sidebar responsive on small windows` work without file-specific command syntax. Unmatched language first enters a bounded intent router that uses recent Agent history to distinguish conversation, clarification, workspace questions, existing-file changes, new-file creation, and Python execution. The reasoning model selects evidenced existing files, the coding model drafts replacements, and Agent displays `PROPOSED CHANGE`. Nothing is written until `Apply Change` or `Apply & Test` is selected. Destructive requests remain outside the model-routed execution path.

Reviewable edits use a highlighted unified-diff viewer with addition, deletion, hunk, and file counts. Multi-file proposals default to an `All Changes` view and allow each file to be inspected independently before the unchanged transactional apply step. The visible diff can be copied, long paths retain tooltips, truncated previews remain labeled, and empty diffs disable apply actions.

`fix workspace issue` and `fix the bug where ...` extend that evidence path into a staged repair. The reasoning model selects at most three files from the reviewed evidence and returns a validated structured plan; the coding model then drafts coordinated whole-file replacements. If the latest test run in the same Agent folder failed, its bounded output is included as untrusted diagnostic evidence for both planning and drafting. Agent shows one combined `PROPOSED FIX` diff while every workspace file remains unchanged. `Apply Fix` uses the existing syntax validation, concurrent-change detection, backups, and transactional batch apply. `Apply & Test` performs the same reviewed apply and then runs the detected allowlisted Python, npm, or Cargo test command. If those tests fail, Agent offers `Draft Follow-up Fix`; it rescans the changed workspace, uses the captured failure, and produces another no-write proposal for review. Invalid model plans, unreviewed paths, oversized files, and unsupported test setups fail closed.

Natural-language new-file requests such as `create a Python file src/health.py that exposes a health check` also route to the coding model, but remain a preview until `Create File` is selected. Paths are validated before model work, generated Python/JSON/TOML is parsed before preview and again before creation, and exclusive creation refuses to overwrite a file that appears after review. Literal commands using `with content`, `containing`, or `that contains` still write exactly the supplied text without model generation.

Create-and-run requests such as `build a Python file that prints the first five squares then run it` produce a complete Python proposal and a `Create & Run` approval action. After approval, Jarvis creates the reviewed file exclusively and launches it with the selected workspace virtual-environment interpreter, or the application interpreter when no workspace environment exists. The runner never invokes a shell, provides no interactive stdin, captures combined stdout/stderr, enforces a 60-second timeout, supports cancellation, and reports the exit result. Existing scripts can be invoked with `run path/to/script.py`; contextual follow-ups such as `run it` resolve the recent reviewed artifact through the Agent conversation router.

Explicit `edit files` commands accept two to four comma-separated paths. The coding model first creates a coordinated implementation plan, then produces a combined validated diff. Apply verifies every file before writing, creates all backups first, and treats the batch as a transaction: if a later write fails, previously changed files are restored automatically.

`run tests`, `build project`, `run lint`, and `check formatting` detect Python, npm, or Cargo projects and execute only a known tool or configured script through a fixed argument list. Output streams into Agent with a fixed size limit. Runs have a three-minute timeout and a task-specific Stop control that terminates the process tree. Python supports unittest/pytest, package builds, Ruff/Flake8/Pylint, and non-writing Ruff/Black format checks. Node requires the matching `test`, `build`, `lint`, or explicit format-check package script. Rust uses Cargo test/build, Clippy, and `cargo fmt --check`. Optional test targets must remain inside the selected workspace. `check formatting` never writes source files. Model-backed Agent work reports its current scan, plan, draft, and validation phase and exposes `Stop Agent`; cancellation works while Ollama is loading or generating and never applies a partial proposal.

`format project` uses Ruff or Black for Python, Prettier for Node, and rustfmt for Rust. The formatter runs against a bounded temporary workspace copy. Jarvis compares the result with unchanged source files, displays a per-file unified diff for up to 40 changed files, and offers `Apply Formatting`, `Apply & Test`, or `Discard`. Applying revalidates every original digest, creates backups, and uses the existing transactional batch writer; cancellation, formatter failure, concurrent source changes, and discarded previews never apply staged output.

`list project scripts` reads the bounded `scripts` section from the selected folder's `package.json` without executing anything. `run project script <name>` displays the exact configured command, working folder, and a standard or high-risk warning in Agent. The script runs only after `Run Script` is selected. Jarvis revalidates the unchanged `package.json` immediately before launch, streams bounded output, supports cancellation and timeout, and does not persist pending approvals. The explicit npm script runs through the platform shell, while automatic `pre<name>` and `post<name>` lifecycle hooks are suppressed.

## Architecture

Jarvis uses a layered design:

1. `run_assistant.py` adds `src/` to the import path and starts the application.
2. `app.py` creates the Qt application and main window.
3. UI modules own presentation, signals, task cards, previews, and user approval state.
4. Service modules own parsing, model access, workspace boundaries, persistence, audio, and process execution.
5. Worker objects keep model requests, file analysis, audio work, and subprocess output off the UI thread.

The Agent uses deterministic parsers for known operations, then a model-backed intent router for natural unmatched requests. Intent output is validated against a closed schema before it can select a tool path. Workspace reads exclude sensitive and oversized files. Workspace writes are resolved under the active folder, previewed, revalidated against concurrent changes, and applied atomically where supported. Python execution and project workflows use fixed argument arrays with `shell=False`; npm scripts are the explicit exception and require a separate approval step.

Primary boundaries:

- Chat can use local conversation history, attachments, and optional web search.
- Agent can converse and use workspace-scoped tools.
- Standard access permits reviewed writes and supported execution.
- Read-only access permits inspection and conversation but blocks writes and executable project tasks.
- Repository content, model output, search results, and task output are treated as untrusted data.

## Local Data and Privacy

Runtime state is intentionally excluded from Git:

- `data/`: settings, chat history, Agent history, and permission state
- `models/`: downloaded Vosk and Piper model files
- `cache/`: generated audio and temporary cached artifacts
- `.venv-win/`: local Python environment

Ollama prompts remain local unless web search is explicitly enabled. Enabling web search sends the search query to Bing RSS. Attachments are processed locally before selected text or image content is sent to the local model. Never commit `.env` files, credentials, private keys, runtime history, or downloaded model weights; the included `.gitignore` excludes these by default.

## Development

Create the environment and install dependencies using the Quick Start instructions. Source code follows a `src/` layout and tests use Python's standard `unittest` runner.

Useful commands:

```bat
:: Run every test
.venv-win\Scripts\python.exe -m unittest discover -s tests

:: Run one test module
.venv-win\Scripts\python.exe -m unittest tests.test_agent_window

:: Compile-check the package
.venv-win\Scripts\python.exe -m compileall -q src

:: Run environment and device checks
scripts\self_check_windows.bat
```

When adding Agent capabilities:

1. Keep deterministic parsing for exact, security-sensitive operations.
2. Validate every model-authored route or plan against a closed schema.
3. Resolve paths against the active workspace before reading or writing.
4. Require a preview for model-generated writes.
5. Use fixed subprocess arguments, bounded output, cancellation, and timeouts.
6. Add service-level and UI-routing regression tests.

## Validation

Run the built-in local verification script:

```bat
scripts\self_check_windows.bat
```

This checks:

- Ollama connectivity
- installed Ollama models
- STT model availability
- TTS model availability
- installed local voices
- selected voice configuration
- microphone visibility
- speaker/output availability
- local TTS synthesis
- local STT transcription of generated speech
- local playback start
- a basic Ollama chat response

To run a full microphone round trip:

```bat
scripts\self_check_windows.bat --voice-roundtrip
```

That captures microphone audio, transcribes it locally, sends it to Ollama, synthesizes the reply locally, and plays the reply through the active output device.

## Troubleshooting

- `Ollama Offline` in the UI:
  Start Ollama locally and confirm it responds on `http://127.0.0.1:11434`.
- `Selected model is not installed`:
  Pull the model in Ollama first, then refresh status in the app.
- `Missing STT model` or `Missing TTS model`:
  Run `.venv-win\Scripts\python.exe scripts\download_models.py` again.
- Voice list shows fewer options than expected:
  Confirm the `.onnx` and `.onnx.json` files exist in `models/tts`.
- App launches but voice output is silent:
  Leave speaker output on `Automatic` first so the Windows default playback fallback is used, then try an explicit device if needed.
- Web search returns no sources:
  Confirm the machine has internet access. The assistant will fall back to local-only model responses when search is unavailable.

The notice bar below the header prioritizes unsaved-settings errors, then local runtime issues. Use `Retry`, `Retry Save`, `Open Settings`, or `Open Voice` directly from the notice. Runtime notices can be dismissed for the current session and return if the underlying state changes.

## Project Status

Jarvis is an actively developed personal desktop application. Windows is the primary supported platform because app launching, audio fallback, batch launchers, and parts of process management use Windows-specific behavior. Linux launch support exists for basic development through `scripts/run_local.sh`, but parity is not guaranteed.
