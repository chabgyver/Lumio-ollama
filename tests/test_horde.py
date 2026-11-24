"""Tests du fournisseur AI Horde (prompt, file d'attente, clé) — aucun réseau."""

import io
import sys
import unittest
import urllib.error
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import ollama_fs_access as ox  # noqa: E402


class FournisseurTests(unittest.TestCase):
    def test_normalisation(self):
        for name in ("horde", "AI Horde", "ai-horde", "aihorde", "AIHorde"):
            self.assertEqual(ox.normalize_provider(name), "horde")

    def test_libelle(self):
        self.assertEqual(ox.provider_label("horde"), "AI Horde")

    def test_present_dans_les_fournisseurs(self):
        self.assertIn("horde", ox.PROVIDERS)

    def test_budget_de_contexte_court(self):
        self.assertEqual(
            ox.context_budget("huihui-qwen3.8-27b-abliterated", provider="horde"),
            ox.HORDE_CONTEXT_CHARS,
        )
        # Un modèle local garde un budget de fichiers bien plus large.
        local = ox.context_budget("qwen2.5-coder:3b", provider="ollama")
        self.assertGreater(local, ox.HORDE_CONTEXT_CHARS)

    def test_modele_par_defaut(self):
        names = ["koboldcpp/Llama-3.2-1B-Instruct", "huihui-qwen3.8-27b-abliterated"]
        self.assertEqual(ox.pick_listed_model(names, "", "horde"), names[0])
        self.assertEqual(ox.pick_listed_model(names, names[1], "horde"), names[1])

    def test_pas_de_cout(self):
        self.assertEqual(ox.stream_usage({}, "horde"), {})
        self.assertEqual(ox.stream_usage({"usage": {}}, "horde"), {})


class ClesTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(ox.configure_horde, ox.horde_config().get("key") or "")

    def test_anonyme_par_defaut(self):
        ox.configure_horde("")
        headers = ox.horde_headers()
        self.assertEqual(headers["apikey"], ox.HORDE_ANON_KEY)
        self.assertIn("User-Agent", headers)
        self.assertTrue(headers["Client-Agent"])

    def test_cle_enregistree(self):
        ox.configure_horde("abc123")
        self.assertEqual(ox.horde_headers()["apikey"], "abc123")
        self.assertEqual(ox.horde_config()["key"], "abc123")

    def test_identifiants_ne_sont_pas_partages(self):
        ox.configure_horde("abc123")
        copie = ox.horde_config()
        copie["key"] = "autre"
        self.assertEqual(ox.horde_config()["key"], "abc123")


class ListeTests(unittest.TestCase):
    def test_tri_liberte_puis_taille(self):
        rows = [
            {"name": "koboldcpp/Llama-3.2-1B-Instruct", "count": 4},
            {"name": "huihui-qwen3.8-27b-abliterated", "count": 2},
            {"name": "koboldcpp/Cydonia-24B-v4.3-heretic-Q4_K_M", "count": 1},
            {"name": "koboldcpp/L3-8B-Stheno-v3.2", "count": 3},
        ]
        names = ox.parse_horde_models(rows)
        self.assertEqual(
            names,
            [
                "huihui-qwen3.8-27b-abliterated",
                "koboldcpp/Cydonia-24B-v4.3-heretic-Q4_K_M",
                "koboldcpp/L3-8B-Stheno-v3.2",
                "koboldcpp/Llama-3.2-1B-Instruct",
            ],
        )

    def test_doublons_et_vides_ignores(self):
        rows = [
            {"name": "a/x-abliterated", "count": 1},
            {"name": "a/x-abliterated", "count": 3},
            {"name": "", "count": 2},
            {"count": 5},
        ]
        self.assertEqual(ox.parse_horde_models(rows), ["a/x-abliterated"])

    def test_sans_worker_en_ligne(self):
        rows = [{"name": "huihui/a-abliterated", "count": 0}, {"name": "b/model", "count": 0}]
        self.assertEqual(ox.parse_horde_models(rows), ["huihui/a-abliterated", "b/model"])

    def test_replis_sur_les_chaines(self):
        self.assertEqual(ox.parse_horde_models(["a/b", "a/b", "c/d"]), ["a/b", "c/d"])

    def test_liste_vide(self):
        self.assertEqual(ox.parse_horde_models([]), [])
        self.assertEqual(ox.parse_horde_models(None), [])


class PromptTests(unittest.TestCase):
    def test_structure_alpaca(self):
        prompt = ox.horde_prompt("Que fait ce fichier ?", "demo.py : print(1)", None, True)
        self.assertTrue(prompt.startswith("Tu réponds en français"))
        self.assertIn("### Instruction:", prompt)
        self.assertTrue(prompt.endswith("### Response:\n"))
        self.assertIn("demo.py : print(1)", prompt)
        self.assertIn("Que fait ce fichier ?", prompt)
        self.assertEqual(prompt.count("### Instruction:"), 1)

    def test_historique_en_roles(self):
        history = [
            {"role": "user", "content": "première question"},
            {"role": "assistant", "content": "première réponse"},
        ]
        prompt = ox.horde_prompt("deuxième question", "", history, False)
        self.assertEqual(prompt.count("### Instruction:"), 2)
        self.assertIn("### Response:\npremière réponse", prompt)
        self.assertNotIn("Fichiers :", prompt)

    def test_prompt_borne(self):
        gros = "x" * 40_000
        prompt = ox.horde_prompt("question finale", gros, None, True, limit=4_000)
        self.assertLessEqual(len(prompt), 4_000)
        self.assertIn("[…tronqué…]", prompt)
        self.assertTrue(prompt.endswith("### Response:\n"))

    def test_coupe_garde_le_debut_et_la_fin(self):
        text = "DEBUT" + "m" * 5_000 + "FIN"
        out = ox.shrink_prompt(text, 1_000, keep_head=50)
        self.assertLessEqual(len(out), 1_000)
        self.assertTrue(out.startswith("DEBUT"))
        self.assertTrue(out.endswith("FIN"))

    def test_coupe_sans_effet_si_court(self):
        self.assertEqual(ox.shrink_prompt("court", 1_000), "court")
        self.assertEqual(ox.shrink_prompt("", 1_000), "")

    def test_limite_nulle(self):
        self.assertEqual(ox.shrink_prompt("abc", 0), "abc")


class ReponseTests(unittest.TestCase):
    def test_premiere_generation_utile(self):
        status = {
            "generations": [
                {"text": "  ", "censored": False},
                {"text": "censure", "censored": True},
                {"text": "bonjour", "censored": False},
            ]
        }
        texte, gen = ox.horde_reply(status)
        self.assertEqual(texte, "bonjour")
        self.assertEqual(gen["text"], "bonjour")

    def test_aucune_generation(self):
        self.assertEqual(ox.horde_reply({}), ("", {}))
        self.assertEqual(ox.horde_reply({"generations": []}), ("", {}))
        self.assertEqual(ox.horde_reply({"generations": [{"censored": True, "text": "x"}]}), ("", {}))

    def test_reflexion_separee(self):
        texte = "<think>je réfléchis</think>La réponse."
        thinking, answer = ox.split_horde_thinking(texte)
        self.assertEqual(thinking, "je réfléchis")
        self.assertEqual(answer, "La réponse.")

    def test_reflexion_non_fermee(self):
        thinking, answer = ox.split_horde_thinking("Début utile\n<think>coupé net")
        self.assertEqual(answer, "Début utile")
        self.assertIn("coupé net", thinking)

    def test_sans_reflexion(self):
        self.assertEqual(ox.split_horde_thinking("simple"), ("", "simple"))
        self.assertEqual(ox.split_horde_thinking(""), ("", ""))

    def test_decoupage(self):
        self.assertEqual(ox.chunk_text("abcdef", 2), ["ab", "cd", "ef"])
        self.assertEqual(ox.chunk_text("", 10), [])
        self.assertEqual(len(ox.chunk_text("a" * 1_000)), 7)


class ErreursTests(unittest.TestCase):
    @staticmethod
    def _http_error(code, body=b"{}"):
        return urllib.error.HTTPError(
            "https://aihorde.net/api/v2/test", code, "erreur", {}, io.BytesIO(body),
        )

    def test_cle_refusee(self):
        self.assertIn("clé API", ox.horde_error(self._http_error(401)))

    def test_cle_invalide_nommee(self):
        message = ox.horde_error(
            self._http_error(401, b'{"message": "Invalid API Key"}')
        )
        self.assertIn("clé API", message)
        self.assertNotIn("Aucun worker", message)

    def test_modele_sans_worker(self):
        # AI Horde répond 403 aussi bien pour une clé invalide que pour un
        # modèle qu'aucun worker ne sert : le message doit dire lequel.
        message = ox.horde_error(
            self._http_error(403, b'{"message":"No online worker can serve this request.","rc":"NoEligibleWorker"}')
        )
        self.assertIn("Aucun worker", message)
        self.assertNotIn("clé API", message)

    def test_file_pleine(self):
        message = ox.horde_error(
            self._http_error(429, b'{"message": "Queue is full"}')
        )
        self.assertIn("saturé", message)
        self.assertIn("Queue is full", message)

    def test_demande_perdue(self):
        self.assertIn("relancez", ox.horde_error(self._http_error(404)).casefold())

    def test_autre_refus(self):
        message = ox.horde_error(self._http_error(500, b'{"message": "boom"}'))
        self.assertIn("500", message)
        self.assertIn("boom", message)

    def test_explanation_passe_par_horde(self):
        message = ox.explain_backend_error(self._http_error(429), provider="horde")
        self.assertIn("saturé", message)


class AttenteTests(unittest.TestCase):
    """File d'attente : aucun appel réseau, horde_status est remplacé."""

    def setUp(self):
        for name in ("horde_status", "horde_cancel", "HORDE_POLL_SECONDS"):
            self.addCleanup(setattr, ox, name, getattr(ox, name))
        ox.HORDE_POLL_SECONDS = 0.001
        self.retire = []
        ox.horde_cancel = lambda job_id: self.retire.append(job_id) or True

    def test_fin_normale(self):
        ox.horde_status = lambda job_id: {"done": True, "generations": [{"text": "fini"}]}
        status = ox.horde_wait("job", wait=30)
        self.assertEqual(ox.horde_reply(status)[0], "fini")
        self.assertEqual(self.retire, [])

    def test_annulation_retire_la_demande(self):
        import threading

        stop = threading.Event()
        stop.set()
        ox.horde_status = lambda job_id: {"done": False}
        self.assertIsNone(ox.horde_wait("job", cancel_event=stop, wait=30))
        self.assertEqual(self.retire, ["job"])

    def test_echec_du_worker(self):
        ox.horde_status = lambda job_id: {"done": False, "faulted": True}
        with self.assertRaises(RuntimeError) as ctx:
            ox.horde_wait("job", wait=30)
        self.assertIn("échoué", str(ctx.exception))

    def test_modele_sans_worker(self):
        ox.horde_status = lambda job_id: {"done": False, "faulted": False, "is_possible": False}
        with self.assertRaises(RuntimeError) as ctx:
            ox.horde_wait("job", wait=30)
        self.assertIn("Aucun worker", str(ctx.exception))

    def test_progression_transmise(self):
        etats = [
            {"done": False, "queue_position": 3, "wait_time": 40},
            {"done": False, "queue_position": 1, "wait_time": 10},
            {"done": True, "generations": [{"text": "voilà"}]},
        ]
        ox.horde_status = lambda job_id: etats.pop(0)
        vus = []
        status = ox.horde_wait("job", on_progress=vus.append, wait=30)
        self.assertEqual(ox.horde_reply(status)[0], "voilà")
        self.assertTrue(any("position 3" in item for item in vus))
        self.assertTrue(any("~10 s" in item for item in vus))


if __name__ == "__main__":
    unittest.main()
