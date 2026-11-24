"""Panneau « Coût et tokens » (sept derniers jours), sorti de gui_tool.py.

Comme lumio_ui_versions, ce module ne reçoit que l'application et n'utilise
qu'une poignée de ses services : palette, polices, statut.
"""

import os

import customtkinter as ctk

import lumio_cost


def show(app):
    """Affiche le journal de coût des sept derniers jours."""
    lines = lumio_cost.summary_lines(days=7)

    dialog = ctk.CTkToplevel(app)
    dialog.title("Coût et tokens")
    dialog.geometry("620x480")
    dialog.minsize(480, 320)
    dialog.configure(fg_color=app.C("bg"))
    dialog.transient(app)
    try:
        dialog.grab_set()
    except Exception:
        pass

    ctk.CTkLabel(
        dialog, text="Coût et tokens", text_color=app.C("text"),
        font=app.font_heading, anchor="w",
    ).pack(fill="x", padx=16, pady=(14, 4))
    app._styled(
        ctk.CTkLabel(
            dialog,
            text="Tarifs du catalogue, par million de tokens. "
                 "Les modèles locaux ne coûtent rien.",
            font=app.font_tiny, anchor="w", justify="left", wraplength=560,
        ),
        "muted",
    ).pack(fill="x", padx=16, pady=(0, 8))

    box = app._styled(
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
        except Exception as error:
            app._set_status(f"Dossier introuvable : {error}")

    app._button(bar, "Ouvrir le dossier", open_ledger, width=150).pack(side="left")
    app._button(bar, "Fermer", lambda: dialog.destroy(), primary=True).pack(side="right")
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.protocol("WM_DELETE_WINDOW", lambda: dialog.destroy())
    app.wait_window(dialog)
