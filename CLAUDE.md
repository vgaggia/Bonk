# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
python main.py                                    # Run the bot (Windows: start.bat)
pip install -r requirements.txt                   # Runtime deps
pip install -r dev-requirements.txt               # ruff + pytest
pytest                                            # Run all tests (quiet mode from pytest.ini)
pytest tests/test_aspect_ratios.py                # Single test file
pytest tests/test_aspect_ratios.py::test_dalle_aspect_ratios  # Single test
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

## Architecture

### Command wiring pattern
All slash commands are registered in `src/bot.py` with `@tree.command(...)` and dispatch to a `handle_*` function in `src/commands/<name>.py`. **`@enqueue` is applied per-command, not universally.** Long-running generation commands (`/chat`, `/draw`, `/imagine`, `/3d`, `/video`, `/tts`, `/listen`) bypass the queue and call `interaction.response.defer(thinking=True)` themselves so they can run in parallel; only short control commands (`/play`, `/stop`, `/pause`, `/resume`, `/next`, `/reset`, `/clear`, `/help`) use `@enqueue` to serialize.

### Per-guild audio bus (`src/audio_bus.py`) — central mixing layer
Music and TTS do **not** call `voice_client.play()` directly. Each guild has a single `GuildAudioBus` holding a `MixedAudioSource`; playback features add tracks to that mixer. Consequences worth knowing:

- `src/commands/music.py` keeps music at `music_base_volume = 0.4` and `duck_for_tts()` / `unduck_for_tts()` drop it to half while TTS plays. Ducking uses a reference count so overlapping TTS clips don't un-duck prematurely.
- `/pause` pauses the whole voice client (music + TTS simultaneously) because the mixer is a single source — this is a known limitation documented in `music.py:494`.
- `/next` calls `bus.stop_track(music_player.current_track)` to end just the music track without killing concurrent TTS, then manually invokes `song_finished` since the normal `on_done` callback is suppressed when a track is stopped early.
- The voice reply path in `voice_listen.py` also feeds the same bus, so an active listening reply will mix with music.

### Voice lifecycle (three independent managers — keep them in sync)
1. **`src/voice.connect_to_user_channel`** — low-level connect/move/reuse; also decides whether to instantiate a `voice_recv.VoiceRecvClient` so the same connection can receive audio for `/listen`.
2. **`src/voice_session_manager.VoiceSessionManager`** (global singleton) — 3-minute inactivity disconnect; reschedules itself if the client is still playing, if `/listen` is active, or if the guild is in `stay_guilds` (`/stay on`).
3. **`MusicPlayer.idle_timeout_task`** — music-specific 120s disconnect after the queue drains.

When adding a command that disconnects or changes voice state, clean up in **all three** places or stale state will stay around. See `src/commands/music.py:stop` and `src/commands/tts.py:disconnect_voice` for the template (they each cancel the session manager, clear the music player, and call `bus.stop_all()`).

### Voice listening (`src/voice_listen.py`)

> **Known limitation as of 2026-04-25:** Discord enforced DAVE (end-to-end encryption) on all non-stage voice channels on 2026-03-02. `discord.py 2.7.1` + `davey 0.1.5` send DAVE-encrypted audio correctly, but `discord-ext-voice-recv 0.5.2a179` does **not yet decrypt** the MLS-protected receive payload — every frame surfaces as `OpusError("corrupted stream")` and decodes to silence. Tracked at upstream [issue #53](https://github.com/imayhaveborkedit/discord-ext-voice-recv/issues/53). Until that lands, `/listen` only produces real transcripts in **stage channels**, which remain DAVE-exempt. The OpusError flood is rate-limited to one log line per 30 seconds and `/listen` warns the invoker up front when joining a non-stage channel.

Requires the optional `discord-ext-voice-recv` extension. Pipeline per guild:
- `ListenSession` owns a `TranscriptionSink` that feeds per-user PCM frames into `UserStream` buffers.
- RMS-based VAD (`VAD_RMS_THRESHOLD = 400`) segments utterances on `SILENCE_WINDOW = 0.8s`; a background `_periodic_flush_check` task flushes stalled streams every 100ms when no new frames arrive.
- Discord sends 48kHz stereo, so frames are downmixed to mono (`audioop.tomono`) before buffering; audio is normalized toward RMS ~4000 before being handed to Whisper.
- Completed utterances route to `_transcribe_with_openai` (`WHISPER_MODEL`, default `whisper-1`) or `_transcribe_with_whisperx_http` (the sidecar, `WHISPERX_HTTP_URL`) based on `LISTEN_STT_BACKEND`.
- `should_respond` gates replies: the bot only speaks if the transcript contains a name from `TRIGGER_NAMES` (including common Whisper mishearings like "balk"/"bulk"), ends in `?`, or lands inside a 10s expectation window opened after the bot asked its own question.
- Frames received while the mixer has active tracks are dropped to prevent self-feedback.
- A module-level monkey patch on `discord.ext.voice_recv.opus.Decoder.decode` swallows `OpusError` by returning 20ms of silence, because that upstream exception otherwise kills the packet reader loop.

### Chat / message history split (`src/responses.py`, `src/message_history.py`)
There are **two** `MessageHistory` instances:
- `message_history` (max 10) is keyed by Discord user ID for `/chat`.
- `voice_message_history` (max 40) is keyed by the literal string `'voice_shared'` — **one guild-wide memory** used when `voice_mode=True`, so multiple speakers in the same voice channel see each other's context.

`voice_mode` also swaps to a shorter system prompt tuned for spoken replies. `user_model_preferences` persists the last-used model per user in memory (not on disk).

Chat backend selection cascades: explicit `model` arg → stored user preference → `CHAT_MODEL` env (mapped in `responses.py` to `anthropic` / `gpt-4o` / `local-model`). Anthropic model name comes from `GPT_ENGINE`.

### Image / video / 3D generation (`src/art/`)
`image_generation.py` holds DALL-E, Stable Diffusion 3 (Stability AI), GPT Image 1 edit/iterate, and Replicate paths. `replicate_models.py` caches popular-model discovery for 1 hour and falls back to a hardcoded FLUX list on API failure. `video_generation.py` is Luma Labs. UI flows live in `src/ui/` (aspect-ratio picker, model selector, draw buttons) and are wired to command handlers via Discord views.

### Error handling
Use `src/error_handler.handle_error(e)` to map errors to user-facing strings (it recognises OpenAI, Anthropic, Stability, Replicate, and Discord exceptions) and `handle_interaction_error(interaction, e)` when you already have the interaction — it picks between `response.send_message` and `followup.send`. There is a second `src/art/error_handler.py` focused on image-generation specifics; don't conflate them.

### Logging
`src/log.setup_logger(__name__)` gives a color-coded console logger plus a 10MB rotating file handler under `logs/discord_bot.log` that is only active when `LOGGING=True` in `.env`.

## Configuration

`.env` is loaded in both `src/bot.py` and `src/responses.py`; required keys are `DISCORD_BOT_TOKEN` and `ANTHROPIC_API_KEY` (checked in `src/health_check.py` at startup, which also probes the Anthropic API and raises `APIError` if it fails). See `.env.example` for the full list including `LISTEN_STT_BACKEND`, `WHISPERX_HTTP_URL`, `WHISPERX_MODEL`, `LOCAL_API_BASE`, and the per-service keys.

## Related documents
- `AGENTS.md` — additional contributor conventions (commit style, test layout).
- `README.md` — user-facing feature list and setup walkthrough.
