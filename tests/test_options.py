"""Réglages du modèle local : plafond de contexte et réflexion (aucun réseau)."""

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import ollama_fs_access as ox  # noqa: E402


class PlafondTests(unittest.TestCase):
    def test_sans_plafond(self):
        self.assertEqual(ox.context_budget("modele:1b"), ox.CONTEXT_CHAR_LIMIT)

    def test_plafond_applique(self):
        self.assertEqual(ox.context_budget("modele:1b", cap=20_000), 20_000)

    def test_plafond_sous_le_minimum(self):
        # On garde toujours de quoi envoyer un fichier entier.
        self.assertEqual(ox.context_budget("modele:1b", cap=500), 4_000)

    def test_plafond_texte_et_invalide(self):
        self.assertEqual(ox.context_budget("modele:1b", cap="8000"), 8_000)
        self.assertEqual(ox.context_budget("modele:1b", cap="beaucoup"), ox.CONTEXT_CHAR_LIMIT)
        self.assertEqual(ox.context_budget("modele:1b", cap=None), ox.CONTEXT_CHAR_LIMIT)

    def test_plafond_horde(self):
        self.assertEqual(ox.context_budget("x", provider="horde"), ox.HORDE_CONTEXT_CHARS)
        self.assertEqual(ox.context_budget("x", provider="horde", cap=3_000), 4_000)
        self.assertEqual(ox.context_budget("x", provider="horde", cap=5_000), 5_000)

    def test_plafond_ne_remonte_pas_un_budget_bas(self):
        # Un petit modèle a déjà un budget calculé : le plafond ne le gonfle pas.
        ox._SHOW_CACHE["petit:1b"] = {"model_info": {"general.context_length": 8_000}}
        self.addCleanup(ox._SHOW_CACHE.pop, "petit:1b", None)
        base = ox.context_budget("petit:1b")
        self.assertLess(base, 200_000)
        self.assertGreaterEqual(ox.context_budget("petit:1b", cap=200_000), base)


class ReflexionTests(unittest.TestCase):
    def setUp(self):
        self._orig_show = ox._ollama_show
        self.addCleanup(setattr, ox, "_ollama_show", self._orig_show)
        self._orig_support = ox.supports_thinking
        self.addCleanup(setattr, ox, "supports_thinking", self._orig_support)

    def test_capacite_annoncee(self):
        ox._ollama_show = lambda name: {"capabilities": ["completion", "tools", "thinking"]}
        self.assertTrue(ox.supports_thinking("modele:latest"))
        ox._ollama_show = lambda name: {"capabilities": ["completion", "tools"]}
        self.assertFalse(ox.supports_thinking("modele:latest"))

    def test_repli_sur_le_nom(self):
        def boum(name):
            raise RuntimeError("ollama injoignable")

        ox._ollama_show = boum
        self.assertTrue(ox.supports_thinking("qwen3:8b"))
        self.assertTrue(ox.supports_thinking("lfm3:latest"))
        self.assertFalse(ox.supports_thinking("qwen2.5-coder:3b"))
        self.assertFalse(ox.supports_thinking(""))

    def test_charge_utile_sans_demande(self):
        ox.supports_thinking = lambda name: True
        payload = ox.ollama_chat_payload("lfm3:latest", [{"role": "user", "content": "x"}])
        self.assertNotIn("think", payload)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["keep_alive"], "30m")

    def test_charge_utile_reflexion_coupee(self):
        ox.supports_thinking = lambda name: True
        payload = ox.ollama_chat_payload("lfm3:latest", [], think=False, options={"num_ctx": 4096})
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["options"], {"num_ctx": 4096})

    def test_charge_utile_modele_sans_reflexion(self):
        ox.supports_thinking = lambda name: False
        payload = ox.ollama_chat_payload("qwen2.5-coder:3b", [], think=False)
        self.assertNotIn("think", payload)

    def test_reflexion_transmise_par_ask_ollama(self):
        vus = {}
        orig_stream, orig_up = ox.read_chat_stream, ox.ollama_up
        self.addCleanup(setattr, ox, "read_chat_stream", orig_stream)
        self.addCleanup(setattr, ox, "ollama_up", orig_up)
        ox.ollama_up = lambda: True
        ox.read_chat_stream = lambda payload, **kwargs: vus.update(payload) or ""
        ox.supports_thinking = lambda name: True

        ox.ask_ollama("lfm3:latest", "bonjour", "", think=False)
        self.assertIs(vus.get("think"), False)

        vus.clear()
        ox.ask_ollama("lfm3:latest", "bonjour", "")
        self.assertNotIn("think", vus)


if __name__ == "__main__":
    unittest.main()
