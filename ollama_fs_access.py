#!/usr/bin/env python3
import difflib
import importlib.metadata
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


OLLAMA_URL = "http://127.0.0.1:11434"
PGPT_URL = "http://127.0.0.1:8080"
PROVIDERS = ("ollama", "pgpt", "online")
PROVIDER_LABELS = {
    "ollama": "Ollama",
    "pgpt": "PrivateGPT",
    "online": "En ligne",
}
ONLINE_PRESETS = (
    ("OpenRouter", "https://openrouter.ai/api/v1"),
    ("DeepSeek", "https://api.deepseek.com/v1"),
    ("Groq", "https://api.groq.com/openai/v1"),
    ("Together", "https://api.together.xyz/v1"),
    ("Personnalisé", ""),
)
_ONLINE = {
    "url": "https://openrouter.ai/api/v1",
    "key": "",
    "preset": "OpenRouter",
}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".mdx", ".rst", ".py", ".pyw", ".json", ".jsonc", ".csv",
    ".log", ".ini", ".cfg", ".conf", ".config",
    ".xml", ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts",
    ".bat", ".cmd", ".ps1", ".sh", ".bash", ".zsh",
    ".java", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".php", ".rb", ".erb",
    ".go", ".rs", ".sql", ".yml", ".yaml", ".toml",
    ".vue", ".svelte", ".astro",
    ".kt", ".kts", ".swift", ".r", ".lua", ".dart", ".gradle",
    ".properties", ".env", ".gitignore", ".dockerignore", ".editorconfig",
    ".npmrc", ".nvmrc", ".prettierrc", ".eslintrc", ".babelrc",
    ".prisma", ".graphql", ".gql", ".proto", ".tf",
    ".svg", ".plist", ".lock", ".ipynb", ".tex",
}
MAX_FILE_BYTES = 64 * 1024
MAX_FILES = 200
CONTEXT_CHAR_LIMIT = 64_000
HISTORY_MESSAGE_CHARS = 2_500
MAX_SMART_BODIES = 8
SMALL_FILE_POOL = 4
INVENTORY_MAX = 80
SKIP_DIR_NAMES = {
    ".git", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "venv", ".venv", "node_modules", ".idea", ".tox", "dist", "build",
}
_FILE_LIST_CACHE = {}


def _as_path(folder_path):
    path = Path(folder_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def root_folder(folder_path):
    path = Path(folder_path)
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise NotADirectoryError(f"Pas un dossier : {path}")
    return path.resolve()


def parse_extensions(raw):
    """Transforme « .txt, .py » en ensemble de suffixes. Vide = extensions texte connues."""
    if raw is None or not str(raw).strip():
        return set(TEXT_EXTENSIONS)
    found = set()
    for part in str(raw).replace(";", ",").split(","):
        suffix = part.strip().lower()
        if not suffix:
            continue
        if not suffix.startswith("."):
            suffix = "." + suffix
        found.add(suffix)
    return found or set(TEXT_EXTENSIONS)


def resolve_inside(folder_path, relative):
    root = root_folder(folder_path)
    candidate = Path(relative)
    target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    target.relative_to(root)
    return root, target


def file_stats(folder_path, relative):
    """Taille et date de modification d'un fichier du dossier."""
    _, target = resolve_inside(folder_path, relative)
    if not target.is_file():
        raise FileNotFoundError(target)
    info = target.stat()
    return {"size": info.st_size, "mtime": info.st_mtime}


def _skip_dir(name):
    low = str(name or "").casefold()
    return low in SKIP_DIR_NAMES or low.endswith(".egg-info")


def iter_text_files(root, allowed, limit):
    found = []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if not _skip_dir(name))
        for name in sorted(filenames):
            entry = Path(dirpath) / name
            if not entry.is_file() or entry.suffix.lower() not in allowed:
                continue
            found.append(entry.relative_to(root))
            if len(found) >= limit:
                return found
    return found


def invalidate_file_cache():
    _FILE_LIST_CACHE.clear()


def list_files(folder_path, extensions=None, max_files=MAX_FILES):
    """Liste les fichiers texte du dossier, sous-dossiers compris."""
    root = root_folder(folder_path)
    allowed = set(extensions) if extensions is not None else set(TEXT_EXTENSIONS)
    limit = max(1, int(max_files))
    key = (str(root), tuple(sorted(allowed)), limit)
    now = time.monotonic()
    hit = _FILE_LIST_CACHE.get(key)
    if hit and now - hit[0] < 5:
        return list(hit[1])
    found = iter_text_files(root, allowed, limit)
    _FILE_LIST_CACHE[key] = (now, found)
    return list(found)


def read_file(folder_path, relative):
    _, target = resolve_inside(folder_path, relative)
    if not target.is_file():
        raise FileNotFoundError(target)
    if target.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{relative} dépasse {MAX_FILE_BYTES} octets")
    return target.read_text(encoding="utf-8", errors="replace")


def normalize_text(text):
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def same_text(folder_path, relative, content):
    """Vrai si le disque a déjà exactement ce texte."""
    try:
        old = read_file(folder_path, relative)
    except FileNotFoundError:
        return False
    except Exception:
        return False
    return normalize_text(old).strip("\n") == normalize_text(content).strip("\n")


def _looks_binary(path):
    try:
        chunk = Path(path).read_bytes()[:8192]
    except Exception:
        return False
    return b"\x00" in chunk


def write_file(folder_path, relative, content):
    """Écrit un fichier à l'intérieur du dossier cible, puis relit pour confirmer."""
    label = Path(str(relative)).as_posix() if not Path(str(relative)).is_absolute() else Path(str(relative)).name
    if not is_safe_rel_path(label) and not is_safe_rel_path(Path(str(relative)).name):
        raise ValueError(
            "Le modèle a proposé un nom de fichier invalide "
            f"(« {label[:80]} »). Relancez en demandant un bloc nommé "
            "```fichier:chemin/relatif.ext"
        )
    _, target = resolve_inside(folder_path, relative)
    if target.exists() and target.is_dir():
        raise IsADirectoryError(f"Pas un fichier : {target}")
    if target.exists() and _looks_binary(target):
        raise ValueError(f"{Path(relative).as_posix()} est binaire : Lumio n'y écrit que du texte")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = normalize_text(content)
    if not payload.endswith("\n"):
        payload += "\n"
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    check = normalize_text(target.read_text(encoding="utf-8", errors="replace"))
    if check != payload:
        raise RuntimeError(f"Écriture non confirmée : {target}")
    invalidate_file_cache()
    return target


def delete_file(folder_path, relative):
    _, target = resolve_inside(folder_path, relative)
    if target.is_file():
        target.unlink()
    invalidate_file_cache()
    return target


def brief_diff(old, new, path, max_lines=36):
    before = normalize_text(old).splitlines()
    after = normalize_text(new).splitlines()
    lines = list(difflib.unified_diff(
        before, after, fromfile=f"actuel/{path}", tofile=f"proposé/{path}", lineterm="",
    ))
    if not lines:
        return "(aucune différence)"
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["…"]
    return "\n".join(lines)


def search_files(folder_path, query, extensions=None, max_files=MAX_FILES):
    """Retourne les fichiers dont le nom ou le texte contient la recherche."""
    needle = query.casefold().strip()
    if not needle:
        return list_files(folder_path, extensions, max_files)
    root = root_folder(folder_path)
    allowed = set(extensions) if extensions is not None else set(TEXT_EXTENSIONS)
    limit = max(1, int(max_files))
    matches = []
    for relative in iter_text_files(root, allowed, max(limit * 4, limit)):
        entry = root / relative
        if not entry.is_file():
            continue
        name_hit = needle in entry.name.casefold()
        text_hit = False
        if not name_hit and entry.stat().st_size <= MAX_FILE_BYTES:
            text = entry.read_text(encoding="utf-8", errors="replace")
            text_hit = needle in text.casefold()
        if name_hit or text_hit:
            matches.append(relative)
            if len(matches) >= limit:
                break
    return matches


def execute_folder(folder_path):
    """Ouvre le dossier dans l'explorateur du système."""
    path = _as_path(folder_path)
    if os.name == "nt":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=True)
    else:
        subprocess.run(["xdg-open", str(path)], check=True)
    return path


def reveal_file(folder_path, relative):
    _, target = resolve_inside(folder_path, relative)
    if os.name == "nt":
        subprocess.run(["explorer", "/select,", str(target)], check=False)
        return target
    execute_folder(target.parent)
    return target


def run_local_file(folder_path, relative):
    """Lance un .py, .pyw ou .bat du dossier, sans bloquer Lumio."""
    _, target = resolve_inside(folder_path, relative)
    if not target.is_file():
        raise FileNotFoundError(target)
    cwd = str(target.parent)
    suffix = target.suffix.lower()
    if suffix == ".bat":
        subprocess.Popen(["cmd", "/c", str(target)], cwd=cwd, close_fds=True)
    elif suffix in {".py", ".pyw"}:
        runner = "pythonw" if suffix == ".pyw" or os.name == "nt" else "python"
        subprocess.Popen([runner, str(target)], cwd=cwd, close_fds=True)
    else:
        return open_local_file(folder_path, relative)
    return target


def open_local_file(folder_path, relative):
    _, target = resolve_inside(folder_path, relative)
    if os.name == "nt":
        os.startfile(target)
    elif sys.platform == "darwin":
        subprocess.run(["open", str(target)], check=False)
    else:
        subprocess.run(["xdg-open", str(target)], check=False)
    return target


def create_file(folder_path, content):
    """Crée ou met à jour dark_edit.txt dans le dossier cible."""
    return write_file(folder_path, "dark_edit.txt", content)


def inspect_folder(folder_path):
    """Parcourt le dossier et ses sous-dossiers, et lit les fichiers texte."""
    path = root_folder(folder_path)
    lines = [f"Dossier : {path}", ""]
    file_count = 0
    for entry in sorted(path.rglob("*")):
        if file_count >= MAX_FILES:
            lines.append("... limite de 200 fichiers atteinte")
            break
        relative = entry.relative_to(path)
        if entry.is_dir():
            if _skip_dir(entry.name):
                continue
            lines.append(f"[dossier] {relative}")
            continue
        if any(_skip_dir(part) for part in relative.parts[:-1]):
            continue
        file_count += 1
        size = entry.stat().st_size
        lines.append(f"[fichier] {relative} ({size} octets)")
        if entry.suffix.lower() in TEXT_EXTENSIONS and size <= MAX_FILE_BYTES:
            text = entry.read_text(encoding="utf-8", errors="replace").rstrip()
            lines.append("---")
            lines.append(text if text else "(fichier vide)")
            lines.append("---")
        elif size > MAX_FILE_BYTES:
            lines.append("(trop volumineux, contenu non affiché)")
        else:
            lines.append("(contenu non texte, non affiché)")
        lines.append("")
    if file_count == 0:
        lines.append("(dossier vide)")
    return "\n".join(lines)


def edit_folder(folder_path, file_content=None):
    """Ouvre le dossier, ou y écrit un fichier si un contenu est fourni."""
    if file_content:
        create_file(folder_path, file_content)
    else:
        execute_folder(folder_path)


def normalize_provider(name):
    key = str(name or "ollama").strip().casefold().replace(" ", "")
    if key in {"online", "enligne", "openrouter", "deepseek", "api", "cloud"}:
        return "online"
    if key in {"pgpt", "privategpt", "private-gpt", "workbench"}:
        return "pgpt"
    return "ollama"


def pgpt_base():
    return str(os.environ.get("LUMIO_PGPT_URL") or PGPT_URL).strip().rstrip("/")


def provider_label(name):
    return PROVIDER_LABELS.get(normalize_provider(name), "Ollama")


def _env_online_key():
    for name in ("LUMIO_ONLINE_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def openai_base(url=None):
    base = str(url if url is not None else _ONLINE.get("url") or "").strip().rstrip("/")
    if not base:
        base = "https://openrouter.ai/api/v1"
    for suffix in ("/chat/completions", "/models"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base.rstrip("/")


def configure_online(url="", key="", preset=""):
    chosen = str(url or "").strip()
    secret = str(key or "").strip() or _env_online_key()
    label = str(preset or "").strip()
    if not label:
        low = chosen.casefold()
        for name, target in ONLINE_PRESETS:
            if target and target.casefold() == low:
                label = name
                break
        else:
            label = "Personnalisé" if chosen else "OpenRouter"
    if not chosen:
        for name, target in ONLINE_PRESETS:
            if name == label and target:
                chosen = target
                break
        else:
            chosen = _ONLINE.get("url") or "https://openrouter.ai/api/v1"
    _ONLINE["url"] = openai_base(chosen)
    _ONLINE["key"] = secret
    _ONLINE["preset"] = label
    return dict(_ONLINE)


def online_config():
    return dict(_ONLINE)


def openai_headers():
    headers = {
        "Authorization": f"Bearer {_ONLINE.get('key') or ''}",
        "Content-Type": "application/json",
    }
    if "openrouter" in openai_base().casefold():
        headers["HTTP-Referer"] = "https://lumio.local"
        headers["X-Title"] = "Lumio"
    return headers


# ==========================================================
#  Catalogue des modèles utilisables en ligne
# ==========================================================
# (identifiant, libellé, base OpenAI-compatible, page des clés, clé obligatoire)
CATALOG_SOURCES = (
    ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
     "https://openrouter.ai/settings/keys", False),
    ("groq", "Groq", "https://api.groq.com/openai/v1",
     "https://console.groq.com/keys", True),
    ("deepseek", "DeepSeek", "https://api.deepseek.com/v1",
     "https://platform.deepseek.com/api_keys", True),
)
CATALOG_TTL = 600  # durée du cache, en secondes
_CATALOG_KEYS = {}
_CATALOG_CACHE = {}


def catalog_key(source="openrouter"):
    """Normalise « Groq », « groq »… en identifiant interne."""
    needle = str(source or "").strip().casefold()
    for key, label, *_rest in CATALOG_SOURCES:
        if needle and needle in {key, label.casefold()}:
            return key
    return "openrouter"


def catalog_labels():
    """Libellés des catalogues, dans l'ordre d'affichage."""
    return [label for _key, label, *_rest in CATALOG_SOURCES]


def catalog_label(source="openrouter"):
    key = catalog_key(source)
    for item, label, *_rest in CATALOG_SOURCES:
        if item == key:
            return label
    return "OpenRouter"


def catalog_base(source="openrouter"):
    key = catalog_key(source)
    for item, _label, base, *_rest in CATALOG_SOURCES:
        if item == key:
            return base
    return "https://openrouter.ai/api/v1"


def catalog_keys_url(source="openrouter"):
    key = catalog_key(source)
    for item, _label, _base, url, *_rest in CATALOG_SOURCES:
        if item == key:
            return url
    return ""


def catalog_needs_key(source="openrouter"):
    key = catalog_key(source)
    for item, _label, _base, _url, needed in CATALOG_SOURCES:
        if item == key:
            return bool(needed)
    return False


def configure_catalog_keys(mapping=None):
    """Clés API des catalogues : {'openrouter': '…', 'groq': '…', 'deepseek': '…'}."""
    if mapping is None:
        return dict(_CATALOG_KEYS)
    for raw_key, raw_value in dict(mapping).items():
        key = catalog_key(raw_key)
        secret = str(raw_value or "").strip()
        if secret:
            _CATALOG_KEYS[key] = secret
        else:
            _CATALOG_KEYS.pop(key, None)
    return dict(_CATALOG_KEYS)


def set_catalog_key(source, key):
    return configure_catalog_keys({source: key})


def catalog_secret(source="openrouter"):
    """Clé utilisable pour ce catalogue (clé dédiée, variable d'environnement, ou clé active)."""
    key = catalog_key(source)
    if _CATALOG_KEYS.get(key):
        return _CATALOG_KEYS[key]
    env_name = {
        "openrouter": "OPENROUTER_API_KEY",
        "groq": "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }.get(key)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name].strip()
    if openai_base().casefold() == catalog_base(key).casefold():
        return str(_ONLINE.get("key") or "").strip()
    return ""


def format_context(value):
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return "contexte n/a"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M ctx"
    if value >= 1_000:
        return f"{value // 1_000}k ctx"
    return f"{value} ctx"


def format_price(value):
    if value is None:
        return "prix n/a"
    if value <= 0:
        return "gratuit"
    if value < 0.1:
        return f"{value:.3f} $/Mtok"
    return f"{value:.2f} $/Mtok"


def _catalog_row(item, source):
    """Normalise une entrée de catalogue (OpenRouter / Groq / DeepSeek)."""
    if isinstance(item, str):
        item = {"id": item}
    if not isinstance(item, dict):
        return None
    ident = str(item.get("id") or item.get("name") or "").strip()
    if not ident:
        return None
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}

    def _price(field):
        try:
            return float(pricing.get(field) or 0) * 1_000_000
        except (TypeError, ValueError):
            return None

    prompt = _price("prompt")
    completion = _price("completion")
    arch = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    modalities = arch.get("input_modalities") or []
    if isinstance(modalities, str):
        modalities = [modalities]
    params = item.get("supported_parameters") or []
    if isinstance(params, str):
        params = [params]
    try:
        context = int(item.get("context_length") or item.get("context_window") or 0)
    except (TypeError, ValueError):
        context = 0

    vendor = str(item.get("owned_by") or "").strip()
    if "/" in ident:
        vendor = ident.split("/")[0]
    elif not vendor:
        vendor = catalog_label(source)

    free = False
    if source == "openrouter":
        free = ident.endswith(":free") or (prompt == 0.0 and completion == 0.0)
    try:
        created = int(item.get("created") or 0)
    except (TypeError, ValueError):
        created = 0

    return {
        "created": created,
        "id": ident,
        "name": str(item.get("name") or ident).strip(),
        "vendor": vendor or catalog_label(source),
        "context": context,
        "prompt": prompt,
        "completion": completion,
        "free": free,
        "vision": "image" in [str(m).casefold() for m in modalities],
        "tools": "tools" in [str(p).casefold() for p in params],
        "description": str(item.get("description") or "").strip(),
        "source": catalog_key(source),
    }


def catalog_models(source="openrouter", refresh=False, timeout=25):
    """Modèles utilisables en ligne (aucun téléchargement sur le poste).

    OpenRouter expose un catalogue public : la navigation ne demande pas de clé.
    Groq et DeepSeek exigent la leur (clé dédiée, variable d'environnement
    OPENROUTER_API_KEY / GROQ_API_KEY / DEEPSEEK_API_KEY, ou clé déjà active).
    """
    key = catalog_key(source)
    cached = _CATALOG_CACHE.get(key)
    if cached and not refresh and (time.time() - cached[0]) < CATALOG_TTL:
        return [dict(row) for row in cached[1]]

    secret = catalog_secret(key)
    if catalog_needs_key(key) and not secret:
        raise RuntimeError(
            f"Clé API {catalog_label(key)} requise pour parcourir ce catalogue "
            f"({catalog_keys_url(key)})."
        )

    headers = {"Accept": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    if key == "openrouter":
        headers["HTTP-Referer"] = "https://lumio.local"
        headers["X-Title"] = "Lumio"
    request = urllib.request.Request(f"{catalog_base(key)}/models", headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    raw = payload.get("data") if isinstance(payload, dict) else payload
    if raw is None and isinstance(payload, dict):
        raw = payload.get("models")
    rows = [row for row in (_catalog_row(item, key) for item in (raw or [])) if row]
    rows.sort(key=lambda entry: (entry["vendor"].casefold(), entry["id"].casefold()))
    _CATALOG_CACHE[key] = (time.time(), [dict(row) for row in rows])
    return rows


UNCENSORED_ALWAYS = (
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition",
    "cognitivecomputations/dolphin3.0-mistral-24b:free",
    "cognitivecomputations/dolphin3.0-mistral-24b",
    "cognitivecomputations/dolphin3.0-r1-mistral-24b:free",
    "cognitivecomputations/dolphin3.0-r1-mistral-24b",
)

UNCENSORED_MARKERS = (
    "uncensored", "abliterat", "unfiltered", "unhinged", "unrestricted",
    "unaligned", "nofilt", "no-filter", "dolphin", "venice", "mythomax",
    "neversleep", "undi95", "thedrummer", "mancer", "noromaid", "magnum",
    "lunaris", "goliath", "llewd", "nsfw", "lewd", "erotic", "jailbreak",
    "cognitivecomputations", "dphn", "euryale", "lumimaid", "stheno",
    "toppy", "midnight-rose", "celeste", "starcannon",
)


FAMILY_LABELS = {
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "google": "Google",
    "qwen": "Qwen",
    "meta-llama": "Llama",
    "mistralai": "Mistral",
    "anthropic": "Anthropic",
    "nousresearch": "Nous",
    "cognitivecomputations": "Dolphin",
    "nvidia": "NVIDIA",
    "microsoft": "Microsoft",
    "cohere": "Cohere",
    "x-ai": "xAI",
    "moonshotai": "Moonshot",
    "01-ai": "Yi",
    "thudm": "GLM",
    "liquid": "Liquid",
    "perplexity": "Perplexity",
    "amazon": "Amazon",
    "venice": "Venice",
    "sao10k": "Sao10k",
    "nothingiisreal": "NothingIsReal",
    "gryphe": "Gryphe",
    "anthracite-org": "Anthracite",
}


def is_uncensored_model(name, extra=""):
    blob = f"{name} {extra}".casefold()
    if any(token in blob for token in UNCENSORED_MARKERS):
        return True
    low = str(name or "").strip().casefold()
    return any(low == item.casefold() or low.startswith(item.casefold()) for item in UNCENSORED_ALWAYS)


def model_family(name):
    text = str(name or "").strip()
    if not text:
        return ""
    if "/" in text:
        return text.split("/", 1)[0].strip()
    return text.split(":")[0].split("-")[0].strip()


def family_label(key):
    token = str(key or "").strip()
    if not token:
        return ""
    return FAMILY_LABELS.get(token.casefold(), token.replace("-", " ").title())


def list_model_families(names):
    counts = {}
    for name in names or []:
        key = model_family(name)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    rows = [
        (key, count, family_label(key))
        for key, count in counts.items()
    ]
    rows.sort(key=lambda item: (-item[1], item[2].casefold()))
    return rows


def filter_models(names, kind="all", family=""):
    names = [str(item).strip() for item in (names or []) if str(item).strip()]
    key = str(kind or "all").strip().casefold()
    if key in {"uncensored", "libre"}:
        names = [name for name in names if is_uncensored_model(name)]
    wanted = str(family or "").strip().casefold()
    if wanted:
        names = [name for name in names if model_family(name).casefold() == wanted]
    return names


def looks_like_ollama_model(name):
    text = str(name or "").strip()
    if not text:
        return False
    if "/" in text:
        return False
    return ":" in text


_WEAK_CODER_MARKERS = (
    "remm", "mythomax", "toppy", "lunaris", "weaver", "unslop",
    "l2-13b", "13b", "12b", "8b", "7b",
)
_STRONG_CODER_MARKERS = (
    "dolphin-mistral-24b", "dolphin3.0", "venice-edition",
    "magnum-v4-72b", "cydonia-24b", "skyfall-36b", "euryale-70b",
)


def is_weak_coder(name):
    low = str(name or "").casefold()
    if any(token in low for token in _STRONG_CODER_MARKERS):
        return False
    return any(token in low for token in _WEAK_CODER_MARKERS)


def model_coding_tag(name):
    if is_weak_coder(name):
        return "RP, pas pour le code"
    low = str(name or "").casefold()
    if any(token in low for token in _STRONG_CODER_MARKERS):
        return "bon pour le code"
    return ""


def best_coder(names):
    names = [str(item).strip() for item in (names or []) if str(item).strip()]
    ranked = sorted(names, key=_online_model_rank)
    for name in ranked:
        if not is_weak_coder(name):
            return name
    return ranked[0] if ranked else ""


def pick_listed_model(names, saved="", provider="ollama"):
    names = [str(item).strip() for item in (names or []) if str(item).strip()]
    saved = str(saved or "").strip()
    if saved in names:
        return saved
    key = normalize_provider(provider)
    if key == "online":
        pick = best_coder(names)
        if pick:
            return pick
    if key == "pgpt":
        for token in ("coder", "qwen", "instruct"):
            for name in names:
                if token in name.casefold():
                    return name
    return names[0] if names else ""


def _online_model_rank(name):
    low = str(name or "").casefold()
    family = 9
    if "venice" in low and "dolphin" in low:
        family = 0
    elif "dolphin" in low:
        family = 1
    elif "magnum" in low or "cydonia" in low or "skyfall" in low:
        family = 2
    elif "abliterat" in low or "uncensored" in low:
        family = 3
    if is_weak_coder(name):
        family = 20
    size = 6
    if "72b" in low or "70b" in low:
        size = 0
    elif "36b" in low or "32b" in low:
        size = 1
    elif "24b" in low:
        size = 2
    elif "13b" in low or "12b" in low:
        size = 8
    elif "8b" in low or "7b" in low:
        size = 9
    return (family, size, low)


def list_online_models():
    if not str(_ONLINE.get("key") or "").strip():
        raise RuntimeError("Aucune clé API. Cliquez sur API pour coller votre clé.")
    names = []
    seen = set()
    base = openai_base()
    url = f"{base}/models?limit=1000"
    offset = 0
    for page in range(20):
        try:
            request = urllib.request.Request(url, headers=openai_headers())
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            if page == 0 and "?" in url:
                url = f"{base}/models"
                continue
            raise
        raw = data.get("data") if isinstance(data, dict) else data
        if raw is None and isinstance(data, dict):
            raw = data.get("models")
        page_items = list(raw or [])
        for item in page_items:
            if isinstance(item, str):
                name, extra = item, ""
            else:
                item = item or {}
                name = item.get("id") or item.get("name") or ""
                extra = f"{item.get('name') or ''} {item.get('description') or ''}"
            name = str(name).strip()
            if not name or name.casefold() in seen:
                continue
            if not is_uncensored_model(name, extra):
                continue
            if is_weak_coder(name):
                continue
            seen.add(name.casefold())
            names.append(name)
        nxt = (data.get("links") or {}).get("next") if isinstance(data, dict) else None
        offset += len(page_items)
        total = 0
        if isinstance(data, dict):
            try:
                total = int(data.get("total_count") or 0)
            except (TypeError, ValueError):
                total = 0
        if nxt:
            url = nxt if str(nxt).startswith("http") else f"https://openrouter.ai{nxt}"
        elif total and offset < total:
            url = f"{base}/models?limit=1000&offset={offset}"
        else:
            break
    names = [name for name in names if not is_weak_coder(name)]
    for forced in UNCENSORED_ALWAYS:
        if is_weak_coder(forced):
            continue
        if forced.casefold() not in seen:
            names.insert(0, forced)
            seen.add(forced.casefold())
    names.sort(key=_online_model_rank)
    return names


def list_pgpt_models():
    request = urllib.request.Request(
        f"{pgpt_base()}/v1/models",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        data = json.loads(response.read().decode("utf-8"))
    raw = data.get("data") if isinstance(data, dict) else data
    names = []
    for item in raw or []:
        if isinstance(item, str):
            name = item
        else:
            name = (item or {}).get("id") or (item or {}).get("display_name") or ""
        name = str(name).strip()
        if name and name not in names:
            names.append(name)
    return names


def list_models(provider="ollama"):
    key = normalize_provider(provider)
    if key == "online":
        return list_online_models()
    if key == "pgpt":
        return list_pgpt_models()
    request = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
    with urllib.request.urlopen(request, timeout=4) as response:
        data = json.loads(response.read().decode("utf-8"))
    return [model.get("name", "") for model in data.get("models", []) if model.get("name")]


def ollama_up():
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def pgpt_up():
    try:
        urllib.request.urlopen(f"{pgpt_base()}/health", timeout=2)
        return True
    except Exception:
        try:
            urllib.request.urlopen(f"{pgpt_base()}/v1/models", timeout=3)
            return True
        except Exception:
            return False


def online_up():
    if not str(_ONLINE.get("key") or "").strip():
        return False
    try:
        request = urllib.request.Request(
            f"{openai_base()}/models",
            headers=openai_headers(),
        )
        urllib.request.urlopen(request, timeout=6)
        return True
    except Exception:
        return False


def backend_up(provider="ollama"):
    key = normalize_provider(provider)
    if key == "online":
        return online_up()
    if key == "pgpt":
        return pgpt_up()
    return ollama_up()


def _http_error_text(error):
    if not isinstance(error, urllib.error.HTTPError):
        return str(error)
    cached = getattr(error, "_lumio_body", None)
    if cached is not None:
        return cached
    try:
        body = error.read().decode("utf-8", errors="replace")
        data = json.loads(body)
        text = str(data.get("error") or body).strip()
    except Exception:
        text = str(error.reason or error)
    error._lumio_body = text
    return text


def _looks_like_image_refusal(error):
    text = _http_error_text(error).casefold()
    return any(word in text for word in ("image", "vision", "multimodal", "does not support"))


VISION_MARKERS = (
    "llava", "bakllava", "moondream", "minicpm", "pixtral",
    "vision", "qwen2-vl", "qwen2.5vl", "qwen2.5-vl", "llama3.2-vision",
    "gemma3", "mistral-small3",
)
VISION_HELPER = "moondream"


_SHOW_CACHE = {}


def _ollama_show(name):
    cached = _SHOW_CACHE.get(name)
    if cached is not None:
        return cached
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/show",
        data=json.dumps({"name": name}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        info = json.loads(response.read().decode("utf-8"))
    _SHOW_CACHE[name] = info
    return info


def supports_vision(name):
    if not name:
        return False
    try:
        info = _ollama_show(name)
        caps = [str(item).casefold() for item in (info.get("capabilities") or [])]
        if "vision" in caps or "image" in caps:
            return True
        if info.get("projector_info"):
            return True
    except Exception:
        pass
    low = str(name).casefold()
    return any(marker in low for marker in VISION_MARKERS)


def pick_vision_model(preferred=None):
    names = []
    try:
        names = list_models()
    except Exception:
        names = []
    ordered = []
    if preferred:
        ordered.append(preferred)
    for name in names:
        if name not in ordered:
            ordered.append(name)
    for name in ordered:
        low = str(name).casefold()
        if any(marker in low for marker in VISION_MARKERS):
            return name
    for name in ordered:
        if supports_vision(name):
            return name
    return None


def close_http(response):
    """Coupe le socket tout de suite, même depuis un autre fil."""
    if response is None:
        return
    sock = None
    try:
        sock = response.fp.raw._sock
    except Exception:
        try:
            sock = response.fp._sock
        except Exception:
            sock = None
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass
    try:
        response.close()
    except Exception:
        pass


def ensure_vision_helper(on_notice=None, on_progress=None, cancel_event=None, on_open=None):
    found = pick_vision_model()
    if found:
        return found
    if on_notice is not None:
        on_notice(
            "Installation de moondream, le lecteur d'images. "
            "Une seule fois, ça peut durer quelques minutes. Laissez Lumio ouvert."
        )
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/pull",
        data=json.dumps({"name": VISION_HELPER, "stream": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    last_pct = -1
    response = None
    try:
        response = urllib.request.urlopen(request, timeout=600)
        if on_open is not None:
            on_open(response)
        for raw in response:
            if cancel_event is not None and cancel_event.is_set():
                return None
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("error"):
                raise RuntimeError(str(data["error"]))
            total = int(data.get("total") or 0)
            done = int(data.get("completed") or 0)
            status = str(data.get("status") or "")
            if total > 0:
                pct = min(100, int(done * 100 / total))
                if pct != last_pct and (pct == 100 or pct - last_pct >= 5):
                    last_pct = pct
                    if on_progress is not None:
                        on_progress(f"Téléchargement moondream : {pct}%")
            elif status and on_progress is not None:
                on_progress(status)
    except Exception as error:
        if cancel_event is not None and cancel_event.is_set():
            return None
        if isinstance(error, RuntimeError):
            raise
        if isinstance(error, urllib.error.URLError):
            raise RuntimeError(explain_ollama_error(error, images=False)) from error
        raise RuntimeError(f"Installation de moondream impossible : {error}") from error
    finally:
        close_http(response)
    if on_progress is not None:
        on_progress("moondream est prêt")
    return pick_vision_model(VISION_HELPER) or VISION_HELPER


def describe_images(images, model, cancel_event=None, on_open=None):
    payload = {
        "model": model,
        "stream": True,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Décris cette capture d'écran en français, de façon précise et factuelle : "
                    "textes visibles, boutons, messages d'erreur, mise en page, "
                    "et ce qui semble anormal. Sans introduction."
                ),
                "images": images,
            }
        ],
    }
    return read_chat_stream(
        payload,
        on_open=on_open,
        cancel_event=cancel_event,
        timeout=180,
    ).strip()


def explain_backend_error(error, images=False, provider="ollama"):
    key = normalize_provider(provider)
    label = provider_label(key)
    if isinstance(error, urllib.error.HTTPError):
        detail = _http_error_text(error)
        if images and _looks_like_image_refusal(error):
            return (
                "Ce modèle ne lit pas les images. "
                "Choisissez un modèle vision, ou renvoyez sans capture."
            )
        if error.code in {401, 403}:
            return (
                f"{label} a refusé la clé API ({error.code}). "
                "Cliquez sur API et collez une clé valide."
            )
        return f"{label} a refusé la requête : {detail}"
    reason = getattr(error, "reason", error)
    text = str(error).casefold()
    if isinstance(reason, TimeoutError) or "timed out" in text:
        if key == "online":
            return f"{label} met trop de temps à répondre. Vérifiez l'URL, la clé et votre connexion."
        return f"{label} met trop de temps à répondre. Vérifiez qu'il est ouvert et qu'un modèle est chargé."
    if isinstance(reason, ConnectionRefusedError) or "refused" in text or "10061" in text:
        if key == "online":
            return f"{label} n'est pas joignable. Vérifiez l'URL et votre connexion Internet."
        if key == "pgpt":
            return (
                "PrivateGPT n'est pas lancé. Ouvrez le workbench "
                "(http://127.0.0.1:8080/ui/), puis réessayez."
            )
        return "Ollama n'est pas lancé. Ouvrez Ollama, attendez qu'il soit prêt, puis cliquez sur Modèles."
    if key == "online":
        return f"{label} ne répond pas ({error})"
    if key == "pgpt":
        return f"{label} ne répond pas sur 127.0.0.1:8080 ({error})"
    return f"Ollama ne répond pas sur 127.0.0.1:11434 ({error})"


def explain_ollama_error(error, images=False, provider="ollama"):
    return explain_backend_error(error, images=images, provider=provider)


def context_budget(model=None):
    """Caractères de fichiers à envoyer, selon la fenêtre du modèle."""
    default = CONTEXT_CHAR_LIMIT
    if not model:
        return default
    try:
        info = _ollama_show(model)
        details = info.get("model_info") or {}
        tokens = 0
        for key, value in details.items():
            if str(key).endswith("context_length"):
                tokens = int(value or 0)
                break
        if tokens <= 0:
            return default
        return max(12_000, min(200_000, int(tokens * 3 * 0.45)))
    except Exception:
        return default


def model_window_label(name):
    if not name:
        return ""
    try:
        info = _ollama_show(name)
        details = info.get("model_info") or {}
        for key, value in details.items():
            if str(key).endswith("context_length"):
                tokens = int(value or 0)
                if tokens >= 1000:
                    return f"{max(1, tokens // 1000)}k"
                return str(tokens) if tokens else ""
    except Exception:
        pass
    return ""


def model_num_ctx(name):
    if not name:
        return 0
    try:
        info = _ollama_show(name)
        details = info.get("model_info") or {}
        for key, value in details.items():
            if str(key).endswith("context_length"):
                return max(0, int(value or 0))
    except Exception:
        pass
    return 0


_WANT_ALL = re.compile(
    r"\b(tous les fichiers|toutes les? (pi[eè]ces|sources)|tout le (projet|dossier|code)|"
    r"l['’ ]ensemble|all files|whole (project|folder)|everything)\b",
    re.I,
)
_FOLLOWUP = re.compile(
    r"^\s*(et |aussi |encore |continue|continues|poursu[ie]s|pareil|idem|"
    r"comme (ça|cela)|corrige[sz]?|modifie[sz]?|ajoute[sz]?|refais)\b",
    re.I,
)
_EXT_HINTS = (
    (re.compile(r"\b(\.bat|\.cmd|fichier bat|le bat|batch)\b", re.I), (".bat", ".cmd")),
    (re.compile(r"\b(\.pyw?|python|le main)\b", re.I), (".py", ".pyw")),
    (re.compile(r"\b(\.json)\b", re.I), (".json",)),
    (re.compile(r"\b(\.md|readme|markdown)\b", re.I), (".md",)),
    (re.compile(r"\b(\.ps1|powershell)\b", re.I), (".ps1",)),
    (re.compile(r"\b(\.js|javascript)\b", re.I), (".js", ".jsx")),
    (re.compile(r"\b(\.html?)\b", re.I), (".html", ".htm")),
    (re.compile(r"\b(\.css)\b", re.I), (".css",)),
    (re.compile(r"\b(\.log)\b", re.I), (".log",)),
)


def wants_all_files(question):
    return bool(_WANT_ALL.search(str(question or "")))


def looks_followup(question):
    return bool(_FOLLOWUP.search(str(question or "")))


def _posix_list(files):
    seen = []
    keys = set()
    for item in files or []:
        label = Path(item).as_posix()
        key = label.casefold()
        if key in keys:
            continue
        keys.add(key)
        seen.append(label)
    return seen


def resolve_mention(raw, available):
    needle = _clean_rel_path(raw)
    if not needle:
        return ""
    low = needle.casefold()
    by_low = {Path(item).as_posix().casefold(): Path(item).as_posix() for item in available}
    if low in by_low:
        return by_low[low]
    name = Path(needle).name.casefold()
    hits = [item for item in available if Path(item).name.casefold() == name]
    if len(hits) == 1:
        return hits[0]
    ends = [item for item in available if item.casefold().endswith("/" + low) or item.casefold() == low]
    if len(ends) == 1:
        return ends[0]
    return ""


def pick_context_files(files, question="", selected=None, recent=None, last_sent=None, pending=None, pinned=None):
    """Choisit les fichiers dont le contenu entier doit partir."""
    available = _posix_list(files)
    if not available:
        return [], {}
    if wants_all_files(question) or len(available) <= SMALL_FILE_POOL:
        reason = "tous" if wants_all_files(question) or len(available) <= SMALL_FILE_POOL else "choisi"
        return available, {item: reason for item in available}

    scores = {item: 0 for item in available}
    why = {item: [] for item in available}

    def bump(label, points, note):
        key = Path(label).as_posix()
        if key not in scores:
            return
        scores[key] += points
        if note and note not in why[key]:
            why[key].append(note)

    if selected:
        bump(selected, 40, "sélectionné")
    for item in pinned or []:
        bump(item, 90, "épinglé")
    for item in pending or []:
        bump(item, 30, "à enregistrer")
    for index, item in enumerate(recent or []):
        bump(item, max(6, 18 - index * 5), "ouvert")
    if looks_followup(question):
        for item in last_sent or []:
            bump(item, 16, "déjà envoyé")

    query = str(question or "")
    for raw in paths_mentioned(query):
        matched = resolve_mention(raw, available)
        if matched:
            bump(matched, 100, "cité")

    folded = query.casefold()
    for item in available:
        name = Path(item).name.casefold()
        stem = Path(item).stem.casefold()
        if len(name) >= 3 and name in folded:
            bump(item, 80, "cité")
        elif len(stem) >= 4 and re.search(r"\b" + re.escape(stem) + r"\b", folded):
            bump(item, 45, "cité")

    for pattern, suffixes in _EXT_HINTS:
        if not pattern.search(query):
            continue
        hits = [item for item in available if Path(item).suffix.lower() in suffixes]
        if len(hits) == 1:
            bump(hits[0], 35, "extension")

    ranked = sorted(available, key=lambda item: (-scores[item], item.casefold()))
    picked = [item for item in ranked if scores[item] > 0][:MAX_SMART_BODIES]
    for item in _posix_list(pinned):
        if item in scores and item not in picked:
            picked.insert(0, item)
    picked = picked[:MAX_SMART_BODIES]
    if not picked:
        fallback = Path(selected).as_posix() if selected else ""
        if fallback in scores:
            picked = [fallback]
        elif recent:
            for item in recent:
                key = Path(item).as_posix()
                if key in scores:
                    picked = [key]
                    break
        if not picked:
            picked = available[:1]
        why[picked[0]] = why.get(picked[0]) or ["par défaut"]
    return picked, {item: ", ".join(why.get(item) or ["choisi"]) for item in picked}


def prioritize_files(files, question="", selected=None, recent=None, last_sent=None, pending=None, pinned=None):
    picked, _reasons = pick_context_files(
        files, question=question, selected=selected,
        recent=recent, last_sent=last_sent, pending=pending, pinned=pinned,
    )
    rest = [Path(item).as_posix() for item in files if Path(item).as_posix() not in set(picked)]
    return picked + rest


def _file_body(folder_path, label, overrides):
    if label in overrides:
        return str(overrides[label]).replace("\r\n", "\n").replace("\r", "\n")
    return read_file(folder_path, label).replace("\r\n", "\n").replace("\r", "\n")


def build_context(
    folder_path, files, overrides=None, question="", selected=None, char_limit=None,
    recent=None, last_sent=None, pending=None, pinned=None,
):
    """Inventaire des fichiers cochés + contenu entier des fichiers pertinents."""
    overrides = {Path(key).as_posix(): value for key, value in (overrides or {}).items()}
    limit = CONTEXT_CHAR_LIMIT if char_limit is None else max(4_000, int(char_limit))
    available = _posix_list(files)
    picked, reasons = pick_context_files(
        available, question=question, selected=selected,
        recent=recent, last_sent=last_sent, pending=pending, pinned=pinned,
    )
    chunks = []
    omitted = []
    sent = []
    used = 0
    if available:
        shown = available[:INVENTORY_MAX]
        listing = "\n".join(f"- {name}" for name in shown)
        if len(available) > INVENTORY_MAX:
            listing += f"\n- … et {len(available) - INVENTORY_MAX} autres"
        header = (
            f"Fichiers accessibles ({len(available)}) :\n{listing}\n\n"
            "Contenu envoyé en entier pour les fichiers pertinents :\n"
        )
        chunks.append(header)
        used += len(header)
    for label in picked:
        if used >= limit:
            omitted.append(f"{label} (non envoyé, plus de place)")
            continue
        try:
            text = _file_body(folder_path, label, overrides)
        except (OSError, ValueError):
            omitted.append(f"{label} (illisible ou trop volumineux)")
            continue
        block = f"### {label}\n{text}\n"
        remaining = limit - used
        if len(block) > remaining:
            chunks.append(block[:remaining] + "\n…")
            omitted.append(
                f"{label} — début seulement ({remaining:,} / {len(block):,} caractères)"
            )
            used = limit
            sent.append({"path": label, "chars": remaining, "why": reasons.get(label, "")})
            continue
        chunks.append(block)
        used += len(block)
        sent.append({"path": label, "chars": len(text), "why": reasons.get(label, "")})
    report = {
        "sent": sent,
        "listed": len(available),
        "used": used,
        "all": bool(available) and len(sent) >= len(available) and not omitted,
    }
    return "\n".join(chunks), omitted, report


def format_send_summary(report, omitted=None):
    sent = (report or {}).get("sent") or []
    if not sent:
        return "Aucun contenu de fichier envoyé, seulement la liste des noms."
    parts = []
    for item in sent:
        why = f" ({item['why']})" if item.get("why") else ""
        parts.append(f"{item['path']}{why}")
    text = "Contenu envoyé : " + ", ".join(parts)
    extra = list(omitted or [])
    if extra:
        text += ". Incomplet : " + ", ".join(extra)
    listed = int((report or {}).get("listed") or 0)
    if listed > len(sent):
        text += f". Les {listed - len(sent)} autres fichiers sont seulement listés : demandez-les par nom si besoin."
    return text


_OLD_FENCE = re.compile(r"```.*?```", re.DOTALL)


def shrink_history(history, per_message=HISTORY_MESSAGE_CHARS):
    """Évite de renvoyer d'énormes anciens fichiers dans l'historique."""
    out = []
    for item in history or []:
        role = item.get("role")
        content = str(item.get("content") or "")
        if role == "assistant" and "```" in content:
            content = _OLD_FENCE.sub("```(code déjà proposé — le fichier actuel est joint)```", content)
        if len(content) > per_message:
            content = content[:per_message].rstrip() + "\n…"
        out.append({"role": role, "content": content})
    return out


_FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_PATH_MARK = re.compile(r"(?:fichier|file|path)\s*[:=]\s*(\S+)", re.I)
_HEADING_PATH = re.compile(
    r"^(?:#{1,6}|fichier|file)\s*[:=]?\s*[`'\"]?(.+?)[`'\"]?\s*$",
    re.I,
)
_LANG_ONLY = {
    "python", "py", "python3", "javascript", "js", "typescript", "ts",
    "json", "md", "markdown", "html", "css", "txt", "xml", "yaml", "yml",
    "bash", "sh", "zsh", "bat", "batch", "cmd", "dos", "ps1", "powershell",
    "ini", "cfg", "log", "text", "plaintext", "plain", "diff", "patch", "csv",
}
_LANG_EXT = {
    "python": ".py", "py": ".py", "python3": ".py",
    "javascript": ".js", "js": ".js", "jsx": ".jsx",
    "typescript": ".ts", "ts": ".ts", "tsx": ".tsx",
    "json": ".json", "md": ".md", "markdown": ".md",
    "html": ".html", "htm": ".html", "css": ".css",
    "bat": ".bat", "batch": ".bat", "cmd": ".cmd", "dos": ".bat",
    "ps1": ".ps1", "powershell": ".ps1",
    "bash": ".sh", "sh": ".sh", "zsh": ".sh",
    "xml": ".xml", "yaml": ".yml", "yml": ".yml",
    "ini": ".ini", "cfg": ".cfg", "log": ".log", "csv": ".csv",
    "java": ".java", "c": ".c", "cpp": ".cpp", "h": ".h",
    "cs": ".cs", "php": ".php", "go": ".go", "rs": ".rs", "sql": ".sql",
}
_PATH_IN_TEXT = re.compile(
    r"(?:[\w.+-]+[/\\])*(?:[\w.+-]+\.[A-Za-z0-9]{1,16}|Makefile|Dockerfile|Gemfile|Procfile|LICENSE|README)",
    re.I,
)
_EDIT_INTENT = re.compile(
    r"\b(cr[ée]e[rz]?|ajout(e[rz]|er)|modifi(e[rz]|er)|g[ée]n[èe]re[rz]?|"
    r"[ée]cris|[ée]crire|corrig(e[rz]|er)|remplac(e[rz]|er)|enregistre[rz]?|"
    r"nouveau|nouvelle|create|add|write|fix|update|generate)\b",
    re.I,
)
_GUI_INTENT = re.compile(
    r"\b(interface graphique|gui|fen[êe]tre|tkinter|customtkinter|"
    r"application (graphique|windows|bureau)|logiciel.{0,60}(interface|fen[êe]tre)|"
    r"belle interface|ergonomique)\b",
    re.I,
)
_GUI_MARKERS = (
    "mainloop(", "ctk.ctk(", "customtkinter.ctk(", "tk.tk(",
)
_ROLEPLAY_MARKERS = re.compile(
    r"\b(was excited|they opened up|felt a sense|lumio was|"
    r"once upon|they double-clicked|peace of mind|"
    r"satisfying click|entering dos mode)\b",
    re.I,
)


_WIN_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
_BARE_NAMES = {
    "makefile", "dockerfile", "gemfile", "rakefile", "procfile",
    "license", "readme", "changelog", "vagrantfile",
}
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def is_safe_rel_path(text):
    """Un vrai chemin relatif, pas une phrase du modèle."""
    raw = str(text or "").replace("\\", "/").strip()
    if not raw or "://" in raw:
        return False
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts or not path.parts or len(path.parts) > 8:
        return False
    if _WIN_BAD.search(raw):
        return False
    name = path.name
    if not name or name in {".", ".."} or name.endswith((" ", ".")):
        return False
    if name.casefold().split(".")[0] in _RESERVED_NAMES:
        return False
    if " " in name and name.count(" ") >= 2:
        return False
    if len(name) > 80 or len(raw) > 180:
        return False
    suffix = path.suffix.lower()
    if suffix:
        return bool(re.fullmatch(r"\.[A-Za-z0-9]{1,16}", suffix))
    if name.startswith(".") and len(name) > 1:
        return True
    return name.casefold() in _BARE_NAMES


def _clean_rel_path(raw):
    text = str(raw or "").strip().strip("\"'`<>()[]")
    text = text.replace("\\", "/").lstrip("/")
    if text.startswith("./"):
        text = text[2:]
    text = text.split("?")[0].split("#")[0]
    if not text or "://" in text:
        return None
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        return None
    posix = path.as_posix()
    if not is_safe_rel_path(posix):
        return None
    return posix


def _path_from_fence_info(info):
    info = str(info or "").strip()
    if not info:
        return None
    marked = _PATH_MARK.search(info)
    if marked:
        return _clean_rel_path(marked.group(1))
    tokens = info.replace(":", " ").split()
    for token in reversed(tokens):
        candidate = _clean_rel_path(token)
        if candidate and Path(candidate).suffix:
            return candidate
    lowered = info.casefold()
    if lowered in _LANG_ONLY or lowered.split()[0] in _LANG_ONLY:
        return None
    if Path(info).suffix:
        return _clean_rel_path(info)
    return None


def _path_before_fence(prefix):
    for line in reversed(prefix.strip().splitlines()[-8:]):
        line = line.strip().strip("-*")
        if not line:
            continue
        marked = _PATH_MARK.search(line)
        if marked:
            found = _clean_rel_path(marked.group(1))
            if found:
                return found
        heading = _HEADING_PATH.match(line)
        if heading:
            found = _clean_rel_path(heading.group(1).strip("*_ "))
            if found and Path(found).suffix:
                return found
        found = _clean_rel_path(line)
        if found and ("/" in found or Path(found).suffix or found.startswith(".")):
            return found
    return None


def _normalize_content(body):
    content = str(body or "").replace("\r\n", "\n").strip("\n")
    if content:
        content += "\n"
    return content


def _fence_ext(info):
    token = str(info or "").strip().split()[0].casefold() if str(info or "").strip() else ""
    return _LANG_EXT.get(token)


def paths_mentioned(*texts):
    found = []
    seen = set()
    for text in texts:
        for match in _PATH_IN_TEXT.finditer(str(text or "")):
            path = _clean_rel_path(match.group(0).replace("\\", "/"))
            if path and path.casefold() not in seen:
                seen.add(path.casefold())
                found.append(path)
    return found


def has_edit_intent(question):
    return bool(_EDIT_INTENT.search(str(question or ""))) or wants_gui(question)


def wants_gui(text):
    return bool(_GUI_INTENT.search(str(text or "")))


def looks_like_gui_code(text):
    return looks_like_runnable_gui(text)


def looks_like_runnable_gui(text):
    body = str(text or "").casefold()
    if "mainloop(" not in body:
        return False
    has_window = any(token in body for token in (
        "ctk.ctk(", "customtkinter.ctk(", "tk.tk(", "ctk()", " = tk()",
    ))
    if not has_window and "class " in body and "mainloop(" in body:
        has_window = "customtkinter" in body or "tkinter" in body
    hidden = "withdraw(" in body and "0x0" in body
    return has_window and not hidden


def looks_like_roleplay(text):
    body = str(text or "")
    if looks_like_runnable_gui(body):
        return False
    if body.count("```") >= 2:
        return False
    return bool(_ROLEPLAY_MARKERS.search(body))


def extract_file_updates(reply, hints=None, question=""):
    """Extrait les fichiers proposés dans une réponse, y compris un .bat sans nom de bloc."""
    text = str(reply or "")
    if not text.strip():
        return []
    found = []
    seen = {}
    unnamed = []
    last = 0
    for match in _FENCE.finditer(text):
        path = _path_from_fence_info(match.group(1))
        if path is None:
            path = _path_before_fence(text[last:match.start()])
        last = match.end()
        content = _normalize_content(match.group(2))
        if path is None:
            unnamed.append((_fence_ext(match.group(1)), content))
            continue
        key = path.casefold()
        if key in seen:
            found[seen[key]] = {"path": path, "content": content}
        else:
            seen[key] = len(found)
            found.append({"path": path, "content": content})
    if found:
        return found
    hints = list(hints or [])
    for extra in paths_mentioned(question, text):
        if extra.casefold() not in {item.casefold() for item in hints}:
            hints.append(extra)
    if not unnamed:
        return []
    if len(unnamed) == 1:
        ext, content = unnamed[0]
        for hint in hints:
            if ext is None or Path(hint).suffix.lower() == ext:
                return [{"path": hint, "content": content}]
        return []
    leftover = list(hints)
    for ext, content in unnamed:
        match_hint = None
        for hint in leftover:
            if ext is None or Path(hint).suffix.lower() == ext:
                match_hint = hint
                break
        if match_hint is None:
            continue
        leftover = [item for item in leftover if item.casefold() != match_hint.casefold()]
        found.append({"path": match_hint, "content": content})
    return found


def extract_code_blocks(reply):
    blocks = []
    for match in _FENCE.finditer(str(reply or "")):
        info = (match.group(1) or "").strip()
        body = match.group(2)
        if not str(body or "").strip():
            continue
        blocks.append({
            "path": _path_from_fence_info(info) or "",
            "info": info,
            "content": body,
        })
    return blocks


def extract_apply_text(reply):
    """Prend le dernier bloc de code d'une réponse, ou la réponse entière."""
    text = str(reply or "").strip()
    if not text:
        return ""
    updates = extract_file_updates(text)
    if len(updates) == 1:
        return updates[0]["content"].rstrip("\n")
    parts = text.split("```")
    if len(parts) >= 3:
        block = parts[-2]
        if "\n" in block:
            first, rest = block.split("\n", 1)
            if first.strip() and " " not in first.strip() and len(first.strip()) < 20:
                block = rest
        return block.strip("\n")
    return text


def parse_chat_line(line):
    """Lit une ligne du flux Ollama. Retourne (texte, raisonnement, terminé)."""
    data = json.loads(line)
    message = data.get("message") or {}
    piece = message.get("content") or ""
    thinking = message.get("thinking") or data.get("thinking") or ""
    return piece, thinking, bool(data.get("done"))


def format_stream_stats(data):
    eval_count = int(data.get("eval_count") or 0)
    eval_ns = int(data.get("eval_duration") or 0)
    prompt_count = int(data.get("prompt_eval_count") or 0)
    seconds = int(data.get("total_duration") or 0) / 1e9
    tps = eval_count / (eval_ns / 1e9) if eval_ns else 0
    bits = []
    if prompt_count:
        bits.append(f"{prompt_count} ctx")
    if eval_count:
        bits.append(f"{eval_count} tok")
    if tps:
        bits.append(f"{tps:.1f} t/s")
    if seconds:
        bits.append(f"{seconds:.1f}s")
    return " · ".join(bits)


LAST_USAGE = {}


def stream_usage(data, provider="ollama"):
    """Tokens réellement consommés par un échange ({} si le fournisseur les cache)."""
    if not isinstance(data, dict):
        return {}
    if normalize_provider(provider) == "online":
        usage = data.get("usage") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        kind = "online"
    else:
        prompt = int(data.get("prompt_eval_count") or 0)
        completion = int(data.get("eval_count") or 0)
        kind = "local"
    total = prompt + completion
    if not total:
        return {}
    return {
        "provider": kind,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def remember_usage(data, provider="ollama"):
    """Mémorise la dernière consommation connue (un seul flux à la fois dans Lumio)."""
    usage = stream_usage(data, provider)
    if usage:
        LAST_USAGE.clear()
        LAST_USAGE.update(usage)
    return usage


def catalog_price(model, source=None):
    """Prix (prompt, completion) par million de tokens, d'après le cache du catalogue.

    Aucune requête réseau : retourne None si le modèle n'est pas déjà en cache.
    """
    target = str(model or "").strip().casefold()
    if not target:
        return None
    sources = [catalog_key(source)] if source else [item[0] for item in CATALOG_SOURCES]
    for item in sources:
        cached = _CATALOG_CACHE.get(item)
        if not cached:
            continue
        for row in cached[1]:
            if row["id"].casefold() == target:
                return {
                    "prompt": row.get("prompt") or 0.0,
                    "completion": row.get("completion") or 0.0,
                    "source": item,
                }
    return None


def read_chat_stream(payload, on_delta=None, on_thinking=None, on_open=None, cancel_event=None, timeout=600, on_stats=None):
    """Lit /api/chat en flux et s'arrête dès que cancel_event est levé."""
    LAST_USAGE.clear()
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    parts = []
    response = None
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
        if on_open is not None:
            on_open(response)
        if cancel_event is not None and cancel_event.is_set():
            return ""
        for raw in response:
            if cancel_event is not None and cancel_event.is_set():
                break
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                piece, thinking, done = parse_chat_line(line)
            except json.JSONDecodeError:
                continue
            if thinking and on_thinking is not None:
                on_thinking(thinking)
            if piece:
                parts.append(piece)
                if on_delta is not None:
                    on_delta(piece)
            if done:
                if on_stats is not None:
                    try:
                        data_line = json.loads(line)
                        remember_usage(data_line, "ollama")
                        label = format_stream_stats(data_line)
                        if label:
                            on_stats(label)
                    except Exception:
                        pass
                break
    finally:
        close_http(response)
    return "".join(parts)


def format_openai_stats(data):
    usage = data.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    tokens = int(usage.get("completion_tokens") or 0)
    bits = []
    if prompt:
        bits.append(f"{prompt} ctx")
    if tokens:
        bits.append(f"{tokens} tok")
    return " · ".join(bits)


def parse_openai_line(line):
    raw = str(line or "").strip()
    if raw.startswith("data:"):
        raw = raw[5:].strip()
    if not raw or raw == "[DONE]":
        return "", "", raw == "[DONE]", None
    data = json.loads(raw)
    choice = (data.get("choices") or [{}])[0]
    delta = choice.get("delta") or {}
    piece = delta.get("content") or choice.get("text") or ""
    thinking = (
        delta.get("reasoning_content")
        or delta.get("reasoning")
        or ""
    )
    done = bool(choice.get("finish_reason")) or data.get("done") is True
    return piece, thinking, done, data


def read_openai_stream(url, payload, on_delta=None, on_thinking=None, on_open=None, cancel_event=None, timeout=600, on_stats=None, extra_headers=None):
    LAST_USAGE.clear()
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    parts = []
    response = None
    last_stats = None
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
        if on_open is not None:
            on_open(response)
        if cancel_event is not None and cancel_event.is_set():
            return ""
        buffer = ""
        for raw in response:
            if cancel_event is not None and cancel_event.is_set():
                break
            buffer += raw.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    piece, thinking, done, data = parse_openai_line(line)
                except json.JSONDecodeError:
                    continue
                if data and data.get("usage"):
                    remember_usage(data, "online")
                    last_stats = format_openai_stats(data)
                if thinking and on_thinking is not None:
                    on_thinking(thinking)
                if piece:
                    parts.append(piece)
                    if on_delta is not None:
                        on_delta(piece)
                if done and line.endswith("[DONE]"):
                    break
        if on_stats is not None and last_stats:
            on_stats(last_stats)
    finally:
        close_http(response)
    return "".join(parts)


def parse_pgpt_event(event_name, data):
    if not isinstance(data, dict):
        return "", "", False
    kind = str(event_name or data.get("type") or "")
    delta = data.get("delta") if isinstance(data.get("delta"), dict) else {}
    piece = ""
    thinking = ""
    dtype = str(delta.get("type") or "")
    if dtype in {"text_delta", "text"} or "text" in delta:
        if dtype not in {"thinking_delta", "reasoning_delta"}:
            piece = str(delta.get("text") or "")
    if dtype in {"thinking_delta", "reasoning_delta"} or delta.get("thinking"):
        thinking = str(delta.get("thinking") or delta.get("text") or "")
    done = kind in {"message_stop", "error"} or data.get("type") in {"message_stop", "error"}
    return piece, thinking, done


def read_pgpt_stream(payload, on_delta=None, on_thinking=None, on_open=None, cancel_event=None, timeout=600, on_stats=None):
    request = urllib.request.Request(
        f"{pgpt_base()}/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    parts = []
    response = None
    last_stats = ""
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
        if on_open is not None:
            on_open(response)
        if cancel_event is not None and cancel_event.is_set():
            return ""
        buffer = ""
        event_name = ""
        for raw in response:
            if cancel_event is not None and cancel_event.is_set():
                break
            buffer += raw.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    event_name = ""
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                    continue
                if line.startswith("data:"):
                    payload_line = line[5:].strip()
                else:
                    payload_line = line
                if not payload_line or payload_line == "[DONE]":
                    continue
                try:
                    data = json.loads(payload_line)
                except json.JSONDecodeError:
                    continue
                usage = data.get("usage") if isinstance(data, dict) else None
                if usage:
                    bits = []
                    inn = int(usage.get("input_tokens") or 0)
                    out = int(usage.get("output_tokens") or 0)
                    if inn:
                        bits.append(f"{inn} ctx")
                    if out:
                        bits.append(f"{out} tok")
                    if bits:
                        last_stats = " · ".join(bits)
                piece, thinking, done = parse_pgpt_event(event_name, data)
                if thinking and on_thinking is not None:
                    on_thinking(thinking)
                if piece:
                    parts.append(piece)
                    if on_delta is not None:
                        on_delta(piece)
                if done:
                    break
        if on_stats is not None and last_stats:
            on_stats(last_stats)
    finally:
        close_http(response)
    return "".join(parts)


def _pgpt_chat_payload(model, question, context, history=None, attach_files=True):
    messages, user = _chat_messages(question, context, history, attach_files)
    messages.append({"role": "user", "content": user})
    system_parts = []
    rows = []
    for item in messages:
        role = str((item or {}).get("role") or "")
        content = (item or {}).get("content")
        if isinstance(content, list):
            bits = []
            for part in content:
                if isinstance(part, dict) and part.get("text"):
                    bits.append(str(part.get("text")))
                elif isinstance(part, str):
                    bits.append(part)
            content = "\n".join(bits)
        text = str(content or "")
        if role == "system":
            if text.strip():
                system_parts.append(text)
            continue
        if role in {"user", "assistant"} and text:
            rows.append({"role": role, "content": text})
    payload = {
        "model": model,
        "messages": rows,
        "max_tokens": 8192,
        "stream": True,
        "temperature": 0.3,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    return payload


def ask_pgpt(model, question, context, history=None, on_delta=None, on_open=None, cancel_event=None, attach_files=True, images=None, on_notice=None, on_thinking=None, on_stats=None):
    images = [item for item in (images or []) if item]
    if not str(model).strip():
        raise RuntimeError("Aucun modèle PrivateGPT sélectionné")
    if not str(question).strip() and not images:
        raise RuntimeError("Question vide")
    if not pgpt_up():
        raise RuntimeError(
            "PrivateGPT n'est pas lancé. Ouvrez le workbench "
            "(http://127.0.0.1:8080/ui/), attendez qu'il soit prêt, puis cliquez sur Actualiser."
        )
    if images:
        if on_notice is not None:
            on_notice("PrivateGPT : la capture n'est pas envoyée pour l'instant, seul le texte l'est.")
    payload = _pgpt_chat_payload(model, question, context, history, attach_files)
    try:
        return read_pgpt_stream(
            payload,
            on_delta=on_delta,
            on_thinking=on_thinking,
            on_open=on_open,
            cancel_event=cancel_event,
            on_stats=on_stats,
        )
    except Exception as error:
        if cancel_event is not None and cancel_event.is_set():
            return ""
        if isinstance(error, urllib.error.URLError):
            raise RuntimeError(explain_backend_error(error, provider="pgpt")) from error
        raise


_PYTHON_ENV_CACHE = {"text": "", "at": 0.0}

_ENV_PACKAGES = (
    ("tkinter", "tkinter", "tkinter (fenêtres, bibliothèque standard)"),
    ("customtkinter", "customtkinter", "customtkinter (interfaces modernes)"),
    ("PIL", "pillow", "Pillow (images)"),
    ("tkinterdnd2", "tkinterdnd2", "tkinterdnd2 (glisser-déposer)"),
    ("darkdetect", "darkdetect", "darkdetect"),
    ("ttkbootstrap", "ttkbootstrap", "ttkbootstrap"),
    ("pygame", "pygame", "pygame"),
    ("PyQt6", "PyQt6", "PyQt6"),
    ("PyQt5", "PyQt5", "PyQt5"),
    ("PySide6", "PySide6", "PySide6"),
    ("wx", "wxPython", "wxPython"),
    ("dearpygui", "dearpygui", "Dear PyGui"),
    ("openpyxl", "openpyxl", "openpyxl (Excel .xlsx)"),
    ("xlsxwriter", "xlsxwriter", "xlsxwriter"),
    ("pandas", "pandas", "pandas"),
    ("requests", "requests", "requests"),
)


def _module_present(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _package_version(dist_name):
    try:
        return importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return ""


def python_env_prompt():
    """État réel du Python qui fait tourner Lumio — à coller dans le prompt."""
    now = time.time()
    cached = _PYTHON_ENV_CACHE["text"]
    if cached and now - _PYTHON_ENV_CACHE["at"] < 120:
        return cached
    present = []
    missing = []
    for module, dist, label in _ENV_PACKAGES:
        if _module_present(module):
            ver = _package_version(dist)
            present.append(f"{label} {ver}".strip() if ver else label)
        else:
            missing.append(label.split(" (")[0])
    preferred = "customtkinter" if _module_present("customtkinter") else (
        "tkinter" if _module_present("tkinter") else "aucune GUI détectée"
    )
    lines = [
        "Environnement Python RÉEL de cette machine (vérifié par Lumio, pas une hypothèse) :",
        f"- Exécutable : {sys.executable}",
        f"- Version : {sys.version.split()[0]} ({sys.platform})",
        f"- GUI à utiliser : {preferred}",
        f"- Présentes : {', '.join(present) if present else '(aucune de la liste)'}",
    ]
    if missing:
        lines.append(f"- Absentes : {', '.join(missing)}")
    lines.append(
        "Interdit : dire que tu ne peux pas vérifier les bibliothèques, "
        "demander pip, ou proposer une lib absente. "
        "Utilise uniquement celles qui sont présentes."
    )
    text = "\n".join(lines)
    _PYTHON_ENV_CACHE["text"] = text
    _PYTHON_ENV_CACHE["at"] = now
    return text


GUI_BUILD_PROMPT = (
    "IMPORTANT — application graphique, code réel uniquement :\n"
    "Tu CONNAIS les bibliothèques (bloc Environnement Python). "
    "Interdit : raconter une histoire, dire que tu as « testé », "
    "« je ne peux pas vérifier », suggestions à la place du code, "
    "copier un .pyw cassé déjà dans le dossier.\n"
    "Réécris FROM SCRATCH une fenêtre qui s'ouvre vraiment :\n"
    "1) un fichier .pyw, API customtkinter 6 STRICTE :\n"
    "   import customtkinter as ctk\n"
    "   ctk.set_appearance_mode('dark')\n"
    "   app = ctk.CTk()\n"
    "   ctk.CTkLabel(..., font=ctk.CTkFont(size=18, weight='bold'))\n"
    "   PAS text_font, PAS hover=True sur CTkFrame, PAS customtk, "
    "   PAS dark_mode.load_theme(), PAS withdraw(), PAS fenêtre 0x0.\n"
    "   if __name__ == '__main__': app.mainloop()\n"
    "2) un fichier .bat SANS pythonw et SANS start (pythonw cache les erreurs) :\n"
    "   @echo off\n"
    "   cd /d \"%~dp0\"\n"
    "   python \"%~dp0nom.pyw\"\n"
    "   if errorlevel 1 pause\n"
    "Chaque fichier ENTIER dans ```fichier:nom.ext```. Rien d'autre."
)

GUI_RETRY_PROMPT = (
    "STOP. Ta réponse précédente n'était pas un logiciel (histoire ou code inventé). "
    "Réécris FROM SCRATCH, sans te souvenir du code cassé. "
    "customtkinter 6 + mainloop(), plus un .bat : "
    "cd /d \"%~dp0\" puis python \"%~dp0app.pyw\" puis if errorlevel 1 pause. "
    "PAS pythonw, PAS start, PAS withdraw, PAS d'histoire. "
    "Uniquement des blocs ```fichier:nom.ext```."
)


WRITE_FILES_PROMPT = (
    "Tes explications sont notées. Applique maintenant les modifications pour de vrai. "
    "Écris uniquement les fichiers dont le contenu CHANGE, chacun ENTIER et déjà modifié, "
    "dans un bloc nommé :\n"
    "```fichier:chemin/relatif.ext\n"
    "contenu final complet\n"
    "```\n"
    "N'inclus pas un fichier identique à l'actuel. Aucune explication autour."
)


CONTINUE_PROMPT = (
    "Ta réponse précédente a été coupée. "
    "Reprend exactement au mot ou au caractère suivant, "
    "sans préambule et sans répéter ce qui est déjà écrit."
)


def looks_cut_off(text):
    """Heuristique : la réponse s'arrête au milieu d'un code ou d'une phrase."""
    body = str(text or "").rstrip()
    if len(body) < 40:
        return False
    if body.count("```") % 2 == 1:
        return True
    return body.endswith(("...", "…", ",", "=", "(", "[", "{", "\\", ":"))


def _chat_messages(question, context, history=None, attach_files=True):
    messages = [
        {
            "role": "system",
            "content": (
                "Tu réponds en français à partir des fichiers fournis, des captures et de la conversation. "
                "Une liste des fichiers du dossier est fournie ; seuls les fichiers pertinents "
                "sont donnés en entier. Si un autre fichier est nécessaire, demande-le par son nom. "
                "Si une capture est jointe, examine-la : elle illustre le bug ou l'écran à commenter. "
                "Si l'information n'y figure pas, dis-le clairement. "
                "Lumio te donne l'environnement Python réel de cette machine. "
                "Ne dis jamais que tu ne peux pas vérifier les bibliothèques ou la configuration. "
                "Quand tu modifies un ou plusieurs fichiers, donne chaque fichier ENTIER "
                "et déjà modifié dans un bloc nommé ainsi :\n"
                "```fichier:chemin/relatif.ext\n"
                "contenu final\n"
                "```\n"
                "Le contenu doit différer du fichier actuel. "
                "N'inclus pas un fichier inchangé. Un bloc par fichier. "
                "Si on te demande de continuer, reprends au caractère suivant sans rien répéter. "
                "Si on colle une fenêtre DOS ou un terminal, respecte sa mise en page "
                "(espaces, colonnes, retours à la ligne)."
            ),
        }
    ]
    messages.extend(shrink_history(history))
    laid_out = str(question or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    env = python_env_prompt()
    if attach_files:
        user = (
            f"{env}\n\nFichiers :\n{context or '(aucun contenu)'}"
            f"\n\nQuestion :\n{laid_out or '(voir la capture jointe)'}"
        )
    else:
        user = f"{env}\n\n{laid_out or '(voir la capture jointe)'}"
    if wants_gui(laid_out) or wants_gui(context):
        user += "\n\n" + GUI_BUILD_PROMPT
    return messages, user


def _ask_openai(completions_url, model, question, context, history=None, on_delta=None, on_open=None, cancel_event=None, attach_files=True, images=None, on_notice=None, on_thinking=None, on_stats=None, extra_headers=None, provider="online"):
    images = [item for item in (images or []) if item]
    label = provider_label(provider)
    if not str(model).strip():
        raise RuntimeError(f"Aucun modèle {label} sélectionné")
    if not str(question).strip() and not images:
        raise RuntimeError("Question vide")
    messages, user = _chat_messages(question, context, history, attach_files)
    if images and not supports_vision(model):
        if on_notice is not None:
            on_notice(f"Ce modèle {label} ne lit pas les images. La capture n'est pas envoyée.")
        images = []
    elif images:
        user += "\n\nUne ou plusieurs captures sont jointes pour illustrer le problème."
    if images:
        content = [{"type": "text", "text": user}]
        for item in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{item}"},
            })
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": user})
    payload = {"model": model, "stream": True, "messages": messages, "temperature": 0.3,
               "stream_options": {"include_usage": True}}
    try:
        return read_openai_stream(
            completions_url,
            payload,
            on_delta=on_delta,
            on_thinking=on_thinking,
            on_open=on_open,
            cancel_event=cancel_event,
            on_stats=on_stats,
            extra_headers=extra_headers,
        )
    except Exception as error:
        if cancel_event is not None and cancel_event.is_set():
            return ""
        if isinstance(error, urllib.error.URLError):
            raise RuntimeError(explain_backend_error(error, images=bool(images), provider=provider)) from error
        raise


def ask_online(model, question, context, history=None, on_delta=None, on_open=None, cancel_event=None, attach_files=True, images=None, on_notice=None, on_progress=None, on_thinking=None, on_stats=None):
    if not str(_ONLINE.get("key") or "").strip():
        raise RuntimeError("Aucune clé API. Cliquez sur API pour coller votre clé.")
    if not online_up():
        raise RuntimeError(
            "Le service en ligne n'est pas joignable.\n"
            "Vérifiez l'URL, la clé API et votre connexion Internet."
        )
    return _ask_openai(
        f"{openai_base()}/chat/completions",
        model, question, context, history=history, on_delta=on_delta, on_open=on_open,
        cancel_event=cancel_event, attach_files=attach_files, images=images,
        on_notice=on_notice, on_thinking=on_thinking, on_stats=on_stats,
        extra_headers=openai_headers(),
        provider="online",
    )


def ask_ollama(model, question, context, history=None, on_delta=None, on_open=None, cancel_event=None, attach_files=True, images=None, on_notice=None, on_progress=None, on_thinking=None, on_stats=None, provider="ollama"):
    """Envoie une question au modèle local ou en ligne et renvoie la réponse au fil de l'eau."""
    key = normalize_provider(provider)
    if key == "online":
        return ask_online(
            model, question, context, history=history, on_delta=on_delta, on_open=on_open,
            cancel_event=cancel_event, attach_files=attach_files, images=images,
            on_notice=on_notice, on_progress=on_progress, on_thinking=on_thinking, on_stats=on_stats,
        )
    if key == "pgpt":
        return ask_pgpt(
            model, question, context, history=history, on_delta=on_delta, on_open=on_open,
            cancel_event=cancel_event, attach_files=attach_files, images=images,
            on_notice=on_notice, on_thinking=on_thinking, on_stats=on_stats,
        )
    images = [item for item in (images or []) if item]
    if not str(model).strip():
        raise RuntimeError("Aucun modèle Ollama sélectionné")
    if not str(question).strip() and not images:
        raise RuntimeError("Question vide")
    messages, user = _chat_messages(question, context, history, attach_files)
    if images:
        user += "\n\nUne ou plusieurs captures sont jointes pour illustrer le problème."
    if images and not supports_vision(model):
        helper = ensure_vision_helper(
            on_notice=on_notice,
            on_progress=on_progress or on_notice,
            cancel_event=cancel_event,
            on_open=on_open,
        )
        if helper:
            if on_notice is not None:
                on_notice(f"Lecture de la capture par {helper}, puis envoi à {model}.")
            try:
                description = describe_images(images, helper, cancel_event=cancel_event, on_open=on_open)
            except Exception as error:
                if cancel_event is not None and cancel_event.is_set():
                    return ""
                if isinstance(error, urllib.error.URLError):
                    raise RuntimeError(explain_ollama_error(error, images=True)) from error
                raise
            if description:
                user += f"\n\nDescription de la capture :\n{description}"
            images = []
        else:
            if cancel_event is not None and cancel_event.is_set():
                return ""
            raise RuntimeError(
                "Pour lire les captures sans changer votre modèle, installez un lecteur d'images :\n"
                "ollama pull moondream"
            )
    message = {"role": "user", "content": user}
    if images:
        message["images"] = images
    messages.append(message)
    payload = {"model": model, "stream": True, "messages": messages, "keep_alive": "30m"}
    ctx = model_num_ctx(model)
    if ctx:
        # Reserve for this conversation and the answer, rather than always
        # allocating the model's maximum window (which can exhaust GPU RAM).
        input_bytes = sum(len(str(item.get("content", "")).encode("utf-8")) + 256 for item in messages)
        needed = max(4096, ((input_bytes + 4096 + 1023) // 1024) * 1024)
        payload["options"] = {"num_ctx": ctx if images else min(ctx, needed)}

    if not ollama_up():
        raise RuntimeError(
            "Ollama n'est pas lancé. Ouvrez Ollama, attendez qu'il soit prêt, puis cliquez sur Modèles."
        )
    try:
        return read_chat_stream(
            payload,
            on_delta=on_delta,
            on_thinking=on_thinking,
            on_open=on_open,
            cancel_event=cancel_event,
            on_stats=on_stats,
        )
    except Exception as error:
        if cancel_event is not None and cancel_event.is_set():
            return ""
        if isinstance(error, urllib.error.URLError):
            raise RuntimeError(explain_ollama_error(error, images=bool(images))) from error
        raise


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Accès aux dossiers.")
    parser.add_argument("folder_path", type=Path, help="Chemin du dossier cible")
    parser.add_argument("--create", help="Créer/modifier un fichier avec ce contenu")
    args = parser.parse_args()

    if args.create:
        edit_folder(args.folder_path, args.create)
    else:
        edit_folder(args.folder_path)
