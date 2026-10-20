"""Tests de lumio_cost : coût, formatage, journal et agrégation."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import lumio_cost as lc  # noqa: E402


class CoutTests(unittest.TestCase):
    def test_cout_par_million(self):
        # 1 000 000 de tokens à 3 $/Mtok = 3 $
        self.assertAlmostEqual(lc.cost_usd(1_000_000, 0, 3.0, 0.0), 3.0)
        self.assertAlmostEqual(lc.cost_usd(0, 1_000_000, 0.0, 15.0), 15.0)

    def test_cout_melange(self):
        # 2000 prompt à 0,03 + 500 completion à 0,12
        expected = (2000 * 0.03 + 500 * 0.12) / 1_000_000
        self.assertAlmostEqual(lc.cost_usd(2000, 500, 0.03, 0.12), expected)

    def test_valeurs_manquantes(self):
        self.assertEqual(lc.cost_usd(None, None, None, None), 0.0)
        self.assertEqual(lc.cost_usd("x", "y", "z", "w"), 0.0)


class FormatageTests(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(lc.format_tokens(0), "0 tok")
        self.assertEqual(lc.format_tokens(1240), "1240 tok")

    def test_cout(self):
        self.assertEqual(lc.format_cost(0, local=True), "gratuit (local)")
        self.assertEqual(lc.format_cost(0), "0 $")
        self.assertEqual(lc.format_cost(0.0042), "0.0042 $")
        self.assertEqual(lc.format_cost(1.5), "1.500 $")
        self.assertEqual(lc.format_cost(None), "coût inconnu")


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lumio_cost_")) / "usage.jsonl"

    def test_enregistrement_et_lecture(self):
        entry = lc.make_entry("qwen/qwen3.8-flash", "online",
                              {"prompt_tokens": 1000, "completion_tokens": 500},
                              {"prompt": 0.03, "completion": 0.12})
        lc.record(entry, self.tmp)
        entries = lc.load_entries(self.tmp)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["tokens"], 1500)
        self.assertAlmostEqual(entries[0]["cost"], 0.00009, places=6)

    def test_modele_local_gratuit(self):
        entry = lc.make_entry("qwen-local:40b", "local",
                              {"prompt_tokens": 300, "completion_tokens": 200})
        self.assertEqual(entry["cost"], 0.0)
        self.assertEqual(entry["tokens"], 500)

    def test_filtre_par_jours(self):
        now = datetime.now()
        lc.record(lc.make_entry("a", "online", {"prompt_tokens": 10}, when=now), self.tmp)
        lc.record(lc.make_entry("b", "online", {"prompt_tokens": 10},
                                when=now - timedelta(days=30)), self.tmp)
        self.assertEqual(len(lc.load_entries(self.tmp, days=7)), 1)

    def test_journal_absent(self):
        self.assertEqual(lc.load_entries(self.tmp.parent / "absent.jsonl"), [])

    def test_ligne_cassee_ignoree(self):
        self.tmp.write_text('{"tokens": 5, "cost": 0}\npas du json\n', encoding="utf-8")
        self.assertEqual(len(lc.load_entries(self.tmp)), 1)


class AgregatTests(unittest.TestCase):
    def test_totaux_par_jour_et_modele(self):
        now = datetime.now()
        entries = [
            lc.make_entry("m1", "online", {"prompt_tokens": 100, "completion_tokens": 50},
                          {"prompt": 1.0, "completion": 2.0}, when=now),
            lc.make_entry("m1", "online", {"prompt_tokens": 100, "completion_tokens": 0},
                          {"prompt": 1.0, "completion": 2.0}, when=now),
            lc.make_entry("m2", "local", {"prompt_tokens": 10}, when=now),
        ]
        data = lc.aggregate(entries)
        self.assertEqual(data["total"]["tokens"], 260)
        self.assertEqual(data["total"]["calls"], 3)
        self.assertEqual(data["by_model"]["m1"]["calls"], 2)
        self.assertEqual(data["by_model"]["m2"]["cost"], 0.0)

    def test_agregat_vide(self):
        data = lc.aggregate([])
        self.assertEqual(data["total"]["tokens"], 0)
        self.assertEqual(data["by_day"], {})

    def test_resume_lisible(self):
        entries = [lc.make_entry("m1", "online", {"prompt_tokens": 1000},
                                 {"prompt": 3.0, "completion": 0})]
        text = "\n".join(lc.summary_lines(entries))
        self.assertIn("Aujourd'hui", text)
        self.assertIn("m1", text)



class BudgetTests(unittest.TestCase):
    def test_sous_le_plafond(self):
        self.assertFalse(lc.over_budget(0.02, 0.05))

    def test_au_plafond(self):
        self.assertTrue(lc.over_budget(0.05, 0.05))

    def test_plafond_illimite(self):
        self.assertFalse(lc.over_budget(10.0, 0))
        self.assertFalse(lc.over_budget(10.0, None))

    def test_reste(self):
        self.assertAlmostEqual(lc.remaining(0.02, 0.05), 0.03)
        self.assertEqual(lc.remaining(0.02, 0), None)
        self.assertEqual(lc.remaining(0.9, 0.05), 0.0)
if __name__ == "__main__":
    unittest.main()
