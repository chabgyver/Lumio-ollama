"""Tests de lumio_router : complexité, décision, estimation de coût."""

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import lumio_router as lr  # noqa: E402


class ComplexiteTests(unittest.TestCase):
    def test_question_courte(self):
        self.assertLessEqual(lr.complexity("renomme la variable a en b"), 2)

    def test_architecture(self):
        self.assertGreaterEqual(lr.complexity("Conçois l'architecture complète du module"), 4)

    def test_multi_fichiers(self):
        self.assertGreaterEqual(lr.complexity("corrige ça", files_count=10), 2)

    def test_gros_contexte(self):
        self.assertGreaterEqual(lr.complexity("corrige ça", context_chars=80_000), 2)

    def test_borne(self):
        score = lr.complexity("architecture refonte migration " * 20, files_count=50, context_chars=500_000)
        self.assertLessEqual(score, 10)
        self.assertGreaterEqual(score, 0)


class DecisionTests(unittest.TestCase):
    def test_politique_locale(self):
        decision = lr.decide("local", "architecture complète du projet")
        self.assertEqual(decision["provider"], "local")

    def test_politique_distante(self):
        decision = lr.decide("remote", "renomme a en b")
        self.assertEqual(decision["provider"], "remote")

    def test_auto_simple_reste_local(self):
        decision = lr.decide("auto", "renomme la variable a en b", files_count=1)
        self.assertEqual(decision["provider"], "local")

    def test_auto_complexe_part_en_ligne(self):
        decision = lr.decide("auto", "refonte de l'architecture et des tests", files_count=12)
        self.assertEqual(decision["provider"], "remote")

    def test_plafond_de_cout(self):
        decision = lr.decide(
            "remote", "refonte", estimated_cost=0.5, ceiling=0.1, remote_ready=True,
        )
        self.assertEqual(decision["provider"], "local")
        self.assertIn("plafond", decision["reason"])

    def test_local_indisponible(self):
        decision = lr.decide("auto", "renomme a en b", local_ready=False)
        self.assertEqual(decision["provider"], "remote")

    def test_ligne_indisponible(self):
        decision = lr.decide("remote", "refonte", remote_ready=False)
        self.assertEqual(decision["provider"], "local")

    def test_politique_inconnue(self):
        self.assertEqual(lr.decide("n'importe quoi", "x")["provider"], "local")


class CoutTests(unittest.TestCase):
    def test_estimation(self):
        # 4000 caractères ≈ 1000 tokens d'entrée + 800 de sortie, à 3 $/Mtok
        value = lr.estimate_cost(4000, 3.0, max_tokens=800)
        self.assertAlmostEqual(value, (1000 + 800) * 3.0 / 1_000_000)

    def test_estimation_nulle(self):
        self.assertEqual(lr.estimate_cost(1000, 0), 0.0)


if __name__ == "__main__":
    unittest.main()
