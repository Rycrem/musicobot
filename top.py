"""
Musiques à partir de Deezer (API publique, aucun compte ni clé nécessaire),
téléchargées depuis YouTube (1er résultat de recherche « artiste - titre audio ») :

  - Top du jour : classements Deezer Charts (France, USA, Monde), N premières musiques
  - Artiste     : toute la discographie d'un artiste (ou ses N titres les plus populaires)

Les musiques déjà téléchargées sont sautées grâce à un registre des identifiants Deezer.
"""
import json
import os
import re
import shutil
import time
import unicodedata
import urllib.parse
import urllib.request

import playlist as dl

# Nom affiché : identifiant de la playlist Deezer « Top … » (Deezer Charts)
TOPS_DEFAUT = "France:1109890291,USA:1313621735,Monde:3155776842"
TOPS = [t.split(":", 1) for t in os.environ.get("TOPS", TOPS_DEFAUT).split(",") if ":" in t]
TOP_NOMBRE = int(os.environ.get("TOP_NOMBRE", "10"))
DOSSIER_TOP = os.path.join(dl.DOSSIER, os.environ.get("DOSSIER_TOP", "Top du jour"))
DOSSIER_ARTISTES = os.path.join(dl.DOSSIER, os.environ.get("DOSSIER_ARTISTES", "Artistes"))
NOM_REGISTRE = ".deja_telecharges_deezer.txt"
# MusicBrainz : vrai nom et chaîne YouTube des artistes (API publique, 1 requête/s maximum)
MB_AGENT = "musicobot/1.0 ( https://github.com/Rycrem/musicobot )"
MB_REQUETES_MAX = 6   # par recherche /artiste, pour garder une réponse rapide


# ---------------------------------------------------------------------------
# API Deezer
# ---------------------------------------------------------------------------
def api(chemin_ou_url):
    url = chemin_ou_url if chemin_ou_url.startswith("http") else f"https://api.deezer.com/{chemin_ou_url}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for essai in range(3):
        with urllib.request.urlopen(req, timeout=30) as r:
            donnees = json.load(r)
        err = donnees.get("error") if isinstance(donnees, dict) else None
        if not err:
            return donnees
        if err.get("code") == 4 and essai < 2:   # quota dépassé (50 requêtes / 5 s) : on patiente
            time.sleep(5)
            continue
        raise RuntimeError(f"Deezer : {err.get('message', err)}")


def api_pages(chemin, maxi=2000):
    """Parcourt toutes les pages d'une liste Deezer (champ « next »)."""
    elements, url = [], chemin
    while url and len(elements) < maxi:
        page = api(url)
        elements += page.get("data", [])
        url = page.get("next")
        time.sleep(0.12)   # reste sous la limite de l'API
    return elements


def classement(playlist_id, nombre=TOP_NOMBRE):
    """[(id_deezer, artiste, titre), ...] des `nombre` premières musiques d'une playlist."""
    donnees = api(f"playlist/{playlist_id}/tracks?limit={nombre}")
    return [(str(t["id"]), t["artist"]["name"], t["title"]) for t in donnees.get("data", [])][:nombre]


def chercher_artistes(nom, limite=5):
    """Artistes Deezer correspondant à `nom`, les plus pertinents d'abord : noms exacts
    (par nombre de fans), puis l'ordre de Deezer. Chaque artiste : {id, name, nb_fan,
    nb_album, picture_xl, link, …} + « nb_titres » (approximatif, None si inconnu)."""
    res = api(f"search/artist?q={urllib.parse.quote(nom)}&limit={limite}").get("data", [])
    exact = sorted((a for a in res if cle_titre(a["name"]) == cle_titre(nom)),
                   key=lambda a: a.get("nb_fan", 0), reverse=True)
    artistes = exact + [a for a in res if a not in exact]
    for a in artistes:
        try:
            a["nb_titres"] = nombre_titres(a)
        except Exception:  # noqa: BLE001 — simple information affichée
            a["nb_titres"] = None
    try:
        completer_musicbrainz(artistes)
    except Exception as e:  # noqa: BLE001 — MusicBrainz est facultatif
        dl.log(f"MusicBrainz indisponible ({e}) : liste sans vrai nom ni chaîne YouTube.")
    return artistes


def nombre_titres(artiste):
    """Nombre approximatif de titres d'un artiste : somme des pistes de ses albums, EP
    et singles trouvés par la recherche Deezer (les rééditions sont comptées en double)."""
    q = urllib.parse.quote(f'artist:"{artiste["name"]}"')
    albums = api_pages(f"search/album?q={q}&limit=100", maxi=300)
    return sum(a.get("nb_tracks") or 0 for a in albums
               if str(a.get("artist", {}).get("id")) == str(artiste["id"]))


# ---------------------------------------------------------------------------
# MusicBrainz (vrai nom, groupe ou personne, chaîne YouTube)
# ---------------------------------------------------------------------------
def mb(chemin):
    req = urllib.request.Request(f"https://musicbrainz.org/ws/2/{chemin}",
                                 headers={"User-Agent": MB_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def fiche_musicbrainz(mbid):
    """{type, vrai_nom, youtube, deezer} d'un artiste MusicBrainz."""
    d = mb(f"artist/{mbid}?inc=url-rels+aliases&fmt=json")
    vrai_nom = next((al["name"] for al in d.get("aliases") or []
                     if al.get("type") == "Legal name" and al.get("name")), None)
    youtube = deezer = None
    for rel in d.get("relations") or []:
        url = (rel.get("url") or {}).get("resource") or ""
        if "youtube.com/" in url and not youtube:
            youtube = url
        m = re.search(r"deezer\.com/(?:[a-z]{2}/)?artist/(\d+)", url)
        if m:
            deezer = m.group(1)
    return {"type": d.get("type"), "vrai_nom": vrai_nom, "youtube": youtube, "deezer": deezer}


def completer_musicbrainz(artistes):
    """Ajoute à chaque artiste Deezer « type » (Person, Group…), « vrai_nom » et « youtube »
    trouvés sur MusicBrainz. Un profil MusicBrainz est relié à l'artiste Deezer par son lien
    Deezer ; à défaut, s'il n'y a qu'un artiste Deezer de ce nom, par le nom seul."""
    par_nom = {}
    for a in artistes:
        par_nom.setdefault(cle_titre(a["name"]), []).append(a)
    requetes = 0
    for cle, groupe in par_nom.items():
        if requetes >= MB_REQUETES_MAX:
            break
        q = urllib.parse.quote(f'artist:"{groupe[0]["name"]}"')
        res = mb(f"artist/?query={q}&fmt=json&limit=10").get("artists", [])
        requetes += 1
        candidats = [x for x in res if cle_titre(x.get("name", "")) == cle and x.get("score", 0) >= 80]
        sans_lien = []
        for x in candidats[:len(groupe) + 1]:
            if requetes >= MB_REQUETES_MAX:
                break
            time.sleep(1.1)   # limite de l'API MusicBrainz
            fiche = fiche_musicbrainz(x["id"])
            requetes += 1
            cible = next((a for a in groupe if str(a["id"]) == fiche["deezer"]), None)
            if cible is not None:
                cible.update(type=fiche["type"], vrai_nom=fiche["vrai_nom"], youtube=fiche["youtube"])
            elif not fiche["deezer"]:
                sans_lien.append(fiche)
        if len(groupe) == 1 and "type" not in groupe[0] and sans_lien:
            f = sans_lien[0]
            groupe[0].update(type=f["type"], vrai_nom=f["vrai_nom"], youtube=f["youtube"])
        time.sleep(1.1)


def cle_titre(titre):
    """Titre simplifié pour repérer les doublons (version album / single, remaster, feat…)."""
    t = unicodedata.normalize("NFKD", titre).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t)                  # (feat. …), [Remastered]…
    t = re.sub(r"\s+-\s+.*$", " ", t)                       # « - Radio Edit », « - 2011 Remaster »
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def discographie(artiste_id, nombre=0, suivi=None):
    """Musiques d'un artiste : ses `nombre` titres les plus populaires, ou (0) toute sa
    discographie (albums, EP, singles), sans les doublons de titre.
    S'arrête (liste vide) si le téléchargement est annulé pendant la lecture."""
    if nombre:
        pistes = api(f"artist/{artiste_id}/top?limit={nombre}").get("data", [])
        return [(str(t["id"]), t["artist"]["name"], t["title"]) for t in pistes]
    morceaux, vus = [], set()
    for album in api_pages(f"artist/{artiste_id}/albums?limit=100"):
        if suivi and suivi.annule:
            return []
        for t in api_pages(f"album/{album['id']}/tracks?limit=200"):
            if str(t.get("artist", {}).get("id")) != str(artiste_id):
                continue   # compilations : on ne garde que les titres de l'artiste
            cle = cle_titre(t["title"])
            if cle and cle not in vus:
                vus.add(cle)
                morceaux.append((str(t["id"]), t["artist"]["name"], t["title"]))
    return morceaux


# ---------------------------------------------------------------------------
# Téléchargement d'une liste de musiques
# ---------------------------------------------------------------------------
def lire_registre(dossier):
    try:
        with open(os.path.join(dossier, NOM_REGISTRE), encoding="utf-8") as f:
            return {ligne.split()[0] for ligne in f if ligne.strip()}
    except FileNotFoundError:
        return set()


def noter(dossier, ids_titres):
    with open(os.path.join(dossier, NOM_REGISTRE), "a", encoding="utf-8") as f:
        for id_deezer, nom in ids_titres:
            f.write(f"{id_deezer}  {nom}\n")


def telecharger_morceaux(titre, dossier, morceaux, suivi=None, cle_url="deezer"):
    """Cherche et télécharge chaque (id_deezer, artiste, titre) sur YouTube, en sautant
    ceux déjà notés dans le registre du dossier. Renvoie le résumé de dl.telecharger
    (+ « deja »). Les échecs ne sont pas notés : ils seront retentés la fois suivante."""
    os.makedirs(dossier, exist_ok=True)
    deja_vus = lire_registre(dossier)
    nouveaux = [m for m in morceaux if m[0] not in deja_vus]
    deja = len(morceaux) - len(nouveaux)
    dl.log(f"[{titre}] {len(morceaux)} musique(s), {deja} déjà téléchargée(s).")

    if not nouveaux:
        if suivi:
            suivi.maj(total=1, faites=1, etat="Terminé")
        return {"titre": titre, "dossier": dossier, "total": len(morceaux),
                "telechargees": 0, "ignorees": 0, "erreurs": 0, "echecs": [],
                "index_echecs": [], "deja": deja}

    entrees = []
    for _, artiste, t in nouveaux:
        recherche = re.sub(r'["]', "", f"{artiste} - {t}")
        entrees.append({"url": f"ytsearch1:{recherche} audio", "title": f"{artiste} - {t}", "id": None})
    resume = dl.telecharger(cle_url, suivi, entrees=entrees, titre=titre, dossier=dossier)

    reussies = set(resume.get("index_ok", []))   # seules les réussites sont notées
    noter(dossier, [(m[0], f"{m[1]} - {m[2]}") for j, m in enumerate(nouveaux) if j in reussies])
    resume["total"] = len(morceaux)
    resume["deja"] = deja
    return resume


# ---------------------------------------------------------------------------
# Top du jour
# ---------------------------------------------------------------------------
def noms_tops():
    return [nom.strip() for nom, _ in TOPS]


def telecharger_top(nom, suivi=None):
    """Télécharge le top `nom` (ex. « France »). Un registre commun à tous les tops :
    une musique présente dans plusieurs classements n'est téléchargée qu'une fois."""
    ids = dict((n.strip(), i.strip()) for n, i in TOPS)
    if nom not in ids:
        raise RuntimeError(f"top inconnu : {nom} (disponibles : {', '.join(ids)})")
    titre = f"Top {TOP_NOMBRE} {nom}"
    if suivi:
        suivi.maj(titre=titre, etat="Lecture du classement Deezer")
    morceaux = classement(ids[nom])
    if not morceaux:
        raise RuntimeError("classement Deezer vide")
    return telecharger_morceaux(titre, DOSSIER_TOP, morceaux, suivi, f"top:{nom}")


# ---------------------------------------------------------------------------
# Artiste
# ---------------------------------------------------------------------------
def telecharger_artiste(artiste, nombre=0, suivi=None):
    """`artiste` = dict renvoyé par chercher_artiste. nombre = 0 : toute la discographie."""
    titre = artiste["name"] if not nombre else f"{artiste['name']} (top {nombre})"
    if suivi:
        suivi.maj(titre=titre, etat="Lecture de la discographie sur Deezer")
    morceaux = discographie(artiste["id"], nombre, suivi)
    dossier = os.path.join(DOSSIER_ARTISTES, dl.nom_dossier(artiste["name"]))
    if suivi and suivi.annule:   # annulé avant le premier téléchargement
        return {"titre": titre, "dossier": dossier, "total": len(morceaux), "telechargees": 0,
                "ignorees": 0, "erreurs": 0, "echecs": [], "index_echecs": [], "deja": 0,
                "annule": True, "supprime": False}
    if not morceaux:
        raise RuntimeError(f"aucune musique trouvée pour {artiste['name']} sur Deezer")
    nouveau = not os.path.isdir(dossier)
    resume = telecharger_morceaux(titre, dossier, morceaux, suivi, f"artiste:{artiste['id']}")
    resume["supprime"] = False
    if resume.get("annule") and nouveau:
        # Dossier créé par ce téléchargement (sans doute le mauvais artiste) : on l'efface.
        # Un dossier qui existait déjà n'est jamais touché.
        shutil.rmtree(dossier, ignore_errors=True)
        resume["supprime"] = True
        dl.log(f"[{titre}] dossier {dossier} supprimé après annulation.")
    return resume
