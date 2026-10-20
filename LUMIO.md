# Lumio — compagnon d'Ollama

Assistant de bureau pour travailler sur **un dossier** avec un modèle local (Ollama),
un modèle en ligne (OpenRouter, Groq, DeepSeek…) ou PrivateGPT.

Double-clique `Lumio.lnk` (ou `Lumio.bat`). Le lanceur démarre Ollama, PrivateGPT
s'il est présent, puis l'interface. Une seule instance à la fois.

## Ce que Lumio sait faire

| Fonction | Où |
|---|---|
| Discuter d'un dossier (fichiers cochés = contexte) | fenêtre principale |
| Choisir / installer un modèle local | bouton du modèle → *Modèles* |
| Chercher un modèle en ligne (470 modèles, tarifs, gratuits) | *Modèles* → **Catalogue en ligne** |
| Trier ces modèles (recommandé, puissance, prix, contexte, date) | Catalogue → **Trier par** |
| Vérifier le code produit (boucle agent) | barre d'actions → **Vérifier** |
| Revenir à une version précédente d'un fichier | *Palette* → **Versions des fichiers** |
| Voir les tokens et le coût | en-tête + *Palette* → **Coût et tokens** |
| Tout piloter au clavier | `Ctrl+K` (palette), `Ctrl+Maj+F` (recherche dans les conversations) |

## Mode agent (les trois réglages qui changent tout)

`Ctrl+K` → **Réglages** :

1. **Vérifier automatiquement après chaque écriture** — lance la commande de
   vérification du dossier et réinjecte sa sortie au modèle.
   Commande : détectée automatiquement (`tests/run_tests.py`, pytest, `npm test`,
   sinon `py_compile`), ou imposée dans le champ.
2. **Tentatives de correction** (0-5) — nombre de reprises automatiques quand la
   vérification échoue. 2 est un bon compromis.
3. **Appliquer les fichiers sans clic** — écrit les fichiers proposés sans
   confirmation. Dangereux : à activer avec les limites ci-dessous (fichiers /
   octets), et sachant que chaque écriture est archivée.

**Budget** : *plafond $ par échange* (routage) et *budget $ par question*
(boucle agent). Au-delà, plus aucune tentative automatique n'est lancée.

**Outils du modèle** : autorise ` ```rechercher: motif``` ` et
` ```lire: chemin``` ` — le modèle consulte le dossier lui-même au lieu de
dépendre de ta sélection.

**Commandes autorisées** : liste blanche, une commande par ligne. Les
enchaînements (`&&`, `|`, `>`, `;`) et les motifs destructeurs sont toujours
refusés.


## Classement du catalogue

Le sélecteur **Trier par** propose : *Recommandé* (rapport puissance / prix,
gratuit favorisé), *Puissance estimée*, *Prix croissant*, *Contexte le plus
large*, *Plus récents*, *Nom*. La taille (27B, 8×22B…) est lue dans le nom du
modèle quand elle y figure.

Aucune API publique ne fournit de benchmark : la « puissance » est une
**estimation** (taille en paramètres, sinon prix d'entrée comme proxy). C'est un
ordre de grandeur pour t'aider à choisir, pas une mesure.
## Arborescence du dossier

Le panneau **FICHIERS** affiche le dossier en arborescence : dossiers dépliables
(`▾` / `▸`) avec leur nombre de fichiers, fichiers indentés avec leur case à
cocher, leur épingle et leur taille. Deux boutons dans l'en-tête replient ou
déplient tout ; l'état des dossiers repliés est mémorisé.

Seuls les fichiers que Lumio sait lire apparaissent (filtre d'extensions en haut
du panneau) : un dossier d'images ou de polices n'apparaît donc pas. Une
recherche active déplie automatiquement les branches concernées.

## Commandes locales

- **Vérifier** : exécute la commande de vérification, affiche code retour,
  sortie et erreurs dans la conversation.
- **Versions** : instantanés pris avant chaque écriture (20 max par fichier),
  restauration en un clic.

## Ce que Lumio ne fait pas

- Il ne modifie jamais un fichier hors du dossier choisi.
- Il ne lance que les commandes de la liste blanche.
- Il ne continue pas à boucler indéfiniment : les reprises sont bornées.

## Fichiers et dépannage

| Chemin | Contenu |
|---|---|
| `%APPDATA%\Lumio\session.json` | session, conversations, réglages (clé API chiffrée DPAPI) |
| `%APPDATA%\Lumio\usage.jsonl` | une ligne par échange (tokens, coût) |
| `%APPDATA%\Lumio\snapshots\` | versions archivées avant écriture |
| `lumio_error.log` | dernière erreur (à côté de `gui_tool.py`) |

Lumio ne s'ouvre pas ? Regarde `lumio_error.log` : toute erreur y est écrite et
affichée dans une boîte de dialogue.

## Tests

```
python tests\run_tests.py
```

118 tests, aucune dépendance : moteur, session/chiffrement, recherche, coûts,
instantanés, outils, routage, vérification.

## Modules

| Module | Rôle |
|---|---|
| `gui_tool.py` | interface (Tkinter / CustomTkinter) |
| `ollama_fs_access.py` | moteur : fichiers, contexte, flux Ollama / OpenAI / PrivateGPT, catalogue |
| `lumio_session.py` | session, chiffrement DPAPI, migration |
| `lumio_search.py` | recherche dans les conversations |
| `lumio_cost.py` | tokens, coût, journal, budgets |
| `lumio_snapshots.py` | versions de fichiers |
| `lumio_tools.py` | blocs `rechercher:` / `lire:` |
| `lumio_verify.py` | commandes de vérification, liste blanche |
| `lumio_router.py` | choix local / distant |

Les sauvegardes de code portent le suffixe `.bak-*` ou `.pro-*` : supprimables
une fois que tout te convient.
