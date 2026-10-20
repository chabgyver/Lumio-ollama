"""Recherche plein texte dans toutes les conversations Lumio.

Fonctions pures (aucune dépendance à Tkinter) pour rester testables : le
dialogue de recherche de l'interface ne fait qu'afficher le résultat.
"""

import re
import unicodedata
from dataclasses import dataclass


@dataclass
class Hit:
    """Une occurrence trouvée dans une conversation."""

    chat_id: str
    title: str
    role: str
    snippet: str
    score: int
    position: int

    def label(self) -> str:
        who = "Vous" if self.role == "user" else "Lumio"
        return f"{self.title} — {who}"


def normalize(text: str) -> str:
    """Minuscules sans accents, pour une recherche tolérante (modèle/modèle)."""
    low = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(char for char in low if not unicodedata.combining(char))


def snippet(text: str, needle: str, width: int = 90) -> str:
    """Extrait autour de la première occurrence, sur une seule ligne."""
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    if not flat:
        return ""
    low_flat, low_needle = normalize(flat), normalize(needle)
    index = low_flat.find(low_needle) if low_needle else -1
    if index < 0:
        return flat[:width] + ("…" if len(flat) > width else "")
    start = max(0, index - width // 3)
    end = min(len(flat), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return f"{prefix}{flat[start:end]}{suffix}"


def _entries(chat: dict):
    """(role, texte, position) pour chaque message d'une conversation."""
    transcript = chat.get("transcript")
    if isinstance(transcript, list):
        for position, item in enumerate(transcript):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            body = str(item.get("content") or "").strip()
            if body:
                yield role, body, position


def search_chats(chats, needle: str, limit: int = 60):
    """Cherche `needle` dans le titre et les messages de chaque conversation.

    Retourne une liste de Hit triée : les correspondances de titre d'abord, puis
    les messages de l'utilisateur, puis ceux de Lumio ; à score égal, l'ordre
    chronologique inverse (les plus récents d'abord).
    """
    query = normalize(needle).strip()
    if not query:
        return []

    hits = []
    for chat in chats or []:
        if not isinstance(chat, dict):
            continue
        chat_id = str(chat.get("id") or "")
        title = str(chat.get("title") or "").strip()
        label = title or "Sans titre"

        occurrences = normalize(title).count(query) if title else 0
        if occurrences:
            hits.append(Hit(chat_id, label, "title", label, 300 + occurrences, -1))

        for role, body, position in _entries(chat):
            count = normalize(body).count(query)
            if not count:
                continue
            score = (200 if role == "user" else 100) + count
            hits.append(Hit(chat_id, label, role, snippet(body, needle), score, position))

    hits.sort(key=lambda hit: (-hit.score, -hit.position))
    return hits[:limit]
