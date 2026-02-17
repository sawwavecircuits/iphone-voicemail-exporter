# Claude Code Prompt: iPhone Voicemail Bulk Export Tool

## Project Goal

Build a portable, cross-platform CLI tool (with optional simple GUI) that extracts all voicemails from an iPhone local backup and exports them as labeled audio files with metadata.

## Context

- iPhone voicemails are stored inside iTunes/Finder local backups
- Backups live in standard OS-specific directories:
  - **macOS**: `~/Library/Application Support/MobileSync/Backup/`
  - **Windows**: `%APPDATA%\Apple Computer\MobileSync\Backup\`
- Each backup is a folder of SHA-1 hashed files with a `Manifest.db` SQLite database mapping hashed filenames to original device paths
- Voicemail audio files are `.amr` format located under `Library/Voicemail/` on the device
- Voicemail metadata (caller, date, duration, read status) lives in a `voicemail.db` SQLite database within the backup

## Requirements

### Core Functionality
1. Auto-detect backup directory based on OS, or accept a custom path as argument
2. If multiple backups exist, list them with device name/date and let the user choose
3. Parse `Manifest.db` to locate all voicemail-related files (audio + `voicemail.db`)
4. Extract voicemail audio files and join with metadata from `voicemail.db`
5. Export audio files renamed with metadata: `YYYY-MM-DD_caller-number_duration.amr`
6. Optionally convert `.amr` to `.mp3` or `.wav` using ffmpeg (if available on system)
7. Generate a `voicemails.csv` manifest with columns: filename, caller, date, duration, read/unread

### Encrypted Backup Support
8. Detect if a backup is encrypted
9. If encrypted, prompt user for their backup password
10. Use `iOSbackup` or `pyiosbackup` library to decrypt and access files
11. If decryption isn't feasible, provide clear instructions to the user on how to create an unencrypted backup

### Portability
12. Python 3.8+ with minimal dependencies
13. Package with PyInstaller for standalone distribution (provide build config)
14. Works on macOS, Windows, and Linux
15. Graceful error handling with user-friendly messages (not stack traces)

### Optional GUI
16. Simple tkinter GUI as an alternative to CLI
17. Backup selection dropdown, output directory picker, progress bar, export button

## Technical Notes

- Use `sqlite3` stdlib module for database access
- The `Manifest.db` schema: `Files` table with columns `fileID` (SHA-1 hash), `relativePath`, `domain`, `flags`
- Voicemail files match: `domain = 'HomeDomain'` and `relativePath LIKE 'Library/Voicemail/%.amr'`
- The `voicemail.db` has a table (likely `voicemail`) with fields like `ROWID`, `sender`, `date`, `duration`, `flags`
- Date values are typically Core Data timestamps (seconds since 2001-01-01) — convert to standard datetime
- For the hashed backup format, the actual file on disk is at: `<backup_dir>/<first 2 chars of fileID>/<fileID>`

## Deliverables

1. `voicemail_exporter.py` — main CLI tool
2. `gui.py` — optional tkinter GUI wrapper
3. `requirements.txt` — dependencies
4. `README.md` — usage instructions including how to create an iPhone backup
5. `build.sh` / `build.bat` — PyInstaller build scripts for Mac/Windows
6. Basic test suite validating backup parsing logic against a mock backup structure

## Out of Scope

- Direct USB device access (no libimobiledevice)
- iCloud backup support
- Transcription of voicemails (could be a follow-up feature)
