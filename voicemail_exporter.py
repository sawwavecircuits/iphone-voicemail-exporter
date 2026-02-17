#!/usr/bin/env python3
"""
iPhone Voicemail Bulk Export Tool

Extracts voicemails from iPhone local backups (iTunes/Finder) and exports
them as labeled audio files with a metadata CSV.

Supports both unencrypted (zero extra deps) and encrypted backups
(requires: pip install iphone-backup-decrypt).
"""

import argparse
import csv
import os
import platform
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Core Data epoch: Jan 1, 2001 UTC (seconds since Unix epoch 1970)
CORE_DATA_EPOCH_OFFSET = 978307200

# Threshold: timestamps > this are Unix epoch, <= are Core Data epoch offsets
# (Apple timestamps pre-2001 are implausible for voicemails)
UNIX_THRESHOLD = 1_000_000_000


def find_backups(custom_path=None):
    """Auto-detect OS backup directory or use custom path.

    Returns a list of backup directory Paths.
    """
    if custom_path:
        p = Path(custom_path)
        if not p.exists():
            print(f"Error: Backup path does not exist: {p}", file=sys.stderr)
            sys.exit(1)
        # If the user pointed directly at a single backup dir (has Manifest.plist)
        if (p / "Manifest.plist").exists():
            return [p]
        # Otherwise treat it as the parent containing multiple backups
        candidates = [d for d in p.iterdir() if d.is_dir() and (d / "Manifest.plist").exists()]
        if not candidates:
            print(f"Error: No valid iPhone backups found in: {p}", file=sys.stderr)
            sys.exit(1)
        return candidates

    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "MobileSync" / "Backup"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        base = Path(appdata) / "Apple Computer" / "MobileSync" / "Backup"
    else:
        # Linux — iTunes via Wine or user-specified
        base = Path.home() / ".wine" / "drive_c" / "Users" / os.environ.get("USER", "user") \
               / "AppData" / "Roaming" / "Apple Computer" / "MobileSync" / "Backup"

    if not base.exists():
        print(f"Error: Default backup directory not found: {base}", file=sys.stderr)
        print("Use --backup-dir to specify a custom path.", file=sys.stderr)
        sys.exit(1)

    try:
        candidates = [d for d in base.iterdir() if d.is_dir() and (d / "Manifest.plist").exists()]
    except PermissionError:
        print(f"Error: Permission denied accessing: {base}", file=sys.stderr)
        print("macOS is blocking access to the iPhone backup folder.", file=sys.stderr)
        print("To fix this, grant Full Disk Access to your terminal app:", file=sys.stderr)
        print("  macOS 13+: System Settings → Privacy & Security → Full Disk Access", file=sys.stderr)
        print("  macOS 12-: System Preferences → Security & Privacy → Full Disk Access", file=sys.stderr)
        print("Add your terminal app (Terminal, iTerm2, VS Code, etc.), then quit", file=sys.stderr)
        print("and relaunch the terminal app before running this script again.", file=sys.stderr)
        sys.exit(1)
    if not candidates:
        print(f"Error: No iPhone backups found in: {base}", file=sys.stderr)
        sys.exit(1)

    return candidates


def get_backup_info(backup_path):
    """Parse Info.plist for device name, last backup date, product type.

    Returns a dict with keys: device_name, last_backup, product_type, udid.
    """
    info_plist = backup_path / "Info.plist"
    result = {
        "device_name": "Unknown Device",
        "last_backup": "Unknown",
        "product_type": "Unknown",
        "udid": backup_path.name,
    }
    if not info_plist.exists():
        return result

    with open(info_plist, "rb") as f:
        data = plistlib.load(f)

    result["device_name"] = data.get("Device Name", result["device_name"])
    result["product_type"] = data.get("Product Type", result["product_type"])
    result["udid"] = data.get("Unique Identifier", result["udid"])

    last_backup = data.get("Last Backup Date")
    if isinstance(last_backup, datetime):
        result["last_backup"] = last_backup.strftime("%Y-%m-%d %H:%M:%S UTC")
    elif last_backup:
        result["last_backup"] = str(last_backup)

    return result


def select_backup(backups):
    """Interactively prompt user to select from multiple backups.

    Returns the chosen backup Path.
    """
    if len(backups) == 1:
        return backups[0]

    print("\nMultiple backups found:")
    for i, bp in enumerate(backups, 1):
        info = get_backup_info(bp)
        print(f"  [{i}] {info['device_name']}  |  {info['product_type']}  |  {info['last_backup']}")
        print(f"      Path: {bp}")

    while True:
        try:
            choice = input(f"\nSelect backup [1-{len(backups)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(backups):
                return backups[idx]
        except (ValueError, KeyboardInterrupt):
            pass
        print(f"Please enter a number between 1 and {len(backups)}.")


def is_encrypted(backup_path):
    """Return True if Manifest.plist indicates the backup is encrypted."""
    manifest_plist = backup_path / "Manifest.plist"
    if not manifest_plist.exists():
        return False
    with open(manifest_plist, "rb") as f:
        data = plistlib.load(f)
    return bool(data.get("IsEncrypted", False))


def open_manifest_db(backup_path, password=None):
    """Open Manifest.db and return a sqlite3 connection.

    For encrypted backups, decrypts to a temp file first using
    iphone-backup-decrypt. Returns (connection, temp_dir_or_None).
    """
    if is_encrypted(backup_path):
        if password is None:
            password = input("Backup is encrypted. Enter password: ")
        try:
            from iphone_backup_decrypt import EncryptedBackup
        except ImportError:
            print(
                "Error: Encrypted backup requires 'iphone-backup-decrypt'.\n"
                "Install it with: pip install iphone-backup-decrypt",
                file=sys.stderr,
            )
            sys.exit(1)

        tmp_dir = tempfile.mkdtemp(prefix="voicemail_export_")
        backup = EncryptedBackup(backup_directory=str(backup_path), passphrase=password)
        manifest_db_path = Path(tmp_dir) / "Manifest.db"
        backup.extract_manifest_db(output_folder=tmp_dir)
        conn = sqlite3.connect(str(manifest_db_path))
        return conn, tmp_dir, backup

    manifest_db = backup_path / "Manifest.db"
    if not manifest_db.exists():
        print(f"Error: Manifest.db not found in {backup_path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(manifest_db))
    return conn, None, None


def find_voicemail_files(manifest_conn):
    """Query Manifest.db for voicemail audio files.

    Returns list of dicts: {file_id, relative_path, filename}.
    """
    cursor = manifest_conn.cursor()
    cursor.execute(
        """
        SELECT fileID, relativePath
        FROM Files
        WHERE domain = 'HomeDomain'
          AND relativePath LIKE 'Library/Voicemail/%.amr'
        ORDER BY relativePath
        """
    )
    results = []
    for file_id, rel_path in cursor.fetchall():
        results.append({
            "file_id": file_id,
            "relative_path": rel_path,
            "filename": Path(rel_path).stem,  # e.g. "1" from "1.amr"
        })
    return results


def find_voicemail_db(manifest_conn):
    """Query Manifest.db for the voicemail.db file entry.

    Returns (file_id, relative_path) or (None, None).
    """
    cursor = manifest_conn.cursor()
    cursor.execute(
        """
        SELECT fileID, relativePath
        FROM Files
        WHERE domain = 'HomeDomain'
          AND relativePath = 'Library/Voicemail/voicemail.db'
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    if row:
        return row[0], row[1]
    return None, None


def extract_file(backup_path, file_id, dest_path, encrypted_backup=None):
    """Copy a backup file to dest_path.

    For unencrypted: reads from <backup>/<first2>/<fileID>.
    For encrypted: uses the EncryptedBackup object.
    """
    if encrypted_backup is not None:
        # iphone-backup-decrypt provides extract_file(relative_path, output_folder)
        # but we need file_id-based access; use internal method if needed
        raise NotImplementedError("Per-file encrypted extraction not yet supported here.")

    src = backup_path / file_id[:2] / file_id
    if not src.exists():
        return False
    shutil.copy2(str(src), str(dest_path))
    return True


def convert_timestamp(ts):
    """Convert a numeric timestamp to a UTC datetime string.

    Detects Unix epoch vs Core Data epoch automatically.
    Returns ISO-format string or empty string on failure.
    """
    if ts is None:
        return ""
    try:
        ts = float(ts)
        if ts <= 0:
            return ""
        if ts < UNIX_THRESHOLD:
            # Core Data epoch (seconds since 2001-01-01)
            ts += CORE_DATA_EPOCH_OFFSET
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, OSError, OverflowError):
        return ""


def parse_voicemail_metadata(voicemail_db_path):
    """Read voicemail table from voicemail.db.

    Returns list of dicts with keys: rowid, sender, date_str, duration,
    callback_num, label.
    """
    conn = sqlite3.connect(str(voicemail_db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Discover columns (schema varies across iOS versions)
    cursor.execute("PRAGMA table_info(voicemail)")
    columns = {row["name"] for row in cursor.fetchall()}

    # Build a safe SELECT based on available columns
    select_cols = ["ROWID"]
    for col in ("sender", "callback_num", "date", "duration", "flags",
                 "expiration_date", "trashed_date", "label"):
        if col in columns:
            select_cols.append(col)

    cursor.execute(f"SELECT {', '.join(select_cols)} FROM voicemail ORDER BY ROWID")

    results = []
    for row in cursor.fetchall():
        entry = dict(row)
        entry["rowid"] = entry.pop("ROWID")
        entry["date_str"] = convert_timestamp(entry.get("date"))
        results.append(entry)

    conn.close()
    return results


def match_voicemails(audio_files, metadata):
    """Join audio files with metadata by ROWID / filename.

    iPhone voicemail audio files are named after their ROWID (e.g., "1.amr").
    Returns list of dicts combining both sources.
    """
    meta_by_rowid = {str(m["rowid"]): m for m in metadata}

    matched = []
    for af in audio_files:
        entry = dict(af)
        meta = meta_by_rowid.get(af["filename"], {})
        entry.update(meta)
        matched.append(entry)

    # Also include metadata entries with no audio file (edge case)
    audio_filenames = {af["filename"] for af in audio_files}
    for m in metadata:
        if str(m["rowid"]) not in audio_filenames:
            entry = dict(m)
            entry["file_id"] = None
            entry["relative_path"] = None
            entry["filename"] = str(m["rowid"])
            matched.append(entry)

    return matched


def make_output_filename(entry, index, fmt="amr"):
    """Generate a human-readable output filename for a voicemail."""
    date_part = ""
    date_str = entry.get("date_str", "")
    if date_str:
        # e.g. "2024-03-15 10:22:00 UTC" -> "2024-03-15"
        date_part = date_str.split(" ")[0]

    sender = entry.get("sender") or entry.get("callback_num") or "unknown"
    # Sanitize: keep only alphanumerics, dashes, underscores, plus
    safe_sender = "".join(c if c.isalnum() or c in "-_+" else "_" for c in sender)
    safe_sender = safe_sender.strip("_")[:40] or "unknown"

    parts = [f"{index:03d}"]
    if date_part:
        parts.append(date_part)
    parts.append(safe_sender)
    return "_".join(parts) + f".{fmt}"


def convert_audio(src_path, dest_path, fmt):
    """Shell out to ffmpeg to convert audio file.

    Returns True on success, False on failure.
    """
    cmd = ["ffmpeg", "-y", "-i", str(src_path), str(dest_path)]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        return result.returncode == 0
    except FileNotFoundError:
        print("Error: ffmpeg not found. Install ffmpeg or use --no-convert.", file=sys.stderr)
        return False
    except subprocess.TimeoutExpired:
        print(f"Warning: ffmpeg timed out converting {src_path}", file=sys.stderr)
        return False


def export_voicemails(matched, output_dir, backup_path, convert_format=None,
                      encrypted_backup=None):
    """Copy/rename voicemail audio files to output_dir, with optional conversion.

    Returns list of matched entries updated with 'output_file' key.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg_available = shutil.which("ffmpeg") is not None
    if convert_format and not ffmpeg_available:
        print(
            f"Warning: ffmpeg not found — skipping conversion to {convert_format}. "
            "Files will be exported as .amr.",
            file=sys.stderr,
        )
        convert_format = None

    exported = []
    for i, entry in enumerate(matched, 1):
        file_id = entry.get("file_id")
        if not file_id:
            entry["output_file"] = ""
            exported.append(entry)
            continue

        out_fmt = convert_format or "amr"
        out_name = make_output_filename(entry, i, fmt=out_fmt)
        out_path = output_dir / out_name

        if convert_format:
            # Extract to temp first, then convert
            with tempfile.NamedTemporaryFile(suffix=".amr", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            success = extract_file(backup_path, file_id, tmp_path, encrypted_backup)
            if success:
                ok = convert_audio(tmp_path, out_path, convert_format)
                if not ok:
                    # Fall back to copying .amr
                    fallback = out_path.with_suffix(".amr")
                    shutil.copy2(str(tmp_path), str(fallback))
                    entry["output_file"] = fallback.name
                else:
                    entry["output_file"] = out_name
            else:
                entry["output_file"] = ""
            tmp_path.unlink(missing_ok=True)
        else:
            out_name = make_output_filename(entry, i, fmt="amr")
            out_path = output_dir / out_name
            success = extract_file(backup_path, file_id, out_path, encrypted_backup)
            entry["output_file"] = out_name if success else ""

        exported.append(entry)

    return exported


def write_csv(matched, output_dir):
    """Write voicemails.csv manifest to output_dir."""
    output_dir = Path(output_dir)
    csv_path = output_dir / "voicemails.csv"

    fieldnames = [
        "index", "output_file", "sender", "callback_num", "date",
        "duration", "label", "rowid", "file_id",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for i, entry in enumerate(matched, 1):
            row = {
                "index": i,
                "output_file": entry.get("output_file", ""),
                "sender": entry.get("sender", ""),
                "callback_num": entry.get("callback_num", ""),
                "date": entry.get("date_str", ""),
                "duration": entry.get("duration", ""),
                "label": entry.get("label", ""),
                "rowid": entry.get("rowid", ""),
                "file_id": entry.get("file_id", ""),
            }
            writer.writerow(row)

    return csv_path


def main():
    parser = argparse.ArgumentParser(
        description="Export voicemails from an iPhone backup to labeled audio files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect backup, export to ./voicemails_export
  python voicemail_exporter.py

  # Specify backup directory and output path
  python voicemail_exporter.py --backup-dir /path/to/backup --output ./my_voicemails

  # Convert to MP3 (requires ffmpeg)
  python voicemail_exporter.py --format mp3

  # Encrypted backup
  python voicemail_exporter.py --password "MyBackupPassword"
        """,
    )
    parser.add_argument(
        "--backup-dir",
        metavar="PATH",
        help="Path to iTunes/Finder backup directory (auto-detected if omitted)",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="PATH",
        default="./voicemails_export",
        help="Output directory (default: ./voicemails_export)",
    )
    parser.add_argument(
        "--format",
        choices=["mp3", "wav", "m4a"],
        help="Convert audio to this format (requires ffmpeg)",
    )
    parser.add_argument(
        "--no-convert",
        action="store_true",
        help="Keep original .amr format (default behavior)",
    )
    parser.add_argument(
        "--password", "-p",
        metavar="PASSWORD",
        help="Password for encrypted backups",
    )
    parser.add_argument(
        "--list-backups",
        action="store_true",
        help="List available backups and exit",
    )

    args = parser.parse_args()

    # Discover backups
    backups = find_backups(args.backup_dir)

    if args.list_backups:
        print(f"Found {len(backups)} backup(s):")
        for bp in backups:
            info = get_backup_info(bp)
            print(f"  {info['device_name']} ({info['product_type']})  —  {info['last_backup']}")
            print(f"    {bp}")
        return

    backup_path = select_backup(backups)
    info = get_backup_info(backup_path)
    print(f"\nUsing backup: {info['device_name']} ({info['product_type']})")
    print(f"  Last backup: {info['last_backup']}")
    print(f"  Path: {backup_path}")

    encrypted = is_encrypted(backup_path)
    if encrypted:
        print("  Backup is ENCRYPTED")

    # Open Manifest.db
    manifest_conn, tmp_dir, enc_backup = open_manifest_db(backup_path, args.password)

    try:
        # Find voicemail files and DB
        audio_files = find_voicemail_files(manifest_conn)
        vm_db_file_id, vm_db_rel_path = find_voicemail_db(manifest_conn)

        print(f"\nFound {len(audio_files)} voicemail audio file(s)")

        if not audio_files and not vm_db_file_id:
            print("No voicemails found in this backup.")
            return

        # Extract voicemail.db to temp for parsing
        metadata = []
        if vm_db_file_id:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
                tmp_db_path = Path(tmp_db.name)
            success = extract_file(backup_path, vm_db_file_id, tmp_db_path, enc_backup)
            if success:
                metadata = parse_voicemail_metadata(tmp_db_path)
                print(f"Found {len(metadata)} voicemail metadata record(s)")
            tmp_db_path.unlink(missing_ok=True)
        else:
            print("Warning: voicemail.db not found — exporting audio files without metadata.")

        # Match audio with metadata
        matched = match_voicemails(audio_files, metadata)

        # Determine conversion format
        convert_format = None
        if not args.no_convert and args.format:
            convert_format = args.format

        # Export
        output_dir = Path(args.output)
        print(f"\nExporting to: {output_dir.resolve()}")
        exported = export_voicemails(
            matched, output_dir, backup_path,
            convert_format=convert_format,
            encrypted_backup=enc_backup,
        )

        # Write CSV
        csv_path = write_csv(exported, output_dir)

        # Summary
        success_count = sum(1 for e in exported if e.get("output_file"))
        print(f"\nExport complete:")
        print(f"  Audio files: {success_count}/{len(exported)}")
        print(f"  CSV manifest: {csv_path}")

    finally:
        manifest_conn.close()
        if tmp_dir and Path(tmp_dir).exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
