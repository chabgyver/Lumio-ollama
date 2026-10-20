"""Tests de lumio_rank : lecture des tailles, estimations et tris."""

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import lumio_rank as lr  # noqa: E402


def row(ident, prompt=0.0, context=1000, free=False, created=0, completion=None):
    return {
        "id": ident, "prompt": prompt, "completion": prompt if completion is None else completion,
        "context": context, "free": free, "created": created,
    }


class TailleTests(unittest.TestCase):
    def test_milliards(self):
        self.assertEqual(lr.parse_params("meta/llama-3.3-70b"), 70_000_000_000)
        self.assertEqual(lr.size_label("meta/llama-3.3-70b"), "70B")

    def test_decimales(self):
        self.assertEqual(lr.parse_params("qwen2.5-coder:1.5b"), 1_500_000_000)
        self.assertEqual(lr.size_label("qwen2.5-coder:1.5b"), "1.5B")

    def test_mixture_of_experts(self):
        self.assertEqual(lr.parse_params("mixtral-8x22b"), 176_000_000_000)
        self.assertEqual(lr.size_label("mixtral-8x22b"), "8×22B")

    def test_millions(self):
        self.assertEqual(lr.parse_params("tiny-350m"), 350_000_000)

    def test_inconnu(self):
        self.assertIsNone(lr.parse_params("gpt-4o"))
        self.assertEqual(lr.size_label("gpt-4o"), "")


class PuissanceTests(unittest.TestCase):
    def test_taille_domine(self):
        self.assertGreater(lr.power(row("x/llama-70b")), lr.power(row("x/llama-8b")))

    def test_prix_en_proxy(self):
        self.assertGreater(lr.power(row("inconnu/a", prompt=3.0)), lr.power(row("inconnu/b", prompt=0.03)))

    def test_bornes(self):
        for item in (row("x/llama-70b"), row("inconnu/a", prompt=10), row("inconnu/b")):
            self.assertGreaterEqual(lr.power(item), 0)
            self.assertLessEqual(lr.power(item), 100)

    def test_gratuit_avantager(self):
        self.assertGreater(lr.value(row("x/gratuit", free=True)), lr.value(row("x/payant", prompt=1.0)))


class TriTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            row("a/small-7b", prompt=0.05, context=32_000, created=1),
            row("b/big-70b", prompt=0.60, context=128_000, created=2),
            row("c/free", prompt=0.0, context=8_000, free=True, created=3),
        ]

    def test_prix_croissant(self):
        ids = [item["id"] for item in lr.sort_rows(self.rows, "prix")]
        self.assertEqual(ids[0], "c/free")
        self.assertEqual(ids[-1], "b/big-70b")

    def test_contexte(self):
        self.assertEqual(lr.sort_rows(self.rows, "contexte")[0]["id"], "b/big-70b")

    def test_recent(self):
        self.assertEqual(lr.sort_rows(self.rows, "recent")[0]["id"], "c/free")

    def test_nom(self):
        self.assertEqual([item["id"] for item in lr.sort_rows(self.rows, "nom")],
                         sorted(item["id"] for item in self.rows))

    def test_puissance(self):
        self.assertEqual(lr.sort_rows(self.rows, "puissance")[0]["id"], "b/big-70b")

    def test_recommande(self):
        ids = [item["id"] for item in lr.sort_rows(self.rows, "recommande")]
        self.assertIn("c/free", ids[:2])

    def test_critere_inconnu(self):
        self.assertEqual(len(lr.sort_rows(self.rows, "n'importe quoi")), 3)

    def test_entree_non_modifiee(self):
        copie = list(self.rows)
        lr.sort_rows(self.rows, "prix")
        self.assertEqual(self.rows, copie)

    def test_valeurs_inconnues_a_la_fin(self):
        rows = [row("z/unknown", prompt=None, context=0), row("a/known", prompt=0.1)]
        self.assertEqual(lr.sort_rows(rows, "prix")[0]["id"], "a/known")


class LibellesTests(unittest.TestCase):
    def test_tous_les_criteres_ont_un_libelle(self):
        for key in lr.CRITERIA:
            self.assertIn(key, lr.LABELS)
            self.assertTrue(lr.explain(key))

    def test_inversion(self):
        self.assertEqual(lr.LABEL_TO_KEY[lr.LABELS["prix"]], "prix")


if __name__ == "__main__":
    unittest.main()
