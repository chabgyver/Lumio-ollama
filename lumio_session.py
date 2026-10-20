"""Stockage de la session Lumio : local, atomique, secrets chiffrés.

Objectifs :

* la session ne vit plus dans le dossier applicatif (qui est un partage web) :
  elle est écrite dans ``%APPDATA%\\Lumio``, propre à chaque utilisateur ;
* les secrets (clé API du fournisseur, clés des catalogues) sont chiffrés avec
  le DPAPI de Windows : déchiffrables seulement par cette session Windows ;
* l'écriture est atomique (fichier temporaire puis remplacement) et une copie
  ``.bak`` de l'état précédent est conservée ;
* un fichier hérité (ancien ``session.json`` du partage) est migré puis
  déplacé hors du partage, sans perte.
"""

import base64
import ctypes
import json
import os
import shutil
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SECRET_PREFIX = "dpapi:"
PLAIN_PREFIX = "plain:"
SECRET_FIELDS = ("online_key",)
SECRET_MAPS = ("catalog_keys",)


# ==========================================================
#  Chiffrement DPAPI (aucune dépendance externe)
# ==========================================================
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob_from(data: bytes):
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    source, _keep = _blob_from(data)
    result = _DATA_BLOB()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if not function(ctypes.byref(source), None, None, None, None, flags, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def encrypt_text(value: str) -> str:
    """Chiffre un secret pour ce compte Windows. Chaîne vide -> chaîne vide."""
    text = str(value or "")
    if not text or text.startswith((SECRET_PREFIX, PLAIN_PREFIX)):
        return text
    try:
        blob = _dpapi(text.encode("utf-8"), protect=True)
        return SECRET_PREFIX + base64.b64encode(blob).decode("ascii")
    except Exception:
        # DPAPI indisponible : on masque au moins le secret en clair.
        return PLAIN_PREFIX + base64.b64encode(text.encode("utf-8")).decode("ascii")


def decrypt_text(value: str) -> str:
    """Déchiffre un secret ; accepte les anciennes valeurs en clair."""
    text = str(value or "")
    if not text:
        return ""
    if text.startswith(SECRET_PREFIX):
        try:
            blob = base64.b64decode(text[len(SECRET_PREFIX):])
            return _dpapi(blob, protect=False).decode("utf-8")
        except Exception:
            return ""
    if text.startswith(PLAIN_PREFIX):
        try:
            return base64.b64decode(text[len(PLAIN_PREFIX):]).decode("utf-8")
        except Exception:
            return ""
    return text


def secure(data: dict) -> dict:
    """Copie de la session avec les secrets chiffrés (avant écriture)."""
    result = dict(data or {})
    for field in SECRET_FIELDS:
        if result.get(field):
            result[field] = encrypt_text(result[field])
    for field in SECRET_MAPS:
        mapping = result.get(field)
        if isinstance(mapping, dict):
            result[field] = {key: encrypt_text(value) for key, value in mapping.items()}
    return result


def unsecure(data: dict) -> dict:
    """Copie de la session avec les secrets déchiffrés (après lecture)."""
    result = dict(data or {})
    for field in SECRET_FIELDS:
        if result.get(field):
            result[field] = decrypt_text(result[field])
    for field in SECRET_MAPS:
        mapping = result.get(field)
        if isinstance(mapping, dict):
            result[field] = {key: decrypt_text(value) for key, value in mapping.items()}
    return result


# ==========================================================
#  Emplacements et lecture/écriture
# ==========================================================
def storage_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "Lumio"


def session_path() -> Path:
    return storage_dir() / "session.json"


def legacy_path() -> Path:
    return APP_DIR / "session.json"


def load(path=None) -> dict:
    """Lit la session (fichier principal, puis .bak en secours)."""
    target = Path(path) if path else session_path()
    for candidate in (target, target.with_suffix(".bak.json")):
        try:
            with open(candidate, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return unsecure(data)
    return {}


def save(data: dict, path=None) -> Path:
    """Écrit la session de façon atomique (et garde une copie .bak)."""
    target = Path(path) if path else session_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(secure(data), ensure_ascii=False, indent=2)
    backup = target.with_suffix(".bak.json")
    temporary = target.with_suffix(target.suffix + ".tmp")
    if target.exists():
        try:
            shutil.copyfile(target, backup)
        except OSError:
            pass
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, target)
    return target


def migrate(legacy=None, path=None) -> bool:
    """Rapatrie l'ancienne session du partage vers %APPDATA%.

    L'ancien fichier (et son .bak) sont déplacés hors du partage, pas
    supprimés : ils restent consultables dans ``%APPDATA%\\Lumio\\legacy``.
    Retourne True si une migration a eu lieu.
    """
    source = Path(legacy) if legacy else legacy_path()
    target = Path(path) if path else session_path()
    if target.exists() or not source.is_file():
        return False

    data = load(source)
    if not data:
        return False

    save(data, target)

    archive = storage_dir() / "legacy"
    archive.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for item in (source, source.with_suffix(".bak.json")):
        if not item.is_file():
            continue
        destination = archive / f"{item.stem}-{stamp}{item.suffix}"
        try:
            shutil.move(str(item), str(destination))
        except OSError:
            try:
                shutil.copyfile(item, destination)
                item.unlink()
            except OSError:
                pass
    return True
