"""Génération d'images : modèle local (Ollama) ou communautaire (AI Horde).

Le reste de Lumio ne parle qu'en conversation : ce module ajoute les deux appels
qui produisent une image et renvoie des octets PNG, prêts à être affichés ou
écrits dans le dossier. Aucune dépendance en dehors de la bibliothèque standard ;
la clé et l'agent AI Horde sont ceux de `ollama_fs_access`.

Ollama : POST /api/generate, la réponse porte « images » (base64). La génération
d'images y est expérimentale, d'où le repli sur une requête minimale quand le
serveur refuse les options de taille.
AI Horde : POST /generate/async puis /generate/status/{id} jusqu'à « done » ;
la génération passe par la même file d'attente que le texte.
"""

import base64
import binascii
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

import ollama_fs_access as ox

IMAGE_PROVIDERS = ("ollama", "horde")
IMAGE_PROVIDER_LABELS = {"ollama": "Ollama — local", "horde": "AI Horde — file d'attente"}

# Tailles proposées : multiples de 64, acceptées par les modèles courants.
IMAGE_SIZES = (
    (1216, 832, "Paysage 1216×832"),
    (1024, 1024, "Carré 1024×1024"),
    (832, 1216, "Portrait 832×1216"),
)
DEFAULT_SIZE = (1216, 832)
DEFAULT_STEPS = 8
MIN_STEPS, MAX_STEPS = 1, 60

# Noms qui trahissent un modèle de génération d'images chez Ollama : la
# bibliothèque ne publie pas encore de capacité dédiée dans /api/show.
OLLAMA_IMAGE_MARKERS = (
    "z-image", "flux", "sdxl", "stable-diffusion", "sd3", "playground",
    "janus", "kolors", "lumina", "hunyuan-image", "qwen-image",
)

HORDE_IMAGE_WAIT = 1800         # secondes avant d'abandonner la demande
# L'API image limite les comptes anonymes à 10 requêtes par minute : interroger
# toutes les 3 s (comme le texte) renvoie un 429 au bout de vingt secondes.
HORDE_IMAGE_POLL_SECONDS = 10
HORDE_IMAGE_429_WAIT = 20       # pause imposée quand la limite est atteinte
HORDE_IMAGE_STEPS = 20
HORDE_IMAGE_MODELS = ("Flux.1-Schnell fp8 (Compact)", "SDXL 1.0", "Deliberate")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
WEBP_MAGIC = b"RIFF"


# ==========================================================
#  Tailles et modèles
# ==========================================================

def image_provider_key(provider="ollama"):
    """Seuls Ollama et AI Horde savent produire une image."""
    key = str(provider or "").strip().casefold()
    if key.startswith("horde") or "horde" in key:
        return "horde"
    return "ollama"


def provider_label(provider="ollama"):
    return IMAGE_PROVIDER_LABELS[image_provider_key(provider)]


def normalize_size(value=DEFAULT_SIZE):
    """Accepte (1216, 832), « 1216x832 », « Paysage 1216×832 »… sinon le défaut."""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        try:
            width, height = int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return DEFAULT_SIZE
        return (width, height) if width > 0 and height > 0 else DEFAULT_SIZE
    text = str(value or "")
    found = re.findall(r"(\d{2,5})\s*[x×*]\s*(\d{2,5})", text)
    if found:
        width, height = (int(part) for part in found[0])
        if width > 0 and height > 0:
            return (width, height)
    return DEFAULT_SIZE


def size_label(size=DEFAULT_SIZE):
    width, height = normalize_size(size)
    for known_w, known_h, label in IMAGE_SIZES:
        if (known_w, known_h) == (width, height):
            return label
    return f"{width}×{height}"


def normalize_steps(value=DEFAULT_STEPS):
    try:
        steps = int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return DEFAULT_STEPS
    return max(MIN_STEPS, min(MAX_STEPS, steps))


def normalize_seed(value=None):
    """None = graine aléatoire, sinon un entier positif stable."""
    text = str(value or "").strip()
    if not text or text.casefold().startswith(("aléa", "alea", "random", "-")):
        return None
    try:
        seed = int(float(text))
    except (TypeError, ValueError):
        return None
    return seed if seed >= 0 else None


def looks_like_image_model(name):
    low = str(name or "").casefold()
    return any(marker in low for marker in OLLAMA_IMAGE_MARKERS)


def parse_ollama_image_models(names):
    """Ne garde que les modèles qui produisent une image."""
    kept = []
    for name in names or []:
        label = str(name or "").strip()
        if label and looks_like_image_model(label) and label not in kept:
            kept.append(label)
    return sorted(kept, key=str.casefold)


def parse_horde_image_models(rows):
    """Modèles image servis en ce moment, les plus disponibles d'abord."""
    found = {}
    for row in rows or []:
        if isinstance(row, str):
            found[row.strip()] = 0
            continue
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        try:
            count = int(row.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        found[name] = max(count, found.get(name, 0))
    live = {name: count for name, count in found.items() if name and count > 0}
    pool = live or {name: count for name, count in found.items() if name}
    return sorted(pool, key=lambda name: (-pool[name], name.casefold()))


def image_models(provider="ollama", timeout=8):
    """Modèles capables de produire une image chez le moteur choisi."""
    if image_provider_key(provider) == "horde":
        request = urllib.request.Request(
            f"{ox.HORDE_URL}/status/models?type=image", headers=ox.horde_headers(),
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        rows = data.get("models") if isinstance(data, dict) else data
        return parse_horde_image_models(rows)

    request = urllib.request.Request(f"{ox.OLLAMA_URL}/api/tags")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    names = [row.get("name", "") for row in data.get("models", []) if row.get("name")]
    return parse_ollama_image_models(names)


def horde_image_kudos(model, size=DEFAULT_SIZE, steps=HORDE_IMAGE_STEPS):
    """Estimation des kudos avant la demande (aucune réservation n'est faite)."""
    width, height = normalize_size(size)
    pixels = (width * height) / 1_000_000
    return round(0.6 + pixels * 12 + normalize_steps(steps) * 0.05, 1)


# ==========================================================
#  Requêtes
# ==========================================================

def build_ollama_payload(model, prompt, size=DEFAULT_SIZE, steps=DEFAULT_STEPS,
                         seed=None, negative="", minimal=False):
    """Corps de /api/generate pour un modèle image."""
    payload = {
        "model": str(model or "").strip(),
        "prompt": str(prompt or "").strip(),
        "stream": False,
    }
    if minimal:
        return payload
    width, height = normalize_size(size)
    payload.update({"width": width, "height": height, "steps": normalize_steps(steps)})
    if seed is not None:
        payload["seed"] = int(seed)
    text = str(negative or "").strip()
    if text:
        payload["negative_prompt"] = text
    return payload


def decode_base64_image(data):
    """Décode un base64 éventuellement préfixé (data URI) ; None si illisible."""
    text = str(data or "").strip()
    if not text:
        return None
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    text = re.sub(r"\s+", "", text)
    text += "=" * (-len(text) % 4)
    try:
        data = base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError):
        return None
    return data or None


def decode_ollama_images(payload):
    """Octets des images renvoyées par /api/generate (liste vide si aucune)."""
    if not isinstance(payload, dict):
        return []
    raw = payload.get("images") or []
    if isinstance(raw, str):
        raw = [raw]
    images = []
    for item in raw:
        if isinstance(item, dict):
            item = item.get("data") or item.get("image") or ""
        data = decode_base64_image(item)
        if data:
            images.append(data)
    return images


def build_horde_payload(model, prompt, size=DEFAULT_SIZE, steps=HORDE_IMAGE_STEPS,
                        seed=None, negative=""):
    width, height = normalize_size(size)
    params = {
        "width": width,
        "height": height,
        "steps": normalize_steps(steps),
        "n": 1,
        "sampler_name": "k_euler",
        "cfg_scale": 7.0,
    }
    if seed is not None:
        params["seed"] = str(int(seed))
    text = str(negative or "").strip()
    if text:
        params["negative_prompt"] = text
    payload = {
        "prompt": str(prompt or "").strip(),
        "params": params,
        "nsfw": False,
        # Pas de stockage R2 : la réponse doit porter l'image en base64.
        "r2": False,
        "censor_nsfw": False,
        "models": [str(model or "").strip()],
        "trusted_workers": False,
        "slow_workers": True,
    }
    return payload


def horde_submit_image(payload, timeout=45):
    """Dépose la demande d'image et renvoie (identifiant, kudos estimés)."""
    request = urllib.request.Request(
        f"{ox.HORDE_URL}/generate/async",
        data=json.dumps(payload).encode("utf-8"),
        headers=ox.horde_headers(),
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data.get("id") or ""), float(data.get("kudos") or 0.0)


def horde_image_status(job_id, timeout=30):
    request = urllib.request.Request(
        f"{ox.HORDE_URL}/generate/status/{job_id}", headers=ox.horde_headers(),
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def horde_cancel_image(job_id):
    try:
        request = urllib.request.Request(
            f"{ox.HORDE_URL}/generate/status/{job_id}",
            headers=ox.horde_headers(), method="DELETE",
        )
        urllib.request.urlopen(request, timeout=10).close()
    except Exception:
        return False
    return True


def _fetch_image_url(url, timeout=60):
    """Récupère une image servie par une URL (réponse R2 de AI Horde)."""
    request = urllib.request.Request(url, headers={"User-Agent": ox.HORDE_USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def horde_image_reply(status):
    """Première image non censurée d'un statut terminé : (octets, brut)."""
    for item in (status or {}).get("generations") or []:
        if not isinstance(item, dict) or item.get("censored"):
            continue
        raw = item.get("img") or ""
        if isinstance(raw, str) and raw.startswith(("http://", "https://")):
            try:
                return _fetch_image_url(raw), item
            except Exception:
                continue
        data = decode_base64_image(raw)
        if data:
            return data, item
    return None, None


def wait_horde_image(job_id, cancel_event=None, on_progress=None, wait=HORDE_IMAGE_WAIT):
    """Attend la fin de la génération. None si l'utilisateur annule."""
    deadline = time.time() + max(60, int(wait or HORDE_IMAGE_WAIT))
    shown = ""
    while time.time() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            horde_cancel_image(job_id)
            return None
        try:
            status = horde_image_status(job_id)
        except urllib.error.HTTPError as error:
            if error.code == 429:
                # Limite de requêtes : on laisse passer la minute en cours.
                if on_progress is not None:
                    on_progress("AI Horde : limite de requêtes atteinte, nouvelle tentative…")
                time.sleep(HORDE_IMAGE_429_WAIT)
                continue
            raise
        if status.get("faulted"):
            raise RuntimeError(
                "AI Horde : la génération de l'image a échoué côté worker. Relancez."
            )
        if status.get("done"):
            return status
        if status.get("is_possible") is False:
            raise RuntimeError(
                "Aucun worker AI Horde ne peut prendre ce modèle pour l'instant. "
                "Choisissez-en un autre, ou réessayez plus tard."
            )
        bits = ["AI Horde : file d'attente"]
        position = status.get("queue_position")
        eta = status.get("wait_time")
        if isinstance(position, (int, float)) and position > 0:
            bits.append(f"position {int(position)}")
        if isinstance(eta, (int, float)) and eta > 0:
            bits.append(f"~{int(eta)} s")
        label = " · ".join(bits)
        if on_progress is not None and label != shown:
            shown = label
            on_progress(label)
        time.sleep(HORDE_IMAGE_POLL_SECONDS)
    horde_cancel_image(job_id)
    raise RuntimeError("AI Horde n'a rien renvoyé dans le délai imparti. Réessayez plus tard.")


def generate_ollama(model, prompt, size=DEFAULT_SIZE, steps=DEFAULT_STEPS, seed=None,
                    negative="", timeout=600):
    """Génère une image en local. Renvoie (octets, secondes, graine)."""
    payload = build_ollama_payload(model, prompt, size, steps, seed, negative)
    started = time.time()
    data = _post_ollama_generate(payload, timeout=timeout)
    images = decode_ollama_images(data)
    if not images:
        raise RuntimeError(
            "Ollama n'a pas renvoyé d'image. Vérifiez que ce modèle sait générer "
            "des images et que votre version d'Ollama le prend en charge."
        )
    used_seed = data.get("seed") if isinstance(data, dict) else None
    return images[0], time.time() - started, used_seed


def _post_ollama_generate(payload, timeout=600):
    request = urllib.request.Request(
        f"{ox.OLLAMA_URL}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # Les options de taille ne sont pas acceptées par toutes les versions :
        # deuxième essai avec la requête minimale (model + prompt).
        if error.code != 400 or not ({'width', 'height', 'steps'} & set(payload)):
            raise RuntimeError(ox.explain_backend_error(error, images=False)) from error
        minimal = build_ollama_payload(payload.get("model"), payload.get("prompt"), minimal=True)
        request = urllib.request.Request(
            f"{ox.OLLAMA_URL}/api/generate",
            data=json.dumps(minimal).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))


def generate_horde(model, prompt, size=DEFAULT_SIZE, steps=HORDE_IMAGE_STEPS, seed=None,
                   negative="", cancel_event=None, on_progress=None):
    """Génère une image via la file AI Horde. None si annulé.

    Renvoie (octets, secondes, graine, kudos).
    """
    payload = build_horde_payload(model, prompt, size, steps, seed, negative)
    if on_progress is not None:
        on_progress(f"AI Horde : envoi de la demande à « {model} »…")
    started = time.time()
    try:
        job_id, kudos = horde_submit_image(payload)
    except urllib.error.HTTPError as error:
        raise RuntimeError(ox.horde_error(error)) from error
    except urllib.error.URLError as error:
        raise RuntimeError(ox.explain_backend_error(error, provider="horde")) from error
    if not job_id:
        raise RuntimeError("AI Horde n'a pas renvoyé d'identifiant de tâche. Réessayez.")
    try:
        status = wait_horde_image(job_id, cancel_event=cancel_event, on_progress=on_progress)
    except urllib.error.HTTPError as error:
        raise RuntimeError(ox.horde_error(error)) from error
    except urllib.error.URLError as error:
        raise RuntimeError(ox.explain_backend_error(error, provider="horde")) from error
    if status is None:
        return None
    data, item = horde_image_reply(status)
    if not data:
        generations = [g for g in (status.get("generations") or []) if isinstance(g, dict)]
        if generations and all(g.get("censored") for g in generations):
            raise RuntimeError(
                "AI Horde a censuré l'image : le worker l'a jugée sensible. "
                "Reformulez l'invite ou changez de modèle."
            )
        raise RuntimeError("AI Horde n'a pas renvoyé d'image. Relancez la génération.")
    used_seed = item.get("seed") if isinstance(item, dict) else None
    return data, time.time() - started, used_seed, kudos


def generate(provider, model, prompt, size=DEFAULT_SIZE, steps=DEFAULT_STEPS, seed=None,
             negative="", cancel_event=None, on_progress=None):
    """Point d'entrée unique : renvoie un dictionnaire de résultat."""
    key = image_provider_key(provider)
    if key == "horde":
        got = generate_horde(model, prompt, size, steps, seed, negative,
                             cancel_event=cancel_event, on_progress=on_progress)
        if got is None:
            return None
        data, seconds, used_seed, kudos = got
    else:
        data, seconds, used_seed = generate_ollama(model, prompt, size, steps, seed, negative)
        kudos = 0.0
    width, height = normalize_size(size)
    return {
        "data": data,
        "provider": key,
        "model": str(model or ""),
        "prompt": str(prompt or ""),
        "width": width,
        "height": height,
        "steps": normalize_steps(steps),
        "seconds": round(seconds, 1),
        "seed": used_seed,
        "kudos": kudos,
    }


# ==========================================================
#  Nom de fichier et écriture
# ==========================================================

def extension_for(data):
    if data.startswith(PNG_MAGIC):
        return ".png"
    if data.startswith(JPEG_MAGIC):
        return ".jpg"
    if data.startswith(WEBP_MAGIC):
        return ".webp"
    return ".png"


def suggest_name(prompt, limit=40):
    """« Une cuisine au petit matin… » → « une-cuisine-au-petit-matin »."""
    text = unicodedata.normalize("NFKD", str(prompt or ""))
    text = text.encode("ascii", "ignore").decode("ascii").casefold()
    words = re.findall(r"[a-z0-9]+", text)
    stop = {"une", "un", "le", "la", "les", "de", "des", "du", "au", "aux", "et", "en",
            "avec", "sur", "dans", "pour", "a", "par", "que", "qui", "se", "sa", "son",
            "the", "of", "with", "an", "image"}
    kept = [word for word in words if word not in stop] or words
    slug = "-".join(kept)
    slug = slug[:limit].strip("-")
    return slug or "image"


def unique_path(folder, base, extension=".png"):
    """`dossier/base.png`, puis `base-2.png`, `base-3.png`… si déjà pris."""
    root = Path(folder)
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(base or "image")).strip("-") or "image"
    candidate = root / f"{stem}{extension}"
    index = 2
    while candidate.exists():
        candidate = root / f"{stem}-{index}{extension}"
        index += 1
    return candidate


def save_image(data, folder, prompt="", extension=None):
    """Écrit l'image dans le dossier et renvoie le chemin retenu."""
    if not data:
        raise RuntimeError("Aucune image à écrire.")
    root = Path(folder)
    if not root.is_dir():
        raise RuntimeError(f"Dossier introuvable : {root}")
    suffix = extension or extension_for(data)
    path = unique_path(root, suggest_name(prompt), suffix)
    path.write_bytes(data)
    return path
