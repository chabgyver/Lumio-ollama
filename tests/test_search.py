"""Tests de lumio_search (recherche dans toutes les conversations)."""

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import lumio_search as ls  # noqa: E402

CHATS = [
    {
        "id": "c1",
        "title": "Corriger le parseur de modèle",
        "transcript": [
            {"role": "user", "content": "Comment corriger le parseur ?"},
            {"role": "assistant", "content": "Le parseur lit les tokens du modele."},
        ],
    },
    {
        "id": "c2",
        "title": "Autre sujet",
        "transcript": [{"role": "user", "content": "Rien à voir ici."}],
    },
]


class RechercheTests(unittest.TestCase):
    def test_requete_vide(self):
        self.assertEqual(ls.search_chats(CHATS, ""), [])
        self.assertEqual(ls.search_chats(CHATS, "   "), [])

    def test_insensible_a_la_casse_et_aux_accents(self):
        hits = ls.search_chats(CHATS, "MODELE")
        self.assertTrue(hits)
        self.assertTrue(all("modele" in ls.normalize(hit.snippet) or "modèle" in hit.snippet
                            for hit in hits if hit.role != "title"))

    def test_titre_classe_en_premier(self):
        hits = ls.search_chats(CHATS, "parseur")
        self.assertEqual(hits[0].role, "title")
        self.assertEqual(hits[0].chat_id, "c1")

    def test_message_utilisateur_avant_l_assistant(self):
        hits = [hit for hit in ls.search_chats(CHATS, "parseur") if hit.role in {"user", "assistant"}]
        self.assertEqual(hits[0].role, "user")

    def test_snippet_contient_le_mot(self):
        hit = ls.search_chats(CHATS, "tokens")[0]
        self.assertIn("tokens", ls.normalize(hit.snippet))

    def test_conversation_sans_correspondance(self):
        chats = [c for c in CHATS if c["id"] == "c2"]
        self.assertEqual(ls.search_chats(chats, "parseur"), [])

    def test_limite(self):
        self.assertLessEqual(len(ls.search_chats(CHATS, "e", limit=2)), 2)

    def test_entrees_invalides(self):
        self.assertEqual(ls.search_chats([None, "x", {}], "a"), [])


class SnippetTests(unittest.TestCase):
    def test_snippet_court(self):
        self.assertEqual(ls.snippet("bonjour", "bonjour"), "bonjour")

    def test_snippet_tronque_autour(self):
        text = "a" * 200 + " aiguille " + "b" * 200
        result = ls.snippet(text, "aiguille", width=40)
        self.assertIn("aiguille", result)
        self.assertLess(len(result), 60)
        self.assertTrue(result.startswith("…"))


if __name__ == "__main__":
    unittest.main()
