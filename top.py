"""
Musiques à partir de Deezer (API publique, aucun compte ni clé nécessaire),
téléchargées depuis YouTube (parmi les premiers résultats de recherche « artiste - titre audio »,
la vidéo qui a la durée du morceau Deezer et n'est pas un live ni un remix) :

  - Top du jour : classements Deezer Charts (France, USA, Monde), N premières musiques
  - Artiste     : toute la discographie d'un artiste (ou ses N titres les plus populaires)

Les musiques déjà téléchargées sont sautées grâce à un registre des identifiants Deezer.
Elles sont ensuite rangées par artiste et album (voir bibliotheque.py).
"""
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import playlist as dl

# Nom affiché : identifiant de la playlist Deezer « Top … » (Deezer Charts)
TOPS_DEFAUT = "France:1109890291,USA:1313621735,Monde:3155776842"
TOPS = [t.split(":", 1) for t in os.environ.get("TOPS", TOPS_DEFAUT).split(",") if ":" in t]
TOP_NOMBRE = int(os.environ.get("TOP_NOMBRE", "10"))
DOSSIER_TOP = os.path.join(dl.DOSSIER, os.environ.get("DOSSIER_TOP", "Top du jour"))
NOM_REGISTRE = ".deja_telecharges_deezer.txt"
REGISTRE = os.path.join(dl.DOSSIER, NOM_REGISTRE)   # un seul registre pour tout le dossier music
# MusicBrainz : vrai nom et chaîne YouTube des artistes (API publique, 1 requête/s maximum)
RESULTATS_YOUTUBE = 5   # résultats de recherche comparés à la durée Deezer pour chaque morceau
MB_AGENT = "musicobot/1.0 ( https://github.com/Rycrem/musicobot )"
MB_REQUETES_MAX = 6   # par recherche /artiste, pour garder une réponse rapide


# ---------------------------------------------------------------------------
# API Deezer
# ---------------------------------------------------------------------------
_verrou_api = threading.Lock()
_derniere_requete = 0.0


def patienter():
    """Au plus ~9 requêtes par seconde : reste sous la limite de Deezer (50 / 5 s),
    même pendant /ranger qui en enchaîne des milliers."""
    global _derniere_requete
    with _verrou_api:
        attente = _derniere_requete + 0.11 - time.monotonic()
        if attente > 0:
            time.sleep(attente)
        _derniere_requete = time.monotonic()


def api(chemin_ou_url):
    url = chemin_ou_url if chemin_ou_url.startswith("http") else f"https://api.deezer.com/{chemin_ou_url}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for essai in range(3):
        patienter()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                donnees = json.load(r)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if isinstance(e, urllib.error.HTTPError) or essai == 2:
                raise
            time.sleep(3)   # coupure réseau passagère (« Connection reset by peer ») : on réessaie
            continue
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
    """[(id_deezer, artiste, titre, durée), ...] des `nombre` premières musiques d'une playlist."""
    donnees = api(f"playlist/{playlist_id}/tracks?limit={nombre}")
    return [morceau(t) for t in donnees.get("data", [])][:nombre]


def morceau(t):
    return (str(t["id"]), t["artist"]["name"], t["title"], t.get("duration"))


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


# Rangs des albums Deezer pour /artiste : un titre présent sur plusieurs albums est pris sur
# l'album studio, avant l'EP, le single, la compilation, et les bonus des éditions deluxe
TYPES_ALBUM = {"album": 0, "ep": 1, "single": 2, "compile": 3}
EDITION_BONUS = re.compile(r"\b(deluxe|box set|expanded|anniversary|edition)\b", re.I)
# Best-of souvent classés « album » sur Deezer : traités comme des compilations
BEST_OF = re.compile(r"\b(greatest hits|best of|hits|collection|anthology|essentials?|the very best)\b", re.I)
# Versions écartées de la discographie, en plus des lives, remixes, démos… de bibliotheque.py
AUTRES_VERSIONS = re.compile(r"\b(rough mix|riff tapes?|take \d+|writing in progress|work in progress"
                             r"|pre-production|interview|commentary|fan recording)\b", re.I)


def annotation(titre):
    """Ce que Deezer ajoute au nom du morceau : « Battery (Live at …) », « Orion - Remastered »."""
    return " ".join(re.findall(r"[\(\[](.*?)[\)\]]", titre) + re.findall(r"\s-\s(.*)$", titre))


def autre_version(titre):
    """Live, démo, remix, prise de studio… : pas la version de l'album."""
    import bibliotheque   # import ici : bibliotheque importe déjà top
    texte = annotation(titre)
    return bool(texte) and bool(bibliotheque.variantes(texte) or AUTRES_VERSIONS.search(texte))


def discographie(artiste_id, nombre=0, suivi=None):
    """Musiques d'un artiste : ses `nombre` titres les plus populaires, ou (0) toute sa
    discographie studio (albums, EP, singles), sans les doublons de titre.
    Les albums studio passent en premier, du plus ancien au plus récent : chaque titre est pris
    sur son album d'origine, qui est donc complet. Les albums live et les lives, démos ou
    remixes des singles et des éditions deluxe sont écartés.
    S'arrête (liste vide) si le téléchargement est annulé pendant la lecture."""
    if nombre:
        pistes = api(f"artist/{artiste_id}/top?limit={nombre}").get("data", [])
        return [morceau(t) for t in pistes]

    def rang(album):
        type_album = TYPES_ALBUM.get(album.get("record_type"), 2)
        if BEST_OF.search(album["title"]):
            type_album = TYPES_ALBUM["compile"]
        # L'édition deluxe juste après l'édition standard du même jour (ou à sa place si Deezer
        # n'a qu'elle) ; ses bonus restent derrière les albums studio
        return (type_album, album.get("release_date") or "9999", bool(EDITION_BONUS.search(album["title"])))

    morceaux, vus = [], set()
    for album in sorted(api_pages(f"artist/{artiste_id}/albums?limit=100"), key=rang):
        if suivi and suivi.annule:
            return []
        if autre_version(album["title"]):
            continue   # « Erase The Pain (Live) »
        pistes = api_pages(f"album/{album['id']}/tracks?limit=200")
        if sum(autre_version(t["title"]) for t in pistes) > len(pistes) / 2:
            continue   # album live (S&M, Live Shit…) : ses titres sont déjà sur les albums studio
        for t in pistes:
            if str(t.get("artist", {}).get("id")) != str(artiste_id):
                continue   # compilations : on ne garde que les titres de l'artiste
            cle = cle_titre(t["title"])
            if cle and cle not in vus and not autre_version(t["title"]):
                vus.add(cle)
                morceaux.append(morceau(t))
    return morceaux


# ---------------------------------------------------------------------------
# Téléchargement d'une liste de musiques
# ---------------------------------------------------------------------------
def lire_registre():
    return {ligne.split()[0] for ligne in dl.lire_lignes(REGISTRE)}


def noter(ids_titres):
    with open(REGISTRE, "a", encoding="utf-8") as f:
        for id_deezer, nom in ids_titres:
            f.write(f"{id_deezer}  {nom}\n")


def choix_video(artiste, titre, duree):
    """Fonction qui choisit, parmi les résultats d'une recherche YouTube, la vidéo du morceau
    Deezer : pas un live ni un remix (sauf si c'en est un sur Deezer), à 15 s près de préférence
    sur la chaîne de l'artiste, sinon la durée la plus proche. None si aucune ne convient
    (morceau retenté la fois suivante)."""
    import bibliotheque   # import ici : bibliotheque importe déjà top
    attendues = bibliotheque.variantes(titre)
    nom = bibliotheque.base(artiste)

    def rang(video):
        ecart = abs(video["duration"] - duree) if duree and video.get("duration") else None
        officielle = bibliotheque.base(bibliotheque.nom_chaine(video.get("channel"))) == nom
        return (ecart is None or ecart > 15, not officielle, ecart if ecart is not None else 0)

    def choisir(resultats):
        bonnes = [v for v in resultats
                  if not bibliotheque.variantes(v.get("title") or "") - attendues
                  and not (duree and v.get("duration") and abs(v["duration"] - duree) > 90)]
        return min(bonnes, key=rang) if bonnes else None
    return choisir


def telecharger_morceaux(titre, morceaux, suivi=None, cle_url="deezer", categorie=None):
    """Cherche et télécharge chaque (id_deezer, artiste, titre) sur YouTube, en sautant
    ceux déjà notés dans le registre. Renvoie le résumé de dl.telecharger (+ « deja »).
    Les échecs ne sont pas notés : ils seront retentés la fois suivante."""
    deja_vus = lire_registre()
    nouveaux = [m for m in morceaux if m[0] not in deja_vus]
    deja = len(morceaux) - len(nouveaux)
    dl.log(f"[{titre}] {len(morceaux)} musique(s), {deja} déjà téléchargée(s).")

    if not nouveaux:
        if suivi:
            suivi.maj(total=1, faites=1, etat="Terminé")
        return {"titre": titre, "total": len(morceaux), "telechargees": 0, "ignorees": 0,
                "erreurs": 0, "echecs": [], "index_echecs": [], "deja": deja}

    entrees = []
    for id_deezer, artiste, t, duree in nouveaux:
        recherche = re.sub(r'["]', "", f"{artiste} - {t}")
        entrees.append({"url": f"ytsearch{RESULTATS_YOUTUBE}:{recherche} audio", "title": f"{artiste} - {t}",
                        "id": None, "choisir": choix_video(artiste, t, duree),
                        "deezer": id_deezer,   # tags et rangement d'après ce morceau Deezer
                        "categorie": categorie})
    resume = dl.telecharger(cle_url, suivi, entrees=entrees, titre=titre,
                            dossier=os.path.join(dl.DOSSIER, dl.nom_dossier(titre)))

    reussies = set(resume.get("index_ok", []))   # seules les réussites sont notées
    noter([(m[0], f"{m[1]} - {m[2]}") for j, m in enumerate(nouveaux) if j in reussies])
    resume["ids_deezer"] = [m[0] for j, m in enumerate(nouveaux) if j in reussies]
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
    return telecharger_morceaux(titre, morceaux, suivi, f"top:{nom}", categorie=DOSSIER_TOP)


# ---------------------------------------------------------------------------
# Artiste
# ---------------------------------------------------------------------------
def telecharger_artiste(artiste, nombre=0, suivi=None):
    """`artiste` = dict renvoyé par chercher_artiste. nombre = 0 : toute la discographie."""
    titre = artiste["name"] if not nombre else f"{artiste['name']} (top {nombre})"
    if suivi:
        suivi.maj(titre=titre, etat="Lecture de la discographie sur Deezer")
    morceaux = discographie(artiste["id"], nombre, suivi)
    if suivi and suivi.annule:   # annulé avant le premier téléchargement
        return {"titre": titre, "total": len(morceaux), "telechargees": 0,
                "ignorees": 0, "erreurs": 0, "echecs": [], "index_echecs": [], "deja": 0,
                "annule": True, "supprime": False}
    if not morceaux:
        raise RuntimeError(f"aucune musique trouvée pour {artiste['name']} sur Deezer")
    nouveau = not os.path.isdir(os.path.join(dl.DOSSIER, dl.nom_dossier(artiste["name"])))
    resume = telecharger_morceaux(titre, morceaux, suivi, f"artiste:{artiste['id']}")
    resume["supprime"] = False
    if resume.get("annule") and nouveau:
        # Artiste absent de la bibliothèque avant ce téléchargement (sans doute le mauvais) :
        # on efface les musiques qu'il a ajoutées et on les retire des registres. Un artiste
        # qui était déjà là n'est jamais touché.
        supprimer_fichiers([f for f, _ in resume.get("fichiers", [])])
        dl.oublier(REGISTRE, set(resume["ids_deezer"]))
        dl.oublier(dl.ARCHIVE, {v for _, v in resume.get("fichiers", []) if v}, colonne=1)
        resume["supprime"] = True
        dl.log(f"[{titre}] {len(resume.get('fichiers', []))} musique(s) supprimée(s) après annulation.")
    return resume


def supprimer_fichiers(chemins):
    """Supprime des musiques, puis leurs dossiers album / artiste s'ils sont devenus vides."""
    for chemin in chemins:
        try:
            os.remove(chemin)
        except OSError:
            continue
        dossier = os.path.dirname(chemin)
        while os.path.abspath(dossier) != os.path.abspath(dl.DOSSIER):
            try:
                os.rmdir(dossier)   # échoue (et on s'arrête) si le dossier n'est pas vide
            except OSError:
                break
            dossier = os.path.dirname(dossier)
