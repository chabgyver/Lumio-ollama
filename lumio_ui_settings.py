"""Fenetre des reglages.

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

def open_settings(app):
    settings = dict(app.settings)
    dialog = ctk.CTkToplevel(app)
    dialog.title("Réglages")
    dialog.geometry("720x620")
    dialog.minsize(560, 460)
    dialog.configure(fg_color=app.C("bg"))
    dialog.transient(app)
    try:
        dialog.grab_set()
    except Exception:
        pass

    ctk.CTkLabel(
        dialog, text="Réglages", text_color=app.C("text"),
        font=app.font_heading, anchor="w",
    ).pack(fill="x", padx=16, pady=(14, 6))

    body = app._styled(ctk.CTkScrollableFrame(dialog, corner_radius=10), "well")
    body.pack(fill="both", expand=True, padx=14, pady=(0, 8))

    app._styled(
        ctk.CTkLabel(body, text="VÉRIFICATION (BOUCLE AGENT)", font=app.font_tiny, anchor="w"),
        "muted",
    ).pack(fill="x", padx=6, pady=(8, 2))
    verify_var = tk.BooleanVar(value=bool(settings.get("verify_auto")))
    app._styled(
        ctk.CTkCheckBox(body, text="Vérifier automatiquement après chaque écriture",
                        variable=verify_var, font=app.font_small,
                        checkbox_width=18, checkbox_height=18),
        "check",
    ).pack(fill="x", padx=6, pady=(2, 6))
    apply_var = tk.BooleanVar(value=bool(settings.get("auto_apply")))
    app._styled(
        ctk.CTkCheckBox(body, text="Appliquer les fichiers sans clic (dangereux : instantanés conservés)",
                        variable=apply_var, font=app.font_small,
                        checkbox_width=18, checkbox_height=18),
        "check",
    ).pack(fill="x", padx=6, pady=(0, 4))
    limits_row = ctk.CTkFrame(body, fg_color="transparent")
    limits_row.pack(fill="x", padx=6, pady=(0, 6))
    files_var = tk.StringVar(value=str(settings.get("auto_apply_max_files") or 2))
    bytes_var = tk.StringVar(value=str(settings.get("auto_apply_max_bytes") or 60000))
    app._styled(ctk.CTkLabel(limits_row, text="Limites auto : max fichiers", font=app.font_tiny), "muted").pack(side="left")
    app._styled(
        ctk.CTkEntry(limits_row, textvariable=files_var, width=56, height=28, font=app.font_small),
        "entry",
    ).pack(side="left", padx=(6, 0))
    app._styled(ctk.CTkLabel(limits_row, text="  max octets", font=app.font_tiny), "muted").pack(side="left")
    app._styled(
        ctk.CTkEntry(limits_row, textvariable=bytes_var, width=90, height=28, font=app.font_small),
        "entry",
    ).pack(side="left", padx=(6, 0))
    command_row = ctk.CTkFrame(body, fg_color="transparent")
    command_row.pack(fill="x", padx=6, pady=(0, 6))
    command_var = tk.StringVar(value=str(settings.get("verify_command") or ""))
    app._styled(
        ctk.CTkEntry(command_row, textvariable=command_var, height=32, corner_radius=8,
                     font=app.font_small, placeholder_text="Commande de vérification (vide = détection)"),
        "entry",
    ).pack(side="left", fill="x", expand=True)

    def detect():
        candidates = lumio_verify.detect_commands(app.target_folder, changed=app._recent_paths())
        if candidates:
            command_var.set(candidates[0])
        else:
            command_var.set("python -m py_compile app.py")

    app._button(command_row, "Détecter", detect, width=90).pack(side="left", padx=(6, 0))
    rounds_var = tk.StringVar(value=str(settings.get("verify_max_rounds") or 2))
    rounds_row = ctk.CTkFrame(body, fg_color="transparent")
    rounds_row.pack(fill="x", padx=6, pady=(0, 10))
    app._styled(ctk.CTkLabel(rounds_row, text="Tentatives de correction :", font=app.font_small), "text").pack(side="left")
    app._styled(
        ctk.CTkEntry(rounds_row, textvariable=rounds_var, width=60, height=30, font=app.font_small),
        "entry",
    ).pack(side="left", padx=(8, 0))

    app._styled(
        ctk.CTkLabel(body, text="MODÈLE LOCAL (OLLAMA)", font=app.font_tiny, anchor="w"), "muted",
    ).pack(fill="x", padx=6, pady=(6, 2))
    think_var = tk.BooleanVar(value=bool(settings.get("think_off")))
    app._styled(
        ctk.CTkCheckBox(body, text="Désactiver la réflexion (réponse directe, beaucoup plus rapide)",
                        variable=think_var, font=app.font_small,
                        checkbox_width=18, checkbox_height=18),
        "check",
    ).pack(fill="x", padx=6, pady=(2, 2))
    app._styled(
        ctk.CTkLabel(
            body,
            text="Concerne les modèles qui réfléchissent avant de répondre (LFM3, Qwen3…). "
                 "Sur une petite carte, le raisonnement coûte souvent plus de temps que la réponse.",
            font=app.font_tiny, anchor="w", justify="left", wraplength=620,
        ),
        "muted",
    ).pack(fill="x", padx=6, pady=(0, 6))
    cap_row = ctk.CTkFrame(body, fg_color="transparent")
    cap_row.pack(fill="x", padx=6, pady=(0, 4))
    cap_var = tk.StringVar(value=str(settings.get("context_cap") or 0))
    app._styled(ctk.CTkLabel(cap_row, text="Plafond de contexte (caractères, 0 = celui du modèle) :", font=app.font_small), "text").pack(side="left")
    app._styled(
        ctk.CTkEntry(cap_row, textvariable=cap_var, width=90, height=30, font=app.font_small),
        "entry",
    ).pack(side="left", padx=(8, 0))
    app._styled(
        ctk.CTkLabel(
            body,
            text="Utile pour un modèle qui annonce une très grande fenêtre (262k…) : "
                 "sans plafond, Lumio peut envoyer 200 000 caractères et saturer une carte de 4 Go.",
            font=app.font_tiny, anchor="w", justify="left", wraplength=620,
        ),
        "muted",
    ).pack(fill="x", padx=6, pady=(0, 8))

    app._styled(
        ctk.CTkLabel(body, text="OUTILS DU MODÈLE", font=app.font_tiny, anchor="w"), "muted",
    ).pack(fill="x", padx=6, pady=(6, 2))

    tools_var = tk.BooleanVar(value=bool(settings.get("tools_enabled")))
    app._styled(
        ctk.CTkCheckBox(body, text="Autoriser ```rechercher:``` et ```lire:``` (le modèle consulte le dossier)",
                        variable=tools_var, font=app.font_small,
                        checkbox_width=18, checkbox_height=18),
        "check",
    ).pack(fill="x", padx=6, pady=(2, 10))

    app._styled(
        ctk.CTkLabel(body, text="COMMANDES AUTORISÉES (UNE PAR LIGNE)", font=app.font_tiny, anchor="w"),
        "muted",
    ).pack(fill="x", padx=6, pady=(6, 2))
    allow_box = app._styled(
        ctk.CTkTextbox(body, height=110, font=ctk.CTkFont(family="Consolas", size=12), wrap="none"),
        "textbox",
    )
    allow_box.pack(fill="x", padx=6, pady=(2, 10))
    allow_box.insert("1.0", "\n".join(settings.get("allowlist") or []))

    app._styled(
        ctk.CTkLabel(body, text="ROUTAGE LOCAL / DISTANT", font=app.font_tiny, anchor="w"), "muted",
    ).pack(fill="x", padx=6, pady=(6, 2))
    router_var = tk.BooleanVar(value=bool(settings.get("router_enabled")))
    app._styled(
        ctk.CTkCheckBox(body, text="Choisir automatiquement le moteur selon la difficulté",
                        variable=router_var, font=app.font_small,
                        checkbox_width=18, checkbox_height=18),
        "check",
    ).pack(fill="x", padx=6, pady=(2, 6))
    policy_row = ctk.CTkFrame(body, fg_color="transparent")
    policy_row.pack(fill="x", padx=6, pady=(0, 6))
    labels = [lumio_router.POLICY_LABELS[key] for key in lumio_router.POLICIES]
    policy_box = app._styled(
        ctk.CTkComboBox(policy_row, values=labels, width=260, height=32, font=app.font_small),
        "combo",
    )
    policy_box.pack(side="left")
    policy_box.set(lumio_router.POLICY_LABELS.get(settings.get("router_policy") or "auto", labels[0]))
    ceiling_var = tk.StringVar(value=str(settings.get("router_ceiling") or "0.05"))
    app._styled(
        ctk.CTkLabel(policy_row, text="  plafond $ / échange :", font=app.font_small), "text",
    ).pack(side="left", padx=(10, 0))
    app._styled(
        ctk.CTkEntry(policy_row, textvariable=ceiling_var, width=80, height=32, font=app.font_small),
        "entry",
    ).pack(side="left", padx=(6, 0))
    turn_row = ctk.CTkFrame(body, fg_color="transparent")
    turn_row.pack(fill="x", padx=6, pady=(0, 8))
    turn_var = tk.StringVar(value=str(settings.get("turn_ceiling") or "0.10"))
    app._styled(ctk.CTkLabel(turn_row, text="Budget $ par question (0 = illimité) :", font=app.font_small), "text").pack(side="left")
    app._styled(
        ctk.CTkEntry(turn_row, textvariable=turn_var, width=80, height=30, font=app.font_small),
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
        try:
            context_cap = max(0, int((cap_var.get() or "0").replace(" ", "")))
        except ValueError:
            context_cap = 0
        updated = dict(app.settings)
        updated.update({
            "verify_command": command_var.get().strip(),
            "verify_auto": bool(verify_var.get()),
            "auto_apply": bool(apply_var.get()),
            "verify_max_rounds": rounds,
            "allowlist": allow or list(lumio_verify.DEFAULT_ALLOWLIST),
            "tools_enabled": bool(tools_var.get()),
            "tools_max_steps": int(settings.get("tools_max_steps") or 2),
            "think_off": bool(think_var.get()),
            "context_cap": context_cap,
            "router_enabled": bool(router_var.get()),
            "router_policy": label_to_key.get(policy_box.get(), "auto"),
            "router_ceiling": ceiling,
            "turn_ceiling": turn_ceiling,
            "auto_apply_max_files": max_files,
            "auto_apply_max_bytes": max_bytes,
        })
        app.settings = updated
        app._save_session(immediate=True)
        dialog.destroy()
        app._set_status("Réglages enregistrés")

    app._button(bar, "Enregistrer", save, primary=True).pack(side="right")
    app._button(bar, "Annuler", lambda: dialog.destroy()).pack(side="right", padx=(0, 8))
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.protocol("WM_DELETE_WINDOW", lambda: dialog.destroy())
    app.wait_window(dialog)