# TG Bridge Bot for Windows

Windows version of a Telegram bridge bot. It stores user pairs in SQLite and relays text, photos, files, stickers, voice messages, video notes, replies and reactions between them. Voice messages are transcribed locally, and an optional short summary can be produced by a local Ollama model.

## Quick start

1. Clone the repository into a permanent folder, e.g. `C:\Bots\tg-bridge-bot`.
2. Run `setup_windows.cmd`.
3. Paste the token from `@BotFather`.
4. For better summaries, run `install_ollama_qwen.cmd`.
5. Run `run_bot.cmd` to test it, or `start_hidden.cmd` to run it in the background.

Do not move the folder after registering autostart. If you need to move it, first delete the old task in Task Scheduler or run:

```powershell
schtasks /Delete /TN "TG Bridge Bot Windows" /F
```

## Management

- `run_bot.cmd`: run in a visible window
- `start_hidden.cmd`: run hidden
- `stop_bot.cmd`: stop
- `restart_bot.cmd`: restart
- `status_bot.cmd`: status and log paths
- `configure_bot.cmd`: change the token and main settings
- `install_ollama_qwen.cmd`: install Ollama and `qwen3:8b`
- `import_database_from_server.cmd`: import pairs and the message map from a Debian server

## Where data is stored

- `.env`: token and settings
- `data\bridge.sqlite3`: pairs and the message map
- `models`: Whisper models
- `tmp`: temporary files, deleted after processing
- `backups`: SQLite backups
- `logs`: logs of the hidden mode

## GPU

`WHISPER_DEVICE=auto` tries NVIDIA CUDA first. If the required CUDA libraries are missing, the bot automatically falls back to CPU `int8`. The device actually used is shown in the logs.

## Quality settings

In `.env`:

```env
WHISPER_MODEL=small
WHISPER_DEVICE=auto
WHISPER_LANGUAGE=ru
TRANSCRIPT_MODE=summary
SUMMARY_BACKEND=local_llm
SUMMARY_MODEL=qwen3:8b
```

On a more powerful computer you can use:

```env
WHISPER_MODEL=large-v3
WHISPER_CPU_THREADS=8
```

On CPU only, start with `small` or `medium`.

## File size limit

`TRANSCRIPTION_MAX_FILE_MB=18` only applies to downloading a voice message or video note for transcription. Regular files are relayed through Telegram without Whisper.

## Migrating from a Debian server

Stop the server instance before starting the Windows version with the same token:

```powershell
ssh host@<server-ip> "sudo systemctl stop tg-bridge.service"
```

Then run `import_database_from_server.cmd`. It downloads `/opt/tg-bridge/data/bridge.sqlite3` and keeps the previous local database as a backup.

One Telegram token cannot be used by two long-polling processes at the same time. For parallel testing, create a separate bot with `@BotFather`.
