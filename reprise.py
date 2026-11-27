"""Reprise de la file de téléchargement après un redémarrage du bot.

La file (tâche en cours + tâches en attente) est écrite dans un petit fichier JSON à chaque changement et relue au
démarrage. Ce module ne dépend ni de discord.py ni de yt-dlp : il décrit une tâche sous forme de données et la
reconstruit, ce qui permet de le tester seul.

Ce qui est repris : les liens (playlists YouTube), et les téléchargements de l'API / de Discord faits avec les
fonctions de `top.py` (chanson, album, artiste, top). Les musiques déjà téléchargées sont sautées (registre de
`top.py`, archive de yt-dlp) : une reprise continue là où la file s'était arrêtée. Une tâche qui était EN COURS
au moment de l'arrêt est reprise au plus REPRISES_MAX fois : si c'est elle qui fait tomber le bot, on ne boucle pas.
"""
import json
import os
from functools import partial

REPRISES_MAX = 3
FONCTIONS = ("telecharger_top", "telecharger_chanson", "telecharger_album", "telecharger_artiste")


def _serialisable(valeur):
    try:
        json.dumps(valeur)
        return True
    except (TypeError, ValueError):
        return False


def decrire(action):
    """Description JSON d'une action de tâche, ou None si elle ne peut pas être reprise.
    `False` ne signifie pas « rien » : une tâche sans action est un lien (dl.telecharger), décrit par {}."""
    if action is None:
        return {}
    if not isinstance(action, partial) or action.keywords:
        return None
    nom = getattr(action.func, "__name__", "")
    if nom not in FONCTIONS or not _serialisable(list(action.args)):
        return None
    return {"fonction": nom, "args": list(action.args)}


def reconstruire(description, moteur):
    """L'action décrite par `description` (None pour un lien), ou False si elle est inconnue."""
    if not description:
        return None
    nom = description.get("fonction")
    if nom not in FONCTIONS or not hasattr(moteur, nom):
        return False
    return partial(getattr(moteur, nom), *description.get("args", []))


def entrees(taches, en_cours=None):
    """Les entrées à écrire : [{url, auteur_id, auto, nom, action, reprises, en_cours}, ...]."""
    sortie = []
    for t in taches:
        description = decrire(t.action)
        if description is None:
            continue   # non reprenable (ex. /ranger : se relance en une commande)
        sortie.append({
            "url": t.url, "auteur_id": t.auteur_id, "auto": bool(t.auto), "nom": t.nom or "",
            "action": description, "reprises": getattr(t, "reprises", 0), "en_cours": t is en_cours,
        })
    return sortie


def ecrire(chemin, liste):
    """Écriture atomique : un arrêt brutal ne laisse jamais un fichier à moitié écrit."""
    if not liste:
        try:
            os.remove(chemin)
        except FileNotFoundError:
            pass
        return
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False)
    os.replace(tmp, chemin)


def lire(chemin):
    """Entrées valides du fichier (liste vide s'il manque ou s'il est illisible)."""
    try:
        with open(chemin, encoding="utf-8") as f:
            brut = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(brut, list):
        return []
    valides = []
    for e in brut:
        if isinstance(e, dict) and isinstance(e.get("url"), str) and e["url"] and isinstance(e.get("action"), dict):
            valides.append(e)
    return valides


def a_reprendre(liste, log=print):
    """Filtre les entrées à reprendre et met leur compteur à jour. Renvoie [(entrée, reprises), ...]."""
    gardees = []
    for e in liste:
        reprises = int(e.get("reprises") or 0)
        if e.get("en_cours"):
            if reprises >= REPRISES_MAX:
                log(f"Reprise abandonnée après {reprises} tentative(s) interrompue(s) : {e.get('nom') or e['url']}")
                continue
            reprises += 1   # elle tournait quand le bot s'est arrêté : une tentative de plus
        gardees.append((e, reprises))
    return gardees
