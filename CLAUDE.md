# voicemail-exporter

CLI tool to extract voicemails from iPhone local backups (iTunes/Finder) and export them as labeled audio files with a metadata CSV.

## Architecture

Single-module procedural design — all logic lives in `voicemail_exporter.py`. No class hierarchy.

- **Unencrypted backups**: stdlib only (`sqlite3`, `plistlib`, `shutil`)
- **Encrypted backups**: `iphone-backup-decrypt` + `pycryptodome` (optional, install via `requirements.txt`)
- **Audio conversion**: shells out to `ffmpeg` via `subprocess` (optional, detected at runtime)

## Key Files

- `voicemail_exporter.py` — Main CLI tool and all logic
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

# Build standalone binary (macOS/Linux)
./build.sh
```

## Implementation Notes

- **Backup file layout**: `<backup_dir>/<first2_of_hash>/<full_hash>` (no extension)
- **Voicemail audio filenames**: named after their `voicemail.db` ROWID (e.g., `1.amr`, `2.amr`)
- **Timestamp detection**: values > `1_000_000_000` are Unix epoch; smaller values are Core Data epoch (add `978307200` offset)
- **Output filename format**: `001_2024-03-15_+15551234567.amr`
- **CSV manifest** (`voicemails.csv`): always written with columns: index, output_file, sender, callback_num, date, duration, label, rowid, file_id

## Dependencies

- Python 3.8+
- `pytest` for tests
- `iphone-backup-decrypt>=0.9.0` and `pycryptodome>=3.9.0` for encrypted backups only
- `ffmpeg` in PATH for audio conversion only
