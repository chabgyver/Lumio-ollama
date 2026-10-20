"""Tests de lumio_snapshots : instantané, historique, restauration, purge."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
for path in (str(APP_DIR),):
    if path not in sys.path:
        sys.path.insert(0, path)

import lumio_session  # noqa: E402
import lumio_snapshots as lsn  # noqa: E402


class InstantaneTests(unittest.TestCase):
    def setUp(self):
        self.store = Path(tempfile.mkdtemp(prefix="lumio_snap_store_"))
        self.folder = Path(tempfile.mkdtemp(prefix="lumio_snap_work_"))
        self.patch = mock.patch.object(lumio_session, "storage_dir", return_value=self.store)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_ecriture_et_lecture(self):
        entry = lsn.snapshot(self.folder, "src/app.py", "version 1\n")
        self.assertIsNotNone(entry)
        rows = lsn.entries(folder=self.folder, relative="src/app.py")
        self.assertEqual(len(rows), 1)
        self.assertEqual(Path(rows[0]["file"]).read_text(encoding="utf-8"), "version 1\n")

    def test_ordre_du_plus_recent(self):
        lsn.snapshot(self.folder, "a.py", "v1")
        lsn.snapshot(self.folder, "a.py", "v2")
        rows = lsn.entries(folder=self.folder, relative="a.py")
        self.assertEqual(len(rows), 2)
        self.assertEqual(Path(rows[0]["file"]).read_text(encoding="utf-8"), "v2")

    def test_restauration(self):
        lsn.snapshot(self.folder, "a.py", "contenu d'origine\n")
        (self.folder / "a.py").write_text("modifié\n", encoding="utf-8")
        rows = lsn.entries(folder=self.folder, relative="a.py")
        target = lsn.restore(rows[0])
        self.assertEqual(target.read_text(encoding="utf-8"), "contenu d'origine\n")

    def test_contenu_vide_ignore(self):
        self.assertIsNone(lsn.snapshot(self.folder, "a.py", None))

    def test_fichiers_avec_historique(self):
        lsn.snapshot(self.folder, "a.py", "1")
        lsn.snapshot(self.folder, "b.py", "1")
        lsn.snapshot(self.folder, "a.py", "2")
        files = {item["relative"]: item["count"] for item in lsn.files_with_history(self.folder)}
        self.assertEqual(files["a.py"], 2)
        self.assertEqual(files["b.py"], 1)

    def test_purge(self):
        for index in range(lsn.MAX_PER_FILE + 5):
            lsn.snapshot(self.folder, "a.py", f"v{index}")
        rows = lsn.entries(folder=self.folder, relative="a.py", limit=1000)
        self.assertLessEqual(len(rows), lsn.MAX_PER_FILE)

    def test_restauration_refuse_hors_dossier(self):
        entry = {"folder": str(self.folder), "relative": "../evasion.txt", "file": ""}
        with self.assertRaises(Exception):
            lsn.restore(entry)


if __name__ == "__main__":
    unittest.main()
