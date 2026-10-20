"""Exécution vérifiée de commandes dans le dossier de travail.

Principes :

* **liste blanche** : seules les commandes dont le préfixe est autorisé peuvent
  tourner (configurable), et les motifs destructeurs sont toujours refusés ;
* **pas de shell libre** : on refuse les métacaractères (`&&`, `|`, `>`, `<`,
  `;`, backtick) qui permettraient d'enchaîner une commande non autorisée ;
* **sortie capturée** (stdout, stderr, code retour, durée) pour être réinjectée
  dans le contexte du modèle : c'est ce qui referme la boucle.
"""

import locale
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

DEFAULT_ALLOWLIST = (
    "python ",
    "python3 ",
    "py ",
    "pytest",
    "python -m pytest",
    "python -m py_compile",
    "npm test",
    "npm run",
    "node ",
    "git status",
    "git diff",
    "git log",
    "bash ",
    "sh ",
)

DEFAULT_DENY = (
    "rm -rf", "rm -fr", "rmdir", "del /", "del /s", "rd /s", "format ",
    "shutdown", "reg delete", "mkfs", "diskpart", "cipher /w",
    ":(){", "curl ", "wget ", "invoke-webrequest", "powershell ",
)

SHELL_META = re.compile(r"[&|<>;`]|\$\(")
TIMEOUT_MAX = 600


def bare_command(command: str) -> str:
    """Commande sans ses segments entre guillemets (pour l'analyse de sûreté).

    python -c "import sys; sys.exit(3)" doit rester autorisé : le point-virgule
    est à l'intérieur d'un argument. En revanche python a.py; del x reste
    refusé, car le ; est alors hors guillemets.
    """
    return re.sub(r'"[^"]*"|\x27[^\x27]*\x27', "", str(command or ""))


def command_is_safe(command: str, allowlist=None, denylist=None):
    """(autorise, raison) pour une commande donnée."""
    text = str(command or "").strip()
    if not text:
        return False, "commande vide"
    if SHELL_META.search(bare_command(text)):
        return False, "caractère d'enchaînement refusé (&&, |, >, ;, …)"
    lowered = text.casefold()
    for pattern in denylist or DEFAULT_DENY:
        if pattern.casefold() in lowered:
            return False, f"motif interdit : « {pattern} »"
    allowed = tuple(allowlist) if allowlist else DEFAULT_ALLOWLIST
    if not allowed:
        return False, "aucune commande autorisée"
    for prefix in allowed:
        if lowered.startswith(str(prefix).casefold()):
            return True, f"autorisé par « {prefix} »"
    return False, "commande absente de la liste blanche"


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", locale.getpreferredencoding(False), "cp1252"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _is_python(command: str) -> bool:
    """Vrai si la commande lance un interpréteur Python."""
    first = str(bare_command(command) or "").strip().split(" ")[0].casefold()
    return first in {"python", "python3", "python.exe", "py", "pytest"} or first.startswith("python")


def _purge_pycache(root: Path, limit: int = 50) -> int:
    """Supprime les __pycache__ du dossier avant une vérification Python.

    Sur un partage réseau, deux versions de même taille écrites dans la même
    seconde partagent leur date : Python réutilise alors l'ancien .pyc et le
    test valide un code qui n'existe plus. On repart donc d'un cache propre.
    """
    removed = 0
    try:
        for path in root.rglob("__pycache__"):
            if removed >= limit:
                break
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    except OSError:
        pass
    return removed


def run_command(command: str, folder, timeout: int = 300, allowlist=None, denylist=None):
    """Exécute une commande autorisée et renvoie son résultat détaillé."""
    ok, reason = command_is_safe(command, allowlist, denylist)
    if not ok:
        return {
            "ok": False, "allowed": False, "reason": reason, "command": command,
            "exit": None, "stdout": "", "stderr": "", "duration": 0.0,
        }

    root = Path(folder).resolve()
    if not root.is_dir():
        return {
            "ok": False, "allowed": True, "reason": "dossier introuvable", "command": command,
            "exit": None, "stdout": "", "stderr": "", "duration": 0.0,
        }

    if _is_python(command):
        _purge_pycache(root)

    limit = max(5, min(int(timeout or 300), TIMEOUT_MAX))
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            cwd=str(root),
            shell=True,
            capture_output=True,
            timeout=limit,
        )
        duration = time.time() - started
        return {
            "ok": completed.returncode == 0,
            "allowed": True,
            "reason": reason,
            "command": command,
            "exit": completed.returncode,
            "stdout": _decode(completed.stdout or b""),
            "stderr": _decode(completed.stderr or b""),
            "duration": duration,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "allowed": True, "reason": f"délai dépassé ({limit} s)",
            "command": command, "exit": None, "stdout": "", "stderr": "", "duration": time.time() - started,
        }
    except OSError as error:
        return {
            "ok": False, "allowed": True, "reason": f"exécution impossible : {error}",
            "command": command, "exit": None, "stdout": "", "stderr": "", "duration": time.time() - started,
        }


def detect_commands(folder, changed=None):
    """Commandes de vérification candidates, de la plus fiable à la plus simple."""
    root = Path(folder)
    found = []
    if (root / "tests" / "run_tests.py").is_file():
        found.append("python tests/run_tests.py")
    if (root / "tests").is_dir() and (root / "tests" / "__init__.py").is_file():
        found.append("python -m pytest -q")
    if (root / "pytest.ini").is_file() or (root / "conftest.py").is_file():
        found.append("python -m pytest -q")
    if (root / "package.json").is_file():
        found.append("npm test")
    for name in changed or []:
        if str(name).endswith(".py"):
            found.append(f'python -m py_compile "{name}"')
            break
    if not found:
        candidates = sorted(root.glob("*.py"))
        if candidates:
            found.append(f'python -m py_compile "{candidates[0].name}"')
    unique = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique


def format_result(result, max_lines: int = 40) -> str:
    """Résumé lisible d'un résultat d'exécution (pour l'interface et le modèle)."""
    if result.get("allowed") is False:
        return f"Commande refusée : {result.get('reason')}\n$ {result.get('command')}"
    bits = [f"$ {result.get('command')}"]
    exit_code = result.get("exit")
    if exit_code is None:
        bits.append(f"(échec : {result.get('reason')})")
    else:
        bits.append(f"code retour : {exit_code}")
    stdout = str(result.get("stdout") or "").strip()
    stderr = str(result.get("stderr") or "").strip()
    for label, text in (("sortie", stdout), ("erreurs", stderr)):
        if not text:
            continue
        lines = text.splitlines()
        head = lines[:max_lines]
        bits.append(f"{label} :")
        bits.extend(head)
        if len(lines) > len(head):
            bits.append(f"… {len(lines) - len(head)} ligne(s) supplémentaires")
    if not stdout and not stderr:
        bits.append("(aucune sortie)")
    return "\n".join(bits)


def failed(result) -> bool:
    """Vrai si la commande n'a pas réussi (ou n'a pas pu tourner)."""
    return not result.get("ok")
