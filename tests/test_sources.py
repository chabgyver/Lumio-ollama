"""Vérifie que tous les modules compilent, y compris l'interface.

Aucun test n'importe `gui_tool` (il faudrait Tk et une fenêtre), donc une faute
de syntaxe dans ses sept mille lignes ne serait vue qu'au lancement. `compile()`
la détecte sans exécuter le module ni ouvrir quoi que ce soit — et la CI le fait
à chaque envoi, sans toucher au workflow.
"""

import re
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class SyntaxeTests(unittest.TestCase):
    def test_tous_les_modules_compilent(self):
        modules = sorted(APP_DIR.glob("*.py"))
        self.assertGreaterEqual(len(modules), 10)
        for module in modules:
            with self.subTest(module=module.name):
                compile(module.read_text(encoding="utf-8-sig"), str(module), "exec")

    def test_les_tests_compilent(self):
        fichiers = sorted((APP_DIR / "tests").glob("*.py"))
        self.assertGreaterEqual(len(fichiers), 10)
        for fichier in fichiers:
            with self.subTest(fichier=fichier.name):
                compile(fichier.read_text(encoding="utf-8-sig"), str(fichier), "exec")

    def test_version_declaree(self):
        source = (APP_DIR / "gui_tool.py").read_text(encoding="utf-8")
        trouve = re.search(r'^APP_VERSION = "(\d+\.\d+)"$', source, re.M)
        self.assertIsNotNone(trouve, "APP_VERSION doit rester une constante simple")
        majeur, mineur = trouve.group(1).split(".")
        self.assertGreaterEqual(int(majeur), 1)
        self.assertGreaterEqual(int(mineur), 0)


if __name__ == "__main__":
    unittest.main()
