"""Instantanés de fichiers avant écriture, avec restauration.

Chaque écriture de Lumio dépose la version précédente dans
``%APPDATA%\\Lumio\\snapshots`` (hors du dossier de travail, donc invisible pour
le modèle). On peut ensuite lister les versions d'un fichier et en restaurer une.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

import lumio_session

SNAPSHOT_DIRNAME = "snapshots"
MAX_PER_FILE = 20


def root_dir() -> Path:
    return lumio_session.storage_dir() / SNAPSHOT_DIRNAME


def _folder_key(folder) -> str:
    import hashlib

    text = str(Path(folder).resolve()).casefold()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _index_path() -> Path:
    return root_dir() / "index.jsonl"


def snapshot(folder, relative: str, content, reason: str = "write") -> dict | None:
    """Enregistre la version courante d'un fichier. Retourne l'entrée créée."""
    text = string_or_none(content)
    folder = Path(folder)
    relative = Path(str(relative)).as_posix()
    if text is None:
        return None

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    name = Path(relative).name
    target_dir = root_dir() / _folder_key(folder) / Path(relative).parent.as_posix()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{name}.{stamp}.bak"
        target.write_text(text, encoding="utf-8")
    except OSError:
        return None

    entry = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "folder": str(folder),
        "relative": relative,
        "file": str(target),
        "size": len(text.encode("utf-8")),
        "reason": str(reason or "write"),
    }
    try:
        with open(_index_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return None
    prune(folder, relative)
    return entry


def string_or_none(content):
    if content is None:
        return None
    return content if isinstance(content, str) else str(content)


def entries(folder=None, relative=None, limit=200) -> list:
    """Entrées du journal, les plus récentes d'abord."""
    path = _index_path()
    if not path.is_file():
        return []
    wanted_folder = str(Path(folder).resolve()).casefold() if folder else None
    wanted_rel = Path(str(relative)).as_posix().casefold() if relative else None
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict):
                continue
            if wanted_folder and str(item.get("folder") or "").casefold() != wanted_folder:
                continue
            if wanted_rel and str(item.get("relative") or "").casefold() != wanted_rel:
                continue
            if not Path(str(item.get("file") or "")).is_file():
                continue
            rows.append(item)
    except OSError:
        return []
    rows.reverse()
    return rows[:limit]


def files_with_history(folder, limit=40) -> list:
    """Fichiers du dossier ayant au moins une version sauvegardée."""
    seen = {}
    for entry in entries(folder=folder, limit=2000):
        key = entry["relative"]
        slot = seen.setdefault(key, {"relative": key, "count": 0, "last": entry.get("at")})
        slot["count"] += 1
    return sorted(seen.values(), key=lambda item: item["last"] or "", reverse=True)[:limit]


def restore(entry: dict, folder=None) -> Path:
    """Réécrit la version sauvegardée à sa place dans le dossier."""
    target_root = Path(folder or entry.get("folder") or "").resolve()
    relative = Path(str(entry.get("relative") or ""))
    source = Path(str(entry.get("file") or ""))
    if not source.is_file():
        raise FileNotFoundError(source)
    destination = (target_root / relative).resolve()
    destination.relative_to(target_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def prune(folder, relative, keep=MAX_PER_FILE) -> None:
    """Ne garde que les `keep` versions les plus récentes d'un fichier."""
    rows = entries(folder=folder, relative=relative, limit=10_000)
    for old in rows[keep:]:
        try:
            Path(str(old.get("file"))).unlink()
        except OSError:
            pass
