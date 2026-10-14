"""
Bibliothèque rangée par artiste et album, d'après les informations de Deezer :

  music/<Artiste de l'album>/<Album>/<NN - Titre>.m4a

Après chaque téléchargement, la musique est reconnue sur Deezer : directement par son
identifiant pour /top et /artiste, à partir du titre de la vidéo pour /add et les
playlists. Ses tags sont réécrits (titre, artiste, artiste de l'album, album, numéro de
piste, année, genre, pochette de l'album) pour que les lecteurs (Audio Station, Plex…)
la classent correctement, puis elle est rangée. Une musique non reconnue va dans
« Non classés ».

ranger_bibliotheque() fait de même pour les musiques déjà présentes (commande /ranger).
"""
import os
import re
import shutil
import unicodedata
import urllib.parse
import urllib.request
from functools import lru_cache

from mutagen import File as FichierAudio
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TXXX
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

import playlist as dl
import top

DOSSIER_NON_CLASSES = os.path.join(dl.DOSSIER, os.environ.get("DOSSIER_NON_CLASSES", "Non classés"))
EXTENSIONS = (".m4a", ".mp3", ".mp4")
CLE_TAG = "DEEZER_TRACK_ID"   # identifiant Deezer écrit dans les tags : musique déjà rangée
CLE_MP4 = f"----:com.apple.iTunes:{CLE_TAG}"
REGISTRES = (".deja_telecharges.txt", top.NOM_REGISTRE)
# Dossiers jamais parcourus : corbeille et miniatures des NAS, fichiers en cours
DOSSIERS_IGNORES = {"@eaDir", "#recycle", "#snapshot", "@Recycle", ".Trash-1000"}
# Variantes d'un morceau : une vidéo « Remix » ne doit pas être reconnue comme l'original
VARIANTES = {"remix", "live", "instrumental", "karaoke", "acoustic", "acoustique", "cover"}


# ---------------------------------------------------------------------------
# Reconnaissance d'une vidéo YouTube sur Deezer
# ---------------------------------------------------------------------------
def minuscules(texte):
    return unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode().lower()


def base(texte):
    """Texte simplifié pour comparer un titre YouTube et un titre Deezer : sans parenthèses,
    sans « feat. … », sans « Official Video », « Lyrics »…"""
    t = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", minuscules(texte))
    t = re.sub(r"\b(feat|ft|featuring)\b.*$", " ", t)
    t = re.sub(r"\b(official|officiel|officielle|music|video|clip|audio|lyrics?|paroles|visualizer|hd|hq|4k)\b",
               " ", t)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def variantes(texte):
    return VARIANTES & set(re.findall(r"[a-z]+", minuscules(texte)))


def contient(texte, morceau):
    return bool(morceau) and f" {morceau} " in f" {texte} "


def nom_chaine(chaine):
    """« Falling In Reverse - Topic », « FallingInReverseVEVO » → nom de l'artiste."""
    chaine = re.sub(r"\s*-\s*Topic$", "", chaine or "")
    return re.sub(r"VEVO$", "", chaine).strip()


def note(candidat, titre_video, texte_titre, texte_artistes, duree):
    """Note d'un résultat Deezer (plus petit = meilleur), None s'il ne correspond pas."""
    titre = base(candidat["title"])
    if not contient(texte_titre, titre) or variantes(candidat["title"]) != variantes(titre_video):
        return None
    ecart = abs(duree - candidat["duration"]) if duree and candidat.get("duration") else None
    if ecart is not None and ecart > 90:   # même un clip avec une longue intro n'en est pas là
        return None
    artiste_ok = contient(texte_artistes, base(candidat["artist"]["name"]))
    if not artiste_ok and (ecart is None or ecart > 2):
        return None   # artiste absent du titre : il faut au moins la même durée à 2 s près
    single = base(candidat["album"]["title"]) == titre   # on préfère la version de l'album
    return (not artiste_ok, single, min(ecart if ecart is not None else 30, 30) // 5)


def identifier(titre_video, artiste=None, piste=None, chaine=None, duree=None):
    """Morceau Deezer (résultat de recherche) correspondant à une vidéo, ou None."""
    chaine = nom_chaine(chaine)
    texte_titre = base(f"{piste or ''} {titre_video}")
    texte_artistes = base(f"{titre_video} {artiste or ''} {chaine}")
    recherches = []
    if piste and artiste:
        recherches.append(f"{artiste} {piste}")
    recherches.append(base(titre_video))
    if chaine and not contient(base(titre_video), base(chaine)):
        recherches.append(f"{chaine} {base(titre_video)}")
    for recherche in dict.fromkeys(r for r in recherches if r.strip()):
        resultats = top.api(f"search/track?q={urllib.parse.quote(recherche)}&limit=10").get("data", [])
        notes = [(n, i, c) for i, c in enumerate(resultats)
                 if (n := note(c, titre_video, texte_titre, texte_artistes, duree)) is not None]
        if notes:
            return min(notes, key=lambda x: (x[0], x[1]))[2]
    return None


# ---------------------------------------------------------------------------
# Informations Deezer d'un morceau
# ---------------------------------------------------------------------------
@lru_cache(maxsize=256)
def album(album_id):
    return top.api(f"album/{album_id}")


@lru_cache(maxsize=16)
def pochette(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except OSError as e:   # musique rangée quand même, avec la miniature YouTube
        dl.log(f"Pochette Deezer indisponible ({e}) : la miniature YouTube est conservée.")
        return None


def details(deezer_id):
    """Tags d'un morceau Deezer : titre, artiste, album, piste, année, genre, pochette."""
    piste = top.api(f"track/{deezer_id}")
    alb = album(piste["album"]["id"])
    genres = (alb.get("genres") or {}).get("data") or []
    return {
        "deezer_id": str(piste["id"]),
        "titre": piste["title"],
        "artiste": piste["artist"]["name"],
        "artiste_album": (alb.get("artist") or {}).get("name") or piste["artist"]["name"],
        "album": alb.get("title") or piste["album"]["title"],
        "piste": piste.get("track_position") or 0,
        "pistes": alb.get("nb_tracks") or 0,
        "disque": piste.get("disk_number") or 1,
        "annee": (alb.get("release_date") or piste.get("release_date") or "")[:4],
        "genre": genres[0]["name"] if genres else "",
        "pochette": alb.get("cover_xl") or piste["album"].get("cover_xl"),
    }


# ---------------------------------------------------------------------------
# Tags et rangement d'un fichier
# ---------------------------------------------------------------------------
def ecrire_tags(chemin, meta):
    image = pochette(meta["pochette"]) if meta.get("pochette") else None
    if chemin.lower().endswith(".mp3"):
        try:
            tags = ID3(chemin)
        except ID3NoHeaderError:
            tags = ID3()
        for cadre in (TIT2(encoding=3, text=meta["titre"]), TPE1(encoding=3, text=meta["artiste"]),
                      TPE2(encoding=3, text=meta["artiste_album"]), TALB(encoding=3, text=meta["album"]),
                      TXXX(encoding=3, desc=CLE_TAG, text=meta["deezer_id"])):
            tags.setall(cadre.HashKey, [cadre])
        if meta["piste"]:
            tags.setall("TRCK", [TRCK(encoding=3, text=f"{meta['piste']}/{meta['pistes'] or ''}".rstrip("/"))])
        tags.setall("TPOS", [TPOS(encoding=3, text=str(meta["disque"]))])
        if meta["annee"]:
            tags.setall("TDRC", [TDRC(encoding=3, text=meta["annee"])])
        if meta["genre"]:
            tags.setall("TCON", [TCON(encoding=3, text=meta["genre"])])
        if image:
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=image))
        tags.save(chemin)
        return
    f = MP4(chemin)   # m4a et mp4
    if f.tags is None:
        f.add_tags()
    t = f.tags
    t["©nam"], t["©ART"], t["aART"], t["©alb"] = (
        [meta["titre"]], [meta["artiste"]], [meta["artiste_album"]], [meta["album"]])
    t[CLE_MP4] = [MP4FreeForm(meta["deezer_id"].encode())]
    if meta["piste"]:
        t["trkn"] = [(meta["piste"], meta["pistes"])]
    t["disk"] = [(meta["disque"], 0)]
    if meta["annee"]:
        t["©day"] = [meta["annee"]]
    if meta["genre"]:
        t["©gen"] = [meta["genre"]]
    if image:
        t["covr"] = [MP4Cover(image, imageformat=MP4Cover.FORMAT_JPEG)]
    f.save()


def lire_tags(chemin):
    """{titre, artiste, deezer_id, duree} d'un fichier déjà présent (None si absent)."""
    f = FichierAudio(chemin)
    infos = {"titre": None, "artiste": None, "deezer_id": None,
             "duree": getattr(getattr(f, "info", None), "length", None)}
    tags = getattr(f, "tags", None)
    if tags is None:
        return infos
    if chemin.lower().endswith(".mp3"):
        lire = lambda cle: str(tags[cle].text[0]) if cle in tags else None  # noqa: E731
        infos.update(titre=lire("TIT2"), artiste=lire("TPE1"), deezer_id=lire(f"TXXX:{CLE_TAG}"))
    else:
        lire = lambda cle: str(tags[cle][0]) if tags.get(cle) else None  # noqa: E731
        infos.update(titre=lire("©nam"), artiste=lire("©ART"))
        if tags.get(CLE_MP4):
            infos["deezer_id"] = bytes(tags[CLE_MP4][0]).decode()
    return infos


def coupe(texte, n=100):
    """Nom de fichier ou de dossier valide sous Windows/SMB et pas trop long."""
    return dl.nom_dossier(texte[:n])


def chemin_cible(meta, ext):
    numero = (f"{meta['disque']}-{meta['piste']:02d}" if meta["disque"] > 1 else f"{meta['piste']:02d}")
    nom = f"{numero} - {meta['titre']}" if meta["piste"] else meta["titre"]
    return os.path.join(dl.DOSSIER, coupe(meta["artiste_album"]), coupe(meta["album"]), coupe(nom, 150) + ext)


def deplacer(chemin, cible):
    """Déplace un fichier ; renvoie True si c'est un nouveau fichier de la bibliothèque,
    False si la musique y était déjà (doublon supprimé)."""
    if os.path.abspath(cible) == os.path.abspath(chemin):
        return True
    if os.path.exists(cible):
        os.remove(chemin)
        return False
    os.makedirs(os.path.dirname(cible), exist_ok=True)
    shutil.move(chemin, cible)
    return True


def placer(chemin, meta):
    """Écrit les tags Deezer puis range le fichier. Renvoie (chemin final, nouveau)."""
    ecrire_tags(chemin, meta)
    cible = chemin_cible(meta, os.path.splitext(chemin)[1].lower())
    return cible, deplacer(chemin, cible)


def ranger_telechargement(chemin, info, deezer_id=None):
    """Appelée par playlist.telecharger après chaque musique téléchargée.
    `deezer_id` : morceau déjà connu (/top, /artiste) ; sinon reconnaissance d'après la vidéo."""
    if not deezer_id:
        trouve = identifier(info.get("title") or "", artiste=info.get("artist") or info.get("creator"),
                            piste=info.get("track"), chaine=info.get("channel") or info.get("uploader"),
                            duree=info.get("duration"))
        deezer_id = trouve and trouve["id"]
    if not deezer_id:
        cible = os.path.join(DOSSIER_NON_CLASSES, os.path.basename(chemin))
        if os.path.exists(cible):   # même vidéo (son identifiant est dans le nom) : on remplace
            os.remove(cible)
        deplacer(chemin, cible)
        return {"chemin": cible, "nouveau": True, "classee": False}
    cible, nouveau = placer(chemin, details(deezer_id))
    return {"chemin": cible, "nouveau": nouveau, "classee": True}


dl.RANGEMENT = ranger_telechargement


# ---------------------------------------------------------------------------
# Registres : un seul pour toute la bibliothèque
# ---------------------------------------------------------------------------
def dossiers_parcourus():
    """(racine, noms de fichiers) de la bibliothèque, sans les dossiers système ni cachés
    (sauf le dossier des téléchargements en cours)."""
    for racine, dossiers, noms in os.walk(dl.DOSSIER):
        dossiers[:] = [d for d in dossiers if d not in DOSSIERS_IGNORES
                       and (not d.startswith(".") or os.path.join(racine, d) == dl.DOSSIER_ATTENTE)]
        yield racine, noms


def migrer_registres():
    """Les anciennes versions tenaient un registre par dossier (playlist, Top du jour,
    Artistes/<nom>…) : on les fusionne dans le registre commun, à la racine du dossier music."""
    anciens = {nom: [] for nom in REGISTRES}
    for racine, noms in dossiers_parcourus():
        if racine != dl.DOSSIER:
            for nom in REGISTRES:
                if nom in noms:
                    anciens[nom].append(os.path.join(racine, nom))
    for nom, fichiers in anciens.items():
        commun = os.path.join(dl.DOSSIER, nom)
        lignes = dl.lire_lignes(commun)
        connues = {l.split()[0] if nom == top.NOM_REGISTRE else l for l in lignes}
        ajouts = []
        for f in fichiers:
            for l in dl.lire_lignes(f):
                cle = l.split()[0] if nom == top.NOM_REGISTRE else l
                if cle not in connues:
                    connues.add(cle)
                    ajouts.append(l)
        if ajouts:
            with open(commun, "a", encoding="utf-8") as sortie:
                sortie.writelines(l + "\n" for l in ajouts)
            dl.log(f"Registre {nom} : {len(ajouts)} entrée(s) reprise(s) des anciens dossiers.")


def supprimer_dossiers_vides():
    """Après /ranger : supprime les anciens dossiers vidés (seuls des registres y restent)."""
    restes = set(REGISTRES) | {".DS_Store", "Thumbs.db", "desktop.ini"}
    supprimes = 0
    for racine, dossiers, noms in os.walk(dl.DOSSIER, topdown=False):
        if racine == dl.DOSSIER or any(p in DOSSIERS_IGNORES for p in racine.split(os.sep)):
            continue
        try:
            contenu = os.listdir(racine)
            if all(n in restes for n in contenu):
                for n in contenu:
                    os.remove(os.path.join(racine, n))
                os.rmdir(racine)
                supprimes += 1
        except OSError:
            pass
    return supprimes


# ---------------------------------------------------------------------------
# /ranger : toute la bibliothèque
# ---------------------------------------------------------------------------
def deja_rangee(chemin, infos):
    """Musique déjà rangée par cette version : tag Deezer présent et fichier à sa place
    (music/<Artiste>/<Album>/<fichier>, hors « Non classés »)."""
    relatif = os.path.relpath(chemin, dl.DOSSIER).split(os.sep)
    return (bool(infos["deezer_id"]) and len(relatif) == 3
            and os.path.join(dl.DOSSIER, relatif[0]) != DOSSIER_NON_CLASSES)


def titre_depuis_nom(chemin):
    """« Titre de la vidéo [identifiant].m4a » → « Titre de la vidéo »."""
    return re.sub(r"\s*\[[\w-]{11}\]$", "", os.path.splitext(os.path.basename(chemin))[0])


def ranger_bibliotheque(suivi=None):
    """Reconnaît sur Deezer, retague et range toutes les musiques du dossier music.
    Les musiques non reconnues restent où elles sont. Renvoie un résumé pour Discord."""
    titre = "Rangement de la bibliothèque"
    if suivi:
        suivi.maj(titre=titre, etat="Inventaire du dossier music")
    migrer_registres()
    fichiers = sorted(os.path.join(r, n) for r, noms in dossiers_parcourus() for n in noms
                      if n.lower().endswith(EXTENSIONS) and not n.startswith("."))
    if suivi:
        suivi.maj(total=len(fichiers))
    deja = rangees = doublons = 0
    non_reconnues, erreurs = [], []
    annule = False
    for i, chemin in enumerate(fichiers):
        if suivi and suivi.annule:
            annule = True
            break
        relatif = os.path.relpath(chemin, dl.DOSSIER)
        if suivi:
            suivi.debut_video(i, relatif)
            suivi.maj(etat="Reconnaissance sur Deezer")
        try:
            infos = lire_tags(chemin)
            if deja_rangee(chemin, infos):
                deja += 1
                continue
            deezer_id = infos["deezer_id"]
            if not deezer_id:
                titre_video = infos["titre"] or titre_depuis_nom(chemin)
                trouve = identifier(titre_video, chaine=infos["artiste"], duree=infos["duree"])
                deezer_id = trouve and trouve["id"]
            if not deezer_id:
                non_reconnues.append(relatif)
                continue
            _, nouveau = placer(chemin, details(deezer_id))
            rangees += 1
            doublons += not nouveau
        except Exception as e:  # noqa: BLE001 — un fichier illisible n'arrête pas le rangement
            dl.log(f"[{titre}] {relatif} : {e}")
            erreurs.append(relatif)
    vides = supprimer_dossiers_vides()
    if suivi:
        suivi.maj(faites=len(fichiers), video_frac=0.0, etat="Annulé" if annule else "Terminé")
    dl.log(f"[{titre}] {rangees} rangée(s) ({doublons} doublon(s) supprimé(s)), {deja} déjà rangée(s), "
           f"{len(non_reconnues)} non reconnue(s), {len(erreurs)} erreur(s), {vides} dossier(s) vide(s) supprimé(s).")
    message = (f"{'Annulé' if annule else 'Terminé'} : **{titre}**\n"
               f"{rangees} musique(s) rangée(s) par artiste et album"
               f"{f' (dont {doublons} doublon(s) supprimé(s))' if doublons else ''} · "
               f"{deja} déjà rangée(s) · {len(non_reconnues)} non reconnue(s) · {len(erreurs)} en erreur")
    if non_reconnues:
        liste = "\n".join(f"- `{court(n)}`" for n in non_reconnues[:10])
        if len(non_reconnues) > 10:
            liste += f"\n… et {len(non_reconnues) - 10} autre(s) (liste complète dans les journaux)"
        message += f"\nNon reconnues sur Deezer (laissées à leur place) :\n{liste}"
        for n in non_reconnues:
            print("  - non reconnue :", n, flush=True)
    return {"titre": titre, "message": message[:1990]}


def court(texte, n=80):
    return texte if len(texte) <= n else texte[: n - 1] + "…"
