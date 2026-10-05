"""Offline tests for secret-safe restore command handling."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("restore", Path(__file__).with_name("restore-test.py"))
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


class RestoreTests(unittest.TestCase):
    def test_sensitive_error_output_is_suppressed(self):
        result = subprocess.CompletedProcess(["docker"], 1, b"", b"private database data")
        with patch.object(restore.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError) as error:
                restore.run(["docker", "exec", "temporary", "pg_restore"])
        self.assertNotIn("private database data", str(error.exception))

    def test_unchecked_restore_errors_are_available_for_classification(self):
        result = subprocess.CompletedProcess(["docker"], 1, b"", b'extension "supabase_vault" is not available')
        with patch.object(restore.subprocess, "run", return_value=result):
            self.assertEqual(restore.run(["docker", "exec"], check=False), result)


if __name__ == "__main__":
    unittest.main()
