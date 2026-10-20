"""Arborescence du dossier : construction, comptage et aplatissement.

Fonctions pures (aucune dépendance à Tkinter) pour rester testables :
l'interface ne fait qu'afficher le résultat de `flatten()`.
"""

from pathlib import Path

DIR = "dir"
FILE = "file"


def build_tree(relatives) -> dict:
    """Arborescence {dirs: {nom: noeud}, files: [Path]} à partir des chemins."""
    root = {"dirs": {}, "files": []}
    for relative in relatives or []:
        path = relative if isinstance(relative, Path) else Path(str(relative))
        node = root
        for part in path.parts[:-1]:
            node = node["dirs"].setdefault(part, {"dirs": {}, "files": []})
        node["files"].append(path)
    return root


def count_files(node) -> int:
    """Nombre de fichiers sous un nœud (récursif)."""
    return len(node.get("files") or []) + sum(
        count_files(child) for child in (node.get("dirs") or {}).values()
    )


def count_dirs(node) -> int:
    """Nombre de sous-dossiers sous un nœud (récursif)."""
    return len(node.get("dirs") or {}) + sum(
        count_dirs(child) for child in (node.get("dirs") or {}).values()
    )


def dir_keys(relatives) -> set:
    """Tous les chemins de dossiers contenant au moins un fichier affiché."""
    keys = set()
    for relative in relatives or []:
        path = relative if isinstance(relative, Path) else Path(str(relative))
        walked = Path()
        for part in path.parts[:-1]:
            walked = walked / part
            keys.add(walked.as_posix())
    return keys


def flatten(tree, collapsed=(), query: str = "") -> list:
    """Liste ordonnée pour l'affichage.

    Chaque élément : ("dir", nom, clé, profondeur, nombre_de_fichiers) ou
    ("file", chemin, clé, profondeur, 0). Les dossiers passent avant les
    fichiers, à chaque niveau. Une recherche active ignore les repliages.
    """
    folded = set() if str(query or "").strip() else {
        str(item).casefold() for item in (collapsed or [])
    }
    rows = []

    def walk(node, base: Path, depth: int):
        dirs = node.get("dirs") or {}
        for name in sorted(dirs, key=str.casefold):
            child = dirs[name]
            key = (base / name).as_posix()
            rows.append((DIR, name, key, depth, count_files(child)))
            if key.casefold() not in folded:
                walk(child, base / name, depth + 1)
        for path in sorted(node.get("files") or [], key=lambda item: item.name.casefold()):
            rows.append((FILE, path, path.as_posix(), depth, 0))

    walk(tree or {"dirs": {}, "files": []}, Path(), 0)
    return rows
