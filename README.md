# iPhone Voicemail Bulk Export Tool

Export voicemails from iPhone local backups (iTunes/Finder) to labeled audio files with a metadata CSV.

## Features

- Works with **unencrypted backups** using zero extra dependencies (stdlib only)
- Works with **encrypted backups** via `iphone-backup-decrypt`
- Exports `.amr` files with human-readable names: `001_2024-03-15_+15551234567.amr`
- Converts to **MP3, WAV, or M4A** via ffmpeg
- Generates a **`voicemails.csv`** manifest with sender, date, duration, and more
- Auto-detects backup location on macOS, Windows, and Linux
- Interactive backup selection when multiple devices are found

## Requirements

- Python 3.8+
- **Unencrypted backups**: no extra packages
- **Encrypted backups**: `pip install iphone-backup-decrypt`
- **Audio conversion**: [ffmpeg](https://ffmpeg.org/) in your PATH

## Installation

```bash
# Clone or download this repository
git clone <repo-url>
cd voicemail-exporter

# For encrypted backup support
pip install -r requirements.txt
```

## Usage

### Basic export (auto-detects backup, keeps .amr format)

```bash
python voicemail_exporter.py
```

### Specify output directory

```bash
python voicemail_exporter.py --output ./my_voicemails
```

### Convert to MP3 (requires ffmpeg)

```bash
python voicemail_exporter.py --format mp3 --output ./my_voicemails
```

### Encrypted backup

```bash
python voicemail_exporter.py --password "MyBackupPassword"
```

### Custom backup path

```bash
python voicemail_exporter.py --backup-dir /path/to/backup/AABBCCDD1122
```

### List available backups

```bash
python voicemail_exporter.py --list-backups
```

## All Options

```
usage: voicemail_exporter.py [-h] [--backup-dir PATH] [--output PATH]
                             [--format {mp3,wav,m4a}] [--no-convert]
                             [--password PASSWORD] [--list-backups]

options:
  --backup-dir PATH       Path to iTunes/Finder backup (auto-detected if omitted)
  --output, -o PATH       Output directory (default: ./voicemails_export)
  --format {mp3,wav,m4a}  Convert audio to this format (requires ffmpeg)
  --no-convert            Keep original .amr format
  --password, -p PASSWORD Password for encrypted backups
  --list-backups          List available backups and exit
```

## Output

After export, the output directory will contain:

```
voicemails_export/
├── 001_2024-03-15_+15551234567.amr
├── 002_2024-03-16_+15559876543.amr
├── 003_2024-03-17_unknown.amr
└── voicemails.csv
```

### CSV columns

| Column       | Description                              |
|--------------|------------------------------------------|
| index        | Export order (1-based)                   |
| output_file  | Exported filename                        |
| sender       | Caller phone number                      |
| callback_num | Callback number                          |
| date         | Voicemail date/time (UTC)                |
| duration     | Duration in seconds                      |
| label        | User label (if set)                      |
| rowid        | Internal voicemail.db row ID             |
| file_id      | Backup file hash                         |

## Creating an iPhone Backup

1. Connect your iPhone to your Mac or PC
2. **macOS (Catalina+)**: Open Finder → select your iPhone → click "Back Up Now"
3. **macOS (Mojave and earlier) / Windows**: Open iTunes → select your iPhone → click "Back Up Now"
4. For encrypted backups, set a password in the same backup settings panel
5. Wait for the backup to complete before running this tool

## Building a Standalone Binary

### macOS / Linux

```bash
pip install pyinstaller
./build.sh
# Binary: dist/voicemail_exporter
```

### Windows

```batch
pip install pyinstaller
build.bat
REM Binary: dist\voicemail_exporter.exe
```

## Running Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

## How It Works

1. Locates the iTunes/Finder backup directory for your OS
2. Reads `Manifest.db` to find voicemail audio files (`.amr`) and `voicemail.db`
3. Extracts `voicemail.db` and parses caller info, timestamps, and duration
4. Copies each audio file to the output directory with a descriptive filename
5. Optionally converts `.amr` to MP3/WAV/M4A via ffmpeg
6. Writes a `voicemails.csv` summary

## Notes

- Voicemail audio is stored in AMR (Adaptive Multi-Rate) format. Most media players support it; ffmpeg can convert it to any format.
- Deleted voicemails may still appear in the backup. Check the `flags` field in the CSV — flag `1` typically means active, `5` means deleted.
- This tool is read-only and never modifies your backup.
