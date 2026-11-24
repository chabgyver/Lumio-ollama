"""Palette de commandes et aide.

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

def command_palette(app):
    """Ctrl+K : tout piloter au clavier (modèle, moteur, chat, dossier…)."""
    dialog = ctk.CTkToplevel(app)
    dialog.title("Commande")
    dialog.geometry("620x460")
    dialog.minsize(480, 320)
    dialog.configure(fg_color=app.C("bg"))
    dialog.transient(app)
    try:
        dialog.grab_set()
    except Exception:
        pass

    entry_var = tk.StringVar()
    entry = app._styled(
        ctk.CTkEntry(
            dialog, textvariable=entry_var, height=38, corner_radius=10, font=app.font_ui,
            placeholder_text="Que voulez-vous faire ? (modèle, conversation, dossier…)",
        ),
        "entry",
    )
    entry.pack(fill="x", padx=14, pady=(14, 8))

    listing = app._styled(ctk.CTkScrollableFrame(dialog, corner_radius=10), "well")
    listing.pack(fill="both", expand=True, padx=14, pady=(0, 8))

    app._styled(
        ctk.CTkLabel(
            dialog, text="Entrée : première commande  ·  Échap : fermer",
            font=app.font_tiny, anchor="w",
        ),
        "muted",
    ).pack(fill="x", padx=16, pady=(0, 12))

    actions = app._palette_commands()
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
            app._button(row, label, lambda act=action: run(act), width=390).pack(side="left")
            if hint:
                app._styled(
                    ctk.CTkLabel(row, text=hint, font=app.font_tiny, anchor="w"), "muted"
                ).pack(side="left", padx=(8, 0))
        if not items:
            app._styled(
                ctk.CTkLabel(listing, text="Aucune commande ne correspond.", font=app.font_small),
                "muted",
            ).pack(pady=12)
        app._bind_scrollable_wheel(listing, dialog)

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
    app.wait_window(dialog)

def show_help(app):
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
        "Moteur AI Horde — modèles communautaires gratuits (file d'attente, sans clé)\n"
        "Clic sur le modèle — liste filtrable : Tous, Uncensored, Abliterated, Obliterated\n"
        "Double-clic / clic droit — fichier",
    )
    return "break"