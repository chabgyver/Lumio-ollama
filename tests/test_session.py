"""Tests de lumio_session : chiffrement DPAPI, écriture atomique, migration."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import lumio_session as ls  # noqa: E402


class SecretsTests(unittest.TestCase):
    def test_aller_retour_dpapi(self):
        secret = "sk-or-v1-abcdef0123456789"
        encrypted = ls.encrypt_text(secret)
        self.assertTrue(encrypted.startswith(ls.SECRET_PREFIX))
        self.assertNotIn(secret, encrypted)
        self.assertEqual(ls.decrypt_text(encrypted), secret)

    def test_valeur_vide(self):
        self.assertEqual(ls.encrypt_text(""), "")
        self.assertEqual(ls.decrypt_text(""), "")

    def test_ancienne_valeur_en_clair(self):
        self.assertEqual(ls.decrypt_text("cle-en-clair"), "cle-en-clair")

    def test_idempotent(self):
        once = ls.encrypt_text("secret")
        self.assertEqual(ls.encrypt_text(once), once)

    def test_secure_et_unsecure(self):
        data = {"online_key": "abc", "catalog_keys": {"groq": "gsk-1"}, "folder": "D:\\x"}
        secured = ls.secure(data)
        self.assertNotEqual(secured["online_key"], "abc")
        self.assertNotIn("gsk-1", secured["catalog_keys"]["groq"])
        self.assertEqual(secured["folder"], "D:\\x")
        restored = ls.unsecure(secured)
        self.assertEqual(restored["online_key"], "abc")
        self.assertEqual(restored["catalog_keys"]["groq"], "gsk-1")


class StockageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lumio_session_"))
        self.path = self.tmp / "session.json"

    def test_aller_retour(self):
        ls.save({"folder": "D:\\projet", "online_key": "secret"}, self.path)
        data = ls.load(self.path)
        self.assertEqual(data["folder"], "D:\\projet")
        self.assertEqual(data["online_key"], "secret")

    def test_fichier_sur_disque_ne_contient_pas_le_secret(self):
        ls.save({"online_key": "tres-secret"}, self.path)
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("tres-secret", raw)
        self.assertIn("dpapi:", raw)

    def test_ecriture_atomique_sans_residu(self):
        ls.save({"a": 1}, self.path)
        ls.save({"a": 2}, self.path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["a"], 2)
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())
        self.assertTrue(self.path.with_suffix(".bak.json").exists())

    def test_repli_sur_le_bak(self):
        ls.save({"v": 1}, self.path)
        ls.save({"v": 2}, self.path)
        self.path.write_text("{cassé", encoding="utf-8")
        self.assertEqual(ls.load(self.path).get("v"), 1)

    def test_session_absente(self):
        self.assertEqual(ls.load(self.tmp / "absent.json"), {})


class MigrationTests(unittest.TestCase):
    def test_migration_depuis_le_partage(self):
        tmp = Path(tempfile.mkdtemp(prefix="lumio_migrate_"))
        legacy = tmp / "session.json"
        legacy.write_text(json.dumps({"folder": "D:\\projet", "online_key": "abc"}),
                          encoding="utf-8")
        target = tmp / "appdata" / "session.json"

        with mock.patch.object(ls, "storage_dir", return_value=tmp / "appdata"):
            self.assertTrue(ls.migrate(legacy, target))

        self.assertTrue(target.exists())
        self.assertFalse(legacy.exists())  # déplacé hors du partage
        archived = list((tmp / "appdata" / "legacy").glob("session-*.json"))
        self.assertTrue(archived)
        self.assertEqual(ls.load(target)["online_key"], "abc")

    def test_pas_de_migration_si_session_locale(self):
        tmp = Path(tempfile.mkdtemp(prefix="lumio_migrate2_"))
        legacy = tmp / "session.json"
        legacy.write_text(json.dumps({"folder": "x"}), encoding="utf-8")
        target = tmp / "session.json"
        target.write_text(json.dumps({"folder": "deja-la"}), encoding="utf-8")
        self.assertFalse(ls.migrate(legacy, target))
        self.assertTrue(legacy.exists())


if __name__ == "__main__":
    unittest.main()
