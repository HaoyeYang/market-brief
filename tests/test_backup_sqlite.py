import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from backup_sqlite import backup_database


class BackupSqliteTests(unittest.TestCase):
    def test_backup_is_consistent_and_prunes_expired_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "state.sqlite3"
            backups = root / "backups"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE sample (value TEXT)")
                connection.execute("INSERT INTO sample VALUES ('kept')")

            backups.mkdir()
            expired = backups / "market_brief.20000101T000000Z.sqlite3"
            expired.write_bytes(b"old")
            old_time = time.time() - 40 * 86_400
            os.utime(expired, (old_time, old_time))

            output = backup_database(database, backups, keep_days=30)
            self.assertTrue(output.is_file())
            self.assertFalse(expired.exists())
            with sqlite3.connect(output) as connection:
                self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "kept")
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertFalse(list(backups.glob(".*.tmp*")))


if __name__ == "__main__":
    unittest.main()
