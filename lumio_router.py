"""Routage hybride : choisir entre le modèle local et un modèle en ligne.

Heuristique assumée, pas magique : une question courte sur un dossier simple
part en local (gratuit, privé) ; une demande d'architecture, de refonte ou
multi-fichiers part en ligne — sauf si le plafond de coût l'interdit.
"""

import re

HARD_MARKERS = (
    "architecture", "refonte", "refactor", "migration", "plan de", "planifie",
    "conception", "conçois", "diagramme", "compare", "audit", "revue de code",
    "optimise", "performance", "sécurité", "securite", "toute la base",
    "plusieurs fichiers", "de bout en bout", "explique en profondeur",
    "pourquoi", "stratégie", "strategie", "tests unitaires", "documente",
)

SIMPLE_MARKERS = (
    "renomme", "corrige la faute", "ajoute un commentaire", "quelle heure",
    "combien", "traduis", "résume", "resume", "formate", "indente", "liste",
)

POLICIES = ("auto", "local", "remote", "remote_si_besoin")
POLICY_LABELS = {
    "auto": "Automatique (recommandé)",
    "local": "Toujours en local",
    "remote": "Toujours en ligne",
    "remote_si_besoin": "Local sauf si c'est complexe",
}


def complexity(question: str, files_count: int = 0, context_chars: int = 0) -> int:
    """Score 0-10 : plus il est haut, plus la tâche mérite un modèle puissant."""
    text = str(question or "").casefold()
    score = 0
    if len(text) > 400:
        score += 2
    elif len(text) > 200:
        score += 1
    for marker in HARD_MARKERS:
        if marker in text:
            score += 4
            break
    for marker in SIMPLE_MARKERS:
        if marker in text:
            score -= 2
            break
    if files_count >= 8:
        score += 2
    elif files_count >= 3:
        score += 1
    if context_chars > 60_000:
        score += 2
    elif context_chars > 20_000:
        score += 1
    if re.search(r"\b(et|puis)\b.{0,40}\b(et|puis)\b", text):
        score += 1
    return max(0, min(10, score))


def decide(policy: str, question: str, files_count: int = 0, context_chars: int = 0,
           local_ready: bool = True, remote_ready: bool = True,
           estimated_cost: float | None = None, ceiling: float | None = None) -> dict:
    """Décide du moteur ; retourne {provider, reason, score}."""
    score = complexity(question, files_count, context_chars)
    policy = str(policy or "auto").strip().casefold()
    if policy not in POLICIES:
        policy = "auto"

    if policy == "local":
        if local_ready:
            return {"provider": "local", "reason": "politique : toujours en local", "score": score}
        return {"provider": "remote", "reason": "local indisponible", "score": score}

    if policy == "remote":
        if remote_ready:
            if ceiling and estimated_cost is not None and estimated_cost > ceiling:
                return {"provider": "local", "reason": "plafond de coût atteint", "score": score}
            return {"provider": "remote", "reason": "politique : toujours en ligne", "score": score}
        return {"provider": "local", "reason": "hors ligne indisponible", "score": score}

    wants_remote = score >= 4 if policy == "auto" else score >= 3
    if wants_remote and remote_ready:
        if ceiling and estimated_cost is not None and estimated_cost > ceiling:
            return {
                "provider": "local",
                "reason": f"tâche complexe ({score}/10) mais coût estimé au-dessus du plafond",
                "score": score,
            }
        return {"provider": "remote", "reason": f"tâche complexe ({score}/10)", "score": score}
    if local_ready:
        reason = f"tâche simple ({score}/10)" if not wants_remote else "local préféré"
        return {"provider": "local", "reason": reason, "score": score}
    return {"provider": "remote", "reason": "local indisponible", "score": score}


def estimate_cost(prompt_chars: int, price_per_mtok: float, max_tokens: int = 800) -> float:
    """Estimation grossière : ~4 caractères par token en entrée."""
    tokens = max(0, int(prompt_chars) // 4) + max(0, int(max_tokens))
    return tokens * float(price_per_mtok or 0) / 1_000_000
