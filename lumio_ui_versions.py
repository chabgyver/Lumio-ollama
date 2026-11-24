"""Fenêtre « Versions des fichiers », sortie de gui_tool.py.

Elle ne connaît de l'application que ce qu'elle utilise : la palette (C, _styled,
_button), les polices, le dossier courant et trois actions (recharger la liste,
poser une bulle, écrire le statut). Le découpage se poursuivra module par
module, un dialogue à la fois.
"""

from tkinter import messagebox

import customtkinter as ctk

import lumio_snapshots

APP_NAME = "Lumio"


def show(app):
    """Ouvre la liste des versions archivées pour le dossier courant."""
    rows = lumio_snapshots.entries(folder=app.target_folder, limit=60)

    dialog = ctk.CTkToplevel(app)
    dialog.title("Versions des fichiers")
    dialog.geometry("720x520")
    dialog.minsize(520, 360)
    dialog.configure(fg_color=app.C("bg"))
    dialog.transient(app)
    try:
        dialog.grab_set()
    except Exception:
        pass

    ctk.CTkLabel(
        dialog, text="Versions des fichiers", text_color=app.C("text"),
        font=app.font_heading, anchor="w",
    ).pack(fill="x", padx=16, pady=(14, 2))
    app._styled(
        ctk.CTkLabel(
            dialog, text="Chaque écriture de Lumio archive la version précédente.",
            font=app.font_tiny, anchor="w",
        ),
        "muted",
    ).pack(fill="x", padx=16, pady=(0, 8))

    listing = app._styled(ctk.CTkScrollableFrame(dialog, corner_radius=10), "well")
    listing.pack(fill="both", expand=True, padx=14, pady=(0, 8))

    if not rows:
        app._styled(
            ctk.CTkLabel(
                listing, text="Aucune version enregistrée pour ce dossier.",
                font=app.font_small,
            ),
            "muted",
        ).pack(pady=12)

    def make_restore(entry):
        def restore():
            try:
                target = lumio_snapshots.restore(entry, folder=app.target_folder)
            except Exception as error:
                messagebox.showerror(APP_NAME, f"Restauration impossible : {error}")
                return
            app.reload_files(confirm=False)
            app._add_bubble("notice", f"Version restaurée : {target.name} ({entry.get('at')})")
            app._set_status("Version restaurée")
            dialog.destroy()

        return restore

    for entry in rows:
        card = app._styled(ctk.CTkFrame(listing, corner_radius=8), "raised")
        card.pack(fill="x", padx=4, pady=2)
        app._button(card, "Restaurer", make_restore(entry), width=90).pack(
            side="right", padx=8, pady=6,
        )
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(side="left", fill="x", expand=True, padx=8, pady=6)
        app._styled(
            ctk.CTkLabel(body, text=str(entry.get("relative")), font=app.font_small, anchor="w"),
            "text",
        ).pack(fill="x")
        app._styled(
            ctk.CTkLabel(
                body,
                text=f"{entry.get('at')} · {int(entry.get('size') or 0)} octets · "
                     f"{entry.get('reason')}",
                font=app.font_tiny, anchor="w",
            ),
            "muted",
        ).pack(fill="x")
    app._bind_scrollable_wheel(listing, dialog)
    app._button(dialog, "Fermer", lambda: dialog.destroy()).pack(pady=(0, 12))
