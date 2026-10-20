"""Tests de lumio_tree : construction, comptage, repliage et ordre d'affichage."""

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import lumio_tree as lt  # noqa: E402

FILES = [
    Path("README.md"),
    Path("src/app.py"),
    Path("src/gui/main.py"),
    Path("src/tests/test_app.py"),
    Path("docs/index.md"),
]


class ConstructionTests(unittest.TestCase):
    def test_structure(self):
        tree = lt.build_tree(FILES)
        self.assertIn("src", tree["dirs"])
        self.assertIn("README.md", [p.name for p in tree["files"]])
        self.assertIn("app.py", [p.name for p in tree["dirs"]["src"]["files"]])

    def test_comptages(self):
        tree = lt.build_tree(FILES)
        self.assertEqual(lt.count_files(tree), 5)
        self.assertEqual(lt.count_dirs(tree), 4)  # src, src/gui, src/tests, docs

    def test_dossiers_vides(self):
        self.assertEqual(lt.count_files(lt.build_tree([])), 0)

    def test_cles_de_dossiers(self):
        keys = lt.dir_keys(FILES)
        self.assertEqual(keys, {"src", "src/gui", "src/tests", "docs"})

    def test_fichier_a_la_racine(self):
        self.assertEqual(lt.dir_keys([Path("a.py")]), set())


class AplatissementTests(unittest.TestCase):
    def setUp(self):
        self.tree = lt.build_tree(FILES)

    def test_ordre_dossiers_avant_fichiers(self):
        rows = lt.flatten(self.tree)
        kinds = [row[0] for row in rows]
        self.assertEqual(kinds[0], lt.DIR)
        self.assertEqual(kinds[-1], lt.FILE)

    def test_profondeurs(self):
        rows = {row[2]: row for row in lt.flatten(self.tree)}
        self.assertEqual(rows["src"][3], 0)
        self.assertEqual(rows["src/gui"][3], 1)
        self.assertEqual(rows["src/gui/main.py"][3], 2)

    def test_compte_par_dossier(self):
        rows = {row[2]: row for row in lt.flatten(self.tree)}
        self.assertEqual(rows["src"][4], 3)
        self.assertEqual(rows["docs"][4], 1)

    def test_replie_un_dossier(self):
        rows = lt.flatten(self.tree, collapsed={"src"})
        keys = [row[2] for row in rows]
        self.assertIn("src", keys)
        self.assertNotIn("src/app.py", keys)
        self.assertNotIn("src/gui", keys)
        self.assertIn("docs/index.md", keys)

    def test_repliage_insensible_a_la_casse(self):
        rows = lt.flatten(self.tree, collapsed={"SRC"})
        self.assertNotIn("src/app.py", [row[2] for row in rows])

    def test_recherche_force_le_depiage(self):
        rows = lt.flatten(self.tree, collapsed={"src"}, query="main")
        self.assertIn("src/gui/main.py", [row[2] for row in rows])

    def test_fichiers_tries_par_nom(self):
        tree = lt.build_tree([Path("b.py"), Path("A.py"), Path("c.py")])
        names = [row[1].name for row in lt.flatten(tree)]
        self.assertEqual(names, ["A.py", "b.py", "c.py"])

    def test_arbre_vide(self):
        self.assertEqual(lt.flatten(lt.build_tree([])), [])


if __name__ == "__main__":
    unittest.main()
