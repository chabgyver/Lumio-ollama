"""Coût et tokens des échanges Lumio.

Les tarifs viennent du catalogue en ligne (dollars par million de tokens). Pour
un modèle local (Ollama), le coût est nul : seuls les tokens sont comptés.

Le journal ``%APPDATA%\\Lumio\\usage.jsonl`` garde une ligne par échange, ce qui
permet les totaux du jour, sur 7 jours, et par modèle, sans dépendance.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import lumio_session

LEDGER_NAME = "usage.jsonl"


def ledger_path() -> Path:
    return lumio_session.storage_dir() / LEDGER_NAME


def cost_usd(prompt_tokens, completion_tokens, prompt_price, completion_price) -> float:
    """Coût en dollars ; les tarifs sont exprimés par million de tokens."""
    try:
        prompt = int(prompt_tokens or 0)
        completion = int(completion_tokens or 0)
        return (
            prompt * float(prompt_price or 0) + completion * float(completion_price or 0)
        ) / 1_000_000
    except (TypeError, ValueError):
        return 0.0


def format_tokens(value) -> str:
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return "0 tok"
    return f"{value} tok"


def format_cost(value, local: bool = False) -> str:
    if local and not value:
        return "gratuit (local)"
    if value is None:
        return "coût inconnu"
    if value <= 0:
        return "0 $"
    if value < 0.01:
        return f"{value:.4f} $"
    return f"{value:.3f} $"


def make_entry(model, provider, usage, price=None, when=None) -> dict:
    """Ligne de journal pour un échange."""
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    local = str(provider or "").strip().casefold() in {"local", "ollama", "pgpt"}
    cost = 0.0 if local else cost_usd(prompt, completion, (price or {}).get("prompt"),
                                      (price or {}).get("completion"))
    return {
        "at": (when or datetime.now()).isoformat(timespec="seconds"),
        "model": str(model or ""),
        "provider": str(provider or "local"),
        "prompt": prompt,
        "completion": completion,
        "tokens": prompt + completion,
        "cost": round(cost, 6),
        "priced": bool(price) or local,
    }


def record(entry: dict, path=None) -> Path:
    """Ajoute un échange au journal (une ligne JSON par échange)."""
    target = Path(path) if path else ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return target


def load_entries(path=None, days=None) -> list:
    """Lit le journal ; `days` limite aux N derniers jours."""
    target = Path(path) if path else ledger_path()
    if not target.is_file():
        return []
    limit = None
    if days:
        limit = date.today() - timedelta(days=int(days) - 1)
    entries = []
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict):
                continue
            if limit is not None:
                try:
                    day = datetime.fromisoformat(str(item.get("at"))).date()
                except ValueError:
                    continue
                if day < limit:
                    continue
            entries.append(item)
    except OSError:
        return []
    return entries


def aggregate(entries) -> dict:
    """Totaux globaux, par jour et par modèle."""
    total = {"tokens": 0, "cost": 0.0, "calls": 0}
    by_day, by_model = {}, {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        tokens = int(entry.get("tokens") or 0)
        cost = float(entry.get("cost") or 0.0)
        total["tokens"] += tokens
        total["cost"] += cost
        total["calls"] += 1

        day = str(entry.get("at") or "")[:10] or "?"
        slot = by_day.setdefault(day, {"tokens": 0, "cost": 0.0, "calls": 0})
        slot["tokens"] += tokens
        slot["cost"] += cost
        slot["calls"] += 1

        model = str(entry.get("model") or "?")
        slot = by_model.setdefault(model, {"tokens": 0, "cost": 0.0, "calls": 0})
        slot["tokens"] += tokens
        slot["cost"] += cost
        slot["calls"] += 1

    total["cost"] = round(total["cost"], 6)
    for slot in by_day.values():
        slot["cost"] = round(slot["cost"], 6)
    for slot in by_model.values():
        slot["cost"] = round(slot["cost"], 6)
    return {"total": total, "by_day": by_day, "by_model": by_model}


def today(entries=None) -> dict:
    """Totaux de la journée en cours."""
    entries = entries if entries is not None else load_entries(days=1)
    key = date.today().isoformat()
    return aggregate(entries)["by_day"].get(key, {"tokens": 0, "cost": 0.0, "calls": 0})


def summary_lines(entries=None, days: int = 7, limit: int = 8):
    """Lignes lisibles pour le panneau de statistiques."""
    entries = entries if entries is not None else load_entries(days=days)
    data = aggregate(entries)
    lines = [
        f"Aujourd'hui : {format_tokens(today(entries)['tokens'])} · {format_cost(today(entries)['cost'])}",
        f"{days} jours    : {format_tokens(data['total']['tokens'])} · "
        f"{format_cost(data['total']['cost'])} · {data['total']['calls']} échange(s)",
    ]
    models = sorted(data["by_model"].items(), key=lambda item: -item[1]["tokens"])[:limit]
    if models:
        lines.append("")
        lines.append("Par modèle :")
        for name, slot in models:
            lines.append(
                f"  {name} — {format_tokens(slot['tokens'])} · {format_cost(slot['cost'])}"
                f" · {slot['calls']} échange(s)"
            )
    return lines

def over_budget(spent: float, ceiling: float | None) -> bool:
    """Vrai si le plafond est dépassé (plafond nul ou absent = illimité)."""
    try:
        limit = float(ceiling or 0)
        total = float(spent or 0)
    except (TypeError, ValueError):
        return False
    return limit > 0 and total >= limit


def remaining(spent: float, ceiling: float | None) -> float | None:
    """Reste à dépenser avant le plafond (None si illimité)."""
    try:
        limit = float(ceiling or 0)
        total = float(spent or 0)
    except (TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    return max(0.0, limit - total)