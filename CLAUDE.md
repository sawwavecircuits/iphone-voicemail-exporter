# voicemail-exporter

CLI tool to extract voicemails from iPhone local backups (iTunes/Finder) and export them as labeled audio files with a metadata CSV.

## Communication Style

Be direct and concise. Skip pleasantries and filler. Don't sugarcoat feedback: explore good ideas in depth, push back on weak ones. Skip flowery language. Be a genuinely useful tool, not a positive feedback loop.

## Architecture

Single-file design — all logic lives in `voicemail_exporter.py` (~600 lines). No class hierarchy, uses pure functions for testability.

**Key functions:**
- `find_backup_directories()` — Auto-detect backups across macOS/Windows/Linux
- `read_manifest_db()` / `read_voicemail_db()` — SQLite database parsing
- `extract_files_from_backup()` — File hash resolution and extraction
- `convert_audio()` — Shell out to ffmpeg for format conversion
- `export_voicemails()` — Main orchestration function

**Dependencies:**
- **Unencrypted backups**: stdlib only (`sqlite3`, `plistlib`, `shutil`)
- **Encrypted backups**: `iphone-backup-decrypt` + `pycryptodome` (optional, install via `requirements.txt`)
- **Audio conversion**: shells out to `ffmpeg` via `subprocess` (optional, detected at runtime)

## Key Files

- `voicemail_exporter.py` — Main CLI tool and all logic (~600 lines)
- `requirements.txt` — Optional deps for encrypted backup support
- `tests/test_voicemail_exporter.py` — Test suite (31 tests, uses mock backup fixtures)
- `build.sh` / `build.bat` — PyInstaller standalone binary builds

## Common Commands

```bash
# Run the tool (auto-detects backup)
python voicemail_exporter.py

# Export with MP3 conversion (requires ffmpeg)
python voicemail_exporter.py --format mp3 --output ./my_voicemails

# Encrypted backup
python voicemail_exporter.py --password "MyBackupPassword"

# List available backups
python voicemail_exporter.py --list-backups

# Run tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=voicemail_exporter --cov-report=html

# Build standalone binary (macOS/Linux)
./build.sh                # Creates dist/voicemail_exporter

# Build standalone binary (Windows)
build.bat                 # Creates dist/voicemail_exporter.exe
```

## Implementation Notes

- **Backup file layout**: `<backup_dir>/<first2_of_hash>/<full_hash>` (no extension)
- **Voicemail audio filenames**: named after their `voicemail.db` ROWID (e.g., `1.amr`, `2.amr`)
- **Timestamp detection**: values > `1_000_000_000` are Unix epoch; smaller values are Core Data epoch (add `978307200` offset)
- **Output filename format**: `001_2024-03-15_+15551234567.amr`
- **CSV manifest** (`voicemails.csv`): always written with columns: index, output_file, sender, callback_num, date, duration, label, rowid, file_id

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=voicemail_exporter --cov-report=html

# Run specific test file
python -m pytest tests/test_voicemail_exporter.py -v
```

**Test structure:**
- `tests/test_voicemail_exporter.py` — 31 tests covering all major functionality
- Mock fixtures simulate backup directories without requiring real iPhone backups
- Tests cover: backup detection, database parsing, file extraction, audio conversion, encrypted backups

## Building Standalone Binaries

```bash
# macOS/Linux
./build.sh              # Creates dist/voicemail_exporter

# Windows
build.bat               # Creates dist/voicemail_exporter.exe
```

Binaries are created with PyInstaller and include all dependencies. No Python installation required on target system.

## Dependencies

- Python 3.8+
- `pytest` for tests
- `iphone-backup-decrypt>=0.9.0` and `pycryptodome>=3.9.0` for encrypted backups only
- `ffmpeg` in PATH for audio conversion only
