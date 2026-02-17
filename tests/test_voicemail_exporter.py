"""
Tests for voicemail_exporter.py

Creates mock backup structures in temp directories to exercise the
core functions without requiring a real iPhone backup.
"""

import csv
import os
import plistlib
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure parent directory is in path so we can import the module
sys.path.insert(0, str(Path(__file__).parent.parent))
import voicemail_exporter as ve


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="vm_test_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def mock_backup(tmp_dir):
    """Create a minimal unencrypted mock backup structure."""
    backup_dir = tmp_dir / "AABBCCDD1122"
    backup_dir.mkdir()

    # --- Manifest.plist (unencrypted) ---
    manifest_data = {
        "IsEncrypted": False,
        "BackupKeyBag": b"",
        "Version": "10.0",
    }
    with open(backup_dir / "Manifest.plist", "wb") as f:
        plistlib.dump(manifest_data, f)

    # --- Info.plist ---
    info_data = {
        "Device Name": "Test iPhone",
        "Product Type": "iPhone14,2",
        "Unique Identifier": "AABBCCDD1122",
        "Last Backup Date": datetime(2024, 3, 15, 10, 22, 0, tzinfo=timezone.utc),
    }
    with open(backup_dir / "Info.plist", "wb") as f:
        plistlib.dump(info_data, f)

    # --- Manifest.db ---
    manifest_db_path = backup_dir / "Manifest.db"
    conn = sqlite3.connect(str(manifest_db_path))
    conn.execute(
        """CREATE TABLE Files (
            fileID TEXT PRIMARY KEY,
            domain TEXT,
            relativePath TEXT,
            flags INTEGER,
            file BLOB
        )"""
    )
    # Two voicemail audio files
    conn.execute(
        "INSERT INTO Files VALUES (?,?,?,?,?)",
        ("aabbccdd1111111111111111111111111111111111", "HomeDomain",
         "Library/Voicemail/1.amr", 1, None),
    )
    conn.execute(
        "INSERT INTO Files VALUES (?,?,?,?,?)",
        ("bbccddee2222222222222222222222222222222222", "HomeDomain",
         "Library/Voicemail/2.amr", 1, None),
    )
    # voicemail.db entry
    conn.execute(
        "INSERT INTO Files VALUES (?,?,?,?,?)",
        ("ccddee003333333333333333333333333333333333", "HomeDomain",
         "Library/Voicemail/voicemail.db", 1, None),
    )
    conn.commit()
    conn.close()

    # --- Actual audio stub files in backup structure ---
    for file_id, filename in [
        ("aabbccdd1111111111111111111111111111111111", "1.amr"),
        ("bbccddee2222222222222222222222222222222222", "2.amr"),
    ]:
        bucket = backup_dir / file_id[:2]
        bucket.mkdir(exist_ok=True)
        (bucket / file_id).write_bytes(b"#!AMR\x00" * 10)  # fake AMR data

    # --- voicemail.db ---
    vm_db_bucket = backup_dir / "cc"
    vm_db_bucket.mkdir(exist_ok=True)
    vm_db_path = vm_db_bucket / "ccddee003333333333333333333333333333333333"
    vm_conn = sqlite3.connect(str(vm_db_path))
    vm_conn.execute(
        """CREATE TABLE voicemail (
            ROWID INTEGER PRIMARY KEY,
            sender TEXT,
            callback_num TEXT,
            date REAL,
            duration REAL,
            flags INTEGER,
            label TEXT
        )"""
    )
    # Unix timestamp: 2024-03-15 10:22:00 UTC = 1710498120
    vm_conn.execute(
        "INSERT INTO voicemail VALUES (?,?,?,?,?,?,?)",
        (1, "+15551234567", "+15551234567", 1710498120.0, 30.5, 0, None),
    )
    # Core Data timestamp: seconds since 2001-01-01
    core_data_ts = 1710498120.0 - ve.CORE_DATA_EPOCH_OFFSET
    vm_conn.execute(
        "INSERT INTO voicemail VALUES (?,?,?,?,?,?,?)",
        (2, "+15559876543", "+15559876543", core_data_ts, 60.0, 0, "Saved"),
    )
    vm_conn.commit()
    vm_conn.close()

    return backup_dir


# ---------------------------------------------------------------------------
# Test: backup discovery
# ---------------------------------------------------------------------------

class TestFindBackups:
    def test_finds_backup_by_direct_path(self, mock_backup):
        result = ve.find_backups(custom_path=str(mock_backup))
        assert len(result) == 1
        assert result[0] == mock_backup

    def test_finds_backup_in_parent_dir(self, mock_backup):
        parent = mock_backup.parent
        result = ve.find_backups(custom_path=str(parent))
        assert mock_backup in result

    def test_nonexistent_path_exits(self, tmp_dir):
        with pytest.raises(SystemExit):
            ve.find_backups(custom_path=str(tmp_dir / "does_not_exist"))

    def test_empty_dir_exits(self, tmp_dir):
        empty = tmp_dir / "empty_backup_dir"
        empty.mkdir()
        with pytest.raises(SystemExit):
            ve.find_backups(custom_path=str(empty))


# ---------------------------------------------------------------------------
# Test: backup info parsing
# ---------------------------------------------------------------------------

class TestGetBackupInfo:
    def test_parses_info_plist(self, mock_backup):
        info = ve.get_backup_info(mock_backup)
        assert info["device_name"] == "Test iPhone"
        assert info["product_type"] == "iPhone14,2"
        assert info["udid"] == "AABBCCDD1122"
        assert "2024-03-15" in info["last_backup"]

    def test_missing_info_plist_returns_defaults(self, tmp_dir):
        empty_backup = tmp_dir / "no_info"
        empty_backup.mkdir()
        info = ve.get_backup_info(empty_backup)
        assert info["device_name"] == "Unknown Device"


# ---------------------------------------------------------------------------
# Test: encrypted detection
# ---------------------------------------------------------------------------

class TestIsEncrypted:
    def test_unencrypted_backup(self, mock_backup):
        assert ve.is_encrypted(mock_backup) is False

    def test_encrypted_backup(self, tmp_dir):
        enc_backup = tmp_dir / "encrypted"
        enc_backup.mkdir()
        manifest_data = {"IsEncrypted": True}
        with open(enc_backup / "Manifest.plist", "wb") as f:
            plistlib.dump(manifest_data, f)
        assert ve.is_encrypted(enc_backup) is True

    def test_missing_manifest_returns_false(self, tmp_dir):
        no_manifest = tmp_dir / "no_manifest"
        no_manifest.mkdir()
        assert ve.is_encrypted(no_manifest) is False


# ---------------------------------------------------------------------------
# Test: Manifest.db queries
# ---------------------------------------------------------------------------

class TestManifestDb:
    def test_find_voicemail_files(self, mock_backup):
        conn, _, _ = ve.open_manifest_db(mock_backup)
        files = ve.find_voicemail_files(conn)
        conn.close()
        assert len(files) == 2
        filenames = {f["filename"] for f in files}
        assert "1" in filenames
        assert "2" in filenames

    def test_find_voicemail_db(self, mock_backup):
        conn, _, _ = ve.open_manifest_db(mock_backup)
        file_id, rel_path = ve.find_voicemail_db(conn)
        conn.close()
        assert file_id == "ccddee003333333333333333333333333333333333"
        assert rel_path == "Library/Voicemail/voicemail.db"

    def test_find_voicemail_files_empty_backup(self, tmp_dir):
        empty_db = tmp_dir / "empty_manifest.db"
        conn = sqlite3.connect(str(empty_db))
        conn.execute(
            """CREATE TABLE Files (
                fileID TEXT, domain TEXT, relativePath TEXT,
                flags INTEGER, file BLOB
            )"""
        )
        conn.commit()
        files = ve.find_voicemail_files(conn)
        conn.close()
        assert files == []


# ---------------------------------------------------------------------------
# Test: timestamp conversion
# ---------------------------------------------------------------------------

class TestConvertTimestamp:
    def test_unix_timestamp(self):
        # 2024-03-15 10:22:00 UTC
        result = ve.convert_timestamp(1710498120.0)
        assert "2024-03-15" in result
        assert "UTC" in result

    def test_core_data_timestamp(self):
        # Same date but Core Data epoch offset
        core_data_ts = 1710498120.0 - ve.CORE_DATA_EPOCH_OFFSET
        result = ve.convert_timestamp(core_data_ts)
        assert "2024-03-15" in result
        assert "UTC" in result

    def test_zero_returns_empty(self):
        assert ve.convert_timestamp(0) == ""

    def test_none_returns_empty(self):
        assert ve.convert_timestamp(None) == ""

    def test_negative_returns_empty(self):
        assert ve.convert_timestamp(-1) == ""


# ---------------------------------------------------------------------------
# Test: metadata parsing
# ---------------------------------------------------------------------------

class TestParseVoicemailMetadata:
    def test_parses_both_rows(self, mock_backup):
        # Extract voicemail.db path directly
        vm_db_path = mock_backup / "cc" / "ccddee003333333333333333333333333333333333"
        metadata = ve.parse_voicemail_metadata(vm_db_path)
        assert len(metadata) == 2

        row1 = next(m for m in metadata if m["rowid"] == 1)
        assert row1["sender"] == "+15551234567"
        assert "2024-03-15" in row1["date_str"]

        row2 = next(m for m in metadata if m["rowid"] == 2)
        assert row2["sender"] == "+15559876543"
        assert row2["label"] == "Saved"
        # Core Data timestamp should resolve to same date
        assert "2024-03-15" in row2["date_str"]


# ---------------------------------------------------------------------------
# Test: matching
# ---------------------------------------------------------------------------

class TestMatchVoicemails:
    def test_matches_by_rowid(self):
        audio_files = [
            {"file_id": "aaa", "relative_path": "Library/Voicemail/1.amr", "filename": "1"},
            {"file_id": "bbb", "relative_path": "Library/Voicemail/2.amr", "filename": "2"},
        ]
        metadata = [
            {"rowid": 1, "sender": "+15551234567", "date_str": "2024-03-15 10:22:00 UTC",
             "duration": 30.5, "date": 1710498120.0},
            {"rowid": 2, "sender": "+15559876543", "date_str": "2024-03-15 10:25:00 UTC",
             "duration": 60.0, "date": 1710498300.0},
        ]
        matched = ve.match_voicemails(audio_files, metadata)
        assert len(matched) == 2
        senders = {m.get("sender") for m in matched}
        assert "+15551234567" in senders
        assert "+15559876543" in senders

    def test_unmatched_audio_included(self):
        audio_files = [
            {"file_id": "aaa", "relative_path": "Library/Voicemail/99.amr", "filename": "99"},
        ]
        metadata = []
        matched = ve.match_voicemails(audio_files, metadata)
        assert len(matched) == 1
        assert matched[0]["file_id"] == "aaa"

    def test_metadata_without_audio_included(self):
        audio_files = []
        metadata = [
            {"rowid": 5, "sender": "+15550000000", "date_str": "",
             "duration": 10.0, "date": None},
        ]
        matched = ve.match_voicemails(audio_files, metadata)
        assert len(matched) == 1
        assert matched[0]["rowid"] == 5
        assert matched[0]["file_id"] is None


# ---------------------------------------------------------------------------
# Test: filename generation
# ---------------------------------------------------------------------------

class TestMakeOutputFilename:
    def test_basic_filename(self):
        entry = {
            "date_str": "2024-03-15 10:22:00 UTC",
            "sender": "+15551234567",
        }
        name = ve.make_output_filename(entry, 1)
        assert name == "001_2024-03-15_+15551234567.amr"

    def test_unknown_sender(self):
        entry = {"date_str": "", "sender": None, "callback_num": None}
        name = ve.make_output_filename(entry, 3, fmt="mp3")
        assert name.startswith("003_")
        assert name.endswith(".mp3")
        assert "unknown" in name

    def test_sanitizes_special_chars(self):
        entry = {"date_str": "2024-03-15 10:22:00 UTC", "sender": "+1 (555) 123-4567"}
        name = ve.make_output_filename(entry, 2)
        assert "/" not in name
        assert " " not in name

    def test_format_extension(self):
        entry = {"date_str": "2024-01-01 00:00:00 UTC", "sender": "+1"}
        assert ve.make_output_filename(entry, 1, fmt="wav").endswith(".wav")
        assert ve.make_output_filename(entry, 1, fmt="mp3").endswith(".mp3")


# ---------------------------------------------------------------------------
# Test: file extraction
# ---------------------------------------------------------------------------

class TestExtractFile:
    def test_extracts_existing_file(self, mock_backup, tmp_dir):
        file_id = "aabbccdd1111111111111111111111111111111111"
        dest = tmp_dir / "output.amr"
        result = ve.extract_file(mock_backup, file_id, dest)
        assert result is True
        assert dest.exists()
        assert dest.stat().st_size > 0

    def test_missing_file_returns_false(self, mock_backup, tmp_dir):
        dest = tmp_dir / "output.amr"
        result = ve.extract_file(mock_backup, "0" * 40, dest)
        assert result is False


# ---------------------------------------------------------------------------
# Test: full export pipeline
# ---------------------------------------------------------------------------

class TestExportVoicemails:
    def test_exports_files(self, mock_backup, tmp_dir):
        conn, _, _ = ve.open_manifest_db(mock_backup)
        audio_files = ve.find_voicemail_files(conn)
        conn.close()

        vm_db_path = mock_backup / "cc" / "ccddee003333333333333333333333333333333333"
        metadata = ve.parse_voicemail_metadata(vm_db_path)
        matched = ve.match_voicemails(audio_files, metadata)

        output_dir = tmp_dir / "export"
        exported = ve.export_voicemails(matched, output_dir, mock_backup)

        assert output_dir.exists()
        exported_files = [e for e in exported if e.get("output_file")]
        assert len(exported_files) == 2

    def test_writes_csv(self, mock_backup, tmp_dir):
        conn, _, _ = ve.open_manifest_db(mock_backup)
        audio_files = ve.find_voicemail_files(conn)
        conn.close()

        vm_db_path = mock_backup / "cc" / "ccddee003333333333333333333333333333333333"
        metadata = ve.parse_voicemail_metadata(vm_db_path)
        matched = ve.match_voicemails(audio_files, metadata)

        output_dir = tmp_dir / "export_csv"
        exported = ve.export_voicemails(matched, output_dir, mock_backup)
        csv_path = ve.write_csv(exported, output_dir)

        assert csv_path.exists()
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == len(exported)
        # Verify expected columns exist
        for col in ("index", "output_file", "sender", "date", "duration"):
            assert col in rows[0]

    def test_csv_has_correct_sender(self, mock_backup, tmp_dir):
        conn, _, _ = ve.open_manifest_db(mock_backup)
        audio_files = ve.find_voicemail_files(conn)
        conn.close()

        vm_db_path = mock_backup / "cc" / "ccddee003333333333333333333333333333333333"
        metadata = ve.parse_voicemail_metadata(vm_db_path)
        matched = ve.match_voicemails(audio_files, metadata)

        output_dir = tmp_dir / "export_sender"
        exported = ve.export_voicemails(matched, output_dir, mock_backup)
        csv_path = ve.write_csv(exported, output_dir)

        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        senders = {r["sender"] for r in rows if r["sender"]}
        assert "+15551234567" in senders or "+15559876543" in senders


# ---------------------------------------------------------------------------
# Test: convert_audio skips gracefully when ffmpeg is absent
# ---------------------------------------------------------------------------

class TestConvertAudio:
    def test_returns_false_when_ffmpeg_missing(self, tmp_dir):
        src = tmp_dir / "test.amr"
        src.write_bytes(b"fake amr")
        dest = tmp_dir / "test.mp3"
        with patch("shutil.which", return_value=None):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                result = ve.convert_audio(src, dest, "mp3")
        assert result is False
