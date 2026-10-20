"""Tests de lumio_tools : protocole de blocs et exécution."""

import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import lumio_tools as lt  # noqa: E402


class AnalyseTests(unittest.TestCase):
    def test_recherche(self):
        calls = lt.parse_calls("Je regarde.\n```rechercher: panic```\nEnsuite ?")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["kind"], "search")
        self.assertEqual(calls[0]["arg"], "panic")

    def test_lecture(self):
        calls = lt.parse_calls("```lire: src/app.py```")
        self.assertEqual(calls[0]["kind"], "read")
        self.assertEqual(calls[0]["arg"], "src/app.py")

    def test_doublons_ignores(self):
        calls = lt.parse_calls("```lire: a.py``````lire: a.py```")
        self.assertEqual(len(calls), 1)

    def test_blocs_non_outils_ignores(self):
        calls = lt.parse_calls("```python\nprint(1)\n```\n```fichier: a.py\nx\n```")
        self.assertEqual(calls, [])

    def test_texte_vide(self):
        self.assertEqual(lt.parse_calls(""), [])


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="lumio_tools_"))
        (self.folder / "src").mkdir()
        (self.folder / "src" / "app.py").write_text("import panic\nprint('ok')\n", encoding="utf-8")
        (self.folder / "autre.py").write_text("rien ici\n", encoding="utf-8")

    def test_recherche_trouve(self):
        result = lt.execute({"kind": "search", "arg": "panic"}, self.folder)
        self.assertTrue(result["ok"])
        self.assertIn("src/app.py", result["output"])

    def test_recherche_sans_resultat(self):
        result = lt.execute({"kind": "search", "arg": "introuvable_xyz"}, self.folder)
        self.assertTrue(result["ok"])
        self.assertIn("Aucun fichier", result["output"])

    def test_lecture(self):
        result = lt.execute({"kind": "read", "arg": "src/app.py"}, self.folder)
        self.assertTrue(result["ok"])
        self.assertIn("import panic", result["output"])

    def test_lecture_hors_dossier_refusee(self):
        result = lt.execute({"kind": "read", "arg": "../secret.txt"}, self.folder)
        self.assertFalse(result["ok"])
        self.assertIn("refusé", result["output"])

    def test_lecture_introuvable(self):
        result = lt.execute({"kind": "read", "arg": "absent.py"}, self.folder)
        self.assertFalse(result["ok"])
        self.assertIn("introuvable", result["output"])

    def test_format_pour_le_modele(self):
        results = lt.execute_all(lt.parse_calls("```lire: src/app.py```"), self.folder)
        text = lt.format_for_model(results)
        self.assertIn("Résultats des outils", text)
        self.assertIn("import panic", text)

    def test_guide_non_vide(self):
        self.assertIn("rechercher", lt.guide())



class LimitesTests(unittest.TestCase):
    def test_taille(self):
        updates = [{"path": "a.py", "content": "x" * 100}, {"path": "b.py", "content": "y" * 50}]
        self.assertEqual(lt.updates_size(updates), 150)

    def test_trop_de_fichiers(self):
        updates = [{"path": f"{i}.py", "content": "x"} for i in range(4)]
        self.assertIn("4 fichiers", lt.too_big(updates, max_files=2))

    def test_trop_gros(self):
        updates = [{"path": "a.py", "content": "x" * 5000}]
        self.assertIn("octets", lt.too_big(updates, max_bytes=1000))

    def test_autorise(self):
        updates = [{"path": "a.py", "content": "x"}]
        self.assertEqual(lt.too_big(updates, max_files=3, max_bytes=1000), "")
if __name__ == "__main__":
    unittest.main()
