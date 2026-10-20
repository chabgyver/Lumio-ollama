"""Classement des modèles du catalogue : puissance, prix, contexte, fraîcheur.

Aucune API publique ne fournit de benchmark. La « puissance » est donc une
**estimation** : taille en paramètres quand le nom la donne (27b, 8x22b, 1.5b),
sinon le prix d'entrée comme proxy (les fournisseurs facturent la capacité).
C'est assumé, documenté, et l'interface ne l'affiche pas comme une vérité.
"""

import math
import re

CRITERIA = ("recommande", "puissance", "prix", "contexte", "recent", "nom")
LABELS = {
    "recommande": "Recommandé (rapport puissance / prix)",
    "puissance": "Puissance estimée",
    "prix": "Prix croissant",
    "contexte": "Contexte le plus large",
    "recent": "Plus récents",
    "nom": "Nom (A → Z)",
}
LABEL_TO_KEY = {label: key for key, label in LABELS.items()}

_MOE = re.compile(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)
_BILLIONS = re.compile(r"(\d+(?:\.\d+)?)\s*b(?![a-z0-9])", re.IGNORECASE)
_MILLIONS = re.compile(r"(\d+(?:\.\d+)?)\s*m(?![a-z0-9])", re.IGNORECASE)


def parse_params(model_id: str):
    """Nombre de paramètres estimé d'après le nom (None si inconnu)."""
    text = str(model_id or "")
    match = _MOE.search(text)
    if match:
        expertes, taille = float(match.group(1)), float(match.group(2))
        return int(expertes * taille * 1e9)
    match = _BILLIONS.search(text)
    if match:
        return int(float(match.group(1)) * 1e9)
    match = _MILLIONS.search(text)
    if match:
        return int(float(match.group(1)) * 1e6)
    return None


def size_label(model_id: str) -> str:
    """Étiquette lisible : 27B, 8×22B, 1.5B, ou vide."""
    text = str(model_id or "")
    match = _MOE.search(text)
    if match:
        return f"{_short(float(match.group(1)))}×{_short(float(match.group(2)))}B"
    match = _BILLIONS.search(text)
    if match:
        return f"{_short(float(match.group(1)))}B"
    match = _MILLIONS.search(text)
    if match:
        return f"{_short(float(match.group(1)))}M"
    return ""


def _short(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def power(row: dict) -> float:
    """Estimation 0-100 de la capacité du modèle (voir l'avertissement du module)."""
    params = parse_params(row.get("id"))
    if params:
        score = 20.0 * math.log10(max(params, 1e8) / 1e8)
        return max(5.0, min(100.0, score))
    price = float(row.get("prompt") or 0)
    if price > 0:
        score = 40.0 + 18.0 * math.log10(price / 0.01) if price > 0 else 40.0
        return max(10.0, min(90.0, score))
    return 30.0  # inconnu : neutre


def value(row: dict) -> float:
    """Rapport puissance / prix : le gratuit est favorisé, sans écraser le reste."""
    score = power(row)
    if row.get("free"):
        return score + 25.0
    price = float(row.get("prompt") or 0)
    if price <= 0:
        return score
    penalty = 18.0 * math.log10(max(price, 1e-4) / 0.01)
    return score - penalty


def _price_key(row: dict):
    if row.get("free"):
        return 0.0
    price = row.get("prompt")
    return float("inf") if price is None else float(price)


def sort_rows(rows, criterion: str = "recommande") -> list:
    """Trie sans modifier l'entrée ; les valeurs inconnues passent à la fin."""
    items = list(rows or [])
    key = str(criterion or "recommande").strip().casefold()
    if key not in CRITERIA:
        key = "recommande"

    if key == "prix":
        return sorted(items, key=lambda row: (_price_key(row), row.get("id") or ""))
    if key == "contexte":
        return sorted(items, key=lambda row: (-int(row.get("context") or 0), row.get("id") or ""))
    if key == "recent":
        return sorted(items, key=lambda row: (-int(row.get("created") or 0), row.get("id") or ""))
    if key == "nom":
        return sorted(items, key=lambda row: (row.get("id") or "").casefold())
    if key == "puissance":
        return sorted(items, key=lambda row: (-power(row), _price_key(row), row.get("id") or ""))
    return sorted(items, key=lambda row: (-value(row), -power(row), row.get("id") or ""))


def explain(criterion: str) -> str:
    """Phrase affichée sous le sélecteur de tri."""
    key = str(criterion or "recommande")
    if key == "puissance":
        return "Puissance estimée d'après la taille en paramètres (ou le prix quand elle manque)."
    if key == "prix":
        return "Prix d'entrée par million de tokens, gratuit d'abord."
    if key == "contexte":
        return "Fenêtre de contexte la plus large d'abord."
    if key == "recent":
        return "Modèles ajoutés le plus récemment au catalogue."
    if key == "nom":
        return "Ordre alphabétique."
    return "Mélange de puissance estimée et de prix, gratuit favorisé."
