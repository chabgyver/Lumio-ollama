import json, os, pathlib, sys, tempfile, tkinter, traceback
tmp = pathlib.Path(tempfile.mkdtemp(prefix="lumio_opts_"))
os.environ["APPDATA"] = str(tmp / "appdata")
import customtkinter as ctk
import gui_tool
gui_tool.SESSION_PATH = tmp / "appdata" / "session.json"
gui_tool.LEGACY_SESSION_PATH = tmp / "legacy.json"

def log(*a): print(*a, flush=True)

try:
    app = gui_tool.FileSystemWrapper()
    app.withdraw(); app.update()
    log("avant : think_off =", app.settings.get("think_off"), "| cap =", app.settings.get("context_cap"),
        "| _think_pref =", app._think_pref(), "| _context_cap =", app._context_cap())

    app.open_settings(); app.update()
    dlg = [w for w in app.winfo_children() if isinstance(w, tkinter.Toplevel) and w.title() == "Réglages"][0]

    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from walk(child)

    cases = [w for w in walk(dlg) if isinstance(w, ctk.CTkCheckBox)]
    log("cases à cocher :", [c.cget("text")[:52] for c in cases])
    cible = next(c for c in cases if "réflexion" in str(c.cget("text")).lower())
    cible.select(); app.update()

    entrees = [w for w in walk(dlg) if isinstance(w, ctk.CTkEntry)]
    log("entrées :", [(e.get() or "")[:12] for e in entrees])
    cap_entry = next(e for e in entrees if (e.get() or "").strip() in {"0", ""})
    cap_entry.delete(0, "end"); cap_entry.insert(0, "15000"); app.update()

    boutons = [w for w in walk(dlg) if isinstance(w, ctk.CTkButton)]
    enregistrer = next(b for b in boutons if str(b.cget("text")) == "Enregistrer")
    if hasattr(enregistrer, "_command"):
        enregistrer._command()
    else:
        enregistrer.command()
    app.update()

    log("après : think_off =", app.settings.get("think_off"), "| cap =", app.settings.get("context_cap"))
    log("  _think_pref =", app._think_pref(), "| _context_cap =", app._context_cap())
    app._save_session(immediate=True)
    data = json.loads((tmp / "appdata" / "session.json").read_text(encoding="utf-8"))
    log("session enregistrée :", data.get("settings", {}).get("think_off"), "/", data.get("settings", {}).get("context_cap"))
    app.destroy()
    log("OK")
except Exception:
    traceback.print_exc(); sys.exit(1)
