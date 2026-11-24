import os, pathlib, shutil, tempfile
import tkinter
tmp = pathlib.Path(tempfile.mkdtemp(prefix="lumio_repl_"))
appdata = tmp / "appdata"; appdata.mkdir()
reel = pathlib.Path(os.environ["APPDATA"]) / "Lumio" / "session.json"
if reel.is_file():
    shutil.copyfile(reel, appdata / "session.json")   # copie : la vraie session n'est pas touchée
os.environ["APPDATA"] = str(appdata)
import gui_tool
gui_tool.SESSION_PATH = appdata / "session.json"
gui_tool.LEGACY_SESSION_PATH = tmp / "legacy.json"
app = gui_tool.FileSystemWrapper()
app.withdraw()
app.update()
box = app.provider_box
print("valeur affichée :", box.get())
print("values configurées :", list(box.cget("values")))
box._open_dropdown_menu()
app.update()
menu = getattr(box, "_dropdown_menu", None)
if menu is not None:
    labels = [w.cget("text") for w in menu.winfo_children() if hasattr(w, "cget")]
    print("entrées du menu déroulant :", [l for l in labels if l])
else:
    print("pas de menu déroulant")
app.destroy()
