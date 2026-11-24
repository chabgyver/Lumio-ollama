"""Fenetre de choix du modele.

Sorti de gui_tool.py ; les fonctions recoivent l'application et n'utilisent
qu'une poignee de ses services (palette, polices, statut, session).
"""

import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

import lumio_cost
import lumio_image
import lumio_rank
import lumio_router
import lumio_search
import lumio_session
import lumio_snapshots
import lumio_tree
import lumio_verify
import ollama_fs_access

APP_NAME = "Lumio"

def pick_model(app):
    if not app._all_models:
        app.refresh_models()
    dialog = ctk.CTkToplevel(app)
    dialog.title("Modèles")
    dialog.geometry("660x600")
    dialog.minsize(560, 420)
    dialog.configure(fg_color=app.C("bg"))
    dialog.transient(app)
    try:
        dialog.grab_set()
    except Exception:
        pass
    app._model_picker = dialog
    hint = (
        "Uniquement les modèles uncensored capables de coder (Dolphin 24B, Magnum, Euryale…)."
        if app._backend() == "online"
        else "Modèles communautaires AI Horde : gratuits, en file d'attente, sans téléchargement."
        if app._backend() == "horde"
        else "Tous les modèles Ollama installés localement."
    )
    app._styled(
        ctk.CTkLabel(
            dialog,
            text=hint,
            font=app.font_small, anchor="w",
        ),
        "muted",
    ).pack(fill="x", padx=16, pady=(14, 8))
    tools = ctk.CTkFrame(dialog, fg_color="transparent")
    tools.pack(fill="x", padx=16)
    search_var = tk.StringVar()
    search = app._styled(
        ctk.CTkEntry(
            tools, textvariable=search_var, height=36, corner_radius=10,
            font=app.font_small, placeholder_text="Rechercher un modèle…",
        ),
        "entry",
    )
    search.pack(side="left", fill="x", expand=True)
    app._button(tools, "Actualiser", app.refresh_models, width=100).pack(side="left", padx=(8, 0))
    app._button(tools, "Catalogue en ligne", app.browse_catalog, width=160).pack(side="left", padx=(8, 0))

    filter_row = ctk.CTkFrame(dialog, fg_color="transparent")
    filter_row.pack(fill="x", padx=16, pady=(8, 0))
    filter_buttons = {}

    def paint_filters():
        for token, button in filter_buttons.items():
            app._role(button, "btn_primary" if token == app.model_filter else "btn")

    def set_filter(kind):
        app._on_model_filter(kind)
        paint_filters()

    for token, label in ollama_fs_access.MODEL_FILTERS:
        button = app._button(
            filter_row, label, lambda item=token: set_filter(item), width=118,
        )
        button.pack(side="left", padx=(0, 6))
        filter_buttons[token] = button
    paint_filters()
    filter_hint = app._styled(
        ctk.CTkLabel(
            dialog, text="", font=app.font_tiny, anchor="w", justify="left",
            wraplength=600,
        ),
        "muted",
    )
    filter_hint.pack(fill="x", padx=16, pady=(6, 0))
    count = app._styled(ctk.CTkLabel(dialog, text="", font=app.font_tiny, anchor="w"), "muted")
    count.pack(fill="x", padx=16, pady=(8, 4))
    listing = app._styled(ctk.CTkScrollableFrame(dialog, corner_radius=12), "well")
    listing.pack(fill="both", expand=True, padx=16, pady=(0, 8))
    app._model_picker_list = listing

    def choose(name):
        app._on_model(name)
        close()

    job = {"id": None}

    def fill():
        for child in listing.winfo_children():
            child.destroy()
        summary = ollama_fs_access.freedom_summary(
            ollama_fs_access.freedom_counts(app._all_models)
        )
        label_hint = ollama_fs_access.model_filter_hint(app.model_filter)
        filter_hint.configure(text=f"{label_hint}  {summary}".strip())
        if not app._all_models:
            text = app._models_error or "Chargement des modèles…"
            app._styled(ctk.CTkLabel(listing, text=text, font=app.font_small, anchor="w"), "muted").pack(
                fill="x", padx=8, pady=12
            )
            count.configure(text="0")
            return
        names = app._visible_models(search_var.get())
        if not names:
            empty = (
                f"Aucun modèle « {ollama_fs_access.model_filter_label(app.model_filter)} » "
                "dans cette liste : choisissez « Tous » ou Actualisez."
            )
            app._styled(
                ctk.CTkLabel(
                    listing,
                    text=empty,
                    font=app.font_small, anchor="w",
                ),
                "muted",
            ).pack(fill="x", padx=8, pady=12)
            count.configure(text=f"0 modèle sur {len(app._all_models)}")
            return
        count.configure(
            text=(
                f"{len(names)} modèle{'s' if len(names) != 1 else ''} "
                f"sur {len(app._all_models)}"
            )
        )
        current = app._current_model()
        for name in names:
            mark = "●  " if name == current else ""
            bits = [ollama_fs_access.model_coding_tag(name)]
            level = ollama_fs_access.freedom_level(name)
            if level in {"obliterated", "abliterated"}:
                bits.append(level)
            label = f"{mark}{name}"
            detail = "   ·   ".join(bit for bit in bits if bit)
            if detail:
                label = f"{label}   ·   {detail}"
            app._button(
                listing, label,
                lambda item=name: choose(item),
                primary=name == current,
            ).pack(fill="x", padx=6, pady=3)
        app._bind_scrollable_wheel(listing, dialog)

    def schedule_fill(_event=None):
        if job["id"] is not None:
            dialog.after_cancel(job["id"])
        job["id"] = dialog.after(120, fill)

    def close():
        app._model_picker = None
        app._model_picker_list = None
        dialog.destroy()

    dialog._fill_models = fill
    search.bind("<KeyRelease>", schedule_fill)
    fill()
    dialog.protocol("WM_DELETE_WINDOW", close)
    dialog.bind("<Escape>", lambda _event: close())
    search.focus_set()
    app.wait_window(dialog)
