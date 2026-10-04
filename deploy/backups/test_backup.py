"""Offline tests: no production databases or AWS access."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("backup", Path(__file__).with_name("vps-backup.py"))
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def test_pg_secret_not_in_argv(self):
        env = backup.pg_environment("postgresql://u:p%40ss@localhost:5432/db?sslmode=require")
        self.assertEqual(env["PGPASSWORD"], "p@ss")
        self.assertEqual(env["PGSSLMODE"], "require")

    def test_missing_database_does_not_mark_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(backup, "STATE", Path(directory)), patch.object(backup, "load_env", return_value={}):
                with self.assertRaisesRegex(RuntimeError, "Missing"):
                    backup.backup_project("rcca", backup.PROJECTS["rcca"], dt.datetime.now(dt.timezone.utc))
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_sqlite_online_backup_roundtrip_cleanup_and_tiers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            with sqlite3.connect(source) as conn:
                conn.execute("create table test (value text)")
                conn.execute("insert into test values ('preserved')")
            config = root / "config.env"
            config.write_text("SECRET=private\n")
            state = root / "state"
            state.mkdir()
            objects = {}
            def fake_command(argv, env=None):
                if argv[3].startswith("s3://"):
                    Path(argv[4]).write_bytes(objects[argv[3]])
                else:
                    objects[argv[4]] = Path(argv[3]).read_bytes()
                return b""
            with patch.object(backup, "STATE", state), patch.object(backup, "command", side_effect=fake_command):
                backup.backup_project("sillage", {"sqlite": str(source), "files": [str(config)]},
                                      dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc))
            status = json.loads((state / "sillage-success.json").read_text())
            self.assertEqual(len(status["keys"]), 3)
            self.assertEqual(len(objects), 3)
            self.assertFalse(list(state.glob("vps-backup-*")))

    def test_upload_failure_cleans_staging_and_does_not_mark_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            with sqlite3.connect(source) as conn:
                conn.execute("create table test (value text)")
            state = root / "state"
            state.mkdir()
            with patch.object(backup, "STATE", state), patch.object(backup, "command", side_effect=RuntimeError("upload failed")):
                with self.assertRaisesRegex(RuntimeError, "upload failed"):
                    backup.backup_project("sillage", {"sqlite": str(source), "files": []},
                                          dt.datetime.now(dt.timezone.utc))
            self.assertFalse(list(state.iterdir()))


if __name__ == "__main__":
    unittest.main()
