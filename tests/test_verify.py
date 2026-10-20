"""Tests de lumio_verify : garde-fous et exécution capturée."""

import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import lumio_verify as lv  # noqa: E402


class GardeFousTests(unittest.TestCase):
    def test_commande_autorisee(self):
        ok, reason = lv.command_is_safe("python tests/run_tests.py")
        self.assertTrue(ok, reason)

    def test_prefixe_inconnu_refuse(self):
        ok, _ = lv.command_is_safe("del C:\\important.txt")
        self.assertFalse(ok)

    def test_enchainement_refuse(self):
        for command in ("python a.py && rm -rf /", "python a.py | more", "echo x > y"):
            ok, _ = lv.command_is_safe(command)
            self.assertFalse(ok, command)

    def test_motif_destructeur_refuse(self):
        ok, reason = lv.command_is_safe("rm -rf dossier")
        self.assertFalse(ok)
        self.assertIn("interdit", reason)

    def test_commande_vide(self):
        self.assertFalse(lv.command_is_safe("   ")[0])

    def test_liste_blanche_personnalisee(self):
        ok, _ = lv.command_is_safe("mesure --rapide", allowlist=("mesure ",))
        self.assertTrue(ok)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="lumio_verify_"))

    def test_succes(self):
        result = lv.run_command('python -c "print(\'bonjour\')"', self.folder)
        self.assertTrue(result["ok"])
        self.assertEqual(result["exit"], 0)
        self.assertIn("bonjour", result["stdout"])
        self.assertFalse(lv.failed(result))

    def test_echec_avec_code(self):
        result = lv.run_command('python -c "import sys; sys.exit(3)"', self.folder)
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit"], 3)
        self.assertTrue(lv.failed(result))
        self.assertIn("code retour : 3", lv.format_result(result))

    def test_commande_refusee_ne_tourne_pas(self):
        result = lv.run_command("rm -rf .", self.folder)
        self.assertFalse(result["allowed"])
        self.assertIn("refusée", lv.format_result(result))

    def test_sortie_d_erreur_capturee(self):
        result = lv.run_command('python -c "import sys; print(\'erreur\', file=sys.stderr)"', self.folder)
        self.assertIn("erreur", result["stderr"])

    def test_format_resultat_borné(self):
        long_result = {"command": "x", "exit": 0, "stdout": "\n".join(str(i) for i in range(100)),
                       "stderr": "", "allowed": True}
        text = lv.format_result(long_result, max_lines=10)
        self.assertIn("ligne(s) supplémentaires", text)


class DetectionTests(unittest.TestCase):
    def test_pytest_detecte(self):
        folder = Path(tempfile.mkdtemp(prefix="lumio_detect_"))
        (folder / "tests").mkdir()
        (folder / "tests" / "__init__.py").write_text("", encoding="utf-8")
        commands = lv.detect_commands(folder)
        self.assertTrue(any("pytest" in item for item in commands))

    def test_script_de_tests_detecte(self):
        folder = Path(tempfile.mkdtemp(prefix="lumio_detect2_"))
        (folder / "tests").mkdir()
        (folder / "tests" / "run_tests.py").write_text("", encoding="utf-8")
        self.assertEqual(lv.detect_commands(folder)[0], "python tests/run_tests.py")

    def test_fallback_py_compile(self):
        folder = Path(tempfile.mkdtemp(prefix="lumio_detect3_"))
        (folder / "app.py").write_text("print(1)\n", encoding="utf-8")
        commands = lv.detect_commands(folder, changed=["app.py"])
        self.assertTrue(any("py_compile" in item for item in commands))



class CacheTests(unittest.TestCase):
    def test_purge_pycache(self):
        """Un __pycache__ périmé ne doit pas survivre à une vérification."""
        folder = Path(tempfile.mkdtemp(prefix="lumio_cache_"))
        cache = folder / "sous" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "calc.cpython-313.pyc").write_bytes(b"perime")
        (folder / "calc.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(lv._purge_pycache(folder), 1)
        self.assertFalse(cache.exists())
        self.assertTrue((folder / "calc.py").is_file())

    def test_commande_python_reconnue(self):
        self.assertTrue(lv._is_python("python check.py"))
        self.assertTrue(lv._is_python('py -3 tests/run_tests.py'))
        self.assertTrue(lv._is_python("python -m pytest -q"))
        self.assertFalse(lv._is_python("npm test"))
        self.assertFalse(lv._is_python("node index.js"))
if __name__ == "__main__":
    unittest.main()
