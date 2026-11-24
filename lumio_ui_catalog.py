"""Catalogue des modeles en ligne.

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

def browse_catalog(app, source=None):
    source = ollama_fs_access.catalog_key(source or getattr(app, "catalog_source", "openrouter"))

    dialog = ctk.CTkToplevel(app)
    dialog.title("Catalogue en ligne")
    dialog.geometry("920x640")
    dialog.minsize(760, 480)
    dialog.configure(fg_color=app.C("bg"))
    dialog.transient(app)
    picker = getattr(app, "_model_picker", None)
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
        head, text="Catalogue en ligne", text_color=app.C("text"),
        font=app.font_heading, anchor="w",
    ).pack(side="left")
    state = app._styled(ctk.CTkLabel(head, text="", font=app.font_tiny, anchor="e"), "muted")
    state.pack(side="right")

    app._styled(
        ctk.CTkLabel(
            dialog,
            text=(
                "Les modèles tournent chez le fournisseur : rien n'est téléchargé et aucune "
                "carte graphique n'est nécessaire. En revanche, les fichiers envoyés en "
                "contexte quittent ce poste."
            ),
            font=app.font_tiny, anchor="w", justify="left", wraplength=860,
        ),
        "muted",
    ).pack(fill="x", padx=18, pady=(6, 12))

    tools = ctk.CTkFrame(dialog, fg_color="transparent")
    tools.pack(fill="x", padx=18)
    search_var = tk.StringVar()
    search = app._styled(
        ctk.CTkEntry(
            tools, textvariable=search_var, height=36, corner_radius=10, font=app.font_small,
            placeholder_text="Rechercher un modèle (qwen, deepseek, llama…)",
        ),
        "entry",
    )
    search.pack(side="left", fill="x", expand=True)
    source_box = app._styled(
        ctk.CTkComboBox(
            tools, values=ollama_fs_access.catalog_labels(), width=150, height=36,
            corner_radius=10, font=app.font_small,
            command=lambda value: pick_source(value),
        ),
        "combo",
    )
    source_box.set(ollama_fs_access.catalog_label(source))
    source_box.pack(side="left", padx=(8, 0))
    app._button(tools, "Actualiser", lambda: load(refresh=True), width=100).pack(
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
        app._styled(
            ctk.CTkCheckBox(
                filters, text=text, variable=variable, font=app.font_tiny,
                command=lambda: render(), checkbox_width=18, checkbox_height=18,
            ),
            "check",
        ).pack(side="left", padx=(0, 16))
    count = app._styled(ctk.CTkLabel(filters, text="", font=app.font_tiny, anchor="e"), "muted")
    count.pack(side="right")

    sort_row = ctk.CTkFrame(dialog, fg_color="transparent")
    sort_row.pack(fill="x", padx=18, pady=(6, 0))
    app._styled(ctk.CTkLabel(sort_row, text="Trier par", font=app.font_tiny), "muted").pack(side="left")
    sort_labels = [lumio_rank.LABELS[key] for key in lumio_rank.CRITERIA]
    current_sort = str(app.settings.get("catalog_sort") or "recommande")
    sort_box = app._styled(
        ctk.CTkComboBox(sort_row, values=sort_labels, width=250, height=30, font=app.font_tiny),
        "combo",
    )
    sort_box.pack(side="left", padx=(8, 0))
    sort_box.set(lumio_rank.LABELS.get(current_sort, sort_labels[0]))
    sort_hint = app._styled(
        ctk.CTkLabel(sort_row, text="", font=app.font_tiny, anchor="w"), "muted",
    )
    sort_hint.pack(side="left", padx=(10, 0))

    def sort_key():
        return lumio_rank.LABEL_TO_KEY.get(sort_box.get(), "recommande")

    def on_sort(_choice=None):
        app.settings["catalog_sort"] = sort_key()
        sort_hint.configure(text=lumio_rank.explain(sort_key()))
        render()
        app._save_session()

    sort_box.configure(command=on_sort)
    sort_hint.configure(text=lumio_rank.explain(current_sort))

    key_slot = ctk.CTkFrame(dialog, fg_color="transparent")
    key_row = ctk.CTkFrame(key_slot, fg_color="transparent")
    key_row.pack(fill="x")
    key_var = tk.StringVar()
    listing = app._styled(ctk.CTkScrollableFrame(dialog, corner_radius=12), "well")
    listing.pack(fill="both", expand=True, padx=18, pady=(10, 6))

    app._styled(
        ctk.CTkLabel(
            dialog,
            text="Choisir un modèle le rend actif : le moteur passe sur « En ligne ».",
            font=app.font_tiny, anchor="w",
        ),
        "muted",
    ).pack(fill="x", padx=18, pady=(0, 14))

    rows = []
    job = {"id": None}

    def set_state(text, role="muted"):
        state.configure(text=text)
        app._paint(state, role)

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
        app._save_session()
        key_slot.pack_forget()
        load(refresh=True)

    key_entry = app._styled(
        ctk.CTkEntry(
            key_row, textvariable=key_var, height=32, corner_radius=10,
            font=app.font_tiny, show="•",
        ),
        "entry",
    )
    key_entry.pack(side="left", fill="x", expand=True)
    app._button(key_row, "Enregistrer la clé", save_key, width=170).pack(side="left", padx=(8, 0))

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
            app._styled(
                ctk.CTkLabel(
                    listing, text="Aucun modèle ne correspond à ces critères.",
                    font=app.font_small, anchor="w",
                ),
                "muted",
            ).pack(fill="x", padx=8, pady=12)
            return
        for row in picked[:60]:
            card = app._styled(ctk.CTkFrame(listing, corner_radius=10), "raised")
            card.pack(fill="x", padx=4, pady=3)
            app._button(
                card, "Choisir", lambda item=row: choose(item), primary=True, width=92,
            ).pack(side="right", padx=10, pady=8)
            body = ctk.CTkFrame(card, fg_color="transparent")
            body.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            app._styled(
                ctk.CTkLabel(body, text=row["id"], font=app.font_small, anchor="w"), "text",
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
            level = ollama_fs_access.freedom_level(
                row["id"], row.get("description") or ""
            )
            if level in {"obliterated", "abliterated"}:
                bits.append(level)
            app._styled(
                ctk.CTkLabel(
                    body, text="  ·  ".join(bit for bit in bits if bit),
                    font=app.font_tiny, anchor="w",
                ),
                "muted",
            ).pack(fill="x")
        if len(picked) > 60:
            app._styled(
                ctk.CTkLabel(
                    listing,
                    text=f"… {len(picked) - 60} modèle(s) de plus : affinez la recherche.",
                    font=app.font_tiny, anchor="w",
                ),
                "muted",
            ).pack(fill="x", padx=8, pady=8)
        app._bind_scrollable_wheel(listing, dialog)
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
            app._styled(
                ctk.CTkLabel(
                    listing, text=error, font=app.font_small, anchor="w",
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
        app.catalog_source = source
        key_slot.pack_forget()
        load()

    def choose(row):
        if app._choose_catalog_model(row):
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
        app.catalog_source = source
        app._save_session()
        try:
            dialog.destroy()
        finally:
            if picker is not None:
                try:
                    if picker.winfo_exists():
                        picker.grab_set()
                except Exception:
                    pass
                app._model_picker = picker

    search.bind("<KeyRelease>", lambda _event: schedule())
    dialog.protocol("WM_DELETE_WINDOW", close)
    dialog.bind("<Escape>", lambda _event: close())
    load()
    search.focus_set()
    app.wait_window(dialog)

def _choose_catalog_model(app, row):
    """Rend actif un modèle du catalogue : moteur « En ligne » + bonne base/URL."""
    row = dict(row or {})
    ident = str(row.get("id") or "").strip()
    source = ollama_fs_access.catalog_key(row.get("source") or "openrouter")
    if not ident:
        return False
    secret = ollama_fs_access.catalog_secret(source)
    if ollama_fs_access.catalog_needs_key(source) and not secret:
        app._set_status(
            f"Clé {ollama_fs_access.catalog_label(source)} requise : collez-la dans le catalogue."
        )
        return False
    ollama_fs_access.configure_online(
        ollama_fs_access.catalog_base(source), secret, ollama_fs_access.catalog_label(source)
    )
    app.catalog_source = source
    app._catalog_choice = ident
    app.provider = "online"
    if getattr(app, "provider_box", None) is not None:
        app.provider_box.set(ollama_fs_access.provider_label("online"))
    app._model_by_provider["online"] = ident
    app.model_var.set(ident)
    app._refresh_model_button()
    if getattr(app, "ollama_state", None) is not None:
        app._paint(app.ollama_state, "sage")
        app.ollama_state.configure(
            text=f"·  prêt · En ligne · {ollama_fs_access.catalog_label(source)}"
        )
    app._set_status(f"Modèle en ligne : {ident}")
    app._save_session()
    if secret:
        app.refresh_models()
    return True