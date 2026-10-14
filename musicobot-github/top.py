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


def chercher_artiste(nom):
    """Renvoie l'artiste Deezer le plus pertinent : {id, name, nb_fan, nb_album} ou None."""
    res = api(f"search/artist?q={urllib.parse.quote(nom)}&limit=5").get("data", [])
    if not res:
        return None
    exact = [a for a in res if cle_titre(a["name"]) == cle_titre(nom)]
    return max(exact, key=lambda a: a.get("nb_fan", 0)) if exact else res[0]


def cle_titre(titre):
    """Titre simplifié pour repérer les doublons (version album / single, remaster, feat…)."""
    t = unicodedata.normalize("NFKD", titre).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t)                  # (feat. …), [Remastered]…
    t = re.sub(r"\s+-\s+.*$", " ", t)                       # « - Radio Edit », « - 2011 Remaster »
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def discographie(artiste_id, nombre=0):
    """Musiques d'un artiste : ses `nombre` titres les plus populaires, ou (0) toute sa
    discographie (albums, EP, singles), sans les doublons de titre."""
    if nombre:
        pistes = api(f"artist/{artiste_id}/top?limit={nombre}").get("data", [])
        return [(str(t["id"]), t["artist"]["name"], t["title"]) for t in pistes]
    morceaux, vus = [], set()
    for album in api_pages(f"artist/{artiste_id}/albums?limit=100"):
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
    morceaux = discographie(artiste["id"], nombre)
    if not morceaux:
        raise RuntimeError(f"aucune musique trouvée pour {artiste['name']} sur Deezer")
    dossier = os.path.join(DOSSIER_ARTISTES, dl.nom_dossier(artiste["name"]))
    return telecharger_morceaux(titre, dossier, morceaux, suivi, f"artiste:{artiste['id']}")
