# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
python main.py                                    # Run the bot (Windows: start.bat)
pip install -r requirements.txt                   # Runtime deps
pip install -r dev-requirements.txt               # ruff + pytest
pytest                                            # Run all tests (quiet mode from pyproject.toml)
pytest tests/test_voice_memory.py                 # Single test file
pytest tests/test_voice_memory.py::test_extraction # Single test
ruff check .                                      # Lint (line-length 100, py310 target)
ruff format .                                     # Format
```

External runtime dependencies (not in `requirements.txt`) must be on PATH or configured:
- **FFmpeg** — required for all voice/audio playback. Override with `FFMPEG_BIN` env var.
- **libopus** — required for Discord voice. `src/voice.ensure_opus()` tries several load strategies.
- **PyNaCl** is pinned in `requirements.txt` and is also required for voice.

### WhisperX sidecar (optional local STT)

`whisperx_sidecar/` is a **separate** Python venv that serves local transcription over HTTP on port 5001. It is only reached when `LISTEN_STT_BACKEND=whisperx_http`.

```bash
whisperx_sidecar/install.bat       # Create sidecar venv & install whisperx
start_local_stt.bat                # Activate sidecar venv and run server.py
```

The HTTP handler lives in `whisperx_sidecar/server.py` and is the only place the sidecar logic exists — the main repo just sends it audio when `LISTEN_STT_BACKEND=whisperx_http`.

## Architecture

### Command wiring pattern
All slash commands are registered in `src/bot.py` with `@tree.command(...)` and dispatch to a `handle_*` function in `src/commands/<name>.py`. **`@enqueue` is applied per-command, not universally.** Long-running generation commands (`/chat`, `/draw`, `/imagine`, `/3d`, `/video`, `/tts`, `/tts11`, `/listen`, `/voice`) bypass the queue and call `interaction.response.defer(thinking=True)` themselves so they can run in parallel; the privacy commands (`/memories`, `/forget`) likewise skip `@enqueue` and `defer(ephemeral=True, thinking=True)` so the "thinking…" indicator stays private. Only short control commands (`/play`, `/stop`, `/pause`, `/resume`, `/next`, `/reset`, `/clear`, `/help`) use `@enqueue` to serialize. Both paths end up deferred — `QueueManager.add_to_queue` defers the interaction itself before running the task (`src/queue_manager.py:21`), so handlers under `@enqueue` should *not* defer again.

### Per-guild audio bus (`src/audio_bus.py`) — central mixing layer
Music and TTS do **not** call `voice_client.play()` directly. Each guild has a single `GuildAudioBus` holding a `MixedAudioSource`; playback features add tracks to that mixer. Consequences worth knowing:

- `src/commands/music.py` keeps music at `music_base_volume = 0.4` and `duck_for_tts()` / `unduck_for_tts()` drop it to half while TTS plays. Ducking uses a reference count so overlapping TTS clips don't un-duck prematurely.
- `/pause` pauses the whole voice client (music + TTS simultaneously) because the mixer is a single source — this is a known limitation documented in `music.py:494`.
- `/next` calls `bus.stop_track(music_player.current_track)` to end just the music track without killing concurrent TTS, then manually invokes `song_finished` since the normal `on_done` callback is suppressed when a track is stopped early.
- The voice reply path in `voice_listen.py` also feeds the same bus, so an active listening reply will mix with music.

### Voice lifecycle (three independent managers — keep them in sync)
1. **`src/voice.connect_to_user_channel`** — low-level connect/move/reuse; also decides whether to instantiate a `voice_recv.VoiceRecvClient` so the same connection can receive audio for `/listen`.
2. **`src/voice_session_manager.VoiceSessionManager`** (global singleton, constructed with `timeout_minutes=3` at module bottom) — inactivity disconnect; reschedules itself if the client is still playing, if `/listen` is active, or if the guild is in `stay_guilds` (`/stay on`).
3. **`MusicPlayer.idle_timeout_task`** — music-specific 120s disconnect after the queue drains.

When adding a command that disconnects or changes voice state, clean up in **all three** places or stale state will stay around. See `src/commands/music.py:stop` and `src/commands/tts.py:disconnect_voice` for the template (they each cancel the session manager, clear the music player, and call `bus.stop_all()`).

### Voice listening (`src/voice_listen.py`)

> **Voice receive is currently on a third-party fork.** `requirements.txt` pins `discord-ext-voice-recv` to [`rdphillips7/discord-ext-voice-recv@ddd28601`](https://github.com/rdphillips7/discord-ext-voice-recv) — the open [PR #54](https://github.com/imayhaveborkedit/discord-ext-voice-recv/pull/54) that calls `davey.DaveSession.decrypt()` between SRTP-decrypt and Opus-decode. The PyPI release predates Discord's DAVE E2EE rollout (enforced on non-stage channels 2026-03-02) and decodes every frame to silence with `OpusError("corrupted stream")`. **Drop the fork pin and switch back to PyPI once PR #54 merges.** The rate-limited Opus-error warning in `src/voice_listen.py` is defense-in-depth — silent when decryption succeeds. Full incident details, contributor confirmations, and the unresolved Safari-iPhone edge case are in `VOICE_LISTENING.md` and the message of commit `c0ba8b6`.

Requires the optional `discord-ext-voice-recv` extension. Pipeline per guild:
- `ListenSession` owns a `TranscriptionSink` that feeds per-user PCM frames into `UserStream` buffers.
- RMS-based VAD (`VAD_RMS_THRESHOLD = 400`) segments utterances on `SILENCE_WINDOW = 0.8s`; a background `_periodic_flush_check` task flushes stalled streams every 100ms when no new frames arrive.
- Discord sends 48kHz stereo, so frames are downmixed to mono (`audioop.tomono`) before buffering; audio is normalized toward RMS ~4000 before being handed to Whisper.
- Completed utterances route to `_transcribe_with_openai` (`WHISPER_MODEL`, default `whisper-1`) or `_transcribe_with_whisperx_http` (the sidecar, `WHISPERX_HTTP_URL`) based on `LISTEN_STT_BACKEND`.
- `should_respond` gates replies: the bot only speaks if the transcript contains a name from `TRIGGER_NAMES` (including common Whisper mishearings like "balk"/"bulk"), ends in `?`, or lands inside a 10s expectation window opened after the bot asked its own question.
- Frames received while the mixer has active tracks are dropped to prevent self-feedback.
- Two module-level monkey patches defend against upstream voice-recv bugs:
  - `discord.ext.voice_recv.opus.Decoder.decode` swallows `OpusError` by returning 20ms of silence — the upstream exception otherwise kills the packet reader loop and is also expected during the DAVE decrypt gap.
  - `VoiceRecvClient._remove_ssrc` is replaced with a guarded version because `connect_to_user_channel` always instantiates `VoiceRecvClient` (so any later `/listen` reuses the connection). When voice is used *without* `/listen` (`/tts11`, `/play`, `/tts`, …) `self._reader` stays `MISSING`, and the upstream `_remove_ssrc` dereferences `_reader.speaking_timer` unconditionally — any SSRC drop (user stops speaking, leaves channel, DAVE re-key) raises `AttributeError` into `_poll_voice_ws`, killing the voice-WS poller. The symptom is "TTS goes silent until /disconnect+rejoin"; the patch no-ops when `_reader` isn't ready.

### Reply coalescing (`ReplyScheduler` in `src/voice_listen.py:147`)
Every `ListenSession` owns one `ReplyScheduler` that batches utterances arriving close together into a single LLM call and a single TTS clip — without it, two people speaking near-simultaneously each fired an independent `_handle_utterance` and the audio bus mixer overlaid the resulting replies (Bonk talking over itself).

- One drainer task per guild, so only one batch is ever in flight; utterances arriving while a reply is being produced queue for the next batch instead of overlapping.
- Solo speakers see only `MERGE_SETTLE = 0.20s` of added latency. Multi-speaker batches wait up to `MERGE_DEADLINE_SHORT = 1.5s` for the merge window to settle, with a hard `MERGE_DEADLINE_HARD = 3.0s` ceiling.
- The scheduler is also why `responses.handle_response` carries a `record_history` flag — the scheduler records one assistant entry per *batch* and passes `record_history=False` so the API helper doesn't double-write the user side.

### Voice memory (`src/voice_memory.py`, `/memories`, `/forget`)
A separate Haiku call (model from `MEMORY_EXTRACTION_MODEL`, default `claude-haiku-4-5-20251001`) periodically extracts facts/vibe/notes about each speaker from recent voice transcripts and merges them into a JSON store; that store is read on every voice reply and a memory block is appended to the system prompt. The store outlives sessions and is shared across the process via the module-level `voice_memory_store` singleton (`voice_memory.py:1182`), which loads from disk at import time.

- **Keying is per-guild × per-user.** Same Discord user_id in two guilds = two independent memories (privacy boundary).
- Storage is JSON at `data/voice_memories.json` with snapshot-then-executor atomic-rename writes; `.gitignore` excludes `data/`.
- Speakers in the extraction transcript are addressed by stable `speaker_<user_id>` labels, never display names, so duplicate nicknames cannot cross-attribute facts.
- Extraction is debounced (`EXTRACTION_MIN_INTERVAL = 30s` and `EXTRACTION_MIN_PENDING = 3` new utterances) and runs as a background task with a per-guild `asyncio.Lock`; the LLM call is wrapped in `asyncio.wait_for(EXTRACTION_TIMEOUT)` so failures never block listen disable.
- A deterministic regex PII filter is the second line of defense against the model ignoring the prompt's "do not extract sensitive data" rule.
- The injected memory block carries hard "do not recite" rules; if a user asks what Bonk knows about anyone, the model is instructed to redirect them to `/memories`.
- `/memories` is per-user and ephemeral; `/forget` supports `me`, `wipe-and-opt-out`, `re-enable`, and admin-only `all`. Opted-out users are filtered out before the extraction transcript is built and skipped by `format_for_prompt`.

### Conlang tracker (`src/conlang/`, optional companion website at `website/`)
Background task that monitors `CONLANG_CHANNEL_ID` for a constructed-language design discussion, periodically sends new messages to Claude (`CONLANG_MODEL`, default `claude-sonnet-4-6`) for vocabulary/grammar extraction, and merges findings into `data/conlang_dictionary.json`. Gated entirely on env: if `CONLANG_CHANNEL_ID` is unset the tracker is inactive and `start()` returns early.

- **Loop shape** (`tracker.py`): single `asyncio.Task` started from `on_ready` once (guarded by `client_instance._conlang_started`). Polls every 60s; runs a sync cycle when either `meta.auto_sync_enabled` is true and `sync_interval_hours` has elapsed since `meta.last_updated`, or `meta.force_sync_requested_at > meta.last_updated`. So toggling auto-sync off doesn't kill the tracker — it just disables the auto trigger while keeping force-sync responsive.
- **Authoritative source**: only the user with `id == JORN_USER_ID` can move entries to `confirmed`. Other speakers cap at `high`. The analyzer prompt enforces this; the merge layer is monotonic-upgrade-only as a second line of defense (`database._apply_update_to_entry`).
- **Dual-role words** (Ha = yes + noun, Wa = what + noun, etc.) are first-class — entries are keyed on `(word, category)` not on `word` alone. Seed creates separate entries per role.
- **Atomic writes with website re-merge**: bot's `database.save()` re-reads the on-disk `meta` immediately before each atomic write and overwrites `WEBSITE_OWNED_META_FIELDS` (`sync_interval_hours`, `auto_sync_enabled`) onto its in-memory snapshot. Without this, a UI toggle landing during a 30-second Anthropic call would be clobbered by the bot's stale snapshot. `force_sync_requested_at` is bot-owned (it clears the field after processing).
- **Cross-batch context**: `meta.recent_context` keeps the last ~10 messages with their text. The analyzer sees them but doesn't re-extract — lets it spot "Jorn denies in N, confirms in N+1" trolling patterns.
- **Structured output via tool use**: analyzer forces `tool_choice={"type":"tool","name":"record_findings"}` with a strict input_schema. No raw-JSON-in-text parsing.
- **Backfill chunking**: first run pulls up to 200 messages and processes them in 50-message chunks, advancing `last_message_id` between chunks so a partial failure doesn't replay everything.
- **Companion website** (`website/`, separate Node project): Express + Alpine.js + Tailwind CDN, no build step. Reads `data/conlang_dictionary.json`, exposes `PATCH /api/meta` for the auto-sync toggle / interval / force-sync button, and pushes SSE on file change. Optional `WEBSITE_ADMIN_TOKEN` env gates writes only (reads stay open). Recommended hosting: Cloudflare Quick Tunnel for a throwaway URL or named tunnel + Cloudflare Access (free email allowlist) for a stable, auth-gated one — both avoid port forwarding.

### Chat / message history split (`src/responses.py`, `src/message_history.py`)
There are **two** `MessageHistory` instances:
- `message_history` is keyed by Discord user ID for `/chat`.
- `voice_message_history` is keyed by the literal string `'voice_shared'` — **one guild-wide memory** used when `voice_mode=True`, so multiple speakers in the same voice channel see each other's context.

Both share the same rolling-window cap: `DEFAULT_HISTORY_TOKEN_BUDGET = 190_000` tokens, sized to fit Claude Haiku's 200K context window with headroom for the system prompt, current user message, and `MAX_TOKENS` (1000) of output. The cap is token-budget, not message-count — eviction uses a coarse `len(content)//4` estimator and pops oldest messages until under budget. Tweak the budget in `message_history.py` if a smaller-context model is targeted.

`voice_mode` also swaps to a shorter system prompt tuned for spoken replies. `user_model_preferences` persists the last-used model per user in memory (not on disk).

Chat backend selection cascades: explicit `model` arg → stored user preference → `CHAT_MODEL` env (mapped in `responses.py` to `anthropic` / `gpt-4o` / `local-model`). Anthropic model name comes from `GPT_ENGINE`.

### TTS backends (`src/tts/`)
Two backends live side-by-side: `playai.py` powers `/tts` (PlayAI/PlayHT via Replicate), and `eleven.py` powers `/tts11` (ElevenLabs SDK — module is named `eleven.py` deliberately so it doesn't shadow the `elevenlabs` package). Both produce a temp audio file that the command handler hands to the audio bus; `/tts11` adds a model/voice picker UI. ElevenLabs config keys live under `ELEVENLABS_*` in `.env`.

`/listen` reply audio is configured separately via `/voice` (`src/commands/voice.py`). It writes to a per-guild `ListenVoiceConfig` (defined in `voice_listen.py`) selecting either the OpenAI `tts-1` voice (`alloy` … `shimmer`, default `alloy`) or an ElevenLabs voice+model. `ListenSession._handle_utterance` reads that config when synthesizing a reply, so `/voice` is the only place that controls how Bonk *sounds* in voice channels — `GPT_ENGINE` / `CHAT_MODEL` / per-user model prefs still control what Bonk *says*.

### Image / video / 3D generation (`src/art/`)
`image_generation.py` holds DALL-E, Stable Diffusion 3 (Stability AI), GPT Image 1 edit/iterate, and Replicate paths. `replicate_models.py` caches popular-model discovery for 1 hour and falls back to a hardcoded FLUX list on API failure. `video_generation.py` is Luma Labs. UI flows live in `src/ui/` (aspect-ratio picker, model selector, draw buttons) and are wired to command handlers via Discord views.

### Error handling
Use `src/error_handler.handle_error(e)` to map errors to user-facing strings (it recognises OpenAI, Anthropic, Stability, Replicate, and Discord exceptions) and `handle_interaction_error(interaction, e)` when you already have the interaction — it picks between `response.send_message` and `followup.send`. There is a second `src/art/error_handler.py` focused on image-generation specifics; don't conflate them.

### Logging
`src/log.setup_logger(__name__)` gives a color-coded console logger plus a 10MB rotating file handler under `logs/discord_bot.log` that is only active when `LOGGING=True` in `.env`.

## Configuration

`.env` is loaded in both `src/bot.py` and `src/responses.py`; required keys are `DISCORD_BOT_TOKEN` and `ANTHROPIC_API_KEY` (checked in `src/health_check.py` at startup, which also probes the Anthropic API and raises `APIError` if it fails). See `.env.example` for the full list including `LISTEN_STT_BACKEND`, `WHISPERX_HTTP_URL`, `WHISPERX_MODEL`, `LOCAL_API_BASE`, and the per-service keys.

Run `python -m src.health_check` to validate the env keys and probe the Anthropic API without booting the Discord client.

## Commit style

Imperative mood with a type prefix: `fix: handle voice connect timeout`, `feat: add /video command params`. PRs should include summary, motivation, and steps to verify.

## Related documents
- `AGENTS.md` — additional contributor conventions (commit style, test layout).
- `README.md` — user-facing feature list and setup walkthrough.
