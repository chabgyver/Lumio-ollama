"""Outils que le modèle peut demander lui-même, au moyen de blocs délimités.

Tous les modèles locaux ne savent pas appeler des « tools » au sens strict ;
en revanche, ils savent écrire un bloc de code. On leur apprend donc deux
blocs, exécutés par Lumio puis réinjectés dans la conversation :

    ```rechercher: motif```   → liste des fichiers du dossier qui contiennent motif
    ```lire: chemin/relatif``` → contenu du fichier (borné)

C'est ce qui permet au modèle d'aller chercher l'information au lieu de
dépendre des fichiers que l'utilisateur a cochés.
"""

import re
from pathlib import Path

import ollama_fs_access

MAX_READ_CHARS = 20_000
MAX_SEARCH_RESULTS = 25

CALL_PATTERN = re.compile(
    r"```[ \t]*(rechercher|recherche|search|lire|read)[ \t]*:[ \t]*([^\n`]+?)[ \t]*\n?```",
    re.IGNORECASE,
)

KIND_BY_KEYWORD = {
    "rechercher": "search",
    "recherche": "search",
    "search": "search",
    "lire": "read",
    "read": "read",
}


def parse_calls(text: str) -> list:
    """Liste des appels demandés par une réponse, dans l'ordre d'apparition."""
    calls, seen = [], set()
    for match in CALL_PATTERN.finditer(str(text or "")):
        kind = KIND_BY_KEYWORD.get(match.group(1).casefold(), "")
        argument = match.group(2).strip().strip("\"'")
        if not kind or not argument:
            continue
        key = (kind, argument.casefold())
        if key in seen:
            continue
        seen.add(key)
        calls.append({"kind": kind, "arg": argument})
    return calls


def execute(call: dict, folder) -> dict:
    """Exécute un appel ; retourne {kind, arg, ok, title, output}."""
    kind = str((call or {}).get("kind") or "")
    argument = str((call or {}).get("arg") or "").strip()
    title = f"{'Recherche' if kind == 'search' else 'Lecture'} : {argument}"
    try:
        if kind == "search":
            found = ollama_fs_access.search_files(folder, argument, max_files=MAX_SEARCH_RESULTS)
            if not found:
                output = f"Aucun fichier ne contient « {argument} »."
            else:
                output = f"{len(found)} fichier(s) :\n" + "\n".join(
                    f"- {Path(item).as_posix()}" for item in found[:20]
                )
            return {"kind": kind, "arg": argument, "ok": True, "title": title, "output": output}

        if kind == "read":
            target = Path(argument)
            if target.is_absolute() or ".." in target.parts:
                return {
                    "kind": kind, "arg": argument, "ok": False,
                    "title": title, "output": "Chemin refusé : il doit rester dans le dossier.",
                }
            content = ollama_fs_access.read_file(folder, argument)
            if len(content) > MAX_READ_CHARS:
                content = content[:MAX_READ_CHARS] + f"\n… (tronqué à {MAX_READ_CHARS} caractères)"
            return {
                "kind": kind, "arg": argument, "ok": True, "title": title,
                "output": f"Contenu de {argument} :\n{content}",
            }
    except FileNotFoundError:
        return {"kind": kind, "arg": argument, "ok": False, "title": title,
                "output": f"Fichier introuvable : {argument}"}
    except Exception as error:  # pragma: no cover - dépend du dossier
        return {"kind": kind, "arg": argument, "ok": False, "title": title,
                "output": f"Échec : {error}"}
    return {"kind": kind, "arg": argument, "ok": False, "title": title, "output": "Appel inconnu."}


def execute_all(calls, folder) -> list:
    return [execute(call, folder) for call in (calls or [])]


def format_for_model(results) -> str:
    """Bloc de contexte renvoyé au modèle après exécution des outils."""
    parts = ["Résultats des outils demandés :"]
    for item in results or []:
        parts.append("")
        parts.append(f"[{item.get('title')}]")
        parts.append(str(item.get("output") or "").strip())
    return "\n".join(parts)


def guide() -> str:
    """Texte à ajouter au prompt pour apprendre les deux blocs au modèle."""
    return (
        "Tu peux demander à consulter le dossier avant de répondre, en écrivant "
        "un bloc de code dont la première ligne est une commande :\n"
        "```rechercher: motif``` pour lister les fichiers qui contiennent ce motif ;\n"
        "```lire: chemin/relatif.ext``` pour lire un fichier précis.\n"
        "Ces blocs sont exécutés par l'application et le résultat t'est renvoyé. "
        "N'en demande qu'un ou deux à la fois, et ne les utilise que si "
        "l'information manque vraiment."
    )

def updates_size(updates) -> int:
    """Taille totale (octets) des contenus proposés."""
    total = 0
    for item in updates or []:
        try:
            total += len(str(item.get("content") or "").encode("utf-8"))
        except AttributeError:
            continue
    return total


def too_big(updates, max_files: int = 0, max_bytes: int = 0) -> str:
    """Raison pour laquelle une application automatique est refusée ('' si permis)."""
    items = list(updates or [])
    if max_files and len(items) > int(max_files):
        return f"{len(items)} fichiers (limite {int(max_files)})"
    size = updates_size(items)
    if max_bytes and size > int(max_bytes):
        return f"{size} octets (limite {int(max_bytes)})"
    return ""