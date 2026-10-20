# Contribuer à Lumio-ollama

Merci de l'intérêt. Le projet est petit et volontairement sans dépendances
lourdes : interface en CustomTkinter, moteur en bibliothèque standard.

## Mise en route

```bat
python -m pip install -r requirements.txt
python tests\run_tests.py        doit afficher : Ran 153 tests ... OK
pythonw gui_tool.py
```

Python 3.11 ou plus récent, sous Windows (le chiffrement de la clé API utilise
le DPAPI de Windows).

## Règles du dépôt

- **Logique pure dans des modules dédiés**, interface dans `gui_tool.py` :
  tout ce qui se teste sans écran doit sortir de l'interface
  (`lumio_search`, `lumio_cost`, `lumio_rank`, `lumio_tree`, `lumio_verify`,
  `lumio_router`, `lumio_tools`, `lumio_snapshots`, `lumio_session`).
- **Toute contribution vient avec ses tests** dans `tests/`. Une correction de
  bug sans test de non-régression sera discutée avant fusion.
- **Pas de nouvelle dépendance** sans discussion préalable : les bibliothèques
  standard suffisent pour le moteur.
- **Pas de données personnelles** dans le dépôt : `session.json`, `usage.jsonl`
  et les instantanés sont ignorés par `.gitignore`.
- **Une PR = un sujet.** Préférez plusieurs petites PR à une grosse.
- Style : 4 espaces, noms en français pour les messages et l'interface,
  pas de reformatage massif (le diff doit rester lisible).

## Sécurité

Lumio écrit des fichiers dans le dossier choisi et exécute des commandes
figurant sur une liste blanche. Toute évolution touchant ces mécanismes doit
conserver les garde-fous : refus des enchaînements shell, refus des motifs
destructeurs, instantané avant écriture, application automatique désactivée par
défaut.

## Signaler un problème

Décrivez : version de Python, de Windows, de Lumio, le modèle utilisé (local ou
en ligne), et joignez le contenu de `lumio_error.log` s'il existe.
