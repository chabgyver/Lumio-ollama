import base64
import io
import json
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

try:
    import customtkinter as ctk
except ImportError:
    _root = tk.Tk()
    _root.withdraw()
    messagebox.showerror(
        "Interface",
        "L'interface moderne n'est pas installée.\n\n"
        "Double-cliquez sur Lancer.bat, ou exécutez :\n"
        "python -m pip install customtkinter",
    )
    raise SystemExit(1)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None

import ollama_fs_access
import lumio_cost
import lumio_rank
import lumio_router
import lumio_search
import lumio_session
import lumio_tree
import lumio_snapshots
import lumio_tools
import lumio_verify

if TkinterDnD is not None:
    class _WindowBase(ctk.CTk, TkinterDnD.DnDWrapper):
        pass
else:
    _WindowBase = ctk.CTk

APP_DIR = Path(__file__).resolve().parent
APP_NAME = "Lumio"
APP_TAGLINE = "Compagnon d'Ollama"
# La session vit dans %APPDATA%\Lumio (hors du partage web) ; l'ancien fichier
# du dossier applicatif est migré au premier lancement.
SESSION_PATH = lumio_session.session_path()
LEGACY_SESSION_PATH = APP_DIR / "session.json"
LOG_PATH = APP_DIR / "lumio_error.log"
ICON_PNG = APP_DIR / "assets" / "lumio-icon.png"
ICON_ICO = APP_DIR / "assets" / "lumio.ico"
TITLE_FONT = APP_DIR / "assets" / "InstrumentSerif-Regular.ttf"
SAVE_DELAY_MS = 1500

# Réglages par défaut (persistés dans la session sous la clé « settings »).
DEFAULT_SETTINGS = {
    "verify_command": "",
    "verify_auto": False,
    "auto_apply": False,
    "verify_max_rounds": 2,
    "allowlist": list(lumio_verify.DEFAULT_ALLOWLIST),
    "tools_enabled": True,
    "tools_max_steps": 2,
    "router_enabled": False,
    "router_policy": "auto",
    "router_ceiling": 0.05,
    "turn_ceiling": 0.10,
    "auto_apply_max_files": 2,
    "auto_apply_max_bytes": 60000,
    "catalog_sort": "recommande",
}


def report_error(exc_type, exc, tb) -> None:
    """Journalise l'erreur dans un fichier et la montre à l'utilisateur.

    Lancé par pythonw, Lumio n'a pas de console : sans ça, un plantage se
    traduit par une fenêtre qui ne s'ouvre pas, sans la moindre explication.
    """
    message = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        LOG_PATH.write_text(message, encoding="utf-8")
    except OSError:
        pass
    try:
        from tkinter import messagebox

        messagebox.showerror(
            APP_NAME,
            f"{APP_NAME} a rencontré une erreur :\n\n{message.strip().splitlines()[-1] if message.strip() else 'inconnue'}\n\n"
            f"Détails complets dans :\n{LOG_PATH}",
        )
    except Exception:
        pass


sys.excepthook = report_error


def _window_handle(title: str):
    """Handle Windows de la fenêtre portant ce titre (ou 0)."""
    try:
        import ctypes

        return int(ctypes.windll.user32.FindWindowW(None, title) or 0)
    except Exception:
        return 0


def focus_existing_window(title: str) -> bool:
    """Ramène au premier plan une instance déjà ouverte."""
    handle = _window_handle(title)
    if not handle:
        return False
    try:
        import ctypes

        SW_RESTORE = 9
        ctypes.windll.user32.ShowWindowW(handle, SW_RESTORE)
        ctypes.windll.user32.SetForegroundWindow(handle)
        return True
    except Exception:
        return False


def acquire_single_instance(name: str = "Local\\Lumio.SingleInstance"):
    """Mutex Windows : True si c'est la première instance, False sinon.

    Le handle est conservé jusqu'à la fin du processus (sinon le mutex est
    libéré et une deuxième instance démarre).
    """
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True, None
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return False, handle
        return True, handle
    except Exception:
        return True, None

MAX_HISTORY = 8
MAX_TRANSCRIPT = 100
MAX_RECENTS = 6
MAX_CHATS = 40
DEFAULT_EXTENSIONS = (
    ".txt, .md, .py, .js, .ts, .jsx, .tsx, .html, .css, .scss, .json, "
    ".bat, .cmd, .ps1, .sh, .java, .c, .cpp, .h, .cs, .php, "
    ".go, .rs, .sql, .yml, .yaml, .xml, .ini, .cfg, .log, .csv, "
    ".vue, .svelte, .kt, .env, .toml, .svg"
)
OLD_EXTENSIONS = ".txt, .md, .py, .json, .log"
PREV_DEFAULT_EXTENSIONS = (
    ".txt, .md, .py, .js, .ts, .jsx, .tsx, .html, .css, .json, "
    ".bat, .cmd, .ps1, .sh, .java, .c, .cpp, .h, .cs, .php, "
    ".go, .rs, .sql, .yml, .yaml, .xml, .ini, .cfg, .log, .csv"
)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_ATTACHMENTS = 4

THEME_ORDER = ("dark", "dusk", "light")
THEMES = {
    "dark": {
        "label": "Sombre",
        "icon": "☾",
        "ctk": "dark",
        "bg": "#100e0c",
        "card": "#1b1916",
        "raised": "#26231e",
        "line": "#3c352c",
        "text": "#f4efe6",
        "muted": "#a39686",
        "accent": "#e4a15a",
        "accent_hover": "#f2b56e",
        "ink": "#1a130c",
        "sage": "#8eae86",
        "danger": "#c96b56",
        "danger_hover": "#4a2e28",
        "danger_btn": "#26231e",
        "danger_text": "#e8b0a4",
        "btn": "#26231e",
        "btn_text": "#f4efe6",
        "btn_hover": "#3d3428",
        "selected": "#3d3428",
    },
    "dusk": {
        "label": "Ambre",
        "icon": "◐",
        "ctk": "dark",
        "bg": "#2b241c",
        "card": "#3b3228",
        "raised": "#4c4034",
        "line": "#6d5c4a",
        "text": "#f6eedf",
        "muted": "#cbb79a",
        "accent": "#e8b15f",
        "accent_hover": "#f3c57a",
        "ink": "#1a130c",
        "sage": "#9db88a",
        "danger": "#d27a66",
        "danger_hover": "#5a3830",
        "danger_btn": "#4c4034",
        "danger_text": "#f0c2b6",
        "btn": "#4c4034",
        "btn_text": "#f6eedf",
        "btn_hover": "#5d4c38",
        "selected": "#5d4c38",
    },
    "light": {
        "label": "Clair",
        "icon": "☀",
        "ctk": "light",
        "bg": "#f3eee6",
        "card": "#fffaf3",
        "raised": "#ebe3d4",
        "line": "#d3c6b2",
        "text": "#2a2218",
        "muted": "#4d4338",
        "accent": "#c4842e",
        "accent_hover": "#d4963f",
        "ink": "#1a130c",
        "sage": "#2f5a2c",
        "danger": "#8f3a2c",
        "danger_hover": "#d7a498",
        "danger_btn": "#f0cfc6",
        "danger_text": "#6a2418",
        "btn": "#d7c7a8",
        "btn_text": "#1a130c",
        "btn_hover": "#c4b088",
        "selected": "#ead4b0",
    },
}


class FileSystemWrapper(_WindowBase):
    def __init__(self):
        saved = _read_session()
        self.theme_name = saved.get("theme") if saved.get("theme") in THEMES else "dark"
        self._themed = []
        if TkinterDnD is not None:
            super().__init__(fg_color=self.C("bg"))
            self.TkdndVersion = TkinterDnD._require(self)
        else:
            super().__init__(fg_color=self.C("bg"))
        self.title(APP_NAME)
        self.geometry(saved.get("geometry") or "1180x780")
        self.minsize(720, 520)
        self.resizable(True, True)
        self._apply_icon()
        self.target_folder = saved.get("folder") or str(APP_DIR)
        self.visible_files = []
        self.excluded = {Path(item).as_posix() for item in (saved.get("excluded") or []) if item}
        last_file = str(saved.get("selected_file") or "").strip()
        self.loaded_path = Path(last_file) if last_file else None
        self.loaded_text = ""
        self.selected_key = Path(last_file).as_posix() if last_file else None
        self.history = []
        self.last_reply = ""
        self._transcript = []
        self._live_box = None
        self._last_assistant_label = None
        self._continuing = False
        self._streaming = False
        self._cancel = threading.Event()
        self._thinking_bits = ""
        self._stream_response = None
        self._pending_question = ""
        self._last_sent_files = []
        self._recent_files = [
            str(item) for item in (saved.get("recent_files") or []) if item
        ][:8]
        self._send_preview_job = None
        self._chats = _normalize_chats(saved)
        self._chat_id = saved.get("chat_id") or (self._chats[0]["id"] if self._chats else _fresh_chat_id())
        self._chat_title = ""
        for item in self._chats:
            if item.get("id") == self._chat_id:
                self._chat_title = str(item.get("title") or "")
                break
        self._chat_guard = False
        self._write_backup = []
        self._think_box = None
        self._think_toggle = None
        self._think_open = True
        self._think_shown = False
        self._pinned = {
            Path(item).as_posix() for item in (saved.get("pinned") or []) if item
        }
        self._pin_buttons = {}
        self._question_stack = [str(item) for item in (saved.get("question_stack") or []) if item][-20:]
        self._stream_t0 = 0
        self._stream_chars = 0
        self._pending_updates = {}
        self._writing_files = False
        self._bodies_requested = False
        self._attachments = []
        self._bubble_pictures = []
        self._events = queue.Queue()
        self._bg_events = queue.Queue()
        self._save_job = None
        self._closing = False
        self._session_tokens = 0
        self._session_cost = 0.0
        self._last_cost_text = ""
        self.settings = dict(DEFAULT_SETTINGS)
        self.settings.update(saved.get("settings") or {})
        self._tool_steps = 0
        self._verify_rounds = 0
        self._verify_job = None
        self._auto_followup = False
        self._turn_cost = 0.0
        self._collapsed_dirs = {
            str(item) for item in (saved.get("collapsed_dirs") or []) if str(item).strip()
        }
        self._rows = {}
        self._checks = {}
        self._file_meta = {}
        self._disk_size = {}
        self._disk_mtime = {}
        self._wrap_labels = []
        self._chat_follow = True
        self._last_wrap = 0
        self._scroll_job = None
        self._toast = None
        self._sash = saved.get("sash")
        self._sash_v = saved.get("sash_v")
        self._saved_draft = str(saved.get("draft") or "")
        self._pinned_only = False
        self._diff_acceptors = []
        self._diff_ignores = []
        self._folder_stamp = None

        saved_ext = (saved.get("extensions") or "").strip()
        if not saved_ext or saved_ext in {OLD_EXTENSIONS, PREV_DEFAULT_EXTENSIONS}:
            saved_ext = DEFAULT_EXTENSIONS
        else:
            # Filtre abîmé (liste tronquée, entrée sans point…) : on répare.
            parts = [item.strip() for item in saved_ext.split(",") if item.strip()]
            valid = [item for item in parts if item.startswith(".") and len(item) > 1]
            if len(valid) < 3:
                saved_ext = DEFAULT_EXTENSIONS
            elif len(valid) != len(parts):
                saved_ext = ", ".join(valid)
        self.ext_var = tk.StringVar(value=saved_ext)
        self.max_var = tk.StringVar(value=str(saved.get("max_files") or "200"))
        self.provider = ollama_fs_access.normalize_provider(saved.get("provider") or "ollama")
        self._model_by_provider = {
            "ollama": str(saved.get("ollama_model") or (saved.get("model") if self.provider == "ollama" else "") or ""),
            "pgpt": str(saved.get("pgpt_model") or ""),
            "online": str(saved.get("online_model") or ""),
        }
        if ollama_fs_access.looks_like_ollama_model(self._model_by_provider["online"]):
            self._model_by_provider["online"] = ""
        if self._model_by_provider["online"] in {"Chargement…", "Aucun modèle"}:
            self._model_by_provider["online"] = ""
        ollama_fs_access.configure_online(
            saved.get("online_url") or "",
            saved.get("online_key") or "",
            saved.get("online_preset") or "",
        )
        ollama_fs_access.configure_catalog_keys(saved.get("catalog_keys") or {})
        self.catalog_source = ollama_fs_access.catalog_key(saved.get("catalog_source") or "openrouter")
        self._catalog_choice = str(saved.get("catalog_model") or "").strip()
        self._online_warned = False
        self._all_models = []
        self._models_error = ""
        self._filling_models = False
        self._model_picker = None
        self._model_picker_list = None
        self.model_filter = "uncensored"
        self.model_family = str(saved.get("model_family") or "")
        self.model_var = tk.StringVar(value=self._model_by_provider.get(self.provider) or "")
        self.recent_folders = _recent_folders(saved.get("recent_folders"), self.target_folder)
        self._recent_by_label = {}

        self.font_title = ctk.CTkFont(family=_title_family(self), size=38)
        self.font_heading = ctk.CTkFont(family=_title_family(self), size=22)
        self.font_ui = ctk.CTkFont(family="Segoe UI", size=13)
        self.font_small = ctk.CTkFont(family="Segoe UI", size=12)
        self.font_tiny = ctk.CTkFont(family="Segoe UI", size=11)
        self.font_editor = ctk.CTkFont(family="Consolas", size=14)
        try:
            self._zoom = max(-3, min(8, int(saved.get("zoom") or 0)))
        except (TypeError, ValueError):
            self._zoom = 0
        self._apply_zoom()
        self._find_needle = ""
        self._find_index = 0
        self._tab_matches = []
        self._tab_token = ""
        self._tab_index = 0
        self._preview_wrap = False
        self._last_stats = ""
        self._stack_index = None
        self._search_job = None
        self._draft_job = None
        self._action_bar = None
        self._action_body = None
        self._action_toggle = None
        self._action_open = True

        ctk.set_appearance_mode(self.C("ctk"))
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._enable_drop()
        self.reload_files()
        self._restore_chat(saved)
        self.after(0, lambda: threading.Thread(target=self._load_models, daemon=True).start())
        self.after(400, _ensure_shortcut)

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = self._styled(ctk.CTkFrame(self, corner_radius=0), "header")
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 4))
        header.grid_columnconfigure(1, weight=1)

        mark = ctk.CTkLabel(header, text="", image=self._mark_image(), width=52, height=52)
        mark.grid(row=0, column=0, rowspan=2, padx=(0, 14))

        wordmark = self._wordmark_image()
        if wordmark is not None:
            self.wordmark_label = ctk.CTkLabel(header, text="", image=wordmark, anchor="w")
            self.wordmark_label.grid(row=0, column=1, sticky="w")
        else:
            self.wordmark_label = self._styled(
                ctk.CTkLabel(header, text=APP_NAME, font=self.font_title, anchor="w"), "text"
            )
            self.wordmark_label.grid(row=0, column=1, sticky="w")
        subtitle = ctk.CTkFrame(header, fg_color="transparent")
        subtitle.grid(row=1, column=1, sticky="w")
        self._styled(ctk.CTkLabel(subtitle, text=APP_TAGLINE, font=self.font_small), "muted").pack(side="left")
        self.ollama_state = self._styled(ctk.CTkLabel(subtitle, text="·  Ollama…", font=self.font_small), "muted")
        self.ollama_state.pack(side="left", padx=(8, 0))
        self.services_state = self._styled(
            ctk.CTkLabel(subtitle, text="", font=self.font_tiny), "muted"
        )
        self.services_state.pack(side="left", padx=(12, 0))
        self.cost_state = self._styled(
            ctk.CTkLabel(subtitle, text="", font=self.font_tiny), "muted"
        )
        self.cost_state.pack(side="left", padx=(12, 0))

        cluster = ctk.CTkFrame(header, fg_color="transparent")
        cluster.grid(row=0, column=2, rowspan=2, sticky="e")
        self.theme_button = ctk.CTkButton(
            cluster, text=THEMES[self.theme_name]["icon"], width=52, height=52,
            corner_radius=26, font=ctk.CTkFont(family="Segoe UI", size=22),
            command=self.cycle_theme,
        )
        self._styled(self.theme_button, "theme_btn")
        self.theme_button.pack(side="left", padx=(0, 10))
        path_row = self._styled(ctk.CTkFrame(cluster, corner_radius=12), "raised")
        path_row.pack(side="left")
        self.path_label = self._styled(
            ctk.CTkLabel(path_row, text=_short_path(self.target_folder), font=self.font_tiny, anchor="e"), "text"
        )
        self.path_label.pack(side="left", padx=(12, 8), pady=6)
        self.recent_box = self._styled(
            ctk.CTkComboBox(
                path_row, values=["Récents"], width=150, height=32, corner_radius=10,
                font=self.font_tiny, command=self._open_recent,
            ),
            "combo",
        )
        self.recent_box.pack(side="left", padx=(0, 6), pady=6)
        self.recent_box.set("Récents")
        self._button(path_row, "Parcourir", self.choose_folder, width=96).pack(side="left", padx=(0, 6), pady=6)
        self._refresh_recent_box()

        self.split = tk.PanedWindow(
            self, orient="horizontal", sashwidth=8, sashpad=2,
            bd=0, relief="flat", opaqueresize=True,
        )
        self._styled(self.split, "split")
        self.split.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)

        left = self._styled(ctk.CTkFrame(self.split, corner_radius=18), "card")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(3, weight=3)
        left.grid_rowconfigure(5, weight=2)

        files_head = ctk.CTkFrame(left, fg_color="transparent")
        files_head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        files_head.grid_columnconfigure(0, weight=1)
        self._styled(ctk.CTkLabel(files_head, text="FICHIERS", font=self.font_tiny), "muted").grid(
            row=0, column=0, sticky="w"
        )
        self._button(files_head, "Replier", self._collapse_all_tree, width=72).grid(
            row=0, column=1, padx=(6, 0)
        )
        self._button(files_head, "Déplier", self._expand_all_tree, width=72).grid(
            row=0, column=2, padx=(6, 0)
        )
        self.search_entry = self._styled(
            ctk.CTkEntry(
                left, placeholder_text="Rechercher un nom ou un texte",
                height=36, corner_radius=10, font=self.font_ui,
            ),
            "entry",
        )
        self.search_entry.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.search_entry.bind("<Return>", lambda _event: self.reload_files(confirm=False) or "break")
        self.search_entry.bind("<KeyRelease>", self._schedule_file_search)

        filters = ctk.CTkFrame(left, fg_color="transparent")
        filters.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 8))
        filters.grid_columnconfigure(0, weight=1)
        self.ext_entry = self._styled(
            ctk.CTkEntry(filters, textvariable=self.ext_var, height=32, corner_radius=10, font=self.font_small),
            "entry",
        )
        self.ext_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.max_entry = self._styled(
            ctk.CTkEntry(
                filters, textvariable=self.max_var, width=64, height=32, corner_radius=10,
                font=self.font_small, justify="center",
            ),
            "entry",
        )
        self.max_entry.grid(row=0, column=1)

        self.file_list = self._styled(
            ctk.CTkScrollableFrame(left, corner_radius=12, scrollbar_button_color=self.C("accent")),
            "well",
        )
        self.file_list.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 8))
        self._paint_scrollbar(self.file_list)
        self.smart_label = self._styled(
            ctk.CTkLabel(
                left, text="Cochez les fichiers que Lumio a le droit de lire",
                font=self.font_tiny, anchor="w", justify="left", wraplength=300,
            ),
            "muted",
        )
        self.smart_label.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 4))

        preview_wrap = ctk.CTkFrame(left, fg_color="transparent")
        preview_wrap.grid(row=5, column=0, sticky="nsew", padx=14, pady=(0, 8))
        preview_wrap.grid_columnconfigure(0, weight=1)
        preview_wrap.grid_rowconfigure(1, weight=1)
        preview_head = ctk.CTkFrame(preview_wrap, fg_color="transparent")
        preview_head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.preview_meta = self._styled(ctk.CTkLabel(preview_head, text="APERÇU", font=self.font_tiny), "muted")
        self.preview_meta.pack(side="left")
        self._button(preview_head, "Dossier", self.reveal_selected, width=72).pack(side="right")
        self._button(preview_head, "Ouvrir", self.open_selected, width=72).pack(side="right", padx=(0, 6))
        self._button(preview_head, "Chemin", self.copy_selected_path, width=72).pack(side="right", padx=(0, 6))
        self.wrap_button = self._button(preview_head, "Retour", self.toggle_preview_wrap, width=72)
        self.wrap_button.pack(side="right", padx=(0, 6))
        self.file_preview = self._styled(
            ctk.CTkTextbox(
                preview_wrap, height=140, corner_radius=10, font=self.font_editor,
                wrap="none", activate_scrollbars=True,
            ),
            "textbox",
        )
        self.file_preview.grid(row=1, column=0, sticky="nsew")

        tools = ctk.CTkFrame(left, fg_color="transparent")
        tools.grid(row=6, column=0, sticky="ew", padx=14, pady=(0, 14))
        self._button(tools, "Tout cocher", self.check_all).pack(side="left")
        self._button(tools, "Tout décocher", self.check_none).pack(side="left", padx=8)
        self.pin_filter_button = self._button(tools, "★ seuls", self._toggle_pin_filter, width=72)
        self.pin_filter_button.pack(side="left", padx=8)
        self._button(tools, "Nouveau fichier", self.new_file).pack(side="left", padx=8)
        self._button(tools, "Actualiser", self.reload_files).pack(side="right")

        right = self._styled(ctk.CTkFrame(self.split, corner_radius=18), "card")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        chat_pane = self._styled(ctk.CTkFrame(right, corner_radius=0), "card")
        chat_pane.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        chat_pane.grid_columnconfigure(0, weight=1)
        chat_pane.grid_rowconfigure(3, weight=1)

        self._styled(ctk.CTkFrame(chat_pane, height=1), "line").grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))

        ask = ctk.CTkFrame(chat_pane, fg_color="transparent")
        ask.grid(row=1, column=0, sticky="ew", padx=8, pady=(4, 6))
        ask.grid_columnconfigure(0, weight=1)
        self.question_entry = self._styled(
            ctk.CTkTextbox(
                ask, height=96, corner_radius=10, font=self.font_ui,
                wrap="word", activate_scrollbars=True,
            ),
            "entry",
        )
        self.question_entry.grid(row=0, column=0, sticky="nsew")
        self._composer_console = False
        self._bind_question_entry()
        self.capture_button = self._button(ask, "Capture", self.start_capture, width=88)
        self.capture_button.grid(row=0, column=1, padx=(8, 0), sticky="n")
        self.attach_file_button = self._button(ask, "Joindre", self.attach_image_file, width=80)
        self.attach_file_button.grid(row=0, column=2, padx=(6, 0), sticky="n")
        self.ask_hint = self._styled(
            ctk.CTkLabel(
                ask,
                text="Entrée : envoyer  ·  Tab : fichier  ·  ↑↓ : questions  ·  F1 : raccourcis  ·  clic droit : fichier",
                font=self.font_tiny,
                anchor="w",
            ),
            "muted",
        )
        self.ask_hint.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.attach_row = ctk.CTkFrame(ask, fg_color="transparent")
        self.attach_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.attach_row.grid_remove()
        controls = ctk.CTkFrame(ask, fg_color="transparent")
        controls.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.provider_box = self._styled(
            ctk.CTkComboBox(
                controls, values=["Ollama", "PrivateGPT", "En ligne"], width=150, height=36, corner_radius=10,
                font=self.font_small, command=self._on_provider,
            ),
            "combo",
        )
        self.provider_box.pack(side="left")
        self.provider_box.set(self._backend_name())
        self.model_button = self._button(
            controls, "Chargement…  ▾", self.pick_model, width=280,
        )
        self.model_button.pack(side="left", padx=(8, 0))
        self._button(controls, "API", self.edit_online_api, width=56).pack(side="left", padx=(8, 0))
        self.cancel_button = self._button(controls, "Annuler", self.cancel_ask, danger=True)
        self.cancel_button.pack(side="right")
        self.continue_button = self._button(controls, "Continuer", self.continue_reply)
        self.continue_button.pack(side="right", padx=(0, 8))
        self.retry_button = self._button(controls, "Relancer", self.retry_last, width=80)
        self.retry_button.pack(side="right", padx=(0, 8))
        self.ask_button = self._button(controls, "Demander", self.ask_model, primary=True)
        self.ask_button.pack(side="right", padx=(0, 8))
        self.cancel_button.configure(state="disabled")
        self.continue_button.configure(state="disabled")

        chat_head = ctk.CTkFrame(chat_pane, fg_color="transparent")
        chat_head.grid(row=2, column=0, sticky="ew", padx=8, pady=(4, 6))
        self._styled(ctk.CTkLabel(chat_head, text="CONVERSATION", font=self.font_tiny), "muted").pack(side="left")
        self.chat_picker = self._button(
            chat_head, "Nouvelle conversation  ▾", self.manage_chats, width=260,
        )
        self.chat_picker.pack(side="left", padx=(10, 0))
        self.chat_state = self._styled(ctk.CTkLabel(chat_head, text="", font=self.font_tiny), "sage")
        self.chat_state.pack(side="left", padx=(10, 0))
        self._button(chat_head, "Nouvelle conversation", self.new_chat).pack(side="right")
        self._button(chat_head, "Conversations", self.manage_chats, width=110).pack(side="right", padx=(0, 8))
        self._button(chat_head, "Supprimer", self.delete_chat, width=88).pack(side="right", padx=(0, 8))
        self._button(chat_head, "Nommer", self.rename_chat, width=72).pack(side="right", padx=(0, 8))
        self._button(chat_head, "Dupliquer", self.duplicate_chat, width=88).pack(side="right", padx=(0, 8))
        self._button(chat_head, "Exporter", self.export_chat, width=80).pack(side="right", padx=(0, 8))
        self.undo_button = self._button(chat_head, "Annuler écriture", self.undo_last_write, width=130)
        self.undo_button.pack(side="right", padx=(0, 8))
        self.undo_button.configure(state="disabled")
        self._button(chat_head, "Copier", self.copy_answer, width=80).pack(side="right", padx=(0, 8))
        self.jump_button = self._button(chat_head, "Bas", self._jump_chat, width=64)
        self.jump_button.pack(side="right", padx=(0, 8))

        chat_box = self._build_chat_box(chat_pane)
        chat_box.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.chat_empty = self._styled(
            ctk.CTkLabel(self.chat, text="Posez une question. Lumio répond ici.", font=self.font_small),
            "muted",
        )
        self.chat_empty.pack(pady=18)
        self.split.add(left, minsize=300, stretch="always")
        self.split.add(right, minsize=420, stretch="always")
        self.after(0, self._bind_chat_scroll)
        self.bind("<Configure>", self._on_window_resize)
        if self._sash:
            self.after(80, self._restore_sashes)
        self.bind_all("<Control-s>", self._save_shortcut)
        self.bind_all("<Control-S>", self._save_shortcut)
        self.bind_all("<Control-Shift-s>", lambda _event: self.start_capture() or "break")
        self.bind_all("<Control-Shift-S>", lambda _event: self.start_capture() or "break")
        self.bind_all("<Control-n>", lambda _event: self.new_chat() or "break")
        self.bind_all("<Control-N>", lambda _event: self.new_chat() or "break")
        self.bind_all("<Control-e>", lambda _event: self.export_chat() or "break")
        self.bind_all("<Control-E>", lambda _event: self.export_chat() or "break")
        self.bind_all("<Control-f>", lambda _event: self.find_in_chat() or "break")
        self.bind_all("<Control-F>", lambda _event: self.find_in_chat() or "break")
        self.bind_all("<Control-Return>", lambda _event: self.ask_model() or "break")
        self.bind_all("<Control-k>", lambda _event: self.command_palette() or "break")
        self.bind_all("<Control-K>", lambda _event: self.command_palette() or "break")
        self.bind_all("<Control-Shift-k>", lambda _event: self._focus_question() or "break")
        self.bind_all("<Control-Shift-K>", lambda _event: self._focus_question() or "break")
        self.bind_all("<Control-Shift-f>", lambda _event: self.search_all_chats() or "break")
        self.bind_all("<Control-Shift-F>", lambda _event: self.search_all_chats() or "break")
        self.bind_all("<Control-l>", lambda _event: self._focus_search() or "break")
        self.bind_all("<Control-L>", lambda _event: self._focus_search() or "break")
        self.bind_all("<Control-r>", lambda _event: self.retry_last() or "break")
        self.bind_all("<Control-R>", lambda _event: self.retry_last() or "break")
        self.bind_all("<Control-Shift-q>", lambda _event: self.quote_last() or "break")
        self.bind_all("<Control-Shift-Q>", lambda _event: self.quote_last() or "break")
        self.bind_all("<Control-plus>", lambda _event: self._nudge_zoom(1) or "break")
        self.bind_all("<Control-equal>", lambda _event: self._nudge_zoom(1) or "break")
        self.bind_all("<Control-minus>", lambda _event: self._nudge_zoom(-1) or "break")
        self.bind_all("<Control-0>", lambda _event: self._nudge_zoom(0, reset=True) or "break")
        self.bind_all("<Control-MouseWheel>", self._on_zoom_wheel)
        self.bind_all("<F3>", lambda _event: self.find_in_chat(again=True) or "break")
        self.bind_all("<F1>", lambda _event: self.show_help() or "break")
        self.bind_all("<Control-Shift-c>", lambda _event: self.copy_chat() or "break")
        self.bind_all("<Control-Shift-C>", lambda _event: self.copy_chat() or "break")
        self.bind_all("<Up>", lambda event: self._move_file(-1, event))
        self.bind_all("<Down>", lambda event: self._move_file(1, event))
        self.bind("<Escape>", self._on_escape)
        if self._saved_draft:
            self.after(80, lambda: self._insert_question(self._saved_draft))
        self.after(8000, self._watch_folder)
        self.after(400, self._poll_background)
        self.after(1500, self._refresh_services)
        self.after(6000, self._warm_prices)
        self.after(90_000, self._warm_ollama)
        self.after(20_000, self._autosave_draft)

        self.status = self._styled(ctk.CTkLabel(self, text="", font=self.font_small, anchor="w"), "muted")
        self.status.grid(row=2, column=0, sticky="ew", padx=26, pady=(0, 14))

    def C(self, key):
        return THEMES[self.theme_name][key]

    def _styled(self, widget, role):
        self._themed.append((widget, role))
        self._paint(widget, role)
        return widget

    def _paint(self, widget, role):
        try:
            if role == "split":
                widget.configure(bg=self.C("bg"))
            elif role == "header":
                widget.configure(fg_color=self.C("bg"))
            elif role == "card":
                widget.configure(fg_color=self.C("card"))
            elif role == "raised":
                widget.configure(fg_color=self.C("raised"))
            elif role == "well":
                widget.configure(fg_color=self.C("bg"))
            elif role == "line":
                widget.configure(fg_color=self.C("line"))
            elif role == "text":
                widget.configure(text_color=self.C("text"))
            elif role == "muted":
                widget.configure(text_color=self.C("muted"))
            elif role == "accent":
                widget.configure(text_color=self.C("accent"))
            elif role == "sage":
                widget.configure(text_color=self.C("sage"))
            elif role == "danger":
                widget.configure(text_color=self.C("danger"))
            elif role == "entry":
                widget.configure(fg_color=self.C("bg"), border_color=self.C("line"), text_color=self.C("text"))
            elif role == "combo":
                widget.configure(
                    fg_color=self.C("bg"), border_color=self.C("line"), button_color=self.C("btn"),
                    button_hover_color=self.C("btn_hover"), dropdown_fg_color=self.C("card"),
                    dropdown_hover_color=self.C("selected"), dropdown_text_color=self.C("text"),
                    text_color=self.C("text"),
                )
            elif role == "textbox":
                widget.configure(fg_color=self.C("bg"), text_color=self.C("text"), border_color=self.C("line"))
            elif role == "btn":
                widget.configure(
                    fg_color=self.C("btn"), hover_color=self.C("btn_hover"),
                    text_color=self.C("btn_text"), text_color_disabled=self.C("muted"),
                )
            elif role == "btn_primary":
                widget.configure(
                    fg_color=self.C("accent"), hover_color=self.C("accent_hover"),
                    text_color=self.C("ink"), text_color_disabled=self.C("muted"),
                )
            elif role == "btn_danger":
                widget.configure(
                    fg_color=self.C("danger_btn"), hover_color=self.C("danger_hover"),
                    text_color=self.C("danger_text"), text_color_disabled=self.C("muted"),
                )
            elif role == "theme_btn":
                widget.configure(fg_color=self.C("accent"), hover_color=self.C("accent_hover"), text_color=self.C("ink"))
            elif role == "check":
                widget.configure(border_color=self.C("line"), fg_color=self.C("accent"), hover_color=self.C("accent_hover"))
            elif role == "file_btn":
                widget.configure(hover_color=self.C("selected"), text_color=self.C("text"))
            elif role == "bubble_user":
                widget.configure(fg_color=self.C("selected"))
            elif role == "bubble_ai":
                widget.configure(fg_color=self.C("raised"))
            elif role == "bubble_box":
                widget.configure(fg_color="transparent", text_color=self.C("text"), border_color=self.C("line"))
        except Exception:
            pass

    def _build_chat_box(self, parent):
        wrap = self._styled(ctk.CTkFrame(parent, corner_radius=12), "well")
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(0, weight=1)
        canvas = tk.Canvas(wrap, highlightthickness=0, bd=0, relief="flat", bg=self.C("bg"))
        canvas.grid(row=0, column=0, sticky="nsew", padx=(6, 4), pady=8)
        bar = ctk.CTkScrollbar(
            wrap, orientation="vertical", command=canvas.yview, width=12,
            corner_radius=8,
            fg_color=self.C("raised"),
            button_color=self.C("accent"),
            button_hover_color=self.C("accent_hover"),
        )
        bar.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=10)
        canvas.configure(yscrollcommand=bar.set)
        inner = ctk.CTkFrame(canvas, fg_color="transparent")
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        self.chat_wrap = wrap
        self.chat_canvas = canvas
        self.chat_bar = bar
        self.chat = inner
        self._chat_window = window

        def on_inner(_event=None):
            if self._streaming:
                return
            bbox = canvas.bbox("all")
            canvas.configure(scrollregion=bbox or (0, 0, 0, 0))

        def on_canvas(event):
            canvas.itemconfigure(window, width=max(1, event.width))
            if not self._streaming:
                self._on_chat_resize()

        inner.bind("<Configure>", on_inner)
        canvas.bind("<Configure>", on_canvas)
        return wrap

    def _paint_scrollbar(self, frame):
        bar = getattr(frame, "_scrollbar", None)
        if bar is None:
            return
        try:
            bar.configure(
                width=12,
                corner_radius=8,
                fg_color=self.C("raised"),
                button_color=self.C("accent"),
                button_hover_color=self.C("accent_hover"),
            )
        except Exception:
            pass

    def _paint_chat_bar(self):
        bar = getattr(self, "chat_bar", None)
        if bar is None:
            return
        try:
            bar.configure(
                width=12,
                corner_radius=8,
                fg_color=self.C("raised"),
                button_color=self.C("accent"),
                button_hover_color=self.C("accent_hover"),
            )
        except Exception:
            pass

    def cycle_theme(self):
        index = THEME_ORDER.index(self.theme_name)
        self.theme_name = THEME_ORDER[(index + 1) % len(THEME_ORDER)]
        ctk.set_appearance_mode(self.C("ctk"))
        self.configure(fg_color=self.C("bg"))
        self._themed = [(widget, role) for widget, role in self._themed if widget.winfo_exists()]
        for widget, role in self._themed:
            self._paint(widget, role)
        self.theme_button.configure(text=THEMES[self.theme_name]["icon"])
        image = self._wordmark_image()
        if image is not None and getattr(self, "wordmark_label", None) is not None:
            self.wordmark_label.configure(image=image)
        selected = self.selected_key
        if self.visible_files or self._pending_updates:
            self._fill_list(self._files_to_show())
            if selected:
                self._highlight(Path(selected))
        self._refresh_title()
        self._save_session()
        self._set_status(f"Thème {THEMES[self.theme_name]['label']}")
        self.after(30, self._repaint)

    def _repaint(self):
        self._themed = [(widget, role) for widget, role in self._themed if widget.winfo_exists()]
        for widget, role in self._themed:
            self._paint(widget, role)
        if getattr(self, "file_list", None) is not None:
            self._paint_scrollbar(self.file_list)
        self._paint_chat_bar()
        canvas = getattr(self, "chat_canvas", None)
        if canvas is not None:
            canvas.configure(bg=self.C("bg"))

    def _button(self, parent, text, command, primary=False, danger=False, width=None):
        options = dict(height=34, corner_radius=10, font=self.font_small, command=command, text=text)
        if width:
            options["width"] = width
        button = ctk.CTkButton(parent, **options)
        role = "btn_primary" if primary else "btn_danger" if danger else "btn"
        return self._styled(button, role)

    def _backend(self):
        return ollama_fs_access.normalize_provider(getattr(self, "provider", "ollama"))

    def _backend_name(self):
        return ollama_fs_access.provider_label(self._backend())

    def _on_provider(self, choice):
        key = ollama_fs_access.normalize_provider(choice)
        current = self._current_model()
        if current and current not in {"Chargement…", "Aucun modèle"}:
            self._model_by_provider[self._backend()] = current
        if key == self.provider:
            return
        self.provider = key
        self.model_var.set(self._model_by_provider.get(key) or "")
        self._set_status(f"Moteur : {self._backend_name()}")
        if key == "online" and not ollama_fs_access.online_config().get("key"):
            self.after(80, self.edit_online_api)
        self.refresh_models()
        self._save_session()

    def _current_model(self):
        name = str(self.model_var.get() or "").strip()
        if name in {"Chargement…", "Aucun modèle", "Choisir un modèle"}:
            return ""
        return name

    def _model_button_label(self, name=""):
        name = str(name or self._current_model()).strip()
        if not name or name in {"Chargement…", "Aucun modèle"}:
            return "Choisir un modèle  ▾"
        short = name if len(name) <= 34 else "…" + name[-33:]
        return f"{short}  ▾"

    def _refresh_model_button(self):
        button = getattr(self, "model_button", None)
        if button is None:
            return
        button.configure(text=self._model_button_label())

    def _visible_models(self, needle=""):
        names = list(self._all_models)
        if self._backend() == "online":
            names = [name for name in names if not ollama_fs_access.is_weak_coder(name)]
        needle = str(needle or "").strip().casefold()
        if needle:
            names = [name for name in names if needle in name.casefold()]
        return names

    def _visible_families(self, needle=""):
        names = list(self._all_models)
        rows = ollama_fs_access.list_model_families(names)
        needle = str(needle or "").strip().casefold()
        if needle:
            rows = [
                item for item in rows
                if needle in item[0].casefold() or needle in item[2].casefold()
            ]
        return rows

    def _on_model_filter(self, kind):
        self.model_filter = "uncensored"
        self._apply_model_list()

    def _on_model(self, choice, force=False):
        choice = str(choice or "").strip()
        if not choice or choice in {"Chargement…", "Aucun modèle"}:
            return
        if (
            not force
            and choice not in self._all_models
            and not ollama_fs_access.is_uncensored_model(choice)
            and choice != self._catalog_choice
        ):
            return
        self.model_var.set(choice)
        self._model_by_provider[self._backend()] = choice
        family = ollama_fs_access.model_family(choice)
        if family:
            self.model_family = family
        self._refresh_model_button()
        extra = ""
        if self._backend() == "ollama":
            window = ollama_fs_access.model_window_label(choice)
            extra = f" · {window}" if window else ""
        elif self._backend() == "online":
            extra = " · En ligne"
        elif self._backend() == "pgpt":
            extra = " · PrivateGPT"
        if getattr(self, "ollama_state", None) is not None:
            tag = ollama_fs_access.model_coding_tag(choice)
            if tag and "pas pour le code" in tag:
                self._paint(self.ollama_state, "danger")
                self.ollama_state.configure(text=f"·  {tag}{extra}")
            else:
                self._paint(self.ollama_state, "sage")
                self.ollama_state.configure(text=f"·  prêt{extra}")
        self._save_session()

    def pick_model(self):
        if not self._all_models:
            self.refresh_models()
        dialog = ctk.CTkToplevel(self)
        dialog.title("Modèles")
        dialog.geometry("640x580")
        dialog.minsize(480, 400)
        dialog.configure(fg_color=self.C("bg"))
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass
        self._model_picker = dialog
        hint = (
            "Modèles PrivateGPT locaux. Lumio envoie le dossier et écrit les fichiers."
            if self._backend() == "pgpt"
            else "Uniquement les modèles uncensored capables de coder (Dolphin 24B, Magnum, Euryale…)."
            if self._backend() == "online"
            else "Tous les modèles Ollama installés localement."
        )
        self._styled(
            ctk.CTkLabel(
                dialog,
                text=hint,
                font=self.font_small, anchor="w",
            ),
            "muted",
        ).pack(fill="x", padx=16, pady=(14, 8))
        tools = ctk.CTkFrame(dialog, fg_color="transparent")
        tools.pack(fill="x", padx=16)
        search_var = tk.StringVar()
        search = self._styled(
            ctk.CTkEntry(
                tools, textvariable=search_var, height=36, corner_radius=10,
                font=self.font_small, placeholder_text="Rechercher un modèle…",
            ),
            "entry",
        )
        search.pack(side="left", fill="x", expand=True)
        self._button(tools, "Actualiser", self.refresh_models, width=100).pack(side="left", padx=(8, 0))
        self._button(tools, "Catalogue en ligne", self.browse_catalog, width=160).pack(side="left", padx=(8, 0))
        count = self._styled(ctk.CTkLabel(dialog, text="", font=self.font_tiny, anchor="w"), "muted")
        count.pack(fill="x", padx=16, pady=(8, 4))
        listing = self._styled(ctk.CTkScrollableFrame(dialog, corner_radius=12), "well")
        listing.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self._model_picker_list = listing

        def choose(name):
            self._on_model(name)
            close()

        job = {"id": None}

        def fill():
            for child in listing.winfo_children():
                child.destroy()
            if not self._all_models:
                text = self._models_error or "Chargement des modèles…"
                self._styled(ctk.CTkLabel(listing, text=text, font=self.font_small, anchor="w"), "muted").pack(
                    fill="x", padx=8, pady=12
                )
                count.configure(text="0")
                return
            names = self._visible_models(search_var.get())
            if not names:
                empty = (
                    "Aucun modèle PrivateGPT. Lancez le workbench puis Actualiser."
                    if self._backend() == "pgpt"
                    else "Aucun modèle uncensored trouvé. Cliquez sur Actualiser."
                )
                self._styled(
                    ctk.CTkLabel(
                        listing,
                        text=empty,
                        font=self.font_small, anchor="w",
                    ),
                    "muted",
                ).pack(fill="x", padx=8, pady=12)
                count.configure(text="0 modèle")
                return
            count.configure(text=f"{len(names)} modèle{'s' if len(names) != 1 else ''}")
            current = self._current_model()
            for name in names:
                mark = "●  " if name == current else ""
                tag = ollama_fs_access.model_coding_tag(name)
                label = f"{mark}{name}"
                if tag:
                    label = f"{label}   ·   {tag}"
                self._button(
                    listing, label,
                    lambda item=name: choose(item),
                    primary=name == current,
                ).pack(fill="x", padx=6, pady=3)
            self._bind_scrollable_wheel(listing, dialog)

        def schedule_fill(_event=None):
            if job["id"] is not None:
                dialog.after_cancel(job["id"])
            job["id"] = dialog.after(120, fill)

        def close():
            self._model_picker = None
            self._model_picker_list = None
            dialog.destroy()

        dialog._fill_models = fill
        search.bind("<KeyRelease>", schedule_fill)
        fill()
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.bind("<Escape>", lambda _event: close())
        search.focus_set()
        self.wait_window(dialog)

    # ------------------------------------------------------------------
    #  Catalogue des modèles utilisables en ligne (aucun téléchargement)
    # ------------------------------------------------------------------
    def browse_catalog(self, source=None):
        source = ollama_fs_access.catalog_key(source or getattr(self, "catalog_source", "openrouter"))

        dialog = ctk.CTkToplevel(self)
        dialog.title("Catalogue en ligne")
        dialog.geometry("920x640")
        dialog.minsize(760, 480)
        dialog.configure(fg_color=self.C("bg"))
        dialog.transient(self)
        picker = getattr(self, "_model_picker", None)
        if picker is not None:
            try:
                picker.grab_release()
            except Exception:
                picker = None
        try:
            dialog.grab_set()
        except Exception:
            pass

        head = ctk.CTkFrame(dialog, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(16, 0))
        ctk.CTkLabel(
            head, text="Catalogue en ligne", text_color=self.C("text"),
            font=self.font_heading, anchor="w",
        ).pack(side="left")
        state = self._styled(ctk.CTkLabel(head, text="", font=self.font_tiny, anchor="e"), "muted")
        state.pack(side="right")

        self._styled(
            ctk.CTkLabel(
                dialog,
                text=(
                    "Les modèles tournent chez le fournisseur : rien n'est téléchargé et aucune "
                    "carte graphique n'est nécessaire. En revanche, les fichiers envoyés en "
                    "contexte quittent ce poste."
                ),
                font=self.font_tiny, anchor="w", justify="left", wraplength=860,
            ),
            "muted",
        ).pack(fill="x", padx=18, pady=(6, 12))

        tools = ctk.CTkFrame(dialog, fg_color="transparent")
        tools.pack(fill="x", padx=18)
        search_var = tk.StringVar()
        search = self._styled(
            ctk.CTkEntry(
                tools, textvariable=search_var, height=36, corner_radius=10, font=self.font_small,
                placeholder_text="Rechercher un modèle (qwen, deepseek, llama…)",
            ),
            "entry",
        )
        search.pack(side="left", fill="x", expand=True)
        source_box = self._styled(
            ctk.CTkComboBox(
                tools, values=ollama_fs_access.catalog_labels(), width=150, height=36,
                corner_radius=10, font=self.font_small,
                command=lambda value: pick_source(value),
            ),
            "combo",
        )
        source_box.set(ollama_fs_access.catalog_label(source))
        source_box.pack(side="left", padx=(8, 0))
        self._button(tools, "Actualiser", lambda: load(refresh=True), width=100).pack(
            side="left", padx=(8, 0)
        )

        filters = ctk.CTkFrame(dialog, fg_color="transparent")
        filters.pack(fill="x", padx=18, pady=(10, 0))
        free_var = tk.BooleanVar(value=False)
        vision_var = tk.BooleanVar(value=False)
        unc_var = tk.BooleanVar(value=False)
        for text, variable in (
            ("Gratuit uniquement", free_var),
            ("Vision (images)", vision_var),
            ("Non censuré + code", unc_var),
        ):
            self._styled(
                ctk.CTkCheckBox(
                    filters, text=text, variable=variable, font=self.font_tiny,
                    command=lambda: render(), checkbox_width=18, checkbox_height=18,
                ),
                "check",
            ).pack(side="left", padx=(0, 16))
        count = self._styled(ctk.CTkLabel(filters, text="", font=self.font_tiny, anchor="e"), "muted")
        count.pack(side="right")

        sort_row = ctk.CTkFrame(dialog, fg_color="transparent")
        sort_row.pack(fill="x", padx=18, pady=(6, 0))
        self._styled(ctk.CTkLabel(sort_row, text="Trier par", font=self.font_tiny), "muted").pack(side="left")
        sort_labels = [lumio_rank.LABELS[key] for key in lumio_rank.CRITERIA]
        current_sort = str(self.settings.get("catalog_sort") or "recommande")
        sort_box = self._styled(
            ctk.CTkComboBox(sort_row, values=sort_labels, width=250, height=30, font=self.font_tiny),
            "combo",
        )
        sort_box.pack(side="left", padx=(8, 0))
        sort_box.set(lumio_rank.LABELS.get(current_sort, sort_labels[0]))
        sort_hint = self._styled(
            ctk.CTkLabel(sort_row, text="", font=self.font_tiny, anchor="w"), "muted",
        )
        sort_hint.pack(side="left", padx=(10, 0))

        def sort_key():
            return lumio_rank.LABEL_TO_KEY.get(sort_box.get(), "recommande")

        def on_sort(_choice=None):
            self.settings["catalog_sort"] = sort_key()
            sort_hint.configure(text=lumio_rank.explain(sort_key()))
            render()
            self._save_session()

        sort_box.configure(command=on_sort)
        sort_hint.configure(text=lumio_rank.explain(current_sort))

        key_slot = ctk.CTkFrame(dialog, fg_color="transparent")
        key_row = ctk.CTkFrame(key_slot, fg_color="transparent")
        key_row.pack(fill="x")
        key_var = tk.StringVar()
        listing = self._styled(ctk.CTkScrollableFrame(dialog, corner_radius=12), "well")
        listing.pack(fill="both", expand=True, padx=18, pady=(10, 6))

        self._styled(
            ctk.CTkLabel(
                dialog,
                text="Choisir un modèle le rend actif : le moteur passe sur « En ligne ».",
                font=self.font_tiny, anchor="w",
            ),
            "muted",
        ).pack(fill="x", padx=18, pady=(0, 14))

        rows = []
        job = {"id": None}

        def set_state(text, role="muted"):
            state.configure(text=text)
            self._paint(state, role)

        def toggle_key_row():
            needed = (
                ollama_fs_access.catalog_needs_key(source)
                and not ollama_fs_access.catalog_secret(source)
            )
            if needed:
                key_entry.configure(placeholder_text=f"Clé API {ollama_fs_access.catalog_label(source)}")
                if not key_slot.winfo_ismapped():
                    key_slot.pack(fill="x", padx=18, pady=(6, 0), after=sort_row)
            else:
                key_slot.pack_forget()

        def save_key():
            ollama_fs_access.set_catalog_key(source, key_var.get().strip())
            key_var.set("")
            self._save_session()
            key_slot.pack_forget()
            load(refresh=True)

        key_entry = self._styled(
            ctk.CTkEntry(
                key_row, textvariable=key_var, height=32, corner_radius=10,
                font=self.font_tiny, show="•",
            ),
            "entry",
        )
        key_entry.pack(side="left", fill="x", expand=True)
        self._button(key_row, "Enregistrer la clé", save_key, width=170).pack(side="left", padx=(8, 0))

        def visible_rows():
            needle = search_var.get().strip().casefold()
            picked = []
            for row in rows:
                haystack = f"{row['id']} {row.get('name') or ''} {row.get('vendor') or ''}".casefold()
                if needle and needle not in haystack:
                    continue
                if free_var.get() and not row["free"]:
                    continue
                if vision_var.get() and not row["vision"]:
                    continue
                if unc_var.get() and not ollama_fs_access.is_uncensored_model(
                    row["id"], row.get("description") or ""
                ):
                    continue
                picked.append(row)
            return picked

        def render():
            for child in listing.winfo_children():
                child.destroy()
            picked = lumio_rank.sort_rows(visible_rows(), sort_key())
            count.configure(text=f"{len(picked)} modèle{'s' if len(picked) != 1 else ''}")
            if not picked:
                self._styled(
                    ctk.CTkLabel(
                        listing, text="Aucun modèle ne correspond à ces critères.",
                        font=self.font_small, anchor="w",
                    ),
                    "muted",
                ).pack(fill="x", padx=8, pady=12)
                return
            for row in picked[:60]:
                card = self._styled(ctk.CTkFrame(listing, corner_radius=10), "raised")
                card.pack(fill="x", padx=4, pady=3)
                self._button(
                    card, "Choisir", lambda item=row: choose(item), primary=True, width=92,
                ).pack(side="right", padx=10, pady=8)
                body = ctk.CTkFrame(card, fg_color="transparent")
                body.pack(side="left", fill="x", expand=True, padx=10, pady=8)
                self._styled(
                    ctk.CTkLabel(body, text=row["id"], font=self.font_small, anchor="w"), "text",
                ).pack(fill="x")
                size = lumio_rank.size_label(row["id"])
                bits = [
                    row["vendor"],
                    size,
                    ollama_fs_access.format_context(row["context"]),
                    ollama_fs_access.format_price(row["prompt"]),
                ]
                if row["free"] and not ollama_fs_access.format_price(row["prompt"]).startswith("gratuit"):
                    bits.append("gratuit")
                if row["vision"]:
                    bits.append("vision")
                if row["tools"]:
                    bits.append("tools")
                self._styled(
                    ctk.CTkLabel(
                        body, text="  ·  ".join(bit for bit in bits if bit),
                        font=self.font_tiny, anchor="w",
                    ),
                    "muted",
                ).pack(fill="x")
            if len(picked) > 60:
                self._styled(
                    ctk.CTkLabel(
                        listing,
                        text=f"… {len(picked) - 60} modèle(s) de plus : affinez la recherche.",
                        font=self.font_tiny, anchor="w",
                    ),
                    "muted",
                ).pack(fill="x", padx=8, pady=8)
            self._bind_scrollable_wheel(listing, dialog)
            try:
                listing._parent_canvas.yview_moveto(0)
            except Exception:
                pass

        def apply(data, error):
            nonlocal rows
            rows = data
            if error:
                set_state("indisponible", "danger")
                for child in listing.winfo_children():
                    child.destroy()
                self._styled(
                    ctk.CTkLabel(
                        listing, text=error, font=self.font_small, anchor="w",
                        justify="left", wraplength=800,
                    ),
                    "danger",
                ).pack(fill="x", padx=8, pady=14)
                count.configure(text="")
            else:
                set_state(f"{len(rows)} · {ollama_fs_access.catalog_label(source)}", "sage")
                render()
            toggle_key_row()

        def load(refresh=False):
            set_state("chargement…")

            def work():
                try:
                    data = ollama_fs_access.catalog_models(source, refresh=refresh)
                    error = ""
                except Exception as exc:
                    data, error = [], str(exc) or exc.__class__.__name__
                try:
                    dialog.after(0, lambda: apply(data, error))
                except Exception:
                    pass

            threading.Thread(target=work, daemon=True).start()

        def pick_source(value):
            nonlocal source
            source = ollama_fs_access.catalog_key(value)
            self.catalog_source = source
            key_slot.pack_forget()
            load()

        def choose(row):
            if self._choose_catalog_model(row):
                close()
            else:
                set_state("clé requise pour ce fournisseur", "danger")
                toggle_key_row()

        def schedule():
            if job["id"] is not None:
                try:
                    dialog.after_cancel(job["id"])
                except Exception:
                    pass
            job["id"] = dialog.after(220, render)

        def close():
            self.catalog_source = source
            self._save_session()
            try:
                dialog.destroy()
            finally:
                if picker is not None:
                    try:
                        if picker.winfo_exists():
                            picker.grab_set()
                    except Exception:
                        pass
                    self._model_picker = picker

        search.bind("<KeyRelease>", lambda _event: schedule())
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.bind("<Escape>", lambda _event: close())
        load()
        search.focus_set()
        self.wait_window(dialog)

    def _choose_catalog_model(self, row):
        """Rend actif un modèle du catalogue : moteur « En ligne » + bonne base/URL."""
        row = dict(row or {})
        ident = str(row.get("id") or "").strip()
        source = ollama_fs_access.catalog_key(row.get("source") or "openrouter")
        if not ident:
            return False
        secret = ollama_fs_access.catalog_secret(source)
        if ollama_fs_access.catalog_needs_key(source) and not secret:
            self._set_status(
                f"Clé {ollama_fs_access.catalog_label(source)} requise : collez-la dans le catalogue."
            )
            return False
        ollama_fs_access.configure_online(
            ollama_fs_access.catalog_base(source), secret, ollama_fs_access.catalog_label(source)
        )
        self.catalog_source = source
        self._catalog_choice = ident
        self.provider = "online"
        if getattr(self, "provider_box", None) is not None:
            self.provider_box.set(ollama_fs_access.provider_label("online"))
        self._model_by_provider["online"] = ident
        self.model_var.set(ident)
        self._refresh_model_button()
        if getattr(self, "ollama_state", None) is not None:
            self._paint(self.ollama_state, "sage")
            self.ollama_state.configure(
                text=f"·  prêt · En ligne · {ollama_fs_access.catalog_label(source)}"
            )
        self._set_status(f"Modèle en ligne : {ident}")
        self._save_session()
        if secret:
            self.refresh_models()
        return True

    def edit_online_api(self):
        cfg = ollama_fs_access.online_config()
        presets = [name for name, _url in ollama_fs_access.ONLINE_PRESETS]
        dialog = ctk.CTkToplevel(self)
        dialog.title("API en ligne")
        dialog.geometry("560x460")
        dialog.minsize(480, 400)
        dialog.configure(fg_color=self.C("bg"))
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass
        ctk.CTkLabel(
            dialog,
            text="Modèle en ligne",
            text_color=self.C("text"), font=self.font_heading, anchor="w",
        ).pack(fill="x", padx=18, pady=(16, 6))
        self._styled(
            ctk.CTkLabel(
                dialog,
                text=(
                    "Les fichiers cochés partent sur Internet. "
                    "Pour un DeepSeek « uncensored », choisissez OpenRouter puis un modèle "
                    "dont le nom contient uncensored, abliterated ou dolphin. "
                    "L'API officielle DeepSeek n'est pas uncensored."
                ),
                font=self.font_tiny, anchor="w", justify="left", wraplength=500,
            ),
            "muted",
        ).pack(fill="x", padx=18, pady=(0, 12))
        self._styled(ctk.CTkLabel(dialog, text="Fournisseur", font=self.font_tiny, anchor="w"), "muted").pack(
            fill="x", padx=18
        )
        preset_box = self._styled(
            ctk.CTkComboBox(dialog, values=presets, width=320, height=36, corner_radius=10, font=self.font_small),
            "combo",
        )
        preset_box.pack(fill="x", padx=18, pady=(2, 10))
        preset_box.set(cfg.get("preset") or "OpenRouter")
        self._styled(ctk.CTkLabel(dialog, text="URL", font=self.font_tiny, anchor="w"), "muted").pack(
            fill="x", padx=18
        )
        url_var = tk.StringVar(value=cfg.get("url") or "https://openrouter.ai/api/v1")
        url_entry = self._styled(
            ctk.CTkEntry(dialog, textvariable=url_var, height=36, corner_radius=10, font=self.font_small),
            "entry",
        )
        url_entry.pack(fill="x", padx=18, pady=(2, 10))
        self._styled(ctk.CTkLabel(dialog, text="Clé API", font=self.font_tiny, anchor="w"), "muted").pack(
            fill="x", padx=18
        )
        key_var = tk.StringVar(value=cfg.get("key") or "")
        key_entry = self._styled(
            ctk.CTkEntry(
                dialog, textvariable=key_var, height=36, corner_radius=10,
                font=self.font_small, show="•",
            ),
            "entry",
        )
        key_entry.pack(fill="x", padx=18, pady=(2, 8))

        def apply_preset(choice):
            for name, target in ollama_fs_access.ONLINE_PRESETS:
                if name == choice and target:
                    url_var.set(target)
                    break

        preset_box.configure(command=apply_preset)

        bar = ctk.CTkFrame(dialog, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=16)

        def cancel():
            dialog.destroy()

        def save():
            ollama_fs_access.configure_online(url_var.get(), key_var.get(), preset_box.get())
            self._save_session()
            dialog.destroy()
            if self._backend() == "online":
                self.refresh_models()
            else:
                self._set_status("Clé API enregistrée. Passez le moteur sur En ligne pour l'utiliser.")

        self._button(bar, "Annuler", cancel).pack(side="right")
        self._button(bar, "Enregistrer", save, primary=True).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Escape>", lambda _event: cancel())
        self.wait_window(dialog)

    def choose_folder(self):
        if not self._confirm_leave():
            return
        chosen = filedialog.askdirectory(initialdir=self.target_folder, title="Dossier à lire")
        if not chosen:
            return
        self._switch_folder(chosen)

    def _open_recent(self, label):
        folder = self._recent_by_label.get(label)
        if not folder:
            return
        if Path(folder) == Path(self.target_folder):
            self.recent_box.set("Récents")
            return
        if not self._confirm_leave():
            self.recent_box.set("Récents")
            return
        self._switch_folder(folder)

    def _switch_folder(self, chosen, open_after=None):
        self._clear_editor()
        self.target_folder = str(Path(chosen))
        self.path_label.configure(text=_short_path(self.target_folder))
        self.excluded.clear()
        self._remember_folder(self.target_folder)
        self._refresh_recent_box()
        self.reload_files(open_after=open_after, confirm=False)
        self._save_session()

    def _remember_folder(self, folder):
        folder = str(Path(folder))
        kept = [folder]
        for item in self.recent_folders:
            if Path(item) != Path(folder):
                kept.append(str(Path(item)))
        self.recent_folders = kept[:MAX_RECENTS]

    def _refresh_recent_box(self):
        self._recent_by_label = {}
        labels = []
        for folder in self.recent_folders:
            label = _recent_label(folder)
            base = label
            number = 2
            while label in self._recent_by_label:
                label = f"{base} ({number})"
                number += 1
            self._recent_by_label[label] = folder
            labels.append(label)
        self.recent_box.configure(values=labels or ["Récents"])
        self.recent_box.set("Récents")

    def refresh_models(self):
        self.ollama_state.configure(text="·  recherche…", text_color=self.C("muted"))
        threading.Thread(target=self._load_models, daemon=True).start()

    def _save_shortcut(self, _event=None):
        self.save_file()
        return "break"

    def _refresh_title(self):
        return

    def copy_answer(self):
        for role, text in reversed(self._transcript):
            if role == "assistant" and str(text or "").strip() and text != "…":
                self._copy_text(text)
                self._set_status("Dernière réponse copiée")
                return
        lines = []
        for role, text in self._transcript:
            who = "Vous" if role == "user" else "Lumio"
            lines.append(f"{who} : {text}")
        payload = "\n\n".join(lines).strip()
        if not payload:
            messagebox.showinfo(APP_NAME, "Rien à copier pour le moment.")
            return
        self._copy_text(payload)
        self._set_status("Conversation copiée")

    def find_in_chat(self, again=False):
        if again and self._find_needle:
            needle = self._find_needle
        else:
            dialog = ctk.CTkInputDialog(text="Texte à trouver dans la conversation", title="Rechercher")
            needle = (dialog.get_input() or "").strip()
        if not needle:
            return "break"
        if needle.casefold() != self._find_needle.casefold():
            self._find_index = 0
        self._find_needle = needle
        low = needle.casefold()
        hits = []
        for widget in self._wrap_labels:
            if widget.winfo_exists() and low in self._bubble_text(widget).casefold():
                hits.append(widget)
        if not hits:
            self._set_status(f"Aucun résultat pour « {needle} »")
            return "break"
        self._find_index = self._find_index % len(hits)
        widget = hits[self._find_index]
        self._find_index += 1
        inner = getattr(widget, "_textbox", None)
        if inner is not None:
            try:
                inner.tag_remove("sel", "1.0", "end")
                found = inner.search(needle, "1.0", stopindex="end", nocase=True)
                if found:
                    inner.tag_add("sel", found, f"{found}+{len(needle)}c")
                    inner.see(found)
            except Exception:
                pass
        canvas = getattr(self, "chat_canvas", None)
        if canvas is not None:
            try:
                top = widget.winfo_y()
                height = max(1, self.chat.winfo_height())
                canvas.yview_moveto(max(0.0, min(1.0, top / height)))
            except Exception:
                pass
        self._set_status(f"Trouvé {self._find_index}/{len(hits)} : {needle}")
        return "break"

    def _insert_file_mention(self, path):
        name = Path(path).as_posix()
        current = self._question_text()
        prefix = "" if not current or current.endswith((" ", "\n")) else " "
        self._insert_question(prefix + name)
        self._schedule_send_preview()
        try:
            self.question_entry.focus_set()
        except Exception:
            pass
        return "break"

    def _toggle_pin_filter(self):
        self._pinned_only = not self._pinned_only
        button = getattr(self, "pin_filter_button", None)
        if button is not None:
            button.configure(text="★ seuls" if not self._pinned_only else "★ filtre")
            self._paint(button, "btn_primary" if self._pinned_only else "btn")
        self._fill_list(self._files_to_show())
        self._schedule_send_preview()

    def _watch_folder(self):
        if self._streaming:
            self.after(15000, self._watch_folder)
            return
        try:
            root = Path(self.target_folder)
            stamp = (root.stat().st_mtime, len(self.visible_files))
        except Exception:
            stamp = None
        if self._folder_stamp is None:
            self._folder_stamp = stamp
        elif stamp != self._folder_stamp:
            self._folder_stamp = stamp
            ollama_fs_access.invalidate_file_cache()
            previous = self.loaded_path
            self.reload_files(open_after=previous, confirm=False)
            self._set_status("Liste actualisée")
        self.after(15000, self._watch_folder)

    def export_chat(self):
        rows = self._chat_snapshot()
        if not rows:
            messagebox.showinfo(APP_NAME, "Rien à exporter pour le moment.")
            return
        title = _chat_title_from(rows, self.history)
        chosen = filedialog.asksaveasfilename(
            title="Exporter la conversation",
            defaultextension=".md",
            initialfile=re.sub(r"[^\w\- ]+", "", title)[:40].strip() or "lumio",
            filetypes=[("Markdown", "*.md"), ("Texte", "*.txt"), ("Tous", "*.*")],
        )
        if not chosen:
            return
        lines = [f"# {title}", ""]
        for item in rows:
            who = "Vous" if item["role"] == "user" else "Lumio"
            lines.append(f"## {who}")
            lines.append("")
            lines.append(item["content"])
            lines.append("")
        Path(chosen).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        self._set_status(f"Exporté : {Path(chosen).name}")

    def rename_chat(self):
        dialog = ctk.CTkInputDialog(text="Nom de cette conversation", title="Nommer")
        name = dialog.get_input()
        if not name or not name.strip():
            return
        title = name.strip()[:60]
        self._chat_title = title
        for item in self._chats:
            if item.get("id") == self._chat_id:
                item["title"] = title
                break
        else:
            rec = self._chat_record()
            rec["title"] = title
            self._chats = [rec] + [item for item in self._chats if item.get("id") != rec["id"]]
        self._refresh_chat_picker()
        picker = getattr(self, "chat_picker", None)
        if picker is not None:
            self._chat_guard = True
            try:
                picker.set(title)
            except Exception:
                pass
            self._chat_guard = False
        self._save_session()
        self._set_status(f"Conversation : {title}")

    def _max_files(self):
        try:
            return max(1, min(500, int(self.max_var.get())))
        except ValueError:
            return ollama_fs_access.MAX_FILES

    def reload_files(self, open_after=None, confirm=True):
        if confirm and not self._confirm_leave():
            return
        try:
            extensions = ollama_fs_access.parse_extensions(self.ext_var.get())
            query = self.search_entry.get().strip()
            if query:
                files = ollama_fs_access.search_files(
                    self.target_folder, query, extensions, self._max_files()
                )
            else:
                files = ollama_fs_access.list_files(
                    self.target_folder, extensions, self._max_files()
                )
        except Exception as error:
            messagebox.showerror("Erreur", f"Lecture du dossier impossible : {error}")
            return
        if open_after is not None:
            previous = Path(open_after)
        elif self.loaded_path is None:
            previous = None
        else:
            previous = Path(self.loaded_path)
        self._clear_editor()
        self.visible_files = files
        self._fill_list(self._files_to_show())
        if previous is not None and any(path.as_posix() == previous.as_posix() for path in files):
            self._show_file(previous)
        query = self.search_entry.get().strip() if getattr(self, "search_entry", None) else ""
        if query:
            self._set_status(f"{len(files)} fichier(s) pour « {query} »")
        else:
            self._set_status()
        self._schedule_send_preview()
        self._save_session()

    def _add_file_row(self, relative, depth):
        """Ligne de fichier (case à cocher, épingle, nom, taille)."""
        key = relative.as_posix()
        row = ctk.CTkFrame(self.file_list, fg_color="transparent")
        row.pack(fill="x", padx=(10 + depth * 14, 4), pady=1)
        variable = tk.BooleanVar(value=key not in self.excluded)
        self._styled(
            ctk.CTkCheckBox(
                row, text="", variable=variable, command=self._make_toggle(key, variable),
                width=28, checkbox_width=18, checkbox_height=18, corner_radius=5,
                onvalue=True, offvalue=False,
            ),
            "check",
        ).pack(side="left")
        pin = self._styled(
            ctk.CTkButton(
                row, text="★" if key in self._pinned else "☆", width=28, height=28,
                corner_radius=8, font=self.font_tiny,
                command=lambda path=key: self._toggle_pin(path),
            ),
            "btn",
        )
        pin.pack(side="left", padx=(0, 2))
        self._pin_buttons[key] = pin
        button = self._styled(
            ctk.CTkButton(
                row, text=relative.name, anchor="w", height=32, corner_radius=8,
                fg_color="transparent", font=self.font_ui,
                command=lambda path=relative: self._open_from_list(path),
            ),
            "file_btn",
        )
        button.pack(side="left", fill="x", expand=True, padx=(2, 6))
        button.bind("<Double-Button-1>", lambda _event, path=relative: self._insert_file_mention(path))
        button.bind("<Button-3>", lambda event, path=relative: self._file_menu(event, path))
        try:
            stats = ollama_fs_access.file_stats(self.target_folder, relative)
        except Exception:
            stats = None
        pending = self._pending_content(key)
        if stats is not None:
            self._disk_size[key] = stats["size"]
            self._disk_mtime[key] = stats["mtime"]
            meta = self._styled(
                ctk.CTkLabel(
                    row, text=_file_meta_text(stats["size"], stats["mtime"], None),
                    font=self.font_tiny, anchor="e",
                ),
                "muted",
            )
            meta.pack(side="right", padx=(0, 4))
            self._file_meta[key] = meta
        elif pending is not None:
            self._disk_size[key] = 0
            self._disk_mtime[key] = 0
            meta = self._styled(
                ctk.CTkLabel(
                    row, text=_file_meta_text(0, None, len(pending.encode("utf-8")), created=True),
                    font=self.font_tiny, anchor="e",
                ),
                "accent",
            )
            meta.pack(side="right", padx=(0, 4))
            self._file_meta[key] = meta
        self._rows[key] = button
        self._checks[key] = variable

    def _fill_list(self, files):
        """Affiche le dossier sous forme d'arborescence dépliable."""
        for child in self.file_list.winfo_children():
            child.destroy()
        self._rows = {}
        self._checks = {}
        self._pin_buttons = {}
        self._file_meta = {}
        self._disk_size = {}
        self._disk_mtime = {}
        if not files:
            self._styled(
                ctk.CTkLabel(
                    self.file_list, text="Aucun fichier texte ici.\nChangez de dossier ou d'extensions.",
                    font=self.font_small, anchor="w", justify="left",
                ),
                "muted",
            ).pack(anchor="w", padx=8, pady=10)
            return

        entry = getattr(self, "search_entry", None)
        try:
            query = entry.get().strip() if entry is not None else ""
        except Exception:
            query = ""

        tree = lumio_tree.build_tree(files)
        rows = lumio_tree.flatten(tree, self._collapsed_dirs, query)
        root_name = Path(self.target_folder).name or str(self.target_folder)
        collapsed = {str(item).casefold() for item in self._collapsed_dirs}

        head = ctk.CTkFrame(self.file_list, fg_color="transparent")
        head.pack(fill="x", padx=6, pady=(4, 6))
        self._styled(
            ctk.CTkLabel(
                head, text=f"▾ {root_name}",
                font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"), anchor="w",
            ),
            "text",
        ).pack(fill="x")
        self._styled(
            ctk.CTkLabel(
                head,
                text=f"{len(files)} fichier(s) · {lumio_tree.count_dirs(tree)} dossier(s)",
                font=self.font_tiny, anchor="w",
            ),
            "muted",
        ).pack(fill="x")

        for kind, value, key, depth, total in rows:
            if kind == lumio_tree.DIR:
                folded = key.casefold() in collapsed
                row = ctk.CTkFrame(self.file_list, fg_color="transparent")
                row.pack(fill="x", padx=(6 + depth * 14, 4), pady=(3, 0))
                button = self._styled(
                    ctk.CTkButton(
                        row, text=f"{'▸' if folded else '▾'} {value}", anchor="w",
                        height=28, corner_radius=6, fg_color="transparent",
                        font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
                        command=lambda path=key: self._toggle_tree_dir(path),
                    ),
                    "file_btn",
                )
                button.pack(side="left", fill="x", expand=True)
                self._styled(
                    ctk.CTkLabel(
                        row, text=f"{total} fichier{'s' if total != 1 else ''}",
                        font=self.font_tiny, anchor="e",
                    ),
                    "muted",
                ).pack(side="right", padx=(0, 4))
                continue
            self._add_file_row(value, depth)
        self._refresh_file_meta()

    def _toggle_tree_dir(self, relative_key):
        """Replie ou déplie un dossier de l'arborescence."""
        relative_key = str(relative_key)
        if relative_key in self._collapsed_dirs:
            self._collapsed_dirs.discard(relative_key)
        else:
            self._collapsed_dirs.add(relative_key)
        self._fill_list(self._files_to_show())
        self._save_session()

    def _expand_all_tree(self):
        self._collapsed_dirs.clear()
        self._fill_list(self._files_to_show())
        self._save_session()

    def _collapse_all_tree(self):
        self._collapsed_dirs = lumio_tree.dir_keys(self._files_to_show())
        self._fill_list(self._files_to_show())
        self._save_session()
    def _files_to_show(self):
        files = list(self.visible_files)
        if self._pinned_only:
            files = [path for path in files if path.as_posix() in self._pinned]
        have = {path.as_posix().casefold() for path in files}
        for raw in self._pending_updates:
            if raw.casefold() not in have:
                files.append(Path(raw))
                have.add(raw.casefold())
        return files

    def _pending_content(self, key):
        for path, content in self._pending_updates.items():
            if path.casefold() == str(key).casefold():
                return content
        return None

    def _refresh_file_meta(self, relative=None):
        if relative is None:
            keys = set(self._file_meta)
            keys.update(self._pending_updates)
            if self.loaded_path is not None:
                keys.add(self.loaded_path.as_posix())
            for key in keys:
                self._refresh_file_meta(key)
            return
        target = Path(relative)
        key = target.as_posix()
        label = self._file_meta.get(key)
        if label is None:
            for stored, widget in self._file_meta.items():
                if stored.casefold() == key.casefold():
                    label = widget
                    key = stored
                    break
        if label is None or not label.winfo_exists():
            return
        disk = self._disk_size.get(key, 0)
        mtime = self._disk_mtime.get(key)
        pending = self._pending_content(key)
        buffer = None
        created = disk == 0 and pending is not None and key not in {path.as_posix() for path in self.visible_files}
        if pending is not None:
            buffer = len(pending.encode("utf-8"))
        label.configure(
            text=_file_meta_text(disk, mtime, buffer, created=created),
            text_color=self.C("accent") if buffer is not None else self.C("muted"),
        )

    def _is_typing(self):
        widget = self.focus_get()
        if widget is None:
            return False
        klass = widget.winfo_class()
        return klass in {"Entry", "Text", "TEntry", "TCombobox", "CTkTextbox"} or "Text" in str(klass)

    def _bind_question_entry(self):
        widgets = [self.question_entry]
        inner = getattr(self.question_entry, "_textbox", None)
        if inner is not None:
            widgets.append(inner)
        for widget in widgets:
            widget.bind("<Return>", self._on_question_return)
            widget.bind("<KP_Enter>", self._on_question_return)
            widget.bind("<Up>", self._on_question_up)
            widget.bind("<Down>", self._on_question_down)
            widget.bind("<Tab>", self._on_question_tab)
            widget.bind("<ISO_Left_Tab>", lambda event: self._on_question_tab(event, back=True))
            widget.bind("<Shift-Tab>", lambda event: self._on_question_tab(event, back=True))
            widget.bind("<KeyRelease>", lambda _event: self._schedule_send_preview())
            widget.bind("<Control-v>", self._on_question_paste)
            widget.bind("<Control-V>", self._on_question_paste)
            widget.bind("<<Paste>>", self._on_question_paste)

    def _on_question_return(self, event):
        if event.state & 0x1:
            return None
        self.ask_model()
        return "break"

    def _on_question_up(self, _event=None):
        return self._recall_question(-1)

    def _on_question_down(self, _event=None):
        return self._recall_question(1)

    def _recall_question(self, step):
        if not self._question_stack:
            return None
        current = self._question_text()
        if current.strip() and self._stack_index is None:
            return None
        if self._stack_index is None:
            self._stack_index = len(self._question_stack)
        nxt = self._stack_index + step
        if nxt < 0 or nxt >= len(self._question_stack):
            return "break"
        self._stack_index = nxt
        self._clear_question()
        self._insert_question(self._question_stack[self._stack_index])
        self._schedule_send_preview()
        self._set_status(f"Question {self._stack_index + 1}/{len(self._question_stack)}")
        return "break"

    def _schedule_file_search(self, event=None):
        if event is not None and getattr(event, "keysym", "") in {
            "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
            "Up", "Down", "Left", "Right", "Home", "End", "Tab",
        }:
            return
        job = getattr(self, "_search_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._search_job = self.after(280, self._run_file_search)

    def _run_file_search(self):
        self._search_job = None
        self.reload_files(confirm=False)

    def _autosave_draft(self):
        if not self._streaming:
            try:
                self._save_session()
            except Exception:
                pass
        self.after(20_000, self._autosave_draft)

    def copy_chat(self):
        rows = self._chat_snapshot()
        if not rows:
            messagebox.showinfo(APP_NAME, "Rien à copier pour le moment.")
            return "break"
        title = _chat_title_from(rows, self.history)
        lines = [f"# {title}", ""]
        for item in rows:
            who = "Vous" if item["role"] == "user" else "Lumio"
            lines.append(f"## {who}")
            lines.append("")
            lines.append(item["content"])
            lines.append("")
        self._copy_text("\n".join(lines).rstrip() + "\n")
        self._set_status("Conversation copiée")
        return "break"

    def show_help(self):
        messagebox.showinfo(
            APP_NAME,
            "Entrée — envoyer\n"
            "Maj+Entrée — nouvelle ligne\n"
            "Tab — nom de fichier\n"
            "↑ / ↓ — questions précédentes\n"
            "Ctrl+F / F3 — chercher dans le chat\n"
            "Ctrl+Maj+F — chercher dans toutes les conversations\n"
            "Ctrl+K — palette de commandes (modèle, chat, dossier…)\n"
            "Vérifier (barre d'actions) — lance la vérification et réinjecte la sortie\n"
            "Réglages (Ctrl+K) — vérification auto, outils, commandes autorisées, routage\n"
            "Le modèle peut demander « rechercher: motif » ou « lire: chemin »\n"
            "Ctrl+Maj+K — aller à la question\n"
            "Ctrl+L — recherche de fichiers\n"
            "Ctrl++ / − / 0 — zoom\n"
            "Ctrl+N — nouvelle conversation\n"
            "Ctrl+E — exporter\n"
            "Ctrl+Maj+C — copier la conversation\n"
            "Ctrl+Maj+S — capture\n"
            "Ctrl+R — relancer\n"
            "Échap — arrêter\n"
            "F1 — cette aide\n"
            "API — clé pour un modèle en ligne (OpenRouter, DeepSeek…)\n"
            "Clic sur le modèle — liste uncensored (Dolphin, Venice, MythoMax…)\n"
            "Double-clic / clic droit — fichier",
        )
        return "break"

    # ------------------------------------------------------------------
    #  Palette de commandes (Ctrl+K) et recherche globale (Ctrl+Maj+F)
    # ------------------------------------------------------------------
    def _palette_commands(self):
        """(libellé, indice, action) proposés par la palette."""
        rows = [
            ("Nouvelle conversation", "Ctrl+N", self.new_chat),
            ("Chercher dans toutes les conversations", "Ctrl+Maj+F", self.search_all_chats),
            ("Choisir un modèle…", "", self.pick_model),
            ("Catalogue de modèles en ligne", "", self.browse_catalog),
            ("Revenir au modèle de la conversation", "", self.restore_chat_model),
            ("Moteur : Ollama", "", lambda: self._on_provider("Ollama")),
            ("Moteur : PrivateGPT", "", lambda: self._on_provider("PrivateGPT")),
            ("Moteur : En ligne", "", lambda: self._on_provider("En ligne")),
            ("Clé API en ligne", "", self.edit_online_api),
            ("Changer de dossier…", "", self.choose_folder),
            ("Actualiser les modèles", "", self.refresh_models),
            ("Exporter la conversation", "Ctrl+E", self.export_chat),
            ("Coût et tokens (7 jours)", "", self.usage_stats),
            ("Réglages (vérification, outils, routage)", "", self.open_settings),
            ("Vérifier le dossier maintenant", "", lambda: self.run_verification(auto=False)),
            ("Versions des fichiers…", "", self.versions_dialog),
            ("Aide et raccourcis", "F1", self.show_help),
        ]
        for name in list(self._all_models)[:40]:
            rows.append((f"Modèle : {name}", "actuel" if name == self._current_model() else "",
                         lambda item=name: self._on_model(item, force=True)))
        for record in self._chats_for_save()[:15]:
            title = record.get("title") or "Sans titre"
            rows.append((f"Conversation : {title}", "", lambda rec=record: self._switch_chat(rec)))
        for folder in self.recent_folders[:6]:
            rows.append((f"Dossier : {Path(folder).name or folder}", str(folder),
                         lambda f=folder: self._switch_folder(f)))
        return rows

    def command_palette(self):
        """Ctrl+K : tout piloter au clavier (modèle, moteur, chat, dossier…)."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Commande")
        dialog.geometry("620x460")
        dialog.minsize(480, 320)
        dialog.configure(fg_color=self.C("bg"))
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        entry_var = tk.StringVar()
        entry = self._styled(
            ctk.CTkEntry(
                dialog, textvariable=entry_var, height=38, corner_radius=10, font=self.font_ui,
                placeholder_text="Que voulez-vous faire ? (modèle, conversation, dossier…)",
            ),
            "entry",
        )
        entry.pack(fill="x", padx=14, pady=(14, 8))

        listing = self._styled(ctk.CTkScrollableFrame(dialog, corner_radius=10), "well")
        listing.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        self._styled(
            ctk.CTkLabel(
                dialog, text="Entrée : première commande  ·  Échap : fermer",
                font=self.font_tiny, anchor="w",
            ),
            "muted",
        ).pack(fill="x", padx=16, pady=(0, 12))

        actions = self._palette_commands()
        state = {"items": []}

        def close():
            try:
                dialog.destroy()
            except Exception:
                pass

        def run(action):
            close()
            try:
                action()
            except Exception:
                report_error(*sys.exc_info())

        def fill(_event=None):
            for child in listing.winfo_children():
                child.destroy()
            needle = lumio_search.normalize(entry_var.get()).strip()
            items = []
            for label, hint, action in actions:
                haystack = lumio_search.normalize(f"{label} {hint}")
                if needle and needle not in haystack:
                    continue
                items.append((label, hint, action))
            state["items"] = items[:16]
            for label, hint, action in state["items"]:
                row = ctk.CTkFrame(listing, fg_color="transparent")
                row.pack(fill="x", pady=2)
                self._button(row, label, lambda act=action: run(act), width=390).pack(side="left")
                if hint:
                    self._styled(
                        ctk.CTkLabel(row, text=hint, font=self.font_tiny, anchor="w"), "muted"
                    ).pack(side="left", padx=(8, 0))
            if not items:
                self._styled(
                    ctk.CTkLabel(listing, text="Aucune commande ne correspond.", font=self.font_small),
                    "muted",
                ).pack(pady=12)
            self._bind_scrollable_wheel(listing, dialog)

        def on_return(_event=None):
            if state["items"]:
                run(state["items"][0][2])
            return "break"

        entry.bind("<KeyRelease>", fill)
        entry.bind("<Return>", on_return)
        dialog.bind("<Escape>", lambda _event: close())
        dialog.protocol("WM_DELETE_WINDOW", close)
        fill()
        entry.focus_set()
        self.wait_window(dialog)

    def search_all_chats(self, initial=""):
        """Ctrl+Maj+F : chercher dans toutes les conversations."""
        chats = self._chats_for_save()
        dialog = ctk.CTkToplevel(self)
        dialog.title("Rechercher dans les conversations")
        dialog.geometry("760x520")
        dialog.minsize(560, 360)
        dialog.configure(fg_color=self.C("bg"))
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        entry_var = tk.StringVar(value=str(initial or ""))
        entry = self._styled(
            ctk.CTkEntry(
                dialog, textvariable=entry_var, height=38, corner_radius=10, font=self.font_ui,
                placeholder_text="Mot ou expression (accents et casse ignorés)",
            ),
            "entry",
        )
        entry.pack(fill="x", padx=14, pady=(14, 6))

        count = self._styled(ctk.CTkLabel(dialog, text="", font=self.font_tiny, anchor="w"), "muted")
        count.pack(fill="x", padx=16)

        listing = self._styled(ctk.CTkScrollableFrame(dialog, corner_radius=10), "well")
        listing.pack(fill="both", expand=True, padx=14, pady=(6, 14))

        job = {"id": None}
        state = {"hits": []}

        def close():
            try:
                dialog.destroy()
            except Exception:
                pass

        def open_hit(hit):
            close()
            record = next((item for item in chats if item.get("id") == hit.chat_id), None)
            if record is not None:
                self._switch_chat(record)
                who = "vous" if hit.role == "user" else "Lumio"
                self._set_status(f"« {entry_var.get()} » — message de {who}")

        def fill():
            for child in listing.winfo_children():
                child.destroy()
            needle = entry_var.get().strip()
            state["hits"] = lumio_search.search_chats(chats, needle) if needle else []
            count.configure(text=f"{len(state['hits'])} résultat(s)")
            if not needle:
                self._styled(
                    ctk.CTkLabel(listing, text="Tapez un mot pour chercher partout.", font=self.font_small),
                    "muted",
                ).pack(pady=10)
                return
            if not state["hits"]:
                self._styled(
                    ctk.CTkLabel(listing, text="Aucune correspondance.", font=self.font_small),
                    "muted",
                ).pack(pady=10)
                return
            for hit in state["hits"]:
                card = self._styled(ctk.CTkFrame(listing, corner_radius=8), "raised")
                card.pack(fill="x", padx=4, pady=2)
                self._button(card, "Ouvrir", lambda h=hit: open_hit(h), width=80).pack(
                    side="right", padx=8, pady=6
                )
                body = ctk.CTkFrame(card, fg_color="transparent")
                body.pack(side="left", fill="x", expand=True, padx=8, pady=6)
                self._styled(
                    ctk.CTkLabel(body, text=hit.label(), font=self.font_small, anchor="w"), "text"
                ).pack(fill="x")
                self._styled(
                    ctk.CTkLabel(body, text=hit.snippet, font=self.font_tiny, anchor="w", justify="left"),
                    "muted",
                ).pack(fill="x")
            self._bind_scrollable_wheel(listing, dialog)

        def schedule(_event=None):
            if job["id"] is not None:
                try:
                    dialog.after_cancel(job["id"])
                except Exception:
                    pass
            job["id"] = dialog.after(180, fill)

        entry.bind("<KeyRelease>", schedule)
        dialog.bind("<Escape>", lambda _event: close())
        dialog.protocol("WM_DELETE_WINDOW", close)
        fill()
        entry.focus_set()
        self.wait_window(dialog)

    # ------------------------------------------------------------------
    #  Réglages : vérification, permissions, outils, routage
    # ------------------------------------------------------------------
    def open_settings(self):
        settings = dict(self.settings)
        dialog = ctk.CTkToplevel(self)
        dialog.title("Réglages")
        dialog.geometry("720x620")
        dialog.minsize(560, 460)
        dialog.configure(fg_color=self.C("bg"))
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(
            dialog, text="Réglages", text_color=self.C("text"),
            font=self.font_heading, anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 6))

        body = self._styled(ctk.CTkScrollableFrame(dialog, corner_radius=10), "well")
        body.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        self._styled(
            ctk.CTkLabel(body, text="VÉRIFICATION (BOUCLE AGENT)", font=self.font_tiny, anchor="w"),
            "muted",
        ).pack(fill="x", padx=6, pady=(8, 2))
        verify_var = tk.BooleanVar(value=bool(settings.get("verify_auto")))
        self._styled(
            ctk.CTkCheckBox(body, text="Vérifier automatiquement après chaque écriture",
                            variable=verify_var, font=self.font_small,
                            checkbox_width=18, checkbox_height=18),
            "check",
        ).pack(fill="x", padx=6, pady=(2, 6))
        apply_var = tk.BooleanVar(value=bool(settings.get("auto_apply")))
        self._styled(
            ctk.CTkCheckBox(body, text="Appliquer les fichiers sans clic (dangereux : instantanés conservés)",
                            variable=apply_var, font=self.font_small,
                            checkbox_width=18, checkbox_height=18),
            "check",
        ).pack(fill="x", padx=6, pady=(0, 4))
        limits_row = ctk.CTkFrame(body, fg_color="transparent")
        limits_row.pack(fill="x", padx=6, pady=(0, 6))
        files_var = tk.StringVar(value=str(settings.get("auto_apply_max_files") or 2))
        bytes_var = tk.StringVar(value=str(settings.get("auto_apply_max_bytes") or 60000))
        self._styled(ctk.CTkLabel(limits_row, text="Limites auto : max fichiers", font=self.font_tiny), "muted").pack(side="left")
        self._styled(
            ctk.CTkEntry(limits_row, textvariable=files_var, width=56, height=28, font=self.font_small),
            "entry",
        ).pack(side="left", padx=(6, 0))
        self._styled(ctk.CTkLabel(limits_row, text="  max octets", font=self.font_tiny), "muted").pack(side="left")
        self._styled(
            ctk.CTkEntry(limits_row, textvariable=bytes_var, width=90, height=28, font=self.font_small),
            "entry",
        ).pack(side="left", padx=(6, 0))
        command_row = ctk.CTkFrame(body, fg_color="transparent")
        command_row.pack(fill="x", padx=6, pady=(0, 6))
        command_var = tk.StringVar(value=str(settings.get("verify_command") or ""))
        self._styled(
            ctk.CTkEntry(command_row, textvariable=command_var, height=32, corner_radius=8,
                         font=self.font_small, placeholder_text="Commande de vérification (vide = détection)"),
            "entry",
        ).pack(side="left", fill="x", expand=True)

        def detect():
            candidates = lumio_verify.detect_commands(self.target_folder, changed=self._recent_paths())
            if candidates:
                command_var.set(candidates[0])
            else:
                command_var.set("python -m py_compile app.py")

        self._button(command_row, "Détecter", detect, width=90).pack(side="left", padx=(6, 0))
        rounds_var = tk.StringVar(value=str(settings.get("verify_max_rounds") or 2))
        rounds_row = ctk.CTkFrame(body, fg_color="transparent")
        rounds_row.pack(fill="x", padx=6, pady=(0, 10))
        self._styled(ctk.CTkLabel(rounds_row, text="Tentatives de correction :", font=self.font_small), "text").pack(side="left")
        self._styled(
            ctk.CTkEntry(rounds_row, textvariable=rounds_var, width=60, height=30, font=self.font_small),
            "entry",
        ).pack(side="left", padx=(8, 0))

        self._styled(
            ctk.CTkLabel(body, text="OUTILS DU MODÈLE", font=self.font_tiny, anchor="w"), "muted",
        ).pack(fill="x", padx=6, pady=(6, 2))
        tools_var = tk.BooleanVar(value=bool(settings.get("tools_enabled")))
        self._styled(
            ctk.CTkCheckBox(body, text="Autoriser ```rechercher:``` et ```lire:``` (le modèle consulte le dossier)",
                            variable=tools_var, font=self.font_small,
                            checkbox_width=18, checkbox_height=18),
            "check",
        ).pack(fill="x", padx=6, pady=(2, 10))

        self._styled(
            ctk.CTkLabel(body, text="COMMANDES AUTORISÉES (UNE PAR LIGNE)", font=self.font_tiny, anchor="w"),
            "muted",
        ).pack(fill="x", padx=6, pady=(6, 2))
        allow_box = self._styled(
            ctk.CTkTextbox(body, height=110, font=ctk.CTkFont(family="Consolas", size=12), wrap="none"),
            "textbox",
        )
        allow_box.pack(fill="x", padx=6, pady=(2, 10))
        allow_box.insert("1.0", "\n".join(settings.get("allowlist") or []))

        self._styled(
            ctk.CTkLabel(body, text="ROUTAGE LOCAL / DISTANT", font=self.font_tiny, anchor="w"), "muted",
        ).pack(fill="x", padx=6, pady=(6, 2))
        router_var = tk.BooleanVar(value=bool(settings.get("router_enabled")))
        self._styled(
            ctk.CTkCheckBox(body, text="Choisir automatiquement le moteur selon la difficulté",
                            variable=router_var, font=self.font_small,
                            checkbox_width=18, checkbox_height=18),
            "check",
        ).pack(fill="x", padx=6, pady=(2, 6))
        policy_row = ctk.CTkFrame(body, fg_color="transparent")
        policy_row.pack(fill="x", padx=6, pady=(0, 6))
        labels = [lumio_router.POLICY_LABELS[key] for key in lumio_router.POLICIES]
        policy_box = self._styled(
            ctk.CTkComboBox(policy_row, values=labels, width=260, height=32, font=self.font_small),
            "combo",
        )
        policy_box.pack(side="left")
        policy_box.set(lumio_router.POLICY_LABELS.get(settings.get("router_policy") or "auto", labels[0]))
        ceiling_var = tk.StringVar(value=str(settings.get("router_ceiling") or "0.05"))
        self._styled(
            ctk.CTkLabel(policy_row, text="  plafond $ / échange :", font=self.font_small), "text",
        ).pack(side="left", padx=(10, 0))
        self._styled(
            ctk.CTkEntry(policy_row, textvariable=ceiling_var, width=80, height=32, font=self.font_small),
            "entry",
        ).pack(side="left", padx=(6, 0))
        turn_row = ctk.CTkFrame(body, fg_color="transparent")
        turn_row.pack(fill="x", padx=6, pady=(0, 8))
        turn_var = tk.StringVar(value=str(settings.get("turn_ceiling") or "0.10"))
        self._styled(ctk.CTkLabel(turn_row, text="Budget $ par question (0 = illimité) :", font=self.font_small), "text").pack(side="left")
        self._styled(
            ctk.CTkEntry(turn_row, textvariable=turn_var, width=80, height=30, font=self.font_small),
            "entry",
        ).pack(side="left", padx=(8, 0))

        bar = ctk.CTkFrame(dialog, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 14))

        def save():
            try:
                rounds = max(0, min(5, int(rounds_var.get() or 0)))
            except ValueError:
                rounds = 2
            try:
                ceiling = max(0.0, float((ceiling_var.get() or "0").replace(",", ".")))
            except ValueError:
                ceiling = 0.05
            allow = [line.strip() for line in allow_box.get("1.0", "end").splitlines() if line.strip()]
            label_to_key = {value: key for key, value in lumio_router.POLICY_LABELS.items()}
            try:
                turn_ceiling = max(0.0, float((turn_var.get() or "0").replace(",", ".")))
            except ValueError:
                turn_ceiling = 0.10
            try:
                max_files = max(0, int(files_var.get() or 0))
                max_bytes = max(0, int(bytes_var.get() or 0))
            except ValueError:
                max_files, max_bytes = 2, 60000
            updated = dict(self.settings)
            updated.update({
                "verify_command": command_var.get().strip(),
                "verify_auto": bool(verify_var.get()),
                "auto_apply": bool(apply_var.get()),
                "verify_max_rounds": rounds,
                "allowlist": allow or list(lumio_verify.DEFAULT_ALLOWLIST),
                "tools_enabled": bool(tools_var.get()),
                "tools_max_steps": int(settings.get("tools_max_steps") or 2),
                "router_enabled": bool(router_var.get()),
                "router_policy": label_to_key.get(policy_box.get(), "auto"),
                "router_ceiling": ceiling,
                "turn_ceiling": turn_ceiling,
                "auto_apply_max_files": max_files,
                "auto_apply_max_bytes": max_bytes,
            })
            self.settings = updated
            self._save_session(immediate=True)
            dialog.destroy()
            self._set_status("Réglages enregistrés")

        self._button(bar, "Enregistrer", save, primary=True).pack(side="right")
        self._button(bar, "Annuler", lambda: dialog.destroy()).pack(side="right", padx=(0, 8))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.protocol("WM_DELETE_WINDOW", lambda: dialog.destroy())
        self.wait_window(dialog)

    def _recent_paths(self):
        return [item.get("path") for item in self._write_backup[-5:] if isinstance(item, dict)]

    def verification_command(self):
        command = str(self.settings.get("verify_command") or "").strip()
        if command:
            return command
        candidates = lumio_verify.detect_commands(self.target_folder, changed=self._recent_paths())
        return candidates[0] if candidates else ""

    def run_verification(self, auto=False):
        if self._streaming:
            self._set_status("Attendez la fin de la réponse avant de vérifier")
            return
        command = self.verification_command()
        if not command:
            self._add_bubble(
                "notice",
                "Aucune commande de vérification détectée dans ce dossier.\n"
                "Réglages (Ctrl+K) → « Commande de vérification ».",
            )
            return
        allow = self.settings.get("allowlist") or list(lumio_verify.DEFAULT_ALLOWLIST)
        self._set_status(f"Vérification : {command}")
        self._add_bubble("notice", f"Vérification en cours…\n$ {command}")

        def work():
            result = lumio_verify.run_command(command, self.target_folder, timeout=300, allowlist=allow)
            self._bg_events.put(("verify", result, bool(auto)))

        threading.Thread(target=work, daemon=True).start()

    def _on_verify(self, result, auto):
        text = lumio_verify.format_result(result)
        self._add_bubble("notice", text)
        if result.get("ok"):
            self._verify_rounds = 0
            self._set_status("Vérification réussie")
            return
        self._set_status("Vérification en échec")
        max_rounds = int(self.settings.get("verify_max_rounds") or 0)
        if not auto or self._verify_rounds >= max_rounds or self._streaming:
            return
        if self._budget_exhausted():
            return
        self._verify_rounds += 1
        self._set_status(f"Correction automatique ({self._verify_rounds}/{max_rounds})…")
        question = (
            "La commande de vérification a échoué. Voici sa sortie :\n\n"
            f"{text}\n\n"
            "Corrige le ou les fichiers nécessaires pour que cette commande passe. "
            "Réponds avec des blocs de fichiers complets."
        )
        self._auto_followup = True
        self.ask_model(question=question)

    def _schedule_verify(self):
        if not self.settings.get("verify_auto"):
            return
        job = getattr(self, "_verify_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._verify_job = self.after(900, lambda: self.run_verification(auto=True))

    def versions_dialog(self):
        rows = lumio_snapshots.entries(folder=self.target_folder, limit=60)
        dialog = ctk.CTkToplevel(self)
        dialog.title("Versions des fichiers")
        dialog.geometry("720x520")
        dialog.minsize(520, 360)
        dialog.configure(fg_color=self.C("bg"))
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(
            dialog, text="Versions des fichiers", text_color=self.C("text"),
            font=self.font_heading, anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 2))
        self._styled(
            ctk.CTkLabel(
                dialog, text="Chaque écriture de Lumio archive la version précédente.",
                font=self.font_tiny, anchor="w",
            ),
            "muted",
        ).pack(fill="x", padx=16, pady=(0, 8))

        listing = self._styled(ctk.CTkScrollableFrame(dialog, corner_radius=10), "well")
        listing.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        if not rows:
            self._styled(
                ctk.CTkLabel(listing, text="Aucune version enregistrée pour ce dossier.", font=self.font_small),
                "muted",
            ).pack(pady=12)

        def make_restore(entry):
            def restore():
                try:
                    target = lumio_snapshots.restore(entry, folder=self.target_folder)
                except Exception as error:
                    messagebox.showerror(APP_NAME, f"Restauration impossible : {error}")
                    return
                self.reload_files(confirm=False)
                self._add_bubble("notice", f"Version restaurée : {target.name} ({entry.get('at')})")
                self._set_status("Version restaurée")
                dialog.destroy()

            return restore

        for entry in rows:
            card = self._styled(ctk.CTkFrame(listing, corner_radius=8), "raised")
            card.pack(fill="x", padx=4, pady=2)
            self._button(card, "Restaurer", make_restore(entry), width=90).pack(side="right", padx=8, pady=6)
            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(side="left", fill="x", expand=True, padx=8, pady=6)
            self._styled(
                ctk.CTkLabel(body, text=str(entry.get("relative")), font=self.font_small, anchor="w"), "text",
            ).pack(fill="x")
            self._styled(
                ctk.CTkLabel(
                    body,
                    text=f"{entry.get('at')} · {int(entry.get('size') or 0)} octets · {entry.get('reason')}",
                    font=self.font_tiny, anchor="w",
                ),
                "muted",
            ).pack(fill="x")
        self._bind_scrollable_wheel(listing, dialog)
        self._button(dialog, "Fermer", lambda: dialog.destroy()).pack(pady=(0, 12))

    def _budget_exhausted(self):
        """Vrai si le budget de la question est atteint (et le signale une fois)."""
        ceiling = float(self.settings.get("turn_ceiling") or 0)
        if not lumio_cost.over_budget(self._turn_cost, ceiling):
            return False
        self._add_bubble(
            "notice",
            f"Budget de cette question atteint : {lumio_cost.format_cost(self._turn_cost)} "
            f"pour un plafond de {lumio_cost.format_cost(ceiling)}. "
            "Aucune nouvelle tentative automatique n'est lancée.",
        )
        self._set_status("Budget de la question atteint")
        return True

    def _maybe_auto_apply(self):
        """Applique les fichiers sans clic quand le mode agent est demandé."""
        if self._streaming or not self.settings.get("auto_apply"):
            return
        updates = self._real_updates()
        reason = lumio_tools.too_big(
            updates,
            int(self.settings.get("auto_apply_max_files") or 0),
            int(self.settings.get("auto_apply_max_bytes") or 0),
        )
        if reason:
            self._add_bubble(
                "notice",
                f"Application automatique refusée ({reason}) : acceptez les fichiers à la main.",
            )
            self._set_status("Trop volumineux pour l'application automatique")
            return
        self._add_bubble("notice", "Application automatique des fichiers (mode agent).")
        try:
            if self._apply_real_updates():
                self.after(80, self._accept_all_diffs)
        except Exception:
            report_error(*sys.exc_info())

    def _maybe_run_tools(self):
        if not self.settings.get("tools_enabled") or self._streaming:
            return
        max_steps = int(self.settings.get("tools_max_steps") or 0)
        if getattr(self, "_tool_steps", 0) >= max_steps:
            return
        if self._budget_exhausted():
            return
        calls = lumio_tools.parse_calls(self.last_reply)
        if not calls:
            return
        results = lumio_tools.execute_all(calls[:3], self.target_folder)
        for item in results:
            self._add_bubble("notice", f"{item['title']}\n{str(item['output'])[:1500]}")
        self._tool_steps += 1
        question = (
            f"{self._pending_question}\n\n{lumio_tools.format_for_model(results)}\n\n"
            "Réponds maintenant à la question initiale, sans redemander ces informations."
        )
        self._auto_followup = True
        self.ask_model(question=question)

    def _route_question(self, question):
        if not self.settings.get("router_enabled"):
            return
        files = list(self._last_sent_files or [])[:40]
        context_chars = sum(int(self._disk_size.get(Path(item).as_posix()) or 0) for item in files)
        local_model = self._model_by_provider.get("ollama") or ""
        remote_model = self._model_by_provider.get("online") or ""
        price = ollama_fs_access.catalog_price(remote_model) or {}
        estimated = lumio_router.estimate_cost(context_chars, (price or {}).get("prompt") or 0.15)
        decision = lumio_router.decide(
            self.settings.get("router_policy"),
            question,
            files_count=len(files),
            context_chars=context_chars,
            local_ready=bool(local_model),
            remote_ready=bool(remote_model),
            estimated_cost=estimated,
            ceiling=float(self.settings.get("router_ceiling") or 0),
        )
        target = "online" if decision["provider"] == "remote" else "ollama"
        if target == self._backend():
            self._add_bubble("notice", f"Routage : {decision['reason']} · {self._current_model()}")
            return
        model = self._model_by_provider.get(target) or ""
        if not model:
            self._set_status(f"Routage : {decision['reason']} — aucun modèle « {target} » sélectionné")
            return
        self.provider = target
        if getattr(self, "provider_box", None) is not None:
            self.provider_box.set(self._backend_name())
        self.model_var.set(model)
        self._refresh_model_button()
        self._add_bubble("notice", f"Routage : {decision['reason']} → {self._backend_name()} · {model}")

    def _file_menu(self, event, path):
        key = Path(path).as_posix()
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Aperçu", command=lambda: self._show_file(path))
        menu.add_command(label="Insérer le nom", command=lambda: self._insert_file_mention(path))
        menu.add_command(label="Ouvrir", command=lambda: (self._show_file(path), self.open_selected()))
        menu.add_command(label="Dossier", command=lambda: (self._show_file(path), self.reveal_selected()))
        menu.add_command(label="Copier le chemin", command=lambda: self._copy_text(key))
        menu.add_separator()
        menu.add_command(
            label="Désépingler" if key in self._pinned else "Épingler",
            command=lambda: self._toggle_pin(key),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _on_question_tab(self, _event=None, back=False):
        inner = getattr(self.question_entry, "_textbox", None)
        if inner is None:
            return "break"
        try:
            before = inner.get("insert linestart", "insert")
        except Exception:
            return "break"
        current = re.split(r"[\s`\"']+", before)[-1]
        cycling = current in self._tab_matches
        token = self._tab_token if cycling else current
        if not token:
            return "break"
        names = [path.as_posix() for path in self.visible_files]
        low = token.casefold()
        matches = [name for name in names if Path(name).name.casefold().startswith(low) or name.casefold().startswith(low)]
        if not matches:
            matches = [name for name in names if low in Path(name).name.casefold()]
        if not matches:
            self._set_status("Aucun fichier pour cette complétion")
            return "break"
        if cycling:
            step = -1 if back else 1
            self._tab_index = (getattr(self, "_tab_index", 0) + step) % len(matches)
            self._tab_matches = matches
        else:
            self._tab_token = token
            self._tab_matches = matches
            self._tab_index = 0
        pick = matches[self._tab_index]
        try:
            inner.delete(f"insert-{len(current)}c", "insert")
            inner.insert("insert", pick)
        except Exception:
            return "break"
        extra = f"  ({self._tab_index + 1}/{len(matches)})" if len(matches) > 1 else ""
        self._set_status(f"Complété : {pick}{extra}")
        self._schedule_send_preview()
        return "break"

    def _on_zoom_wheel(self, event):
        self._nudge_zoom(1 if getattr(event, "delta", 0) > 0 else -1)
        return "break"

    def _apply_zoom(self):
        delta = int(getattr(self, "_zoom", 0) or 0)
        try:
            self.font_ui.configure(size=max(10, 13 + delta))
            self.font_small.configure(size=max(9, 12 + delta))
            self.font_tiny.configure(size=max(9, 11 + max(-2, delta - 1)))
            self.font_editor.configure(size=max(10, 14 + delta))
        except Exception:
            pass

    def _nudge_zoom(self, step, reset=False):
        self._zoom = 0 if reset else max(-3, min(8, getattr(self, "_zoom", 0) + step))
        self._apply_zoom()
        self._save_session()
        self._set_status("Texte par défaut" if reset else f"Zoom {self._zoom:+d}")
        return "break"

    def _warm_ollama(self):
        provider = self._backend()
        threading.Thread(target=lambda: ollama_fs_access.backend_up(provider), daemon=True).start()
        self.after(120_000, self._warm_ollama)

    def _refresh_services(self):
        """Voyants Ollama / PrivateGPT / clé en ligne, vérifiés en arrière-plan."""
        def work():
            states = (
                ollama_fs_access.ollama_up(),
                ollama_fs_access.pgpt_up(),
                bool(ollama_fs_access.online_config().get("key")),
            )
            self._bg_events.put(("services", states))

        threading.Thread(target=work, daemon=True).start()
        self.after(30_000, self._refresh_services)

    def _apply_services(self, states):
        label = getattr(self, "services_state", None)
        if label is None:
            return
        ollama_ok, pgpt_ok, has_key = states
        text = "  ·  ".join(
            (
                f"Ollama {'✓' if ollama_ok else '✗'}",
                f"PrivateGPT {'✓' if pgpt_ok else '✗'}",
                f"clé {'✓' if has_key else '✗'}",
            )
        )
        up = {"ollama": ollama_ok, "pgpt": pgpt_ok, "online": has_key}.get(self._backend(), False)
        label.configure(text=text)
        self._paint(label, "sage" if up else "danger")

    # ------------------------------------------------------------------
    #  Coût et tokens (catalogue = tarifs par million de tokens)
    # ------------------------------------------------------------------
    def _warm_prices(self):
        """Précharge le catalogue pour connaître les tarifs (une requête, mise en cache)."""
        if not ollama_fs_access.catalog_secret("openrouter"):
            return

        def work():
            try:
                ollama_fs_access.catalog_models("openrouter")
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _account_usage(self):
        """Comptabilise les tokens du dernier échange et met à jour le compteur."""
        usage = dict(getattr(ollama_fs_access, "LAST_USAGE", {}) or {})
        if not usage:
            self._last_cost_text = ""
            return None
        ollama_fs_access.LAST_USAGE.clear()

        model = self._current_model()
        provider = usage.get("provider") or (
            "online" if self._backend() == "online" else "local"
        )
        price = ollama_fs_access.catalog_price(model)
        entry = lumio_cost.make_entry(model, provider, usage, price)
        try:
            lumio_cost.record(entry)
        except OSError:
            pass

        self._session_tokens += entry["tokens"]
        self._session_cost += entry["cost"]
        self._turn_cost += entry["cost"]
        local = entry["cost"] <= 0 and str(provider) != "online"
        if str(provider) == "online" and not price:
            cost_text = "coût inconnu"
        else:
            cost_text = lumio_cost.format_cost(entry["cost"], local=local)
        self._last_cost_text = f"{lumio_cost.format_tokens(entry['tokens'])} · {cost_text}"

        label = getattr(self, "cost_state", None)
        if label is not None:
            label.configure(
                text=f"{lumio_cost.format_tokens(self._session_tokens)} · "
                     f"{lumio_cost.format_cost(self._session_cost)}"
            )
            self._paint(label, "muted")
        return entry

    def usage_stats(self):
        """Panneau « Coût et tokens » (7 derniers jours, par modèle)."""
        lines = lumio_cost.summary_lines(days=7)
        dialog = ctk.CTkToplevel(self)
        dialog.title("Coût et tokens")
        dialog.geometry("620x480")
        dialog.minsize(480, 320)
        dialog.configure(fg_color=self.C("bg"))
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(
            dialog, text="Coût et tokens", text_color=self.C("text"),
            font=self.font_heading, anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 4))
        self._styled(
            ctk.CTkLabel(
                dialog,
                text="Tarifs du catalogue, par million de tokens. Les modèles locaux ne coûtent rien.",
                font=self.font_tiny, anchor="w", justify="left", wraplength=560,
            ),
            "muted",
        ).pack(fill="x", padx=16, pady=(0, 8))

        box = self._styled(
            ctk.CTkTextbox(dialog, font=ctk.CTkFont(family="Consolas", size=12), wrap="word"),
            "textbox",
        )
        box.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")

        bar = ctk.CTkFrame(dialog, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 14))

        def open_ledger():
            try:
                os.startfile(lumio_cost.ledger_path().parent)  # type: ignore[attr-defined]
            except Exception as exc:
                self._set_status(f"Dossier introuvable : {exc}")

        self._button(bar, "Ouvrir le dossier", open_ledger, width=150).pack(side="left")
        self._button(bar, "Fermer", lambda: dialog.destroy(), primary=True).pack(side="right")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.protocol("WM_DELETE_WINDOW", lambda: dialog.destroy())
        self.wait_window(dialog)

    def _on_escape(self, _event=None):
        if self._streaming:
            self.cancel_ask()
            return "break"
        return None

    def _question_text(self):
        box = getattr(self, "question_entry", None)
        if box is None:
            return ""
        if getattr(box, "_textbox", None) is not None:
            return box.get("1.0", "end-1c")
        return box.get()

    def _clear_question(self):
        box = getattr(self, "question_entry", None)
        if box is None:
            return
        if getattr(box, "_textbox", None) is not None:
            box.delete("1.0", "end")
        else:
            box.delete(0, "end")
        self._apply_composer_mode(False)

    def _insert_question(self, text):
        box = getattr(self, "question_entry", None)
        if box is None:
            return
        try:
            if getattr(box, "_textbox", None) is not None:
                if box.tag_ranges("sel"):
                    box.delete("sel.first", "sel.last")
                box.insert("insert", text)
            else:
                box.insert("insert", text)
        except Exception:
            try:
                box.insert("end", text)
            except Exception:
                pass

    def _clipboard_text(self):
        try:
            return self.clipboard_get()
        except Exception:
            return ""

    def _looks_like_console(self, text):
        body = str(text or "")
        if "\n" not in body:
            return False
        if "\t" in body or "  " in body:
            return True
        if any(0x2500 <= ord(ch) <= 0x257F for ch in body):
            return True
        lines = [line for line in body.split("\n") if line.strip()]
        if len(lines) >= 3:
            widths = [len(line.rstrip()) for line in lines]
            return max(widths) - min(widths) <= 6
        return False

    def _normalize_console_paste(self, text):
        body = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        if "\t" in body:
            body = body.expandtabs(8)
        return body

    def _apply_composer_mode(self, console=False):
        box = getattr(self, "question_entry", None)
        if box is None:
            return
        self._composer_console = bool(console)
        try:
            if console:
                box.configure(font=self.font_editor, wrap="none", height=140)
            else:
                box.configure(font=self.font_ui, wrap="word", height=96)
        except Exception:
            pass

    def _on_question_paste(self, event=None):
        if getattr(self, "_pasting", False):
            return "break"
        self._pasting = True
        try:
            if self._paste_capture(event) == "break":
                return "break"
            raw = self._clipboard_text()
            if not raw:
                return None
            text = self._normalize_console_paste(raw)
            if self._looks_like_console(text):
                self._apply_composer_mode(True)
            self._insert_question(text)
            self._schedule_send_preview()
            return "break"
        finally:
            self.after(20, lambda: setattr(self, "_pasting", False))

    def _on_thinking(self, text):
        self._thinking_bits = getattr(self, "_thinking_bits", "") + str(text or "")
        box = getattr(self, "_think_box", None)
        toggle = getattr(self, "_think_toggle", None)
        if box is None:
            return
        if not self._think_shown:
            self._think_shown = True
            if toggle is not None:
                toggle.pack(anchor="w", padx=12, pady=(6, 0))
            box.pack(fill="x", padx=12, pady=(2, 4))
        inner = getattr(box, "_textbox", None)
        target = inner if inner is not None else box
        try:
            if self._bubble_text(box) in {"", "…"}:
                target.delete("1.0", "end")
            target.insert("end", text)
            if self._think_open:
                needed = min(180, 40 + 16 * min(8, self._thinking_bits.count("\n") + 1))
                box.configure(height=needed)
        except Exception:
            pass
        snippet = " ".join(self._thinking_bits.split())
        if len(snippet) > 80:
            snippet = "…" + snippet[-80:]
        if snippet:
            self._set_status(snippet)

    def _toggle_think(self):
        box = getattr(self, "_think_box", None)
        toggle = getattr(self, "_think_toggle", None)
        if box is None:
            return
        self._think_open = not self._think_open
        if self._think_open:
            try:
                box.pack(fill="x", padx=12, pady=(2, 4))
            except Exception:
                pass
            if toggle is not None:
                toggle.configure(text="Masquer la réflexion")
        else:
            try:
                box.pack_forget()
            except Exception:
                pass
            if toggle is not None:
                toggle.configure(text="Voir la réflexion")

    def _collapse_think(self):
        if getattr(self, "_think_box", None) is None:
            return
        if self._think_open and self._thinking_bits:
            self._toggle_think()

    def _copy_text(self, text):
        payload = str(text or "")
        self.clipboard_clear()
        self.clipboard_append(payload)
        self.update_idletasks()
        self._set_status("Bloc copié")

    def _clear_action_bar(self):
        bar = getattr(self, "_action_bar", None)
        if bar is not None:
            try:
                bar.destroy()
            except Exception:
                pass
        self._action_bar = None
        self._action_body = None
        self._action_toggle = None

    def _toggle_action_bar(self):
        body = getattr(self, "_action_body", None)
        toggle = getattr(self, "_action_toggle", None)
        if body is None:
            return
        self._action_open = not self._action_open
        if self._action_open:
            try:
                body.pack(fill="x", pady=(6, 0))
            except Exception:
                pass
            if toggle is not None:
                toggle.configure(text="Masquer")
        else:
            try:
                body.pack_forget()
            except Exception:
                pass
            if toggle is not None:
                toggle.configure(text="Afficher")

    def _show_action_bar(self, reply, cut_off=False):
        blocks = ollama_fs_access.extract_code_blocks(reply)[:8]
        want_gui = ollama_fs_access.wants_gui(self._pending_question)
        missing_gui = want_gui and not ollama_fs_access.looks_like_gui_code(reply)
        if not cut_off and not blocks and not missing_gui:
            self._clear_action_bar()
            return
        self._clear_action_bar()
        bar = ctk.CTkFrame(self.chat, fg_color="transparent")
        bar.pack(fill="x", padx=8, pady=(0, 8))
        self._action_bar = bar
        head = ctk.CTkFrame(bar, fg_color="transparent")
        head.pack(fill="x")
        title = "Dernière coupure" if cut_off else "Blocs de la réponse"
        self._styled(
            ctk.CTkLabel(head, text=title, font=self.font_tiny, anchor="w"),
            "accent" if cut_off else "muted",
        ).pack(side="left")
        self._action_toggle = self._button(
            head, "Masquer" if self._action_open else "Afficher",
            self._toggle_action_bar, width=80,
        )
        self._action_toggle.pack(side="right")
        body = ctk.CTkFrame(bar, fg_color="transparent")
        self._action_body = body
        if cut_off:
            self._styled(
                ctk.CTkLabel(
                    body,
                    text="La suite s'est arrêtée ici. Cliquez sur Continuer pour reprendre.",
                    font=self.font_tiny, anchor="w",
                ),
                "muted",
            ).pack(fill="x", pady=(0, 6))
        tools_row = ctk.CTkFrame(body, fg_color="transparent")
        tools_row.pack(fill="x", pady=(0, 6))
        self._button(tools_row, "Vérifier", lambda: self.run_verification(auto=False), width=90).pack(side="left")
        self._button(tools_row, "Versions", self.versions_dialog, width=90).pack(side="left", padx=(6, 0))
        self._button(tools_row, "Réglages", self.open_settings, width=90).pack(side="left", padx=(6, 0))
        if blocks:
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x")
            for index, block in enumerate(blocks):
                name = Path(block["path"]).name if block.get("path") else f"bloc {index + 1}"
                self._button(
                    row, f"Copier {name}",
                    lambda content=block["content"]: self._copy_text(content),
                    width=max(100, 18 * len(name) + 70),
                ).pack(side="left", padx=(0, 6))
                if block.get("path"):
                    self._button(
                        row, "Voir",
                        lambda path=block["path"]: self._open_named(path),
                        width=56,
                    ).pack(side="left", padx=(0, 8))
        elif cut_off:
            self._button(body, "Copier la suite", lambda: self._copy_text(reply), width=120).pack(anchor="w")
        launchable = next(
            (
                block.get("path")
                for block in blocks
                if Path(block.get("path") or "").suffix.lower() in {".py", ".pyw", ".bat"}
            ),
            "",
        )
        if launchable or missing_gui:
            extras = ctk.CTkFrame(body, fg_color="transparent")
            extras.pack(fill="x", pady=(8, 0))
            if launchable:
                self._button(
                    extras, "Lancer",
                    lambda raw=launchable: self._run_named(raw),
                    primary=True, width=80,
                ).pack(side="left")
            if missing_gui:
                self._styled(
                    ctk.CTkLabel(
                        body,
                        text="Le modèle n'a pas livré de fenêtre (tkinter / mainloop). Cliquez sur Exiger l'interface.",
                        font=self.font_tiny, anchor="w",
                    ),
                    "muted",
                ).pack(fill="x", pady=(6, 0))
                self._button(extras, "Exiger l'interface", self.demand_gui, primary=True, width=150).pack(
                    side="left", padx=(8, 0)
                )
        if self._action_open:
            body.pack(fill="x", pady=(6, 0))
        self._scroll_chat()

    def _run_named(self, raw):
        path = self._match_update_path(raw)
        try:
            ollama_fs_access.run_local_file(self.target_folder, path)
            self._set_status(f"Lancé : {Path(path).as_posix()}")
        except FileNotFoundError:
            messagebox.showinfo(
                APP_NAME,
                "Ce fichier n'est pas encore sur le disque.\n"
                "Acceptez d'abord le fichier proposé, puis cliquez sur Lancer.",
            )
        except Exception as error:
            messagebox.showerror(APP_NAME, f"Lancement impossible : {error}")

    def demand_gui(self):
        self.ask_model(question=ollama_fs_access.GUI_RETRY_PROMPT)

    def _attach_code_actions(self, reply):
        self._show_action_bar(reply, cut_off=False)

    def _open_named(self, raw):
        path = self._match_update_path(raw)
        self._show_file(path)

    def _move_file(self, delta, event=None):
        if self._is_typing():
            return None
        files = list(self.visible_files)
        if not files:
            return "break"
        keys = [path.as_posix() for path in files]
        if self.selected_key in keys:
            index = keys.index(self.selected_key) + delta
        else:
            index = 0 if delta > 0 else len(files) - 1
        index = max(0, min(len(files) - 1, index))
        self._open_from_list(files[index])
        return "break"

    def _make_toggle(self, key, variable):
        def toggle():
            if variable.get():
                self.excluded.discard(key)
            else:
                self.excluded.add(key)
            self._set_status()
            self._schedule_send_preview()
        return toggle

    def _highlight(self, relative):
        key = None if relative is None else Path(relative).as_posix()
        self.selected_key = key
        for path, button in self._rows.items():
            button.configure(fg_color=self.C("selected") if path == key else "transparent")

    def check_all(self):
        self.excluded.clear()
        for variable in self._checks.values():
            variable.set(True)
        self._set_status()
        self._schedule_send_preview()

    def check_none(self):
        self.excluded = {path.as_posix() for path in self.visible_files}
        for variable in self._checks.values():
            variable.set(False)
        self._set_status()
        self._schedule_send_preview()

    def _checked_count(self):
        return sum(1 for path in self.visible_files if path.as_posix() not in self.excluded)

    def _set_status(self, text=None):
        if text is None:
            text = f"{len(self.visible_files)} fichier(s)   ·   {self._checked_count()} lisible(s)"
        self.status.configure(text=text)

    def _remember_file(self, relative):
        key = Path(relative).as_posix()
        self._recent_files = [key] + [item for item in self._recent_files if item != key]
        self._recent_files = self._recent_files[:8]

    def _context_kwargs(self, question=""):
        return dict(
            question=question or self._question_text(),
            selected=self.selected_key,
            recent=self._recent_files,
            last_sent=self._last_sent_files,
            pending=list(self._pending_updates),
            pinned=list(self._pinned),
        )

    def _schedule_send_preview(self):
        if getattr(self, "_send_preview_job", None) is not None:
            try:
                self.after_cancel(self._send_preview_job)
            except Exception:
                pass
        self._send_preview_job = self.after(180, self._refresh_send_preview)

    def _refresh_send_preview(self):
        self._send_preview_job = None
        label = getattr(self, "smart_label", None)
        if label is None:
            return
        files = self._files_for_context()
        if not files:
            label.configure(text="Dossier vide : le modèle pourra proposer de nouveaux fichiers")
            return
        question = self._question_text()
        picked, _reasons = ollama_fs_access.pick_context_files(files, **self._context_kwargs(question))
        if ollama_fs_access.wants_all_files(question) or len(files) <= ollama_fs_access.SMALL_FILE_POOL:
            label.configure(text=f"{len(files)} fichier(s) lisible(s) · contenu : tous")
            self._paint_send_marks([path.as_posix() for path in files])
            return
        names = ", ".join(Path(item).name for item in picked[:4])
        extra = f" +{len(picked) - 4}" if len(picked) > 4 else ""
        typed = len(self._question_text())
        count = f" · {typed} car." if typed else ""
        label.configure(text=f"{len(files)} lisibles · contenu : {names}{extra}{count}")
        self._paint_send_marks(picked)

    def _paint_send_marks(self, picked):
        chosen = {Path(item).as_posix() for item in picked or []}
        for key, button in self._rows.items():
            try:
                if key in self._pending_updates:
                    button.configure(text_color=self.C("accent"))
                elif key in chosen:
                    button.configure(text_color=self.C("sage"))
                else:
                    button.configure(text_color=self.C("text"))
            except Exception:
                pass
        for key, pin in self._pin_buttons.items():
            try:
                pin.configure(text="★" if key in self._pinned else "☆")
            except Exception:
                pass

    def _toggle_pin(self, key):
        key = Path(key).as_posix()
        if key in self._pinned:
            self._pinned.discard(key)
        else:
            self._pinned.add(key)
            self.excluded.discard(key)
            variable = self._checks.get(key)
            if variable is not None:
                variable.set(True)
        self._save_session()
        self._schedule_send_preview()

    def _excluded_set(self):
        return {str(item).replace("\\", "/").casefold() for item in self.excluded}

    def _files_for_context(self):
        blocked = self._excluded_set()
        chosen = [path for path in self.visible_files if path.as_posix().casefold() not in blocked]
        if not chosen and self.visible_files:
            self.excluded.clear()
            chosen = list(self.visible_files)
        if self.selected_key is not None:
            chosen.sort(key=lambda path: path.as_posix() != self.selected_key)
        return chosen

    def selected_relative(self):
        if self.selected_key is None:
            return None
        return Path(self.selected_key)

    def _is_dirty(self):
        return False

    def _confirm_leave(self):
        return True

    def _clear_editor(self):
        self.loaded_path = None
        self.loaded_text = ""
        self.selected_key = None
        self._set_apply_state()

    def _open_from_list(self, relative):
        self._show_file(relative)

    def _show_file(self, relative):
        self.loaded_path = Path(relative)
        self.loaded_text = ""
        self._remember_file(relative)
        self._highlight(relative)
        self._set_apply_state()
        self._set_status(Path(relative).as_posix())
        self._fill_preview(relative)
        self._schedule_send_preview()

    def _fill_preview(self, relative):
        box = getattr(self, "file_preview", None)
        if box is None:
            return
        try:
            text = ollama_fs_access.read_file(self.target_folder, relative)
        except FileNotFoundError:
            pending = self._pending_content(Path(relative).as_posix())
            text = pending if pending is not None else "(fichier introuvable)"
        except Exception as error:
            text = f"(aperçu impossible : {error})"
        if len(text) > 80_000:
            text = text[:80_000] + "\n…"
        try:
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", text)
        except Exception:
            pass
        meta = getattr(self, "preview_meta", None)
        if meta is not None:
            lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
            meta.configure(text=f"APERÇU · {lines} lignes · {_format_size(len(text.encode('utf-8')))}")
        self._highlight_preview(box)

    def _highlight_preview(self, box):
        needle = ""
        try:
            needle = self.search_entry.get().strip()
        except Exception:
            pass
        if not needle or box is None:
            return
        inner = getattr(box, "_textbox", None)
        target = inner if inner is not None else box
        try:
            target.tag_remove("hit", "1.0", "end")
        except Exception:
            pass
        try:
            start = "1.0"
            first = None
            while True:
                found = target.search(needle, start, stopindex="end", nocase=True)
                if not found:
                    break
                end = f"{found}+{len(needle)}c"
                target.tag_add("hit", found, end)
                if first is None:
                    first = found
                if end == start:
                    break
                start = end
            target.tag_config("hit", background=self.C("accent"), foreground=self.C("ink"))
            if first:
                target.see(first)
        except Exception:
            pass

    def toggle_preview_wrap(self):
        box = getattr(self, "file_preview", None)
        if box is None:
            return
        self._preview_wrap = not self._preview_wrap
        try:
            box.configure(wrap="word" if self._preview_wrap else "none")
        except Exception:
            pass
        button = getattr(self, "wrap_button", None)
        if button is not None:
            button.configure(text="Large" if self._preview_wrap else "Retour")

    def copy_selected_path(self):
        if not self.selected_key:
            messagebox.showinfo(APP_NAME, "Sélectionnez un fichier.")
            return
        self._copy_text(self.selected_key)
        self._set_status(f"Chemin copié : {self.selected_key}")

    def open_selected(self):
        if self.selected_key is None:
            messagebox.showinfo(APP_NAME, "Sélectionnez un fichier.")
            return
        try:
            ollama_fs_access.open_local_file(self.target_folder, self.selected_key)
        except Exception as error:
            messagebox.showerror(APP_NAME, f"Ouverture impossible : {error}")

    def reveal_selected(self):
        target = self.selected_key or ""
        try:
            if target:
                ollama_fs_access.reveal_file(self.target_folder, target)
            else:
                ollama_fs_access.execute_folder(self.target_folder)
        except Exception as error:
            messagebox.showerror(APP_NAME, f"Explorateur impossible : {error}")

    def _focus_question(self):
        try:
            self.question_entry.focus_set()
        except Exception:
            pass
        return "break"

    def _focus_search(self):
        try:
            self.search_entry.focus_set()
        except Exception:
            pass
        return "break"

    def _last_user_question(self):
        pending = str(self._pending_question or "").strip("\n")
        if pending.strip():
            return pending
        if self._question_stack:
            return str(self._question_stack[-1])
        for role, text in reversed(self._transcript):
            if role == "user" and str(text or "").strip():
                return str(text).strip("\n")
        for item in reversed(self.history):
            if item.get("role") == "user" and str(item.get("content") or "").strip():
                return str(item.get("content")).strip("\n")
        return self._question_text().replace("\r\n", "\n").replace("\r", "\n").strip("\n")

    def _drop_last_turn(self, question):
        needle = str(question or "").strip()
        if not needle:
            return
        if (
            len(self.history) >= 2
            and self.history[-1].get("role") == "assistant"
            and self.history[-2].get("role") == "user"
            and str(self.history[-2].get("content") or "").strip() == needle
        ):
            self.history = self.history[:-2]
        elif (
            self.history
            and self.history[-1].get("role") == "user"
            and str(self.history[-1].get("content") or "").strip() == needle
        ):
            self.history = self.history[:-1]

    def retry_last(self, *_args):
        if self._streaming:
            self._set_status("Arrêtez d'abord la réponse en cours, puis Relancer.")
            return "break"
        question = self._last_user_question()
        if not str(question or "").strip():
            messagebox.showinfo(APP_NAME, "Aucune question à relancer.")
            return "break"
        self._drop_last_turn(question)
        self._clear_question()
        self._set_status("Relance de la dernière question…")
        self.ask_model(question=question)
        return "break"

    def quote_last(self):
        for role, text in reversed(self._transcript):
            if role == "assistant" and str(text or "").strip():
                quoted = "\n".join("> " + line for line in str(text).splitlines()[:50])
                current = self._question_text()
                prefix = "" if not current else ("\n" if current.endswith("\n") else "\n\n")
                self._insert_question(prefix + quoted + "\n")
                self._focus_question()
                return "break"
        messagebox.showinfo(APP_NAME, "Aucune réponse à citer.")
        return "break"

    def _chat_labels(self, records=None):
        records = records if records is not None else self._chats_for_save()
        labels = []
        seen = {}
        for item in records:
            title = (item.get("title") or "Conversation").strip() or "Conversation"
            count = seen.get(title, 0) + 1
            seen[title] = count
            labels.append(title if count == 1 else f"{title} ({count})")
        return labels

    def _reset_to_empty_chat(self):
        self._chat_id = _fresh_chat_id()
        self._chat_title = ""
        self.history.clear()
        self._pending_question = ""
        self.last_reply = ""
        self._clear_attachments()
        self._clear_chat()
        self._refresh_chat_picker()

    def _delete_chats(self, ids):
        wanted = {str(item) for item in ids if item}
        if not wanted:
            return 0
        records = self._chats_for_save()
        kept = [item for item in records if str(item.get("id")) not in wanted]
        current_gone = str(self._chat_id) in wanted
        self._chats = kept
        if current_gone:
            if kept:
                self._switch_chat(kept[0])
            else:
                self._reset_to_empty_chat()
        else:
            self._refresh_chat_picker()
        self._save_session()
        return len(records) - len(kept)

    def delete_chat(self):
        if self._streaming:
            self.cancel_ask()
        if not self.history and not self._transcript and len(self._chats_for_save()) <= 1:
            self._set_status("Rien à supprimer")
            return
        if not messagebox.askyesno(APP_NAME, "Supprimer cette conversation ?"):
            return
        removed = self._delete_chats([self._chat_id])
        self._set_status("Conversation supprimée" if removed else "Rien à supprimer")

    def manage_chats(self):
        records = self._chats_for_save()
        self._chats = records
        dialog = ctk.CTkToplevel(self)
        dialog.title("Conversations")
        dialog.geometry("560x520")
        dialog.minsize(420, 360)
        dialog.configure(fg_color=self.C("bg"))
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass
        self._styled(
            ctk.CTkLabel(
                dialog,
                text="Cochez les conversations à supprimer, ou ouvrez-en une.",
                font=self.font_small, anchor="w",
            ),
            "muted",
        ).pack(fill="x", padx=16, pady=(14, 8))
        listing = self._styled(
            ctk.CTkScrollableFrame(dialog, corner_radius=12),
            "well",
        )
        listing.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        labels = self._chat_labels(records)
        picked = set()
        marks = {}

        def row_label(chat_id, title):
            box = "☑" if chat_id in picked else "☐"
            return f"{box}   {title}"

        def paint_mark(chat_id, title):
            button = marks.get(chat_id)
            if button is None:
                return
            button.configure(text=row_label(chat_id, title))
            if chat_id in picked:
                button.configure(fg_color=self.C("accent"), text_color=self.C("ink"), hover_color=self.C("accent_hover"))
            else:
                self._paint(button, "btn")

        def toggle(chat_id, title):
            if chat_id in picked:
                picked.discard(chat_id)
            else:
                picked.add(chat_id)
            paint_mark(chat_id, title)

        def open_one(record):
            dialog.destroy()
            if record.get("id") != self._chat_id:
                self._switch_chat(record)

        if not records:
            self._styled(
                ctk.CTkLabel(listing, text="Aucune conversation enregistrée.", font=self.font_small),
                "muted",
            ).pack(anchor="w", padx=8, pady=10)

        for item, title in zip(records, labels):
            chat_id = str(item.get("id") or "")
            current = chat_id == str(self._chat_id)
            shown = f"{title}  ·  actuelle" if current else title
            row = ctk.CTkFrame(listing, fg_color="transparent")
            row.pack(fill="x", pady=4)
            mark = self._button(
                row, row_label(chat_id, shown),
                lambda i=chat_id, t=shown: toggle(i, t),
                width=380,
            )
            try:
                mark.configure(anchor="w")
            except Exception:
                pass
            mark.pack(side="left", fill="x", expand=True, padx=(0, 8))
            marks[chat_id] = mark
            self._button(
                row, "Ouvrir",
                lambda rec=item: open_one(rec),
                width=72,
            ).pack(side="right")

        bar = ctk.CTkFrame(dialog, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 14))

        def check_all():
            for item, title in zip(records, labels):
                chat_id = str(item.get("id") or "")
                shown = f"{title}  ·  actuelle" if chat_id == str(self._chat_id) else title
                picked.add(chat_id)
                paint_mark(chat_id, shown)

        def check_none():
            for item, title in zip(records, labels):
                chat_id = str(item.get("id") or "")
                shown = f"{title}  ·  actuelle" if chat_id == str(self._chat_id) else title
                picked.discard(chat_id)
                paint_mark(chat_id, shown)

        def delete_selected():
            ids = [chat_id for chat_id in picked if chat_id]
            if not ids:
                messagebox.showinfo(APP_NAME, "Cochez au moins une conversation.")
                return
            word = "conversation" if len(ids) == 1 else "conversations"
            if not messagebox.askyesno(APP_NAME, f"Supprimer {len(ids)} {word} ?"):
                return
            removed = self._delete_chats(ids)
            dialog.destroy()
            self._set_status(f"{removed} conversation(s) supprimée(s)")

        self._button(bar, "Tout cocher", check_all, width=110).pack(side="left")
        self._button(bar, "Tout décocher", check_none, width=120).pack(side="left", padx=(8, 0))
        self._button(bar, "Supprimer la sélection", delete_selected, danger=True, width=170).pack(side="right")
        self._button(bar, "Fermer", dialog.destroy, width=80).pack(side="right", padx=(0, 8))

    def save_file(self, relative=None):
        return self._save_changes()

    def _save_changes(self):
        updates = self._updates_from_reply()
        written, unchanged, errors = [], [], []
        applied = set()
        created_any = False
        for item in updates:
            path = self._match_update_path(item["path"])
            key = Path(path).as_posix()
            result = self._write_one(path, item["content"], report=False, refresh_list=False)
            applied.add(key.casefold())
            if result == "unchanged":
                unchanged.append(key)
            elif result is False:
                errors.append(key)
            elif isinstance(result, dict):
                written.append(result)
                created_any = created_any or result.get("created", False)
        if not updates and not written and not unchanged and not errors:
            messagebox.showinfo(
                APP_NAME,
                "Aucun fichier à enregistrer : la réponse n'indique pas de fichier à écrire.",
            )
            return False
        if written:
            previous = self.loaded_path
            self.reload_files(open_after=previous, confirm=False)
            self._pending_updates.clear()
            names = ", ".join(item["path"] for item in written)
            self._set_status(f"Écrit sur le disque : {names}")
            self._show_save_report(written, [], errors)
            self._set_undo_state()
            return True
        self._pending_updates.clear()
        if errors:
            self._show_save_report([], unchanged, errors)
        return False

    def _updates_from_reply(self):
        if self._pending_updates:
            return [{"path": path, "content": content} for path, content in self._pending_updates.items()]
        if not self.last_reply:
            return []
        hints = list(self._last_sent_files)
        updates = ollama_fs_access.extract_file_updates(
            self.last_reply, hints=hints, question=self._pending_question,
        )
        if updates:
            return updates
        if not ollama_fs_access.has_edit_intent(self._pending_question):
            return []
        unnamed = ollama_fs_access.extract_apply_text(self.last_reply)
        if not unnamed or unnamed.strip() == str(self.last_reply).strip():
            return []
        mentioned = ollama_fs_access.paths_mentioned(self._pending_question, self.last_reply)
        if mentioned:
            return [{"path": mentioned[0], "content": unnamed if unnamed.endswith("\n") else unnamed + "\n"}]
        if len(self._last_sent_files) == 1:
            return [{"path": self._last_sent_files[0], "content": unnamed if unnamed.endswith("\n") else unnamed + "\n"}]
        return []

    def _match_update_path(self, raw):
        posix = Path(raw).as_posix()
        for path in self.visible_files:
            if path.as_posix().casefold() == posix.casefold():
                return path
        named = [path for path in self.visible_files if path.name.casefold() == Path(posix).name.casefold()]
        if len(named) == 1:
            return named[0]
        return Path(posix)

    def _write_one(self, relative, text, report=True, refresh_list=True):
        relative = Path(relative)
        key = relative.as_posix()
        created = False
        before = self._disk_size.get(key)
        try:
            old = ollama_fs_access.read_file(self.target_folder, relative)
        except FileNotFoundError:
            old = None
            created = True
            before = 0
        except Exception:
            old = None
        if old is not None and ollama_fs_access.same_text(self.target_folder, relative, text):
            self._last_write = {"path": key, "unchanged": True, "before": before or 0, "after": before or 0}
            if report:
                self._set_status(f"Inchangé : {key}")
            return "unchanged"
        if old is not None:
            try:
                lumio_snapshots.snapshot(self.target_folder, key, old, reason="write")
            except Exception:
                pass
        try:
            written = ollama_fs_access.write_file(self.target_folder, relative, text)
            check = written.read_text(encoding="utf-8", errors="replace")
            if ollama_fs_access.normalize_text(check).strip("\n") != ollama_fs_access.normalize_text(text).strip("\n"):
                raise RuntimeError("Le fichier relu ne correspond pas à l'écriture")
        except Exception as error:
            if report:
                messagebox.showerror("Erreur", f"Enregistrement impossible : {error}")
            return False
        if not any(item.get("path") == key for item in self._write_backup):
            self._write_backup.append({"path": key, "old": old})
        try:
            stats = ollama_fs_access.file_stats(self.target_folder, relative)
            self._disk_size[key] = stats["size"]
            self._disk_mtime[key] = stats["mtime"]
            after = stats["size"]
        except Exception:
            after = len(str(text).encode("utf-8"))
            self._disk_size[key] = after
            self._disk_mtime[key] = datetime.now().timestamp()
        if refresh_list:
            self._refresh_file_meta(relative)
        info = {
            "path": key,
            "before": 0 if created else (before if before is not None else len((old or "").encode("utf-8"))),
            "after": after,
            "created": created,
        }
        self._last_write = info
        if report:
            self._set_status(f"Enregistré : {key}")
        self._schedule_verify()
        return info

    def _show_save_report(self, written, unchanged, errors):
        lines = []
        if written:
            lines.append("Fichiers enregistrés :")
            for item in written:
                if item.get("created"):
                    extra = f"  ·  créé, {_format_size(item['after'])}"
                else:
                    extra = f"  ·  {_format_size(item['before'])} → {_format_size(item['after'])}"
                lines.append(f"• {item['path']}{extra}")
        if unchanged:
            if lines:
                lines.append("")
            lines.append("Déjà à jour :")
            for path in unchanged:
                lines.append(f"• {path}")
        if errors:
            if lines:
                lines.append("")
            lines.append("Échecs :")
            for path in errors:
                lines.append(f"• {path}")
        if not lines:
            return
        messagebox.showinfo(APP_NAME, "\n".join(lines))

    def new_file(self):
        if not self._confirm_leave():
            return
        dialog = ctk.CTkInputDialog(text="Nom du fichier, par exemple note.txt", title="Nouveau fichier")
        name = dialog.get_input()
        if not name:
            return
        try:
            ollama_fs_access.write_file(self.target_folder, name, "")
        except Exception as error:
            messagebox.showerror("Erreur", f"Création impossible : {error}")
            return
        self._clear_editor()
        self.search_entry.delete(0, "end")
        self.excluded.discard(Path(name).as_posix())
        self.reload_files()
        self._show_file(Path(name))

    def _editor_overrides(self):
        return {}

    def _set_apply_state(self):
        self._set_continue_state()

    def _set_continue_state(self):
        if getattr(self, "continue_button", None) is None:
            return
        ready = bool(self.last_reply) and not self._streaming
        self.continue_button.configure(state="normal" if ready else "disabled")

    def _clear_chat(self):
        for child in self.chat.winfo_children():
            child.destroy()
        self._transcript = []
        self.last_reply = ""
        self._live_box = None
        self._last_assistant_label = None
        self._continuing = False
        self.chat_empty = self._styled(
            ctk.CTkLabel(self.chat, text="Posez une question. Lumio répond ici.", font=self.font_small),
            "muted",
        )
        self.chat_empty.pack(pady=18)
        self._wrap_labels = []
        self._think_box = None
        self._think_toggle = None
        self._think_open = True
        self._action_bar = None
        self._action_body = None
        self._action_toggle = None
        self._set_apply_state()

    def _hide_chat_empty(self):
        empty = getattr(self, "chat_empty", None)
        if empty is not None:
            empty.destroy()
            self.chat_empty = None

    def _bind_chat_scroll(self):
        canvas = getattr(self, "chat_canvas", None)
        wrap = getattr(self, "chat_wrap", None)
        for widget in (canvas, wrap, getattr(self, "chat", None)):
            if widget is None:
                continue
            widget.bind("<MouseWheel>", self._on_chat_mousewheel, add="+")
            widget.bind("<Button-4>", self._on_chat_mousewheel, add="+")
            widget.bind("<Button-5>", self._on_chat_mousewheel, add="+")
        bar = getattr(self, "chat_bar", None)
        if bar is not None:
            bar.bind("<ButtonRelease-1>", lambda _event: self.after(20, self._sync_chat_follow), add="+")
        self.bind_all("<MouseWheel>", self._on_chat_mousewheel_all, add="+")

    def _pointer_inside(self, widget):
        if widget is None:
            return False
        try:
            x, y = widget.winfo_pointerx(), widget.winfo_pointery()
            left, top = widget.winfo_rootx(), widget.winfo_rooty()
            return left <= x <= left + widget.winfo_width() and top <= y <= top + widget.winfo_height()
        except Exception:
            return False

    def _scrollable_canvas(self, frame):
        return getattr(frame, "_parent_canvas", None)

    def _wheel_scroll_canvas(self, canvas, event):
        if canvas is None:
            return "break"
        if getattr(event, "delta", 0):
            canvas.yview_scroll(int(-event.delta / 120), "units")
        elif getattr(event, "num", None) == 4:
            canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(1, "units")
        return "break"

    def _bind_scrollable_wheel(self, scrollable, owner=None):
        canvas = self._scrollable_canvas(scrollable)

        def on_wheel(event):
            target = scrollable
            if not self._pointer_inside(target) and owner is not None and not self._pointer_inside(owner):
                return
            if not self._pointer_inside(target) and owner is None:
                return
            return self._wheel_scroll_canvas(canvas, event)

        def attach(widget):
            if widget is None:
                return
            widget.bind("<MouseWheel>", on_wheel, add="+")
            widget.bind("<Button-4>", on_wheel, add="+")
            widget.bind("<Button-5>", on_wheel, add="+")
            for child in widget.winfo_children():
                attach(child)

        attach(scrollable)
        if canvas is not None:
            attach(canvas)
        if owner is not None:
            owner.bind("<MouseWheel>", on_wheel, add="+")
            owner.bind("<Button-4>", on_wheel, add="+")
            owner.bind("<Button-5>", on_wheel, add="+")
        return on_wheel

    def _on_chat_mousewheel_all(self, event):
        picker = getattr(self, "_model_picker", None)
        listing = getattr(self, "_model_picker_list", None)
        if picker is not None and listing is not None:
            try:
                if picker.winfo_exists() and (
                    self._pointer_inside(picker) or self._pointer_inside(listing)
                ):
                    return self._wheel_scroll_canvas(self._scrollable_canvas(listing), event)
            except Exception:
                pass
        if not self._pointer_inside(getattr(self, "chat_wrap", None)):
            return
        return self._on_chat_mousewheel(event)

    def _on_chat_mousewheel(self, event):
        canvas = getattr(self, "chat_canvas", None)
        if canvas is None:
            return
        if getattr(event, "delta", 0):
            canvas.yview_scroll(int(-event.delta / 120), "units")
        elif getattr(event, "num", None) == 4:
            canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(1, "units")
        self.after(40, self._sync_chat_follow)
        return "break"

    def _on_chat_wheel(self, _event=None):
        self.after(40, self._sync_chat_follow)

    def _chat_near_bottom(self):
        canvas = getattr(self, "chat_canvas", None)
        if canvas is None:
            return True
        try:
            _first, last = canvas.yview()
        except Exception:
            return True
        return last >= 0.97

    def _sync_chat_follow(self):
        self._chat_follow = self._chat_near_bottom()
        self._style_jump(not self._chat_follow)

    def _style_jump(self, pending):
        button = getattr(self, "jump_button", None)
        if button is None:
            return
        if pending:
            button.configure(fg_color=self.C("accent"), text_color=self.C("ink"), hover_color=self.C("accent_hover"))
        else:
            self._paint(button, "btn")

    def _jump_chat(self):
        self._chat_follow = True
        self._style_jump(False)
        self._scroll_chat(force=True)

    def _scroll_chat(self, force=False):
        if force:
            self._chat_follow = True
        if not self._chat_follow:
            self._style_jump(True)
            return
        if self._scroll_job is not None:
            return
        delay = 140 if self._streaming else 80
        self._scroll_job = self.after(delay, self._flush_chat_scroll)

    def _flush_chat_scroll(self):
        self._scroll_job = None
        if not self._chat_follow:
            return
        canvas = getattr(self, "chat_canvas", None)
        if canvas is not None:
            canvas.yview_moveto(1.0)
        self._style_jump(False)

    def _set_chat_state(self, text=""):
        label = getattr(self, "chat_state", None)
        if label is not None:
            label.configure(text=text)

    def _bubble_wrap(self):
        width = 0
        canvas = getattr(self, "chat_canvas", None)
        if canvas is not None:
            width = canvas.winfo_width()
        elif getattr(self, "chat", None) is not None:
            width = self.chat.winfo_width()
        if width < 80:
            width = self.winfo_width() - 420
        return max(220, width - 36)

    def _on_chat_resize(self, _event=None):
        if self._streaming:
            return
        wrap = self._bubble_wrap()
        if abs(wrap - self._last_wrap) < 10:
            return
        self._last_wrap = wrap
        for label in list(self._wrap_labels):
            if not label.winfo_exists():
                continue
            if getattr(label, "_textbox", None) is not None:
                self._fit_text_box(label)
            else:
                try:
                    label.configure(wraplength=wrap)
                except Exception:
                    pass

    def _on_window_resize(self, event):
        if event.widget is not self:
            return
        self._on_chat_resize()

    def _current_sash(self):
        try:
            return self.split.sash_coord(0)[0]
        except Exception:
            return self._sash

    def _restore_sashes(self):
        if self._sash:
            try:
                self.split.sash_place(0, int(self._sash), 0)
            except Exception:
                pass

    def start_capture(self):
        if self._streaming:
            return
        if len(self._attachments) >= MAX_ATTACHMENTS:
            messagebox.showinfo(APP_NAME, f"Vous pouvez joindre {MAX_ATTACHMENTS} captures au plus.")
            return
        self._set_status("Sélectionnez la zone à capturer, Échap pour annuler")
        _SnipOverlay(self, self._on_snip)

    def attach_image_file(self):
        if len(self._attachments) >= MAX_ATTACHMENTS:
            messagebox.showinfo(APP_NAME, f"Vous pouvez joindre {MAX_ATTACHMENTS} captures au plus.")
            return
        chosen = filedialog.askopenfilename(
            title="Joindre une image",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp"), ("Tous", "*.*")],
        )
        if not chosen:
            return
        self._attach_image_file(Path(chosen))

    def _paste_capture(self, _event=None):
        try:
            from PIL import Image, ImageGrab
            grabbed = ImageGrab.grabclipboard()
        except Exception:
            return None
        if grabbed is None:
            return None
        if isinstance(grabbed, list):
            attached = False
            for raw in grabbed:
                path = Path(str(raw))
                if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file():
                    self._attach_image_file(path)
                    attached = True
            return "break" if attached else None
        if hasattr(grabbed, "size"):
            self._attach_image(grabbed, "presse-papiers.png")
            return "break"
        return None

    def _attach_image_file(self, path):
        try:
            from PIL import Image
            image = Image.open(path)
            image.load()
        except Exception as error:
            messagebox.showerror(APP_NAME, f"Image illisible : {error}")
            return
        self._attach_image(image, path.name)

    def _attach_image(self, image, name):
        if len(self._attachments) >= MAX_ATTACHMENTS:
            messagebox.showinfo(APP_NAME, f"Vous pouvez joindre {MAX_ATTACHMENTS} captures au plus.")
            return
        try:
            from PIL import Image
            picture = image.convert("RGBA") if image.mode in {"P", "RGBA"} else image.convert("RGB")
        except Exception:
            picture = image
        encoded, thumb = _prepare_attachment(picture)
        if not encoded:
            messagebox.showerror(APP_NAME, "Impossible de préparer la capture.")
            return
        self._attachments.append({"name": name, "b64": encoded, "thumb": thumb})
        self._render_attachments()
        self._set_status(f"Capture jointe : {name}")
        try:
            self.question_entry.focus_set()
        except Exception:
            pass

    def _on_snip(self, image):
        if image is None:
            self._set_status("Capture annulée")
            return
        self._attach_image(image, "capture.png")

    def _remove_attachment(self, index):
        if 0 <= index < len(self._attachments):
            del self._attachments[index]
        self._render_attachments()

    def _clear_attachments(self):
        self._attachments = []
        self._render_attachments()

    def _render_attachments(self):
        row = getattr(self, "attach_row", None)
        if row is None:
            return
        for child in row.winfo_children():
            child.destroy()
        if not self._attachments:
            row.grid_remove()
            return
        row.grid()
        for index, item in enumerate(self._attachments):
            chip = self._styled(ctk.CTkFrame(row, corner_radius=10), "raised")
            chip.pack(side="left", padx=(0, 8))
            self._styled(ctk.CTkLabel(chip, image=item["thumb"], text=""), "text").pack(side="left", padx=(6, 0), pady=6)
            self._styled(ctk.CTkLabel(chip, text=item["name"], font=self.font_tiny), "muted").pack(side="left", padx=6)
            self._button(chip, "×", lambda i=index: self._remove_attachment(i), width=28).pack(side="left", padx=(0, 6), pady=6)

    def _add_bubble(self, role, text, live=False, pictures=None):
        self._hide_chat_empty()
        row = ctk.CTkFrame(self.chat, fg_color="transparent")
        row.pack(fill="x", pady=5, padx=4)
        if role == "user":
            card = self._styled(ctk.CTkFrame(row, corner_radius=14), "bubble_user")
            card.pack(anchor="e", fill="x", padx=(72, 0))
            who, who_role = "Vous", "accent"
        elif role == "notice":
            card = ctk.CTkFrame(row, fg_color="transparent")
            card.pack(fill="x")
            who, who_role = "", "muted"
        else:
            card = self._styled(ctk.CTkFrame(row, corner_radius=14), "bubble_ai")
            card.pack(anchor="w", fill="x", padx=(0, 72))
            who, who_role = "Lumio", "sage"
        if who:
            self._styled(ctk.CTkLabel(card, text=who, font=self.font_tiny, anchor="w"), who_role).pack(fill="x", padx=12, pady=(8, 0))
        if pictures:
            strip = ctk.CTkFrame(card, fg_color="transparent")
            strip.pack(fill="x", padx=12, pady=(6, 0))
            for picture in pictures:
                self._styled(ctk.CTkLabel(strip, image=picture, text=""), "text").pack(side="left", padx=(0, 6))
                self._bubble_pictures.append(picture)
        if role == "notice":
            label = self._styled(
                ctk.CTkLabel(
                    card, text=text or "…", font=self.font_small, justify="left",
                    anchor="w", wraplength=self._bubble_wrap(),
                ),
                "muted",
            )
            label.pack(fill="x", padx=12, pady=(2, 10))
            self._wrap_labels.append(label)
            self._scroll_chat()
            return label
        if live:
            self._think_open = True
            self._think_shown = False
            self._think_toggle = self._button(card, "Masquer la réflexion", self._toggle_think, width=160)
            self._think_box = self._styled(
                ctk.CTkTextbox(
                    card, font=self.font_tiny, wrap="word", height=72,
                    border_width=0, corner_radius=8, activate_scrollbars=True,
                ),
                "bubble_box",
            )
            self._think_box.insert("1.0", "")
            self._lock_bubble(self._think_box)
        console = role == "user" and self._looks_like_console(text or "")
        start_height = 140 if live else 40
        box = self._styled(
            ctk.CTkTextbox(
                card,
                font=self.font_editor if console else self.font_small,
                wrap="none" if console else "word",
                height=start_height,
                border_width=0, corner_radius=8,
                activate_scrollbars=True,
            ),
            "bubble_box",
        )
        box.pack(fill="x", padx=12, pady=(2, 10))
        box.insert("1.0", text or "…")
        self._lock_bubble(box)
        self._bind_bubble_scroll(box)
        if live:
            self._grow_live_box(box, force=False)
        else:
            self._fit_text_box(box)
        self._wrap_labels.append(box)
        self._transcript.append([role, text])
        if live:
            self._live_box = box
        if role == "assistant":
            self._last_assistant_label = box
        self._scroll_chat()
        return box

    def _lock_bubble(self, box):
        inner = getattr(box, "_textbox", None)
        if inner is None:
            return

        def on_key(event):
            if event.state & 0x4 and event.keysym.lower() in {"c", "a"}:
                return None
            return "break"

        def select_all(_event):
            inner.tag_add("sel", "1.0", "end-1c")
            return "break"

        inner.bind("<Key>", on_key)
        inner.bind("<Control-a>", select_all)
        inner.bind("<Control-A>", select_all)

    def _bind_bubble_scroll(self, box):
        inner = getattr(box, "_textbox", None)
        if inner is None:
            return

        def on_wheel(event):
            try:
                first, last = inner.yview()
            except Exception:
                first, last = 0.0, 1.0
            if getattr(event, "delta", 0):
                delta = int(-event.delta / 120) or (-1 if event.delta > 0 else 1)
            elif getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            else:
                return None
            if delta < 0 and first <= 0.001:
                return self._on_chat_mousewheel(event)
            if delta > 0 and last >= 0.999:
                return self._on_chat_mousewheel(event)
            inner.yview_scroll(delta, "units")
            return "break"

        for widget in (box, inner):
            widget.bind("<MouseWheel>", on_wheel, add="+")
            widget.bind("<Button-4>", on_wheel, add="+")
            widget.bind("<Button-5>", on_wheel, add="+")

    def _bubble_text(self, widget):
        if widget is None:
            return ""
        if hasattr(widget, "get") and getattr(widget, "_textbox", None) is not None:
            return widget.get("1.0", "end-1c")
        try:
            return widget.cget("text")
        except Exception:
            return ""

    def _set_bubble_text(self, widget, text):
        if widget is None:
            return
        inner = getattr(widget, "_textbox", None)
        if inner is not None:
            inner.delete("1.0", "end")
            inner.insert("1.0", text)
            self._fit_text_box(widget)
            return
        try:
            widget.configure(text=text)
        except Exception:
            pass

    def _refresh_chat_region(self):
        canvas = getattr(self, "chat_canvas", None)
        if canvas is None:
            return
        bbox = canvas.bbox("all")
        canvas.configure(scrollregion=bbox or (0, 0, 0, 0))

    def _max_bubble_height(self):
        canvas = getattr(self, "chat_canvas", None)
        if canvas is None:
            return 520
        try:
            height = int(canvas.winfo_height() or 0)
        except Exception:
            height = 0
        return max(240, height - 20) if height > 80 else 520

    def _needed_box_height(self, box):
        text = self._bubble_text(box)
        wrap_mode = "word"
        try:
            wrap_mode = str(box.cget("wrap") or "word")
        except Exception:
            pass
        if wrap_mode == "none":
            return max(40, ((text or "").count("\n") + 1) * 19 + 14)
        width = max(self._bubble_wrap(), 80)
        wrapped = 0
        for line in (text or "").split("\n"):
            wrapped += max(1, (len(line) * 8) // width + 1)
        return max(40, wrapped * 19 + 14)

    def _grow_live_box(self, box=None, force=False):
        box = box or self._live_box
        if box is None:
            return
        needed = self._needed_box_height(box)
        cap = self._max_bubble_height()
        try:
            current = int(box.cget("height") or 40)
        except Exception:
            current = 40
        if force:
            target = min(cap, needed)
        else:
            if needed <= current:
                return
            target = min(cap, ((needed + 79) // 80) * 80)
            if target <= current:
                return
        if target != current:
            try:
                box.configure(height=target, activate_scrollbars=True)
            except Exception:
                try:
                    box.configure(height=target)
                except Exception:
                    pass
            self._refresh_chat_region()

    def _fit_text_box(self, box):
        self._grow_live_box(box, force=True)

    def _append_answer(self, text):
        if self._live_box is None:
            self._add_bubble("assistant", text, live=True)
        else:
            inner = getattr(self._live_box, "_textbox", None)
            if inner is not None:
                current = inner.get("1.0", "end-1c")
                if current == "…":
                    inner.delete("1.0", "end")
                inner.insert("end", text)
                self._grow_live_box(self._live_box)
            else:
                current = self._bubble_text(self._live_box)
                if current == "…":
                    current = ""
                self._set_bubble_text(self._live_box, current + text)
        if self._transcript and self._transcript[-1][0] == "assistant":
            self._transcript[-1][1] += text if self._transcript[-1][1] != "…" else text
        self._stream_chars = getattr(self, "_stream_chars", 0) + len(text or "")
        self._scroll_chat()

    def insert_reply(self):
        self.save_file()

    def replace_with_reply(self):
        self.save_file()

    def _chat_record(self):
        return {
            "id": self._chat_id,
            "title": self._chat_title or _chat_title_from(self._chat_snapshot(), self.history),
            "history": list(self.history[-MAX_HISTORY:]),
            "transcript": self._chat_snapshot(),
            "last_reply": self.last_reply,
            "pending_question": self._pending_question,
            # Mémorise le modèle qui a produit la conversation : sans ça, on la
            # rouvre plus tard avec un autre modèle sans s'en apercevoir.
            "model": self._current_model() or self._model_by_provider.get(self._backend()) or "",
            "provider": self._backend(),
        }

    def _chats_for_save(self):
        current = self._chat_record()
        others = [item for item in self._chats if item.get("id") != current["id"]]
        if current["history"] or current["transcript"]:
            return [current] + others[: MAX_CHATS - 1]
        return others[:MAX_CHATS]

    def _refresh_chat_picker(self):
        picker = getattr(self, "chat_picker", None)
        if picker is None:
            return
        records = self._chats_for_save()
        self._chats = records
        labels = self._chat_labels(records)
        current = labels[0] if labels else "Nouvelle conversation"
        try:
            picker.configure(text=f"{current}  ▾")
        except Exception:
            pass

    def _on_chat_pick(self, _choice=None):
        self.manage_chats()

    def _switch_chat(self, record):
        if self._streaming:
            self.cancel_ask()
        self._chats = self._chats_for_save()
        self._chat_id = record.get("id") or _fresh_chat_id()
        self._chat_title = str(record.get("title") or "")
        self._clear_attachments()
        self._clear_chat()
        self._restore_chat(record)
        self._save_session()
        self._set_status(f"Conversation : {record.get('title') or 'sans titre'}")
        self._warn_model_mismatch(record)

    def _chat_model(self, record):
        """Modèle mémorisé pour une conversation (chaîne vide si inconnu)."""
        if not isinstance(record, dict):
            return ""
        return str(record.get("model") or "").strip()

    def _warn_model_mismatch(self, record):
        """Signale qu'une conversation est reprise avec un autre modèle que le sien."""
        saved = self._chat_model(record)
        if not saved:
            return False
        current = self._current_model()
        if not current or current == saved:
            return False
        if self._backend() != (record.get("provider") or self._backend()):
            return False
        self._add_bubble(
            "notice",
            f"Cette conversation a été démarrée avec « {saved} » ; vous continuez avec "
            f"« {current} ». (Ctrl+K → « Revenir au modèle de la conversation »)",
        )
        return True

    def restore_chat_model(self):
        """Remet la conversation courante sur le modèle qui l'a produite."""
        record = next((item for item in self._chats if item.get("id") == self._chat_id), None)
        saved = self._chat_model(record or {})
        if not saved:
            self._set_status("Aucun modèle mémorisé pour cette conversation")
            return
        provider = (record or {}).get("provider") or self._backend()
        if ollama_fs_access.normalize_provider(provider) != self._backend():
            self.provider = ollama_fs_access.normalize_provider(provider)
            if getattr(self, "provider_box", None) is not None:
                self.provider_box.set(self._backend_name())
            self.refresh_models()
        self._on_model(saved, force=True)
        self._set_status(f"Modèle de la conversation : {saved}")

    def new_chat(self):
        if self._streaming:
            self.cancel_ask()
        self._chats = self._chats_for_save()
        self._chat_id = _fresh_chat_id()
        self._chat_title = ""
        self.history.clear()
        self._pending_question = ""
        self.last_reply = ""
        self._clear_attachments()
        self._clear_chat()
        self._refresh_chat_picker()
        self._save_session()
        self._set_status("Nouvelle conversation")

    def duplicate_chat(self):
        if not self.history and not self._transcript:
            self._set_status("Rien à dupliquer")
            return
        snapshot = self._chat_record()
        snapshot["id"] = _fresh_chat_id()
        title = (snapshot.get("title") or "Conversation").strip()
        snapshot["title"] = f"{title} (copie)"
        self._chats = [snapshot] + [item for item in self._chats_for_save() if item.get("id") != snapshot["id"]]
        self._switch_chat(snapshot)
        self._set_status("Conversation dupliquée")

    def cancel_ask(self):
        if not self._streaming and self._stream_response is None:
            return
        self._cancel.set()
        self._set_chat_state("annulation…")
        self._set_status("Arrêt du raisonnement…")
        response = self._stream_response
        self._stream_response = None
        ollama_fs_access.close_http(response)

    def _set_stream_buttons(self, running):
        ask = getattr(self, "ask_button", None)
        cancel = getattr(self, "cancel_button", None)
        if ask is None:
            return
        if running:
            ask.configure(text="Arrêter", command=self.cancel_ask, state="normal")
            self._paint(ask, "btn_danger")
            if cancel is not None:
                cancel.configure(state="normal")
        else:
            ask.configure(text="Demander", command=self.ask_model, state="normal")
            self._paint(ask, "btn_primary")
            if cancel is not None:
                cancel.configure(state="disabled")

    def _tick_stream(self):
        if not self._streaming:
            return
        elapsed = max(0, int(time.monotonic() - (self._stream_t0 or time.monotonic())))
        parts = [f"{elapsed}s"]
        if self._stream_chars:
            parts.append(f"{self._stream_chars} car.")
        if self._thinking_bits:
            parts.append("réflexion")
        self._set_chat_state(" · ".join(parts))
        self.after(1000, self._tick_stream)

    def _commit_reply(self, reply):
        if self._continuing:
            self.last_reply = (self.last_reply or "") + reply
            if self.history and self.history[-1].get("role") == "assistant":
                self.history[-1]["content"] = self.last_reply
            else:
                if self._pending_question and (not self.history or self.history[-1].get("role") != "user"):
                    self.history.append({"role": "user", "content": self._pending_question})
                self.history.append({"role": "assistant", "content": self.last_reply})
            if self._transcript and self._transcript[-1][0] == "assistant":
                self._transcript[-1][1] = self.last_reply
        else:
            self.last_reply = reply
            self.history.append({"role": "user", "content": self._pending_question})
            self.history.append({"role": "assistant", "content": reply})
        self.history = self.history[-MAX_HISTORY:]
        self._set_apply_state()
        self._save_session()
        self._refresh_chat_picker()
        self.after(120, self._maybe_auto_apply)
        self.after(150, self._maybe_run_tools)

    def _real_updates(self, reply=None):
        text = self.last_reply if reply is None else reply
        raw = ollama_fs_access.extract_file_updates(
            text, hints=self._last_sent_files, question=self._pending_question,
        )
        if not raw and ollama_fs_access.has_edit_intent(self._pending_question):
            raw = self._updates_from_reply()
        real = []
        for item in raw:
            path = self._match_update_path(item["path"])
            label = Path(path).as_posix()
            if not ollama_fs_access.is_safe_rel_path(label):
                if len(self._last_sent_files) == 1:
                    path = Path(self._last_sent_files[0])
                    label = path.as_posix()
                else:
                    continue
            if ollama_fs_access.same_text(self.target_folder, path, item["content"]):
                continue
            real.append({"path": label, "content": item["content"]})
        return real

    def _apply_real_updates(self):
        updates = self._real_updates()
        self._pending_updates = {item["path"]: item["content"] for item in updates}
        if not self._pending_updates:
            return False
        self._write_backup = []
        self._fill_list(self._files_to_show())
        if self.selected_key:
            self._highlight(Path(self.selected_key))
        self._refresh_file_meta()
        self._offer_diffs(updates)
        return True

    def _offer_diffs(self, updates):
        self._diff_acceptors = []
        self._diff_ignores = []
        self._add_bubble("notice", f"{len(updates)} fichier(s) proposé(s). Acceptez ou ignorez avant d'écrire.")
        if len(updates) > 1:
            bar = ctk.CTkFrame(self.chat, fg_color="transparent")
            bar.pack(fill="x", padx=8, pady=(0, 4))
            self._button(bar, "Accepter tout", self._accept_all_diffs, primary=True, width=120).pack(side="left")
            self._button(bar, "Ignorer tout", self._ignore_all_diffs, width=110).pack(side="left", padx=(8, 0))
        for item in updates:
            old = ""
            created = False
            try:
                old = ollama_fs_access.read_file(self.target_folder, item["path"])
            except FileNotFoundError:
                created = True
            except Exception:
                old = ""
            diff = ollama_fs_access.brief_diff(old, item["content"], item["path"])
            self._add_diff_card(item["path"], item["content"], diff, created)

    def _accept_all_diffs(self):
        for action in list(self._diff_acceptors):
            action()
        self._diff_acceptors = []
        self._diff_ignores = []

    def _ignore_all_diffs(self):
        for action in list(self._diff_ignores):
            action()
        self._diff_acceptors = []
        self._diff_ignores = []

    def _add_diff_card(self, path, content, diff, created=False):
        row = ctk.CTkFrame(self.chat, fg_color="transparent")
        row.pack(fill="x", pady=4, padx=4)
        card = self._styled(ctk.CTkFrame(row, corner_radius=14), "raised")
        card.pack(fill="x")
        title = ("Nouveau fichier" if created else "Modification") + f" · {path}"
        self._styled(ctk.CTkLabel(card, text=title, font=self.font_tiny, anchor="w"), "accent").pack(
            fill="x", padx=12, pady=(8, 0)
        )
        box = self._styled(
            ctk.CTkTextbox(
                card, font=self.font_editor, wrap="none",
                height=min(220, 48 + 16 * min(12, diff.count("\n") + 1)),
                border_width=0, corner_radius=8, activate_scrollbars=True,
            ),
            "bubble_box",
        )
        box.pack(fill="x", padx=12, pady=(4, 6))
        box.insert("1.0", diff)
        self._color_diff(box)
        self._lock_bubble(box)
        bar = ctk.CTkFrame(card, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=(0, 10))

        def accept():
            if not row.winfo_exists():
                return
            self._pending_updates[path] = content
            result = self._write_one(path, content, report=True, refresh_list=True)
            self._pending_updates.pop(path, None)
            if result and result != "unchanged":
                self._set_undo_state()
            row.destroy()
            self._scroll_chat()

        def ignore():
            if not row.winfo_exists():
                return
            self._pending_updates.pop(path, None)
            row.destroy()
            self._scroll_chat()

        self._diff_acceptors.append(accept)
        self._diff_ignores.append(ignore)
        self._button(bar, "Accepter", accept, primary=True, width=100).pack(side="left")
        self._button(bar, "Ignorer", ignore, width=90).pack(side="left", padx=(8, 0))
        self._scroll_chat()

    def _color_diff(self, box):
        inner = getattr(box, "_textbox", None)
        if inner is None:
            return
        try:
            inner.tag_configure("plus", foreground=self.C("sage"))
            inner.tag_configure("minus", foreground=self.C("danger"))
            inner.tag_configure("meta", foreground=self.C("muted"))
            last = inner.index("end-1c")
            lines = int(str(last).split(".")[0])
        except Exception:
            return
        for index in range(1, lines + 1):
            start = f"{index}.0"
            head = inner.get(start, f"{index}.3")
            if head.startswith(("+++", "---", "@@")):
                inner.tag_add("meta", start, f"{index}.end")
            elif head.startswith("+"):
                inner.tag_add("plus", start, f"{index}.end")
            elif head.startswith("-"):
                inner.tag_add("minus", start, f"{index}.end")

    def undo_last_write(self):
        if not self._write_backup:
            messagebox.showinfo(APP_NAME, "Aucune écriture récente à annuler.")
            return
        restored, errors = [], []
        for item in reversed(self._write_backup):
            path = item["path"]
            try:
                if item.get("old") is None:
                    try:
                        current = ollama_fs_access.read_file(self.target_folder, path)
                        lumio_snapshots.snapshot(self.target_folder, path, current, reason="delete")
                    except Exception:
                        pass
                    ollama_fs_access.delete_file(self.target_folder, path)
                else:
                    ollama_fs_access.write_file(self.target_folder, path, item["old"])
                restored.append(path)
            except Exception:
                errors.append(path)
        self._write_backup = []
        self._set_undo_state()
        self.reload_files(confirm=False)
        if restored:
            self._add_bubble("notice", "Écriture annulée : " + ", ".join(restored))
            self._set_status("Dernière écriture annulée")
        if errors:
            messagebox.showerror(APP_NAME, "Impossible d'annuler : " + ", ".join(errors))

    def _set_undo_state(self):
        button = getattr(self, "undo_button", None)
        if button is None:
            return
        button.configure(state="normal" if self._write_backup else "disabled")

    def _should_request_bodies(self):
        if getattr(self, "_bodies_requested", False):
            return False
        if ollama_fs_access.looks_like_roleplay(self.last_reply):
            return False
        if ollama_fs_access.wants_gui(self._pending_question):
            return False
        return ollama_fs_access.has_edit_intent(self._pending_question)

    def _should_retry_gui(self, reply):
        if getattr(self, "_gui_retried", False):
            return False
        if self._continuing:
            return False
        if not ollama_fs_access.wants_gui(self._pending_question):
            return False
        return not ollama_fs_access.looks_like_runnable_gui(reply)

    def _retry_gui(self):
        self._gui_retried = True
        self._add_bubble(
            "notice",
            "Le modèle n'a pas livré une fenêtre exécutable. Nouvel essai, sans l'historique cassé.",
        )
        self.after(150, lambda: self.ask_model(question=ollama_fs_access.GUI_RETRY_PROMPT))

    def _notify_no_write(self):
        self._set_chat_state("")
        self._set_status("Aucun fichier modifié sur le disque")
        self._add_bubble("notice", "Aucun fichier n'a été modifié : le contenu proposé était identique au disque.")
        messagebox.showinfo(
            APP_NAME,
            "Aucun fichier n'a été modifié sur le disque.\n"
            "La réponse répétait le contenu actuel, sans changement réel.",
        )

    def _request_real_files(self):
        model = self._current_model()
        if not model or model in {"Chargement…", "Aucun modèle"}:
            self._notify_no_write()
            return
        files = self._files_for_context()
        overrides = self._editor_overrides()
        context, _omitted, report = ollama_fs_access.build_context(
            self.target_folder, files, overrides,
            char_limit=ollama_fs_access.context_budget(model),
            **self._context_kwargs(self._pending_question),
        )
        self._last_sent_files = [item["path"] for item in report.get("sent") or []]
        if self.settings.get("tools_enabled"):
            context = f"{context}\n\n{lumio_tools.guide()}".strip()
        history = list(self.history)
        self._bodies_requested = True
        self._writing_files = True
        self._continuing = False
        self._cancel.clear()
        self._thinking_bits = ""
        self._streaming = True
        self._last_stats = ""
        self.continue_button.configure(state="disabled")
        self._set_stream_buttons(True)
        self._stream_t0 = time.monotonic()
        self._stream_chars = 0
        self._chat_follow = True
        self._set_chat_state("0s")
        self.after(1000, self._tick_stream)
        self._add_bubble("notice", "Demande des fichiers réellement modifiés.")
        self._add_bubble("assistant", "", live=True)
        self._set_status("Écriture des fichiers sur le disque…")
        self.after(50, self._pump)

        def on_open(response):
            self._stream_response = response

        def worker():
            try:
                reply = ollama_fs_access.ask_ollama(
                    model,
                    ollama_fs_access.WRITE_FILES_PROMPT,
                    context,
                    history=history,
                    on_delta=self._events.put,
                    on_open=on_open,
                    cancel_event=self._cancel,
                    attach_files=True,
                    on_notice=lambda text: self._events.put(("notice", text)),
                    on_progress=lambda text: self._events.put(("progress", text)),
                    on_thinking=lambda text: self._events.put(("thinking", text)),
                    on_stats=lambda text: self._events.put(("stats", text)),
                    provider=self._backend(),
                )
                self._events.put(("done", reply, self._cancel.is_set()))
            except Exception as error:
                if self._cancel.is_set():
                    self._events.put(("done", "", True))
                else:
                    self._events.put(("error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def _auto_apply_reply(self):
        return self._apply_real_updates()

    def _notify_done(self, cut_off=False, quiet=False):
        title = "Réponse coupée — vous pouvez continuer" if cut_off else "Réponse terminée"
        extra = f" · {self._last_stats}" if getattr(self, "_last_stats", "") else ""
        if getattr(self, "_last_cost_text", ""):
            extra += f" · {self._last_cost_text}"
        self._set_chat_state(("coupé" if cut_off else "terminé") + extra)
        self._set_status(title)
        self._show_toast(title)
        try:
            if self.state() == "iconic":
                self.deiconify()
            self.bell()
            self.lift()
            self.attributes("-topmost", True)
            self.after(250, lambda: self.attributes("-topmost", False))
        except Exception:
            pass
        away = self.focus_displayof() is None or self.state() == "iconic" or not self._chat_follow
        if away and not quiet:
            messagebox.showinfo(APP_NAME, title + ".")

    def _show_toast(self, text):
        old = getattr(self, "_toast", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        toast = ctk.CTkToplevel(self)
        toast.overrideredirect(True)
        toast.configure(fg_color=self.C("accent"))
        ctk.CTkLabel(toast, text=text, text_color=self.C("ink"), font=self.font_ui).pack(padx=18, pady=12)
        toast.update_idletasks()
        width, height = toast.winfo_reqwidth(), toast.winfo_reqheight()
        x = self.winfo_rootx() + max(16, self.winfo_width() - width - 28)
        y = self.winfo_rooty() + max(16, self.winfo_height() - height - 52)
        toast.geometry(f"+{x}+{y}")
        try:
            toast.attributes("-topmost", True)
        except Exception:
            pass
        self._toast = toast
        self.after(5000, lambda: toast.destroy() if toast.winfo_exists() else None)

    def _finish_ask(self):
        if self._live_box is not None:
            self._grow_live_box(self._live_box, force=True)
        self._collapse_think()
        self._thinking_bits = ""
        self._streaming = False
        self._stream_response = None
        self._continuing = False
        self._set_stream_buttons(False)
        self._set_continue_state()
        self._on_chat_resize()
        self._scroll_chat(force=True)
        if not getattr(self, "chat_state", None):
            return
        if self.chat_state.cget("text") == "écriture en cours…":
            self._set_chat_state("")

    def _poll_background(self):
        """Pompe dédiée aux notifications hors streaming (modèles, voyants)."""
        while True:
            try:
                kind, *payload = self._bg_events.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "models":
                    self._apply_models_result(payload[0], payload[1])
                elif kind == "services":
                    self._apply_services(payload[0])
                elif kind == "verify":
                    self._on_verify(payload[0], payload[1])
            except Exception:
                report_error(*sys.exc_info())
        if self.winfo_exists():
            self.after(400, self._poll_background)

    def _pump(self):
        while True:
            try:
                item = self._events.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, str):
                self._append_answer(item)
                continue
            kind = item[0]
            if kind == "notice":
                self._add_bubble("notice", item[1])
            elif kind == "thinking":
                self._on_thinking(item[1])
            elif kind == "progress":
                self._set_status(item[1])
                self._set_chat_state(item[1])
            elif kind == "stats":
                self._last_stats = item[1]
                self._account_usage()
            elif kind == "error":
                self._add_bubble("notice", f"Erreur : {item[1]}")
                bar = ctk.CTkFrame(self.chat, fg_color="transparent")
                bar.pack(fill="x", padx=8, pady=(0, 8))
                self._button(bar, "Relancer", self.retry_last, primary=True, width=90).pack(side="left")
                self._live_box = None
                self._finish_ask()
            elif kind == "done":
                reply, cancelled = item[1], item[2]
                cut_off = False
                if cancelled:
                    self._add_bubble("notice", "Réponse annulée.")
                    if reply:
                        self._commit_reply(reply)
                elif not reply and not self._continuing:
                    if self._live_box is not None:
                        if getattr(self, "_thinking_bits", ""):
                            self._set_bubble_text(
                                self._live_box,
                                "La réflexion s'est terminée sans réponse écrite. Cliquez sur Continuer.",
                            )
                            self.last_reply = self.last_reply or "(réflexion sans réponse)"
                            cut_off = True
                        else:
                            self._set_bubble_text(self._live_box, "(réponse vide)")
                elif reply:
                    writing = getattr(self, "_writing_files", False)
                    if writing:
                        self.last_reply = reply
                        self.history.append({"role": "user", "content": ollama_fs_access.WRITE_FILES_PROMPT})
                        self.history.append({"role": "assistant", "content": reply})
                        self.history = self.history[-MAX_HISTORY:]
                        self._save_session()
                    else:
                        self._commit_reply(reply)
                    if ollama_fs_access.looks_cut_off(self.last_reply):
                        cut_off = True
                    self._show_action_bar(reply, cut_off=cut_off)
                if self._live_box is not None:
                    self._grow_live_box(self._live_box, force=True)
                self._live_box = None
                finished = not cancelled and bool(self.last_reply)
                writing = getattr(self, "_writing_files", False)
                was_continuing = bool(self._continuing)
                self._writing_files = False
                self._finish_ask()
                if not finished:
                    pass
                elif cut_off:
                    self._notify_done(True)
                elif not was_continuing and self._should_retry_gui(reply if not cancelled else ""):
                    self._retry_gui()
                elif self._apply_real_updates():
                    self._notify_done(False, quiet=True)
                elif writing:
                    self._notify_no_write()
                elif self._should_request_bodies():
                    self._request_real_files()
                else:
                    self._notify_done(False)
        if self._streaming:
            self.after(50, self._pump)

    def ask_model(self, *args, question=None):
        if self._streaming:
            return
        if question is None or not isinstance(question, str):
            question = self._question_text()
        question = str(question).replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        internal = bool(getattr(self, "_auto_followup", False))
        self._auto_followup = False
        if not internal:
            self._tool_steps = 0
            self._verify_rounds = 0
            self._turn_cost = 0.0
            self._route_question(question)
        images = [item["b64"] for item in self._attachments]
        thumbs = [item["thumb"] for item in self._attachments]
        if not question.strip() and images:
            question = "Voici une capture du problème. Que vois-tu et que faut-il corriger ?"
        model = self._current_model()
        if not question.strip() and not images:
            messagebox.showinfo(APP_NAME, "Écrivez une question ou joignez une capture.")
            return
        if not model or model in {"Chargement…", "Aucun modèle"}:
            if self._backend() == "online":
                messagebox.showerror(
                    APP_NAME,
                    "Aucun modèle en ligne.\n\n"
                    "Cliquez sur API, collez une clé, puis choisissez un modèle.",
                )
                self.edit_online_api()
            else:
                messagebox.showerror(
                    APP_NAME,
                    f"Aucun modèle disponible. Vérifiez que {self._backend_name()} est lancé.",
                )
            return
        if self._backend() == "online" and ollama_fs_access.looks_like_ollama_model(model):
            messagebox.showerror(
                APP_NAME,
                "Ce nom est un modèle Ollama, pas un ID OpenRouter.\n\n"
                "Cliquez sur « Choisir un modèle », puis prenez un ID OpenRouter "
                "(par exemple un DeepSeek).",
            )
            self.refresh_models()
            return
        if ollama_fs_access.wants_gui(question) and self._backend() == "online" and ollama_fs_access.is_weak_coder(model):
            better = ollama_fs_access.best_coder(self._all_models)
            if better and better != model:
                if messagebox.askyesno(
                    APP_NAME,
                    f"« {model} » est un modèle de roleplay (13B). "
                    "Il raconte des histoires au lieu d'écrire un logiciel, "
                    "d'où le code cassé et la fenêtre DOS qui clignote.\n\n"
                    f"Passer sur {better} ?",
                ):
                    self._on_model(better)
                    model = better
                else:
                    self._add_bubble(
                        "notice",
                        "Ce modèle n'est pas un codeur. Le résultat sera probablement encore cassé.",
                    )
        if not ollama_fs_access.backend_up(self._backend()):
            if self._backend() == "online":
                if not ollama_fs_access.online_config().get("key"):
                    messagebox.showerror(
                        APP_NAME,
                        "Aucune clé API.\n\nCliquez sur API pour coller votre clé OpenRouter ou DeepSeek.",
                    )
                    self.edit_online_api()
                else:
                    messagebox.showerror(
                        APP_NAME,
                        "Le service en ligne ne répond pas.\n\n"
                        "Vérifiez l'URL, la clé API et votre connexion Internet, puis ouvrez « Choisir un modèle ».",
                    )
            elif self._backend() == "pgpt":
                messagebox.showerror(
                    APP_NAME,
                    "PrivateGPT n'est pas lancé.\n\n"
                    "Ouvrez le workbench (http://127.0.0.1:8080/ui/), "
                    "attendez qu'il soit prêt, puis cliquez sur Choisir un modèle.",
                )
            else:
                messagebox.showerror(
                    APP_NAME,
                    "Ollama n'est pas lancé.\n\n"
                    "Ouvrez Ollama, attendez qu'il soit prêt, puis choisissez un modèle.",
                )
            return
        files = self._files_for_context()
        if not files:
            self.reload_files(confirm=False)
            files = self._files_for_context()
        if not files and not images:
            self._add_bubble("notice", "Dossier vide : Lumio pourra créer les fichiers demandés.")
        if self._backend() == "online" and not self._online_warned:
            self._online_warned = True
            self._add_bubble(
                "notice",
                "Moteur en ligne : les fichiers envoyés quittent votre PC vers le serveur de l'API.",
            )
        folder = self.target_folder
        history = list(self.history)
        skip_history = bool(ollama_fs_access.wants_gui(question) and history)
        if skip_history:
            history = []
        overrides = self._editor_overrides()
        context, omitted, report = ollama_fs_access.build_context(
            folder, files, overrides,
            char_limit=ollama_fs_access.context_budget(model),
            **self._context_kwargs(question),
        )
        if self.settings.get("tools_enabled"):
            context = f"{context}\n\n{lumio_tools.guide()}".strip()
        self._gui_retried = question.strip() == ollama_fs_access.GUI_RETRY_PROMPT.strip()
        self._pending_question = question
        self._last_sent_files = [item["path"] for item in report.get("sent") or []]
        self._pending_updates = {}
        self._writing_files = False
        self._bodies_requested = False
        self.model_var.set(model)
        self._cancel.clear()
        self._streaming = True
        self._last_stats = ""
        self._continuing = False
        self.last_reply = ""
        self._live_box = None
        self._thinking_bits = ""
        if question.strip() and (not self._question_stack or self._question_stack[-1] != question):
            self._question_stack = (self._question_stack + [question])[-20:]
        self._stack_index = None
        self.continue_button.configure(state="disabled")
        self._set_stream_buttons(True)
        self._stream_t0 = time.monotonic()
        self._stream_chars = 0
        self._chat_follow = True
        self._set_chat_state("0s")
        self.after(1000, self._tick_stream)
        self._clear_question()
        self._add_bubble("user", question, pictures=thumbs)
        self._clear_attachments()
        self._add_bubble("notice", ollama_fs_access.format_send_summary(report, omitted))
        if skip_history:
            self._add_bubble(
                "notice",
                "Historique ignoré pour cette interface : les essais cassés polluaient le modèle.",
            )
        if overrides:
            self._add_bubble("notice", "Envoi du texte de l'éditeur, pas encore enregistré.")
        self._add_bubble("assistant", "", live=True)
        sent_names = ", ".join(self._last_sent_files) or "liste seulement"
        self._set_status(f"Envoi : {sent_names}")
        self._refresh_send_preview()
        self.after(50, self._pump)

        def on_open(response):
            self._stream_response = response

        def worker():
            try:
                reply = ollama_fs_access.ask_ollama(
                    model,
                    question,
                    context,
                    history=history,
                    on_delta=self._events.put,
                    on_open=on_open,
                    cancel_event=self._cancel,
                    images=images,
                    on_notice=lambda text: self._events.put(("notice", text)),
                    on_progress=lambda text: self._events.put(("progress", text)),
                    on_thinking=lambda text: self._events.put(("thinking", text)),
                    on_stats=lambda text: self._events.put(("stats", text)),
                    provider=self._backend(),
                )
                self._events.put(("done", reply, self._cancel.is_set()))
            except Exception as error:
                if self._cancel.is_set():
                    self._events.put(("done", "", True))
                else:
                    self._events.put(("error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def continue_reply(self):
        if self._streaming:
            return
        if not self.last_reply:
            messagebox.showinfo(APP_NAME, "Aucune réponse à continuer.")
            return
        model = self._current_model()
        if not model or model in {"Chargement…", "Aucun modèle"}:
            messagebox.showerror(
                APP_NAME,
                f"Aucun modèle disponible. Vérifiez que {self._backend_name()} est lancé.",
            )
            return
        history = list(self.history)
        if not history or history[-1].get("role") != "assistant":
            if self._pending_question:
                history.append({"role": "user", "content": self._pending_question})
            history.append({"role": "assistant", "content": self.last_reply})
        else:
            history[-1] = {"role": "assistant", "content": self.last_reply}
        self._continuing = True
        self._cancel.clear()
        self._thinking_bits = ""
        self._streaming = True
        self._last_stats = ""
        self.continue_button.configure(state="disabled")
        self._set_stream_buttons(True)
        self._stream_t0 = time.monotonic()
        self._stream_chars = 0
        self._chat_follow = True
        self._set_chat_state("0s")
        self.after(1000, self._tick_stream)
        self._clear_action_bar()
        self._live_box = self._last_assistant_label
        if self._live_box is None:
            self._add_bubble("assistant", self.last_reply, live=True)
        self._set_status("Suite de la réponse…")
        self.after(50, self._pump)

        def on_open(response):
            self._stream_response = response

        def worker():
            try:
                reply = ollama_fs_access.ask_ollama(
                    model,
                    ollama_fs_access.CONTINUE_PROMPT,
                    "",
                    history=history,
                    on_delta=self._events.put,
                    on_open=on_open,
                    cancel_event=self._cancel,
                    attach_files=False,
                    on_notice=lambda text: self._events.put(("notice", text)),
                    on_progress=lambda text: self._events.put(("progress", text)),
                    on_thinking=lambda text: self._events.put(("thinking", text)),
                    on_stats=lambda text: self._events.put(("stats", text)),
                    provider=self._backend(),
                )
                self._events.put(("done", reply, self._cancel.is_set()))
            except Exception as error:
                if self._cancel.is_set():
                    self._events.put(("done", "", True))
                else:
                    self._events.put(("error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def _load_models(self):
        """Fil d'arrière-plan : publie le résultat dans la file, jamais dans Tk."""
        error = ""
        try:
            models = ollama_fs_access.list_models(self._backend())
        except Exception as exc:
            models = []
            error = str(exc)
        self._bg_events.put(("models", list(models), error))

    def _apply_models_result(self, models, error):
        self._all_models = list(models)
        self._models_error = error
        self._apply_model_list()
        if error:
            self._set_status(error)
        picker = getattr(self, "_model_picker", None)
        fill = getattr(picker, "_fill_models", None) if picker is not None else None
        if fill is not None:
            try:
                if picker.winfo_exists():
                    fill()
            except Exception:
                pass

    def _apply_model_list(self):
        provider = self._backend()
        label = self._backend_name()
        names = list(self._all_models)
        if provider == "online":
            names = [name for name in names if not ollama_fs_access.is_weak_coder(name)]
        saved = self._current_model() or self._model_by_provider.get(provider) or ""
        if saved in {"Chargement…", "Aucun modèle"}:
            saved = ""
        keep_catalog = bool(provider == "online" and saved and saved == self._catalog_choice)
        if (
            saved and saved not in names
            and not ollama_fs_access.is_uncensored_model(saved)
            and not keep_catalog
        ):
            saved = ""
        if provider == "online" and ollama_fs_access.looks_like_ollama_model(saved):
            saved = ""
        if provider == "online" and saved and ollama_fs_access.is_weak_coder(saved) and not keep_catalog:
            saved = ""
        elif saved and saved not in names:
            names = [saved] + names
        pick = ollama_fs_access.pick_listed_model(names, saved, provider)
        if pick:
            self.model_var.set(pick)
            self._model_by_provider[provider] = pick
            self._paint(self.ollama_state, "sage")
            extra = ""
            if provider == "ollama":
                window = ollama_fs_access.model_window_label(pick)
                extra = f" · {window}" if window else ""
            elif provider == "pgpt":
                extra = " · PrivateGPT"
            else:
                extra = " · En ligne"
            extra += f" · {len(names)} modèle{'s' if len(names) != 1 else ''}"
            tag = ollama_fs_access.model_coding_tag(pick)
            if tag and "pas pour le code" in tag:
                self._paint(self.ollama_state, "danger")
                self.ollama_state.configure(text=f"·  {tag}{extra}")
            else:
                self.ollama_state.configure(text=f"·  prêt{extra}")
        else:
            if not self._current_model() or self._current_model() in {"Chargement…", "Aucun modèle"}:
                self.model_var.set("")
            self._paint(self.ollama_state, "danger")
            if self._models_error:
                hint = "erreur modèles"
            elif not self._all_models:
                hint = "clé API manquante" if provider == "online" and not ollama_fs_access.online_config().get("key") else f"{label} arrêté"
            else:
                hint = f"{label} arrêté"
            self.ollama_state.configure(text=f"·  {hint}")
        self._refresh_model_button()
        self._save_session()

    def _chat_snapshot(self):
        rows = []
        for role, text in self._transcript:
            if role not in {"user", "assistant"}:
                continue
            body = str(text or "").strip()
            if body and body != "…":
                rows.append({"role": role, "content": body})
        return rows[-MAX_TRANSCRIPT:]

    def _restore_chat(self, saved):
        current = None
        for item in self._chats:
            if item.get("id") == self._chat_id:
                current = item
                break
        if current:
            saved = {
                **saved,
                "history": current.get("history") or saved.get("history"),
                "transcript": current.get("transcript") or saved.get("transcript"),
                "last_reply": current.get("last_reply", saved.get("last_reply")),
                "pending_question": current.get("pending_question", saved.get("pending_question")),
            }
        history = saved.get("history") if isinstance(saved.get("history"), list) else []
        transcript = saved.get("transcript") if isinstance(saved.get("transcript"), list) else []
        self.history = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                self.history.append({"role": role, "content": content})
        self.history = self.history[-MAX_HISTORY:]
        self.last_reply = str(saved.get("last_reply") or "")
        self._pending_question = str(saved.get("pending_question") or "")
        if not self.last_reply:
            for item in reversed(self.history):
                if item["role"] == "assistant":
                    self.last_reply = item["content"]
                    break
        if not transcript:
            transcript = [{"role": item["role"], "content": item["content"]} for item in self.history]
        if not transcript:
            self._set_apply_state()
            return
        for item in transcript[-MAX_TRANSCRIPT:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = str(item.get("content") or "")
            if role in {"user", "assistant"} and content:
                self._add_bubble(role, content)
        self._set_apply_state()
        self._set_status("Conversation rétablie")
        self.after(80, self._refresh_chat_picker)
        self.after(120, lambda: self._scroll_chat(force=True))

    def _save_session(self, immediate=False):
        """Planifie l'enregistrement (debounce) ; `immediate` écrit tout de suite.

        Vingt appels déclenchaient vingt écritures sur le partage ; une seule
        écriture 1,5 s après la dernière modification suffit.
        """
        if immediate or getattr(self, "_closing", False):
            self._save_session_now()
            return
        job = getattr(self, "_save_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._save_job = self.after(SAVE_DELAY_MS, self._save_session_now)

    def _save_session_now(self):
        self._save_job = None
        data = {
            "folder": self.target_folder,
            "extensions": self.ext_var.get(),
            "max_files": self.max_var.get(),
            "model": self.model_var.get(),
            "provider": self._backend(),
            "model_filter": self.model_filter,
            "model_family": self.model_family,
            "ollama_model": self._model_by_provider.get("ollama") or "",
            "pgpt_model": self._model_by_provider.get("pgpt") or "",
            "online_model": self._model_by_provider.get("online") or "",
            "online_url": ollama_fs_access.online_config().get("url") or "",
            "online_key": ollama_fs_access.online_config().get("key") or "",
            "online_preset": ollama_fs_access.online_config().get("preset") or "",
            "catalog_keys": ollama_fs_access.configure_catalog_keys(),
            "catalog_source": self.catalog_source,
            "catalog_model": self._catalog_choice,
            "recent_folders": self.recent_folders,
            "geometry": self.geometry(),
            "theme": self.theme_name,
            "sash": self._current_sash(),
            "history": self.history[-MAX_HISTORY:],
            "transcript": self._chat_snapshot(),
            "last_reply": self.last_reply,
            "pending_question": self._pending_question,
            "recent_files": self._recent_files[:8],
            "pinned": sorted(self._pinned),
            "excluded": sorted(self.excluded),
            "question_stack": self._question_stack[-20:],
            "draft": self._question_text(),
            "zoom": self._zoom,
            "selected_file": self.selected_key or "",
            "chat_id": self._chat_id,
            "chats": self._chats_for_save(),
            "settings": dict(self.settings),
            "collapsed_dirs": sorted(self._collapsed_dirs),
        }
        try:
            # lumio_session chiffre les secrets, écrit de façon atomique et
            # conserve une copie .bak.
            lumio_session.save(data, SESSION_PATH)
        except OSError as exc:
            try:
                self._set_status(f"Session non enregistrée : {exc}")
            except Exception:
                pass

    def report_callback_exception(self, exc_type, exc, tb):
        """Erreurs survenant dans un callback Tk (clic, after…) : journal + dialogue."""
        report_error(exc_type, exc, tb)

    def _on_close(self):
        if not self._confirm_leave():
            return
        self.cancel_ask()
        self._closing = True
        self._save_session(immediate=True)
        self.destroy()

    def _apply_icon(self):
        if ICON_ICO.exists():
            try:
                self.iconbitmap(str(ICON_ICO))
            except Exception:
                pass
        if ICON_PNG.exists():
            try:
                from PIL import Image, ImageTk
                picture = Image.open(ICON_PNG).resize((64, 64), Image.Resampling.LANCZOS)
                self._window_icon = ImageTk.PhotoImage(picture)
                self.iconphoto(True, self._window_icon)
            except Exception:
                pass

    def _mark_image(self):
        if not ICON_PNG.exists():
            return None
        from PIL import Image
        picture = Image.open(ICON_PNG)
        self._badge = ctk.CTkImage(light_image=picture, dark_image=picture, size=(50, 50))
        return self._badge

    def _wordmark_image(self):
        if not TITLE_FONT.exists():
            return None
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype(str(TITLE_FONT), 56)
        dummy = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        draw = ImageDraw.Draw(dummy)
        left, top, right, bottom = draw.textbbox((0, 0), APP_NAME, font=font)
        width, height = right - left, bottom - top
        image = Image.new("RGBA", (width + 8, height + 8), (0, 0, 0, 0))
        ImageDraw.Draw(image).text((4 - left, 4 - top), APP_NAME, font=font, fill=self.C("text"))
        self._wordmark = ctk.CTkImage(light_image=image, dark_image=image, size=(image.width // 2, image.height // 2))
        return self._wordmark

    def _enable_drop(self):
        if DND_FILES is None:
            return
        try:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _preview_replace(self, filename, text):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Aperçu du remplacement")
        dialog.geometry("740x560")
        dialog.minsize(480, 360)
        dialog.resizable(True, True)
        dialog.configure(fg_color=self.C("bg"))
        dialog.transient(self)
        dialog.grab_set()
        decided = {"ok": False}

        ctk.CTkLabel(
            dialog,
            text=f"Ce texte remplacera {filename}",
            text_color=self.C("text"), font=self.font_heading, anchor="w",
        ).pack(fill="x", padx=18, pady=(16, 8))
        preview = ctk.CTkTextbox(
            dialog, font=self.font_editor, fg_color=self.C("card"), text_color=self.C("text"),
            border_width=1, border_color=self.C("line"), corner_radius=12, wrap="word",
            activate_scrollbars=True,
        )
        preview.pack(fill="both", expand=True, padx=18)
        preview.insert("1.0", text)
        preview.configure(state="disabled")

        bar = ctk.CTkFrame(dialog, fg_color="transparent")
        bar.pack(fill="x", padx=18, pady=14)

        def accept():
            decided["ok"] = True
            dialog.destroy()

        def cancel():
            dialog.destroy()

        self._button(bar, "Annuler", cancel).pack(side="right")
        self._button(bar, "Remplacer", accept, primary=True).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Escape>", lambda _event: cancel())
        self.wait_window(dialog)
        return decided["ok"]

    def _on_drop(self, event):
        raw = getattr(event, "data", "")
        try:
            paths = self.tk.splitlist(raw)
        except Exception:
            paths = [str(raw).strip("{}")]
        if not paths:
            return
        path = Path(str(paths[0]).strip("{}"))
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            self._attach_image_file(path)
            return
        if path.is_file() and path.suffix.lower() not in IMAGE_SUFFIXES:
            try:
                relative = path.resolve().relative_to(Path(self.target_folder).resolve())
            except ValueError:
                relative = None
            if relative is not None:
                key = relative.as_posix()
                self.excluded.discard(key)
                self._pinned.add(key)
                self.reload_files(open_after=relative, confirm=False)
                self._set_status(f"Épinglé : {key}")
                return
        open_after = None
        folder = path
        if path.is_file():
            folder = path.parent
            try:
                open_after = path.resolve().relative_to(folder.resolve())
            except ValueError:
                open_after = Path(path.name)
        if not folder.is_dir():
            return
        if not self._confirm_leave():
            return
        self._switch_folder(folder, open_after=open_after)


def _ensure_shortcut():
    bat = APP_DIR / "Lumio.bat"
    link = APP_DIR / "Lumio.lnk"
    if not bat.exists() or not ICON_ICO.exists():
        return

    def quote(value):
        return str(value).replace("'", "''")

    command = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{quote(link)}'); "
        f"$s.TargetPath = '{quote(bat)}'; "
        f"$s.WorkingDirectory = '{quote(APP_DIR)}'; "
        f"$s.IconLocation = '{quote(ICON_ICO)}'; "
        f"$s.Description = 'Lumio, compagnon d''Ollama'; "
        f"$s.Save()"
    )
    try:
        import subprocess
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=False,
            creationflags=0x08000000,
        )
    except Exception:
        pass


def _register_title_font():
    if not TITLE_FONT.exists():
        return
    try:
        from ctypes import windll
        windll.gdi32.AddFontResourceExW(str(TITLE_FONT), 0x10, 0)
    except Exception:
        pass


def _title_family(window):
    _register_title_font()
    try:
        families = set(window.tk.call("font", "families"))
    except Exception:
        families = set()
    for name in ("Instrument Serif", "Palatino Linotype", "Georgia", "Times New Roman"):
        if name in families:
            return name
    return "Segoe UI"


def _virtual_screen():
    try:
        from ctypes import windll
        user32 = windll.user32
        return (
            user32.GetSystemMetrics(76),
            user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78),
            user32.GetSystemMetrics(79),
        )
    except Exception:
        return 0, 0, 1920, 1080


def _prepare_attachment(image):
    try:
        from PIL import Image
        picture = image
        if max(picture.size) > 1600:
            picture = picture.copy()
            picture.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        to_save = picture.convert("RGB") if picture.mode not in {"RGB", "RGBA"} else picture
        to_save.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        preview = picture.copy()
        preview.thumbnail((72, 72), Image.Resampling.LANCZOS)
        thumb = ctk.CTkImage(light_image=preview, dark_image=preview, size=preview.size)
        return encoded, thumb
    except Exception:
        return "", None


class _SnipOverlay(tk.Toplevel):
    def __init__(self, master, on_result):
        super().__init__(master)
        self.on_result = on_result
        self.start = None
        self.rect = None
        self.withdraw()
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
            self.attributes("-alpha", 0.28)
        except Exception:
            pass
        left, top, width, height = _virtual_screen()
        self.geometry(f"{width}x{height}+{left}+{top}")
        self.configure(bg="#101010")
        self.canvas = tk.Canvas(self, bg="#101010", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        hint = self.canvas.create_text(
            width // 2, 36, text="Glissez pour capturer   ·   Échap pour annuler",
            fill="#f4efe6", font=("Segoe UI", 14),
        )
        self.canvas.tag_raise(hint)
        self.canvas.bind("<ButtonPress-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._up)
        self.bind("<Escape>", lambda _event: self._finish(None))
        self.deiconify()
        self.lift()
        self.focus_force()

    def _down(self, event):
        self.start = (event.x_root, event.y_root)
        if self.rect is not None:
            self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#e4a15a", width=2)

    def _drag(self, event):
        if self.rect is None or self.start is None:
            return
        x0 = self.start[0] - self.winfo_rootx()
        y0 = self.start[1] - self.winfo_rooty()
        self.canvas.coords(self.rect, x0, y0, event.x, event.y)

    def _up(self, event):
        if self.start is None:
            self._finish(None)
            return
        x1, y1 = self.start
        x2, y2 = event.x_root, event.y_root
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        if right - left < 6 or bottom - top < 6:
            self._finish(None)
            return
        self.withdraw()
        self.update_idletasks()
        self.after(90, lambda: self._grab(left, top, right, bottom))

    def _grab(self, left, top, right, bottom):
        image = None
        try:
            from PIL import ImageGrab
            try:
                image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
            except TypeError:
                image = ImageGrab.grab(bbox=(left, top, right, bottom))
        except Exception:
            image = None
        self._finish(image)

    def _finish(self, image):
        callback = self.on_result
        try:
            self.destroy()
        except Exception:
            pass
        if callback is not None:
            callback(image)


def _format_size(nbytes):
    size = max(0, int(nbytes or 0))
    if size < 1024:
        return f"{size} o"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} Ko".replace(".", ",")
    return f"{size / (1024 * 1024):.1f} Mo".replace(".", ",")


def _format_mtime(timestamp):
    try:
        return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")
    except (OSError, OverflowError, ValueError, TypeError):
        return "—"


def _file_meta_text(disk_size, mtime, buffer_size=None, created=False):
    date = _format_mtime(mtime) if mtime else ""
    before = _format_size(disk_size)
    if created and buffer_size is not None:
        return f"nouveau → {_format_size(buffer_size)}"
    if buffer_size is not None and int(buffer_size) != int(disk_size or 0):
        text = f"{before} → {_format_size(buffer_size)}"
        return f"{text}  ·  {date}" if date else text
    return f"{before}  ·  {date}" if date else before


def _short_path(path, limit=36):
    text = str(path)
    if len(text) <= limit:
        return text
    return "…" + text[-(limit - 1):]


def _recent_label(folder):
    path = Path(folder)
    parent = path.parent.name
    if parent and path.name:
        return f"{parent}/{path.name}"
    return path.name or str(path)


def _recent_folders(saved, current):
    items = []
    seen = set()
    for raw in [current, *(saved or [])]:
        if not raw:
            continue
        folder = str(Path(raw))
        key = str(Path(folder)).casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(folder)
        if len(items) >= MAX_RECENTS:
            break
    return items


def _fresh_chat_id():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _chat_title_from(transcript, history):
    for item in transcript or []:
        if isinstance(item, dict) and item.get("role") == "user":
            line = str(item.get("content") or "").strip().splitlines()
            if line:
                return line[0][:42]
    for item in history or []:
        if isinstance(item, dict) and item.get("role") == "user":
            line = str(item.get("content") or "").strip().splitlines()
            if line:
                return line[0][:42]
    return "Nouvelle conversation"


def _normalize_chats(saved):
    raw = saved.get("chats") if isinstance(saved.get("chats"), list) else []
    chats = []
    for item in raw:
        if isinstance(item, dict) and (item.get("history") or item.get("transcript")):
            chats.append(item)
    if not chats and (saved.get("history") or saved.get("transcript")):
        chats.append({
            "id": saved.get("chat_id") or _fresh_chat_id(),
            "title": _chat_title_from(saved.get("transcript"), saved.get("history")),
            "history": saved.get("history") or [],
            "transcript": saved.get("transcript") or [],
            "last_reply": saved.get("last_reply") or "",
            "pending_question": saved.get("pending_question") or "",
            "model": saved.get("model") or "",
            "provider": saved.get("provider") or "",
        })
    return chats[:MAX_CHATS]


def _read_session():
    """Lit la session (fichier principal ou .bak), secrets déchiffrés."""
    data = lumio_session.load(SESSION_PATH)
    return data if isinstance(data, dict) else {}


def _enable_dpi():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            from ctypes import windll
            windll.user32.SetProcessDPIAware()
        except Exception:
            pass


if __name__ == "__main__":
    _enable_dpi()
    _register_title_font()
    # Rapatrie l'ancienne session du partage vers %APPDATA%\Lumio (une fois).
    try:
        lumio_session.migrate(LEGACY_SESSION_PATH, SESSION_PATH)
    except Exception:
        report_error(*sys.exc_info())
    _first_instance, _mutex = acquire_single_instance()
    if not _first_instance:
        if not focus_existing_window(APP_NAME):
            try:
                messagebox.showinfo(APP_NAME, f"{APP_NAME} est déjà ouvert.")
            except Exception:
                pass
        raise SystemExit(0)
    FileSystemWrapper().mainloop()

