"""API HTTP du bot : permet à une application (Sillon) de chercher et de lancer des
téléchargements sans passer par Discord. Elle utilise exactement la même file d'attente
que les commandes Discord (`/chanson`, `/artiste`, `/add`) : mêmes règles, même rangement.

Désactivée tant que API_TOKEN est vide dans config/bot.conf. Toutes les routes exigent
l'en-tête `Authorization: Bearer <API_TOKEN>`.

Ce module ne dépend ni de discord.py ni de yt-dlp : le bot lui passe son moteur (`top`)
et sa fonction de journal, ce qui permet de le tester seul.
"""
import asyncio
import hmac
import json
import re
import ssl
import time
from functools import partial

from aiohttp import web

VERSION_API = 1
TOKEN_MIN = 24                    # un jeton plus court est refusé : l'API reste éteinte
NB_CHANSONS = 8
NB_ARTISTES = 5
NB_ALBUMS = 6
RECHERCHE_MAX = 100
REGEX_LIEN = re.compile(r"^https?://\S+$")
REGEX_ID = re.compile(r"^\d{1,12}$")
PAUSE_ECHEC = 1.0                 # secondes d'attente après un jeton refusé (freine les essais en série)


def _json(donnees, statut=200):
    return web.json_response(donnees, status=statut, dumps=lambda d: json.dumps(d, ensure_ascii=False))


def _erreur(message, statut):
    return _json({"erreur": message}, statut)


async def _corps(requete):
    try:
        donnees = await requete.json()
    except web.HTTPException:
        raise   # corps trop gros (413) : on garde le bon code
    except Exception:  # noqa: BLE001 — corps absent ou JSON invalide
        return None
    return donnees if isinstance(donnees, dict) else None


# ---------------------------------------------------------------------------
# Mise en forme (clés françaises, uniquement ce dont l'application a besoin)
# ---------------------------------------------------------------------------
def vue_chanson(c):
    album = c.get("album") or {}
    artiste = c.get("artist") or {}
    return {
        "id": str(c["id"]), "titre": c.get("title", ""), "artiste": artiste.get("name", ""),
        "album": album.get("title", ""), "pochette": album.get("cover_medium"),
        "duree": c.get("duration"), "fans": c.get("fans_artiste"), "apercu": c.get("preview"),
        "lien": c.get("link") or f"https://www.deezer.com/track/{c['id']}",
    }


def vue_artiste(a):
    return {
        "id": str(a["id"]), "nom": a.get("name", ""), "vrai_nom": a.get("vrai_nom"),
        "groupe": a.get("type") == "Group", "albums": a.get("nb_album"),
        "titres": a.get("nb_titres"), "fans": a.get("nb_fan"), "photo": a.get("picture_medium"),
    }


def vue_album(a):
    artiste = a.get("artist") or {}
    return {
        "id": str(a["id"]), "titre": a.get("title", ""), "artiste": artiste.get("name", ""),
        "pochette": a.get("cover_medium"), "titres": a.get("nb_tracks"), "type": a.get("record_type"),
        "annee": (a.get("release_date") or "")[:4] or None,
        "lien": a.get("link") or f"https://www.deezer.com/album/{a['id']}",
    }


def vue_tache(bot, tache, position=None):
    p = tache.suivi.photo()
    return {
        "numero": tache.numero, "nom": bot.nom_tache(tache), "etat": p["etat"],
        "en_cours": tache is bot.en_cours, "position": position,
        "pourcentage": round(p["pourcentage"], 1), "total": p["total"], "faites": p["faites"],
        "morceau": p["video_titre"], "vitesse": p["vitesse"], "eta": p["eta"],
        "erreurs": p["erreurs"], "automatique": bool(tache.auto),
    }


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
def creer_app(bot, token, moteur, log=print):
    """`bot` : le bot (file d'attente), `moteur` : le module `top` (recherches Deezer, téléchargements)."""
    token_octets = token.encode()

    @web.middleware
    async def authentification(request, handler):   # noms imposés par aiohttp (appel par mot-clé)
        recu = request.headers.get("Authorization", "")
        if not recu.startswith("Bearer ") or not hmac.compare_digest(recu[7:].encode(), token_octets):
            await asyncio.sleep(PAUSE_ECHEC)
            return _erreur("Jeton refusé.", 401)
        return await handler(request)

    async def sante(requete):
        return _json({"ok": True, "version": VERSION_API, "pause": bool(bot.pause_jusqua)})

    async def chercher_chansons(requete):
        q = requete.query.get("q", "").strip()
        if not 2 <= len(q) <= RECHERCHE_MAX:
            return _erreur("Recherche trop courte ou trop longue.", 400)
        try:
            trouvees = await asyncio.to_thread(moteur.chercher_chansons, q, NB_CHANSONS)
        except Exception as e:  # noqa: BLE001
            return _erreur(f"Recherche impossible sur Deezer : {e}", 502)
        return _json({"resultats": [vue_chanson(c) for c in trouvees]})

    async def chercher_artistes(requete):
        q = requete.query.get("q", "").strip()
        if not 2 <= len(q) <= RECHERCHE_MAX:
            return _erreur("Recherche trop courte ou trop longue.", 400)
        try:
            trouves = await asyncio.to_thread(moteur.chercher_artistes, q, NB_ARTISTES)
        except Exception as e:  # noqa: BLE001
            return _erreur(f"Recherche impossible sur Deezer : {e}", 502)
        return _json({"resultats": [vue_artiste(a) for a in trouves]})

    async def chercher_albums(requete):
        q = requete.query.get("q", "").strip()
        if not 2 <= len(q) <= RECHERCHE_MAX:
            return _erreur("Recherche trop courte ou trop longue.", 400)
        try:
            trouves = await asyncio.to_thread(moteur.chercher_albums, q, NB_ALBUMS)
        except Exception as e:  # noqa: BLE001
            return _erreur(f"Recherche impossible sur Deezer : {e}", 502)
        return _json({"resultats": [vue_album(a) for a in trouves]})

    async def _lancer(cle, nom, action):
        position, tache = await bot.ajouter_tache(cle, 0, None, action=action)
        if position is None:
            return _erreur("Déjà en cours ou dans la file.", 409)
        tache.nom = nom
        return _json({"numero": tache.numero, "nom": nom, "position": position}, 202)

    async def _fiche_deezer(chemin, id_):
        """Fiche Deezer d'un titre ou d'un artiste, ou None si l'identifiant n'existe pas."""
        fiche = await asyncio.to_thread(moteur.api, f"{chemin}/{id_}")
        return None if not isinstance(fiche, dict) or "error" in fiche else fiche

    async def telecharger_chanson(requete):
        corps = await _corps(requete)
        id_ = str((corps or {}).get("id", ""))
        if not REGEX_ID.match(id_):
            return _erreur("Identifiant de chanson invalide.", 400)
        try:
            c = await _fiche_deezer("track", id_)
        except Exception as e:  # noqa: BLE001
            return _erreur(f"Deezer injoignable : {e}", 502)
        if c is None:
            return _erreur("Chanson introuvable sur Deezer.", 404)
        nom = f"{c['artist']['name']} - {c['title']}"
        return await _lancer(f"chanson:{c['id']}", nom, partial(moteur.telecharger_chanson, c))

    async def telecharger_album(requete):
        corps = await _corps(requete)
        id_ = str((corps or {}).get("id", ""))
        if not REGEX_ID.match(id_):
            return _erreur("Identifiant d'album invalide.", 400)
        try:
            a = await _fiche_deezer("album", id_)
        except Exception as e:  # noqa: BLE001
            return _erreur(f"Deezer injoignable : {e}", 502)
        if a is None:
            return _erreur("Album introuvable sur Deezer.", 404)
        artiste = (a.get("artist") or {}).get("name", "")
        nom = f"{artiste} - {a['title']}" if artiste else a["title"]
        return await _lancer(f"album:{a['id']}", nom, partial(moteur.telecharger_album, a))

    async def telecharger_artiste(requete):
        corps = await _corps(requete) or {}
        id_ = str(corps.get("id", ""))
        nombre = corps.get("nombre", 0)
        if not REGEX_ID.match(id_):
            return _erreur("Identifiant d'artiste invalide.", 400)
        if isinstance(nombre, bool) or not isinstance(nombre, int) or not 0 <= nombre <= 200:
            return _erreur("Le nombre de titres doit être entre 0 (tout) et 200.", 400)
        try:
            a = await _fiche_deezer("artist", id_)
        except Exception as e:  # noqa: BLE001
            return _erreur(f"Deezer injoignable : {e}", 502)
        if a is None:
            return _erreur("Artiste introuvable sur Deezer.", 404)
        nom = a["name"] + (f" (top {nombre})" if nombre else "")
        return await _lancer(f"artiste:{a['id']}:{nombre}", nom,
                             partial(moteur.telecharger_artiste, a, nombre))

    async def telecharger_lien(requete):
        corps = await _corps(requete)
        url = str((corps or {}).get("url", "")).strip().strip("<>")
        if not REGEX_LIEN.match(url) or len(url) > 500:
            return _erreur("Ce n'est pas un lien valide.", 400)
        position, tache = await bot.ajouter_tache(url, 0, None)
        if position is None:
            return _erreur("Déjà en cours ou dans la file.", 409)
        return _json({"numero": tache.numero, "nom": url, "position": position}, 202)

    async def file(requete):
        taches = ([bot.en_cours] if bot.en_cours else []) + list(bot.attente)
        return _json({
            "pause_jusqua": int(bot.pause_jusqua) if bot.pause_jusqua else None,
            "taches": [vue_tache(bot, t, None if t is bot.en_cours else i)
                       for i, t in enumerate(taches)],
            "terminees": list(bot.terminees),
            "maintenant": int(time.time()),
        })

    async def annuler(requete):
        try:
            numero = int(requete.match_info["numero"])
        except ValueError:
            return _erreur("Numéro invalide.", 400)
        tache = next((t for t in bot.taches() if t.numero == numero), None)
        if tache is None:
            return _erreur("Ce téléchargement est introuvable ou déjà terminé.", 404)
        return _json({"message": await bot.annuler_tache(tache)})

    app = web.Application(middlewares=[authentification], client_max_size=16 * 1024)
    app.add_routes([
        web.get("/api/sante", sante),
        web.get("/api/chansons", chercher_chansons),
        web.post("/api/chansons", telecharger_chanson),
        web.get("/api/albums", chercher_albums),
        web.post("/api/albums", telecharger_album),
        web.get("/api/artistes", chercher_artistes),
        web.post("/api/artistes", telecharger_artiste),
        web.post("/api/liens", telecharger_lien),
        web.get("/api/file", file),
        web.delete("/api/taches/{numero}", annuler),
    ])
    return app


async def demarrer(bot, conf, moteur, log=print):
    """Lance le serveur si API_TOKEN est renseigné. Renvoie le runner, ou None si l'API reste éteinte."""
    token = conf.get("API_TOKEN", "")
    if not token:
        return None   # fonction facultative : silence
    if len(token) < TOKEN_MIN:
        log(f"API HTTP désactivée : API_TOKEN trop court ({len(token)} caractères, {TOKEN_MIN} minimum). "
            "Génère-en un avec : python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"")
        return None
    port = int(conf["API_PORT"]) if conf.get("API_PORT", "").isdigit() else 8787
    contexte = None
    cert, cle = conf.get("API_CERT", ""), conf.get("API_CLE", "")
    if cert and cle:
        contexte = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexte.minimum_version = ssl.TLSVersion.TLSv1_2
        contexte.load_cert_chain(cert, cle)
    runner = web.AppRunner(creer_app(bot, token, moteur, log))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port, ssl_context=contexte).start()
    log(f"API HTTP{'S' if contexte else ''} à l'écoute sur le port {port}"
        + ("" if contexte else " (sans chiffrement : à placer derrière un reverse proxy HTTPS)"))
    return runner
