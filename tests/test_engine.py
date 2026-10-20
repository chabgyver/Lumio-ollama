"""Tests du moteur Lumio (fonctions pures de ollama_fs_access)."""

import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import ollama_fs_access as ox  # noqa: E402


class ExtensionsTests(unittest.TestCase):
    def test_vide_retombe_sur_les_extensions_texte(self):
        self.assertIn(".txt", ox.parse_extensions(""))
        self.assertIn(".py", ox.parse_extensions("   "))

    def test_liste_simple(self):
        self.assertEqual(ox.parse_extensions(".py, .md"), {".py", ".md"})

    def test_sans_point_et_point_virgule(self):
        self.assertEqual(ox.parse_extensions("py;md"), {".py", ".md"})


class CheminsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="lumio_test_"))
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")

    def test_resolve_inside_accepte_un_enfant(self):
        root, target = ox.resolve_inside(self.root, "src/app.py")
        self.assertEqual(target, (self.root / "src" / "app.py").resolve())
        self.assertEqual(root, target.parents[1])

    def test_resolve_inside_refuse_l_evasion(self):
        with self.assertRaises(ValueError):
            ox.resolve_inside(self.root, "../secret.txt")

    def test_is_safe_rel_path(self):
        self.assertTrue(ox.is_safe_rel_path("src/app.py"))
        self.assertFalse(ox.is_safe_rel_path("../secret.txt"))
        self.assertFalse(ox.is_safe_rel_path("/etc/passwd"))
        self.assertFalse(ox.is_safe_rel_path("https://exemple.test/page"))
        self.assertFalse(ox.is_safe_rel_path(""))


class FluxTests(unittest.TestCase):
    def test_parse_chat_line(self):
        piece, thinking, done = ox.parse_chat_line('{"message": {"content": "bonjour"}, "done": false}')
        self.assertEqual((piece, thinking, done), ("bonjour", "", False))

    def test_parse_chat_line_done(self):
        piece, _, done = ox.parse_chat_line('{"message": {"content": ""}, "done": true}')
        self.assertEqual((piece, done), ("", True))

    def test_format_stream_stats(self):
        stats = ox.format_stream_stats(
            {"eval_count": 10, "eval_duration": 1_000_000_000, "prompt_eval_count": 42}
        )
        self.assertIn("42 ctx", stats)
        self.assertIn("10 tok", stats)
        self.assertIn("10.0 t/s", stats)

    def test_shrink_history_tronque(self):
        rows = ox.shrink_history(
            [{"role": "user", "content": "x" * 5000}, {"role": "assistant", "content": "```py\nprint(1)\n```"}],
            per_message=100,
        )
        self.assertLess(len(rows[0]["content"]), 200)
        self.assertIn("déjà proposé", rows[1]["content"])


class MentionsTests(unittest.TestCase):
    def test_nom_de_fichier_unique(self):
        available = ["src/app.py", "docs/readme.md"]
        self.assertEqual(ox.resolve_mention("app.py", available), "src/app.py")
        self.assertEqual(ox.resolve_mention("src/app.py", available), "src/app.py")

    def test_introuvable(self):
        self.assertEqual(ox.resolve_mention("absent.py", ["a.py"]), "")


class ModelesTests(unittest.TestCase):
    def test_famille(self):
        self.assertEqual(ox.model_family("qwen2.5-coder:7b"), "qwen2.5")
        self.assertEqual(ox.model_family("cognitivecomputations/dolphin-24b"), "cognitivecomputations")
        self.assertEqual(ox.model_family(""), "")

    def test_uncensored(self):
        self.assertTrue(ox.is_uncensored_model("cognitivecomputations/dolphin3.0-mistral-24b"))
        self.assertFalse(ox.is_uncensored_model("qwen2.5-coder:7b"))

    def test_filter_models(self):
        names = ["qwen2.5-coder:7b", "cognitivecomputations/dolphin3.0-mistral-24b"]
        self.assertEqual(len(ox.filter_models(names, kind="uncensored")), 1)
        self.assertEqual(ox.filter_models(names, kind="all", family="qwen2.5"), ["qwen2.5-coder:7b"])

    def test_list_model_families(self):
        rows = ox.list_model_families(["a/x", "a/y", "b/z"])
        self.assertEqual(rows[0][0], "a")
        self.assertEqual(rows[0][1], 2)


class CatalogueTests(unittest.TestCase):
    def test_normalisation_source(self):
        self.assertEqual(ox.catalog_key("Groq"), "groq")
        self.assertEqual(ox.catalog_key("openrouter"), "openrouter")
        self.assertEqual(ox.catalog_key("inconnu"), "openrouter")
        self.assertEqual(ox.catalog_base("deepseek"), "https://api.deepseek.com/v1")

    def test_formatage(self):
        self.assertEqual(ox.format_context(1_000_000), "1.0M ctx")
        self.assertEqual(ox.format_context(131072), "131k ctx")
        self.assertEqual(ox.format_price(0), "gratuit")
        self.assertEqual(ox.format_price(0.03), "0.030 $/Mtok")

    def test_ligne_openrouter(self):
        row = ox._catalog_row(
            {
                "id": "qwen/qwen3.8-flash",
                "name": "Qwen 3.8 Flash",
                "context_length": 1000000,
                "pricing": {"prompt": "0.00000003", "completion": "0.00000012"},
                "architecture": {"input_modalities": ["text", "image"]},
                "supported_parameters": ["tools"],
            },
            "openrouter",
        )
        self.assertEqual(row["vendor"], "qwen")
        self.assertEqual(row["context"], 1_000_000)
        self.assertAlmostEqual(row["prompt"], 0.03, places=6)
        self.assertTrue(row["vision"])
        self.assertTrue(row["tools"])
        self.assertFalse(row["free"])

    def test_modele_gratuit(self):
        row = ox._catalog_row({"id": "meta/llama:free", "pricing": {"prompt": "0", "completion": "0"}},
                              "openrouter")
        self.assertTrue(row["free"])


if __name__ == "__main__":
    unittest.main()
