"""Tests de la génération d'images (Ollama et AI Horde) — aucun réseau."""

import base64
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import lumio_image as mi  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
B64 = base64.b64encode(PNG).decode("ascii")


class _Reponse:
    """Réponse HTTP minimale : juste de quoi lire un corps JSON."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code=400, body=b'{"error": "unknown field width"}'):
    return urllib.error.HTTPError("http://127.0.0.1/api/generate", code, "erreur", None,
                                  io.BytesIO(body))


class TaillesTests(unittest.TestCase):
    def test_tuples_et_chaines(self):
        self.assertEqual(mi.normalize_size((832, 1216)), (832, 1216))
        self.assertEqual(mi.normalize_size("1216x832"), (1216, 832))
        self.assertEqual(mi.normalize_size("Paysage 1216×832"), (1216, 832))

    def test_valeur_invalide_retombe_sur_le_defaut(self):
        self.assertEqual(mi.normalize_size(""), mi.DEFAULT_SIZE)
        self.assertEqual(mi.normalize_size(None), mi.DEFAULT_SIZE)
        self.assertEqual(mi.normalize_size(("a", "b")), mi.DEFAULT_SIZE)

    def test_libelle(self):
        self.assertEqual(mi.size_label((1216, 832)), "Paysage 1216×832")
        self.assertEqual(mi.size_label((1024, 1024)), "Carré 1024×1024")
        self.assertEqual(mi.size_label((640, 640)), "640×640")

    def test_etapes_bornees(self):
        self.assertEqual(mi.normalize_steps("12"), 12)
        self.assertEqual(mi.normalize_steps(0), mi.MIN_STEPS)
        self.assertEqual(mi.normalize_steps(500), mi.MAX_STEPS)
        self.assertEqual(mi.normalize_steps("beaucoup"), mi.DEFAULT_STEPS)

    def test_graine(self):
        self.assertEqual(mi.normalize_seed("42"), 42)
        self.assertIsNone(mi.normalize_seed("aléatoire"))
        self.assertIsNone(mi.normalize_seed(""))
        self.assertIsNone(mi.normalize_seed("-3"))


class ModelesTests(unittest.TestCase):
    def test_reconnaissance(self):
        self.assertTrue(mi.looks_like_image_model("x/z-image-turbo"))
        self.assertTrue(mi.looks_like_image_model("x/flux.2-klein-9b"))
        self.assertFalse(mi.looks_like_image_model("qwen2.5-coder:7b"))
        self.assertFalse(mi.looks_like_image_model("llama3.2-vision"))

    def test_liste_locale_filtree(self):
        noms = ["qwen2.5-coder:7b", "x/z-image-turbo", "moondream", "x/flux.2-klein-4b"]
        self.assertEqual(
            mi.parse_ollama_image_models(noms),
            ["x/flux.2-klein-4b", "x/z-image-turbo"],
        )

    def test_liste_horde(self):
        rows = [
            {"name": "SDXL 1.0", "count": 12},
            {"name": "Flux.1-Schnell fp8 (Compact)", "count": 3},
            {"name": "Deliberate", "count": 0},
            {"name": ""},
            "Pony Diffusion XL",
        ]
        self.assertEqual(
            mi.parse_horde_image_models(rows),
            ["SDXL 1.0", "Flux.1-Schnell fp8 (Compact)"],
        )

    def test_tous_hors_ligne_reste_propose(self):
        self.assertEqual(mi.parse_horde_image_models([{"name": "Deliberate", "count": 0}]),
                         ["Deliberate"])

    def test_kudos_augmente_avec_la_taille(self):
        petit = mi.horde_image_kudos("SDXL 1.0", (512, 512))
        grand = mi.horde_image_kudos("SDXL 1.0", (1216, 832))
        self.assertLess(petit, grand)


class RequeteOllamaTests(unittest.TestCase):
    def test_corps_complet(self):
        payload = mi.build_ollama_payload(
            "x/z-image-turbo", "  un chat  ", (832, 1216), 8, 7, " flou ",
        )
        self.assertEqual(payload["model"], "x/z-image-turbo")
        self.assertEqual(payload["prompt"], "un chat")
        self.assertFalse(payload["stream"])
        self.assertEqual((payload["width"], payload["height"]), (832, 1216))
        self.assertEqual(payload["steps"], 8)
        self.assertEqual(payload["seed"], 7)
        self.assertEqual(payload["negative_prompt"], "flou")

    def test_corps_minimal(self):
        payload = mi.build_ollama_payload("x/z-image-turbo", "un chat", minimal=True)
        self.assertEqual(set(payload), {"model", "prompt", "stream"})

    def test_decodage(self):
        self.assertEqual(mi.decode_base64_image(B64), PNG)
        self.assertEqual(mi.decode_base64_image(f"data:image/png;base64,{B64}"), PNG)
        self.assertEqual(mi.decode_base64_image("\n".join([B64[:6], B64[6:]])), PNG)
        self.assertIsNone(mi.decode_base64_image(""))
        self.assertIsNone(mi.decode_base64_image("%%%"))

    def test_images_absentes(self):
        self.assertEqual(mi.decode_ollama_images({"images": []}), [])
        self.assertEqual(mi.decode_ollama_images({}), [])
        self.assertEqual(mi.decode_ollama_images(None), [])

    def test_images_en_dict(self):
        self.assertEqual(mi.decode_ollama_images({"images": [{"data": B64}]}), [PNG])


class GenerationOllamaTests(unittest.TestCase):
    def test_nominal(self):
        reponse = _Reponse({"images": [B64], "seed": 1234, "done": True})
        with mock.patch.object(mi.urllib.request, "urlopen", return_value=reponse) as appel:
            data, secondes, seed = mi.generate_ollama("x/z-image-turbo", "un chat")
        self.assertEqual(data, PNG)
        self.assertGreaterEqual(secondes, 0)
        self.assertEqual(seed, 1234)
        corps = json.loads(appel.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(corps["width"], mi.DEFAULT_SIZE[0])

    def test_repli_sans_options_apres_un_400(self):
        appels = [_http_error(400), _Reponse({"images": [B64]})]
        with mock.patch.object(mi.urllib.request, "urlopen", side_effect=appels) as appel:
            data, _, _ = mi.generate_ollama("x/z-image-turbo", "un chat", steps=8)
        self.assertEqual(data, PNG)
        self.assertEqual(appel.call_count, 2)
        second = json.loads(appel.call_args[0][0].data.decode("utf-8"))
        self.assertEqual(set(second), {"model", "prompt", "stream"})

    def test_erreur_non_reessayee(self):
        with mock.patch.object(mi.urllib.request, "urlopen", side_effect=_http_error(500)):
            with self.assertRaises(RuntimeError):
                mi.generate_ollama("x/z-image-turbo", "un chat")

    def test_sans_image_leve_une_erreur_lisible(self):
        with mock.patch.object(mi.urllib.request, "urlopen", return_value=_Reponse({})):
            with self.assertRaises(RuntimeError) as ctx:
                mi.generate_ollama("x/z-image-turbo", "un chat")
        self.assertIn("n'a pas renvoyé d'image", str(ctx.exception))


class RequeteHordeTests(unittest.TestCase):
    def test_corps(self):
        payload = mi.build_horde_payload("SDXL 1.0", "un chat", (1024, 1024), 20, 5, "flou")
        self.assertEqual(payload["models"], ["SDXL 1.0"])
        self.assertEqual(payload["params"]["width"], 1024)
        self.assertEqual(payload["params"]["steps"], 20)
        self.assertEqual(payload["params"]["seed"], "5")
        self.assertEqual(payload["params"]["negative_prompt"], "flou")
        self.assertFalse(payload["nsfw"])
        self.assertFalse(payload["r2"])

    def test_sans_graine_ni_negatif(self):
        payload = mi.build_horde_payload("SDXL 1.0", "un chat")
        self.assertNotIn("seed", payload["params"])
        self.assertNotIn("negative_prompt", payload["params"])

    def test_reponse_ignore_les_images_censurees(self):
        statut = {"generations": [
            {"img": B64, "censored": True},
            {"img": B64, "censored": False, "seed": "9"},
        ]}
        data, brut = mi.horde_image_reply(statut)
        self.assertEqual(data, PNG)
        self.assertEqual(brut["seed"], "9")

    def test_image_servie_par_url(self):
        statut = {"generations": [{"img": "https://r2.example/image.png", "censored": False}]}
        with mock.patch.object(mi, "_fetch_image_url", return_value=PNG) as telecharger:
            data, brut = mi.horde_image_reply(statut)
        self.assertEqual(data, PNG)
        telecharger.assert_called_once_with("https://r2.example/image.png")
        self.assertEqual(brut["img"], "https://r2.example/image.png")

    def test_reponse_vide(self):
        self.assertEqual(mi.horde_image_reply({"generations": []}), (None, None))
        self.assertEqual(mi.horde_image_reply({}), (None, None))


class FichiersTests(unittest.TestCase):
    def test_nom_depuis_invite(self):
        self.assertEqual(
            mi.suggest_name("Une cuisine au petit matin, lumière rasante"),
            "cuisine-petit-matin-lumiere-rasante",
        )
        self.assertEqual(mi.suggest_name("Été à Noël"), "ete-noel")
        self.assertEqual(mi.suggest_name(""), "image")
        self.assertLessEqual(len(mi.suggest_name("mot " * 40)), 40)

    def test_extension_par_signature(self):
        self.assertEqual(mi.extension_for(PNG), ".png")
        self.assertEqual(mi.extension_for(b"\xff\xd8\xff\xe0"), ".jpg")
        self.assertEqual(mi.extension_for(b"RIFF0000WEBP"), ".webp")
        self.assertEqual(mi.extension_for(b"??"), ".png")

    def test_nom_unique(self):
        with tempfile.TemporaryDirectory() as dossier:
            premier = mi.unique_path(dossier, "chat", ".png")
            self.assertEqual(premier.name, "chat.png")
            premier.write_bytes(b"x")
            self.assertEqual(mi.unique_path(dossier, "chat", ".png").name, "chat-2.png")

    def test_ecriture(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = mi.save_image(PNG, dossier, "un chat")
            self.assertEqual(chemin.name, "chat.png")
            self.assertEqual(chemin.read_bytes(), PNG)

    def test_dossier_absent(self):
        with self.assertRaises(RuntimeError):
            mi.save_image(PNG, str(Path(tempfile.gettempdir()) / "lumio-absent-xyz"), "x")

    def test_sans_donnees(self):
        with self.assertRaises(RuntimeError):
            mi.save_image(b"", tempfile.gettempdir(), "x")


class AttenteHordeTests(unittest.TestCase):
    def test_limite_de_requetes_puis_succes(self):
        statuts = [_http_error(429), {"done": True, "generations": []}]
        with mock.patch.object(mi, "horde_image_status", side_effect=statuts), \
                mock.patch.object(mi.time, "sleep") as pause:
            resultat = mi.wait_horde_image("tache")
        self.assertTrue(resultat["done"])
        pause.assert_called_once_with(mi.HORDE_IMAGE_429_WAIT)

    def test_intervalle_de_polling(self):
        statuts = [{"done": False, "queue_position": 4, "wait_time": 40},
                   {"done": True, "generations": []}]
        with mock.patch.object(mi, "horde_image_status", side_effect=statuts), \
                mock.patch.object(mi.time, "sleep") as pause:
            mi.wait_horde_image("tache")
        pause.assert_called_once_with(mi.HORDE_IMAGE_POLL_SECONDS)
        self.assertGreaterEqual(mi.HORDE_IMAGE_POLL_SECONDS * 6, 60)

    def test_annulation(self):
        import threading
        jeton = threading.Event()
        jeton.set()
        with mock.patch.object(mi, "horde_cancel_image") as annuler:
            self.assertIsNone(mi.wait_horde_image("tache", cancel_event=jeton))
        annuler.assert_called_once_with("tache")


class AiguillageTests(unittest.TestCase):
    def test_ollama(self):
        with mock.patch.object(mi, "generate_ollama", return_value=(PNG, 2.0, 11)):
            resultat = mi.generate("Ollama", "x/z-image-turbo", "un chat", (1024, 1024), 8)
        self.assertEqual(resultat["provider"], "ollama")
        self.assertEqual(resultat["width"], 1024)
        self.assertEqual(resultat["seed"], 11)
        self.assertEqual(resultat["kudos"], 0.0)

    def test_horde(self):
        with mock.patch.object(mi, "generate_horde", return_value=(PNG, 30.0, 7, 12.5)):
            resultat = mi.generate("AI Horde", "SDXL 1.0", "un chat")
        self.assertEqual(resultat["provider"], "horde")
        self.assertEqual(resultat["kudos"], 12.5)
        self.assertEqual(resultat["seconds"], 30.0)

    def test_annulation(self):
        with mock.patch.object(mi, "generate_horde", return_value=None):
            self.assertIsNone(mi.generate("horde", "SDXL 1.0", "un chat"))


if __name__ == "__main__":
    unittest.main()

