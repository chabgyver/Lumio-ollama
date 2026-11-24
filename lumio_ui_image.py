"""Ecran « Generer une image », sorti de gui_tool.py.

Le dialogue ne connait de l'application que sa palette, ses polices, son statut
et quatre actions (carte de resultat, legende, apercu, enregistrement). L'etat du
dialogue (fenetre, file d'attente, annulation) lui appartient.
"""

import queue
import tkinter as tk
import threading
from tkinter import messagebox

import customtkinter as ctk

import lumio_image
import ollama_fs_access

APP_NAME = "Lumio"


class ImageDialog:
    """Ecran de generation : reglages a gauche, apercu a droite."""

    def __init__(self, app):
        self.app = app
        self.ui = None
        self.cancel = threading.Event()
        self.queue = queue.Queue()
        self.generating = False
        self.settings = {}

    def open(self, prompt="", provider=None, model="", size=None,
                          steps=None, seed=None, negative=""):
        """Écran « Générer une image » : réglages à gauche, aperçu à droite."""
        if self.ui:
            try:
                self.ui["dialog"].deiconify()
                self.ui["dialog"].lift()
                return
            except Exception:
                self.ui = None

        key = lumio_image.image_provider_key(provider or self.app._backend())
        previous = self.settings or {}
        size = size if size is not None else previous.get("size", lumio_image.DEFAULT_SIZE)
        steps = steps if steps is not None else previous.get("steps", lumio_image.DEFAULT_STEPS)
        negative = negative if negative else previous.get("negative", "")

        dialog = ctk.CTkToplevel(self.app)
        dialog.title("Générer une image")
        dialog.geometry("1010x600")
        dialog.minsize(920, 560)
        dialog.configure(fg_color=self.app.C("bg"))
        try:
            dialog.transient(self.app)
            dialog.grab_set()
        except Exception:
            pass
        dialog.protocol("WM_DELETE_WINDOW", self.close)

        page = ctk.CTkFrame(dialog, corner_radius=0)
        self.app._styled(page, "header")
        page.pack(fill="both", expand=True, padx=24, pady=16)

        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x")
        titles = ctk.CTkFrame(head, fg_color="transparent")
        titles.pack(side="left", anchor="w")
        self.app._styled(
            ctk.CTkLabel(titles, text="Générer une image", font=self.app.font_heading, anchor="w"),
            "text",
        ).pack(anchor="w")
        self.app._styled(
            ctk.CTkLabel(
                titles,
                text="Une phrase, un modèle, une image. En local, tout reste sur la machine.",
                font=self.app.font_small, anchor="w",
            ),
            "muted",
        ).pack(anchor="w", pady=(2, 0))
        self.app._button(head, "Fermer", self.close, width=90).pack(side="right", anchor="n")

        body = ctk.CTkFrame(page, fg_color="transparent")
        body.pack(fill="both", expand=True, pady=(14, 0))
        form = ctk.CTkFrame(body, fg_color="transparent")
        form.pack(side="left", fill="both", expand=True, padx=(0, 16))

        engines = ctk.CTkFrame(form, fg_color="transparent")
        engines.pack(fill="x")

        moteur = ctk.CTkFrame(engines, fg_color="transparent")
        moteur.pack(side="left", padx=(0, 14))
        self.app._styled(
            ctk.CTkLabel(moteur, text="MOTEUR", font=self.app.font_tiny, anchor="w"), "muted",
        ).pack(anchor="w")
        provider_box = ctk.CTkComboBox(
            moteur,
            values=[lumio_image.provider_label(item) for item in lumio_image.IMAGE_PROVIDERS],
            width=190, height=36, corner_radius=10, font=self.app.font_small,
            command=self.on_provider,
        )
        provider_box.set(lumio_image.provider_label(key))
        self.app._styled(provider_box, "combo").pack(anchor="w", pady=(4, 0))

        modele = ctk.CTkFrame(engines, fg_color="transparent")
        modele.pack(side="left", padx=(0, 14))
        self.app._styled(
            ctk.CTkLabel(modele, text="MODÈLE", font=self.app.font_tiny, anchor="w"), "muted",
        ).pack(anchor="w")
        model_box = ctk.CTkComboBox(
            modele, values=["Chargement…"], width=250, height=36, corner_radius=10,
            font=self.app.font_small, command=self.on_model,
        )
        model_box.set(model or "Chargement…")
        self.app._styled(model_box, "combo").pack(anchor="w", pady=(4, 0))
        self.app._button(
            engines, "Actualiser", lambda: self.load_models(refresh=True), width=96,
        ).pack(side="left", pady=(20, 0))

        self.app._styled(
            ctk.CTkLabel(form, text="INVITE", font=self.app.font_tiny, anchor="w"), "muted",
        ).pack(anchor="w", pady=(18, 4))
        prompt_box = ctk.CTkTextbox(
            form, height=104, corner_radius=10, font=self.app.font_small, wrap="word", border_width=1,
        )
        self.app._styled(prompt_box, "entry").pack(fill="x")
        prompt_box.insert("1.0", prompt or "")

        options = ctk.CTkFrame(form, fg_color="transparent")
        options.pack(fill="x", pady=(14, 0))

        format_case = ctk.CTkFrame(options, fg_color="transparent")
        format_case.pack(side="left", padx=(0, 14))
        self.app._styled(
            ctk.CTkLabel(format_case, text="FORMAT", font=self.app.font_tiny, anchor="w"), "muted",
        ).pack(anchor="w")
        format_box = ctk.CTkComboBox(
            format_case, values=[item[2] for item in lumio_image.IMAGE_SIZES],
            width=178, height=36, corner_radius=10, font=self.app.font_small,
            command=lambda _value: self.refresh_note(),
        )
        format_box.set(lumio_image.size_label(size))
        self.app._styled(format_box, "combo").pack(anchor="w", pady=(4, 0))

        steps_case = ctk.CTkFrame(options, fg_color="transparent")
        steps_case.pack(side="left", padx=(0, 14))
        self.app._styled(
            ctk.CTkLabel(steps_case, text="ÉTAPES", font=self.app.font_tiny, anchor="w"), "muted",
        ).pack(anchor="w")
        steps_entry = ctk.CTkEntry(
            steps_case, width=70, height=36, corner_radius=10, font=self.app.font_small,
        )
        steps_entry.insert(0, str(lumio_image.normalize_steps(steps)))
        self.app._styled(steps_entry, "entry").pack(anchor="w", pady=(4, 0))

        seed_case = ctk.CTkFrame(options, fg_color="transparent")
        seed_case.pack(side="left")
        self.app._styled(
            ctk.CTkLabel(seed_case, text="GRAINE", font=self.app.font_tiny, anchor="w"), "muted",
        ).pack(anchor="w")
        seed_entry = ctk.CTkEntry(
            seed_case, width=110, height=36, corner_radius=10, font=self.app.font_small,
        )
        seed_entry.insert(0, "aléatoire" if seed is None else str(seed))
        self.app._styled(seed_entry, "entry").pack(anchor="w", pady=(4, 0))

        self.app._styled(
            ctk.CTkLabel(form, text="NÉGATIF (OPTIONNEL)", font=self.app.font_tiny, anchor="w"), "muted",
        ).pack(anchor="w", pady=(14, 4))
        negative_entry = ctk.CTkEntry(
            form, width=420, height=36, corner_radius=10, font=self.app.font_small,
        )
        negative_entry.insert(0, negative or "")
        self.app._styled(negative_entry, "entry").pack(anchor="w")

        join_var = tk.BooleanVar(value=True)
        self.app._styled(
            ctk.CTkCheckBox(
                form, text="Joindre le résultat à la conversation", font=self.app.font_small,
                variable=join_var, checkbox_width=18, checkbox_height=18,
            ),
            "check",
        ).pack(anchor="w", pady=(16, 0))

        side = self.app._styled(ctk.CTkFrame(body, corner_radius=14, width=364), "card")
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        inner = ctk.CTkFrame(side, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16, pady=12)
        self.app._styled(
            ctk.CTkLabel(inner, text="APERÇU", font=self.app.font_tiny, anchor="w"), "muted",
        ).pack(anchor="w")
        preview_box = self.app._styled(
            ctk.CTkFrame(inner, corner_radius=10, width=330, height=212), "well",
        )
        preview_box.pack(pady=(8, 0))
        preview_box.pack_propagate(False)
        preview = self.app._styled(
            ctk.CTkLabel(preview_box, text="l'image apparaîtra ici", font=self.app.font_small),
            "muted",
        )
        preview.place(relx=0.5, rely=0.5, anchor="center")
        caption = self.app._styled(
            ctk.CTkLabel(
                inner,
                text=f"{lumio_image.size_label(size)}  ·  "
                     f"{lumio_image.normalize_steps(steps)} étapes",
                font=self.app.font_tiny, anchor="w", justify="left", wraplength=310,
            ),
            "muted",
        )
        caption.pack(fill="x", pady=(8, 0))

        actions = ctk.CTkFrame(inner, fg_color="transparent")
        actions.pack(fill="x", pady=(10, 0))
        save_button = self.app._button(
            actions, "Enregistrer dans le dossier", self.app._save_image_result, width=200,
        )
        save_button.pack(fill="x")
        actions_row = ctk.CTkFrame(actions, fg_color="transparent")
        actions_row.pack(fill="x", pady=(8, 0))
        attach_button = self.app._button(actions_row, "Joindre", self.app._attach_generated, width=96)
        attach_button.pack(side="left")
        regen_button = self.app._button(actions_row, "Régénérer", self.generate, width=104)
        regen_button.pack(side="left", padx=(8, 0))
        for widget in (save_button, attach_button, regen_button):
            widget.configure(state="disabled")

        self.app._styled(ctk.CTkFrame(page, height=1), "line").pack(fill="x", pady=(14, 10))

        footer = ctk.CTkFrame(page, fg_color="transparent")
        footer.pack(fill="x")
        note = self.app._styled(
            ctk.CTkLabel(footer, text="", font=self.app.font_tiny, anchor="w"), "muted",
        )
        note.pack(side="left")
        generate_button = self.app._button(
            footer, "Générer", self.generate, primary=True, width=130,
        )
        generate_button.pack(side="right")
        self.app._button(footer, "Annuler", self.close, width=110).pack(
            side="right", padx=(0, 8),
        )

        self.ui = {
            "dialog": dialog,
            "provider_key": key,
            "provider": provider_box,
            "model": model_box,
            "prompt": prompt_box,
            "format": format_box,
            "steps": steps_entry,
            "seed": seed_entry,
            "negative": negative_entry,
            "join": join_var,
            "preview": preview,
            "caption": caption,
            "note": note,
            "generate": generate_button,
            "save": save_button,
            "attach": attach_button,
            "regen": regen_button,
        }
        self.app._image_result = None
        self.generating = False
        self.cancel = threading.Event()
        self.queue = queue.Queue()
        self.settings = {
            "size": lumio_image.normalize_size(size),
            "steps": lumio_image.normalize_steps(steps),
            "negative": negative or "",
        }
        prompt_box.focus_set()
        self.load_models()
        self.refresh_note()
        self.app.after(120, self.poll)

    def close(self):
        cancel = self.cancel
        if cancel is not None:
            cancel.set()
        state = self.ui
        self.ui = None
        self.generating = False
        if not state:
            return
        try:
            state["dialog"].grab_release()
        except Exception:
            pass
        try:
            state["dialog"].destroy()
        except Exception:
            pass

    def widgets(self):
        return self.ui or {}

    def on_provider(self, choice):
        state = self.widgets()
        if not state:
            return
        key = lumio_image.image_provider_key(choice)
        if key == state.get("provider_key"):
            return
        state["provider_key"] = key
        state["model"].configure(values=["Chargement…"])
        state["model"].set("Chargement…")
        self.refresh_note()
        self.load_models()

    def on_model(self, choice):
        state = self.widgets()
        if not state:
            return
        state["model"].set(choice)
        self.refresh_note()

    def refresh_note(self):
        state = self.widgets()
        if not state:
            return
        size = lumio_image.normalize_size(state["format"].get())
        steps = lumio_image.normalize_steps(state["steps"].get())
        if state.get("provider_key") == "horde":
            kudos = lumio_image.horde_image_kudos(state["model"].get(), size, steps)
            anonyme = ollama_fs_access.horde_key() == ollama_fs_access.HORDE_ANON_KEY
            texte = f"AI Horde · environ {kudos} kudos"
            if anonyme:
                texte += " · clé anonyme : file longue, une clé gratuite (aihorde.net) passe devant"
            else:
                texte += " · file d'attente"
        else:
            texte = "Modèle local · gratuit · macOS seulement pour l'instant"
        state["note"].configure(text=texte)

    def load_models(self, refresh=False):
        state = self.widgets()
        if not state:
            return
        provider_key = state.get("provider_key", "ollama")
        queue_in = self.queue
        if queue_in is None:
            return

        def worker():
            try:
                names = lumio_image.image_models(provider_key)
                queue_in.put(("models", names))
            except Exception as error:
                queue_in.put(("models", [], str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        if not self.widgets():
            return
        queue_in = self.queue
        if queue_in is None:
            return
        while True:
            try:
                kind, payload, *rest = queue_in.get_nowait()
            except queue.Empty:
                break
            if kind == "models":
                self.apply_models(payload, rest[0] if rest else "")
            elif kind == "progress":
                state = self.widgets()
                if state:
                    state["caption"].configure(text=str(payload))
            elif kind == "result":
                self.show(payload)
            elif kind == "error":
                self.failed(str(payload))
        self.app.after(120, self.poll)

    def apply_models(self, names, error=""):
        state = self.widgets()
        if not state:
            return
        if error:
            self.app._set_status(error)
        placeholders = {"Chargement…", "Aucun modèle image", ""}
        current = state["model"].get().strip()
        values = [str(item) for item in (names or [])]
        if current not in placeholders and current not in values:
            # Un modèle choisi à la main ne doit pas être effacé par un
            # rafraîchissement qui ne le voit pas (moteur absent, filtre).
            values = [current] + values
        if not values:
            values = ["Aucun modèle image"]
        state["model"].configure(values=values)
        state["model"].set(current if current in values else values[0])
        if not names and not error and state.get("provider_key") == "ollama":
            state["caption"].configure(
                text="Ollama ne propose pas de modèle d'images ici : la génération "
                     "locale n'existe que sur macOS. Choisissez le moteur AI Horde.",
            )
        self.refresh_note()

    def generate(self):
        state = self.widgets()
        if not state or self.generating:
            return
        prompt = state["prompt"].get("1.0", "end-1c").strip()
        model = state["model"].get().strip()
        if not prompt:
            messagebox.showinfo(APP_NAME, "Écrivez une phrase pour décrire l'image.")
            return
        if not model or model in {"Chargement…", "Aucun modèle image"}:
            messagebox.showinfo(
                APP_NAME,
                "Aucun modèle image disponible. Cliquez sur « Actualiser » après avoir "
                "installé un modèle (par exemple : ollama pull x/z-image-turbo).",
            )
            return
        provider_key = state.get("provider_key", "ollama")
        size = lumio_image.normalize_size(state["format"].get())
        steps = lumio_image.normalize_steps(state["steps"].get())
        seed = lumio_image.normalize_seed(state["seed"].get())
        negative = state["negative"].get().strip()
        self.settings = {"size": size, "steps": steps, "negative": negative}
        self.generating = True
        cancel = threading.Event()
        self.cancel = cancel
        queue_in = self.queue
        state["generate"].configure(state="disabled", text="Génération…")
        state["caption"].configure(text="Envoi de la demande…")
        self.app._set_status(f"Génération d'image avec {model}…")

        def progress(text):
            queue_in.put(("progress", text))

        def worker():
            try:
                result = lumio_image.generate(
                    provider_key, model, prompt, size, steps, seed, negative,
                    cancel_event=cancel, on_progress=progress,
                )
                queue_in.put(("result", result))
            except Exception as error:
                queue_in.put(("error", str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def show(self, result):
        state = self.widgets()
        if not state:
            return
        self.generating = False
        state["generate"].configure(state="normal", text="Générer")
        if not result:
            state["caption"].configure(text="Génération annulée.")
            self.app._set_status("Génération annulée")
            return
        try:
            _picture, ctk_image = self.app._image_picture(result["data"])
        except Exception as error:
            state["caption"].configure(text=f"Image illisible : {error}")
            return
        self.app._image_result = result
        self.app._image_ctk = ctk_image
        state["preview"].configure(image=ctk_image, text="")
        state["caption"].configure(text=self.app._image_caption(result))
        for name in ("save", "attach", "regen"):
            state[name].configure(state="normal")
        self.app._set_status(f"Image prête ({result['width']}×{result['height']})")
        if state["join"].get():
            self.app._add_image_card(result)

    def failed(self, message):
        state = self.widgets()
        if not state:
            return
        self.generating = False
        state["generate"].configure(state="normal", text="Générer")
        state["caption"].configure(text=message)
        self.app._set_status("Génération impossible")
