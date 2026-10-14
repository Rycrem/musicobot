"""
Télécharge des vidéos / playlists YouTube sur le NAS (version Docker / NAS UGREEN).

Deux usages :
  - en ligne de commande : lit les URL dans /config/playlists.txt (ou la variable PLAYLISTS)
  - comme module : bot.py appelle telecharger(url, suivi) pour chaque lien reçu sur Discord

Sécurité : pour une playlist, seules les vidéos publiées par la chaîne propriétaire
de la playlist (donc toi) sont téléchargées ; les autres sont ignorées et listées.
(désactivable avec SEULEMENT_MA_CHAINE=0). Un lien de vidéo seule est toujours téléchargé.

Tous les réglages se font dans docker-compose.yml (section environment).
"""
import glob
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import yt_dlp


def env_bool(nom, defaut):
    return os.environ.get(nom, str(defaut)).strip().lower() in ("1", "true", "oui", "yes")


# ---------------- Réglages (via variables d'environnement) ----------------
FORMAT = os.environ.get("FORMAT", "m4a").lower()          # m4a | mp3 | mp4
DOSSIER = os.environ.get("DOSSIER", "/music")              # monté sur le dossier music du NAS
DOSSIER_TITRES = os.environ.get("DOSSIER_TITRES", "Titres")  # sous-dossier des vidéos seules
FICHIER_PLAYLISTS = os.environ.get("FICHIER_PLAYLISTS", "/config/playlists.txt")
FICHIER_COOKIES = os.environ.get("COOKIES", "/config/cookies.txt")  # vidéos privées/non répertoriées
PLAYLISTS_EN_PARALLELE = min(int(os.environ.get("PLAYLISTS_EN_PARALLELE", "2")), 3)
SEULEMENT_MA_CHAINE = env_bool("SEULEMENT_MA_CHAINE", False)
INTERVALLE_HEURES = float(os.environ.get("INTERVALLE_HEURES", "0"))  # 0 = une seule passe
PAUSE_REQUETES = float(os.environ.get("PAUSE_REQUETES", "0"))  # pause (s) entre requêtes YouTube
PAUSE_VIDEOS = float(os.environ.get("PAUSE_VIDEOS", "0"))  # pause (s) avant chaque téléchargement
SESSION_MAX = int(os.environ.get("SESSION_MAX", "300"))     # nouvelle session YouTube toutes les N musiques
BLOCAGE_SEUIL = int(os.environ.get("BLOCAGE_SEUIL", "5"))   # échecs d'affilée = session bloquée
BLOCAGE_PAUSE_MIN = float(os.environ.get("BLOCAGE_PAUSE_MIN", "10"))  # pause si une nouvelle session ne suffit pas
BLOCAGE_MAX = int(os.environ.get("BLOCAGE_MAX", "6"))       # nombre max de pauses par playlist
ROBOT_SEUIL = int(os.environ.get("ROBOT_SEUIL", "3"))       # « confirm you're not a bot » d'affilée = arrêt
# --------------------------------------------------------------------------

# Fichiers créés modifiables par le groupe (pratique pour le partage SMB du NAS)
os.umask(0o002)


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def nom_dossier(titre):
    """Nom de dossier valide sous Windows/SMB (le NAS est souvent lu depuis un PC)."""
    titre = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", titre).strip(" .")
    return titre or "Playlist"


def verifier_dossier():
    if not os.path.isdir(DOSSIER):
        sys.exit(f"Le dossier {DOSSIER} n'existe pas : vérifie le volume dans docker-compose.yml")


class Annulation(yt_dlp.utils.DownloadCancelled):
    """Levée dans les hooks yt-dlp quand l'utilisateur annule depuis Discord."""
    msg = "Téléchargement annulé"


# ---------------------------------------------------------------------------
# Suivi de l'avancement (lu par le bot Discord pendant le téléchargement)
# ---------------------------------------------------------------------------
class Suivi:
    """Avancement d'un lien. Mis à jour par le thread de téléchargement,
    lu par le bot : tout passe par un verrou."""

    def __init__(self, url):
        self._verrou = threading.Lock()
        self.url = url
        self.titre = None
        self.etat = "En attente"
        self.total = 0            # nombre de vidéos du lien
        self.faites = 0           # vidéos terminées (téléchargées, déjà présentes ou ignorées)
        self.video_titre = None
        self.video_frac = 0.0     # avancement de la vidéo en cours (0 → 1)
        self.vitesse = None       # octets/s
        self.eta = None           # secondes restantes pour la vidéo en cours
        self.erreurs = 0
        self.ignorees = 0
        self.debut = None
        self._fichiers = []       # parties de la vidéo en cours (vidéo + audio en mp4)
        self.annule = False       # demandé depuis Discord : arrêt au plus vite

    def annuler(self):
        self.annule = True

    def maj(self, **valeurs):
        with self._verrou:
            for cle, val in valeurs.items():
                setattr(self, cle, val)

    def debut_video(self, index, titre):
        with self._verrou:
            self.faites = index
            self.video_titre = titre
            self.video_frac = 0.0
            self.vitesse = self.eta = None
            self.etat = "Téléchargement"
            self._fichiers = []

    def photo(self):
        """Copie cohérente de l'état + pourcentage global."""
        with self._verrou:
            if self.total:
                pct = min(100.0, (self.faites + self.video_frac) / self.total * 100)
            else:
                pct = 0.0
            return {
                "url": self.url, "titre": self.titre, "etat": self.etat,
                "total": self.total, "faites": self.faites,
                "video_titre": self.video_titre, "video_pct": self.video_frac * 100,
                "vitesse": self.vitesse, "eta": self.eta,
                "erreurs": self.erreurs, "ignorees": self.ignorees,
                "debut": self.debut, "pourcentage": pct,
            }

    # --- hooks yt-dlp ---
    def hook_progression(self, d):
        if self.annule:   # interrompt la musique en cours de téléchargement
            raise Annulation()
        if d["status"] not in ("downloading", "finished"):
            return
        with self._verrou:
            fichier = d.get("filename")
            if fichier not in self._fichiers:
                self._fichiers.append(fichier)
            partie = self._fichiers.index(fichier)
            nb_parties = max(len(d.get("info_dict", {}).get("requested_formats") or [1]), partie + 1)
            if d["status"] == "finished":
                frac = 1.0
            else:
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                frac = min(1.0, d.get("downloaded_bytes", 0) / total) if total else 0.0
            # 95 % pour le téléchargement, les 5 % restants pour la conversion
            self.video_frac = max(self.video_frac, (partie + frac) / nb_parties * 0.95)
            self.vitesse = d.get("speed")
            self.eta = d.get("eta")

    def hook_conversion(self, d):
        if d["status"] == "started":
            self.maj(etat="Conversion / métadonnées", vitesse=None, eta=None)


class Journal:
    """Redirige les messages de yt-dlp vers la console et compte les erreurs."""

    def __init__(self, prefixe="", suivi=None):
        self.prefixe = prefixe
        self.suivi = suivi
        self.erreurs = []

    def debug(self, msg):
        if not msg.startswith("[debug] "):
            self.info(msg)

    def info(self, msg):
        print(f"{self.prefixe}{msg}", flush=True)

    def warning(self, msg):
        print(f"{self.prefixe}ATTENTION : {msg}", flush=True)

    def error(self, msg):
        print(f"{self.prefixe}{msg}", flush=True)
        self.erreurs.append(msg)


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------
def options_communes(journal):
    opts = {
        "logger": journal,
        "noprogress": True,             # pas de barre dans les logs Docker (le bot affiche l'avancement)
        "ignoreerrors": True,
        "concurrent_fragment_downloads": 4,
        "retries": 10,
        "fragment_retries": 10,
    }
    if PAUSE_REQUETES > 0:
        opts["sleep_interval_requests"] = PAUSE_REQUETES
    if PAUSE_VIDEOS > 0:   # pause aléatoire avant chaque téléchargement (pas pour les déjà présentes)
        opts["sleep_interval"] = PAUSE_VIDEOS
        opts["max_sleep_interval"] = PAUSE_VIDEOS * 2
    if FICHIER_COOKIES and os.path.isfile(FICHIER_COOKIES):
        opts["cookiefile"] = FICHIER_COOKIES
    return opts


def options_format():
    if FORMAT in ("m4a", "mp3"):
        extraction = {"key": "FFmpegExtractAudio", "preferredcodec": FORMAT}
        if FORMAT == "m4a":
            # audio d'origine de YouTube, sans réencodage : quasi instantané
            fmt = "bestaudio[ext=m4a]/bestaudio/best"
        else:
            fmt = "bestaudio/best"
            extraction["preferredquality"] = "192"
        return {
            "format": fmt,
            "writethumbnail": True,
            "postprocessors": [
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                extraction,
                {"key": "FFmpegMetadata", "add_metadata": True},   # titre, artiste...
                {"key": "EmbedThumbnail"},                          # pochette dans le fichier
            ],
        }
    return {  # mp4
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
    }


def nettoyer_restes(dossier, video_id):
    """Supprime la miniature laissée par un téléchargement échoué."""
    if not video_id:
        return
    for ext in ("jpg", "webp", "png", "part"):
        chemin = os.path.join(dossier, f"*[{video_id}].{ext}")
        for f in glob.glob(glob.escape(chemin).replace(glob.escape("*"), "*", 1)):
            try:
                os.remove(f)
            except OSError:
                pass


def nettoyer_annulation(dossier):
    """Après une annulation : supprime les morceaux de fichiers inachevés (.part, .ytdl)
    et les miniatures orphelines (pochette téléchargée avant la musique)."""
    medias = {".m4a", ".mp3", ".mp4", ".webm", ".opus"}
    try:
        noms = os.listdir(dossier)
    except OSError:
        return
    bases = {os.path.splitext(n)[0] for n in noms if os.path.splitext(n)[1].lower() in medias}
    for n in noms:
        base, ext = os.path.splitext(n)
        ext = ext.lower()
        if ext in (".part", ".ytdl") or (ext in (".jpg", ".webp", ".png") and base not in bases):
            try:
                os.remove(os.path.join(dossier, n))
            except OSError:
                pass


def fermer(ydl):
    """Ferme une session yt-dlp ; une erreur d'écriture de cookies.txt ne doit rien casser."""
    try:
        ydl.close()
    except Exception as e:  # noqa: BLE001
        log(f"ATTENTION : impossible de mettre à jour {FICHIER_COOKIES} ({e}). "
            "Vérifie que le dossier config est modifiable.")


def est_blocage_robot(message):
    """YouTube demande de se connecter (« Sign in to confirm you're not a bot ») :
    l'adresse IP du NAS est bloquée, seuls des cookies de compte YouTube y remédient."""
    m = (message or "").lower().replace("\u2019", "'")
    return "confirm you're not a bot" in m or "sign in to confirm" in m


def url_video(entree):
    return (entree.get("webpage_url") or entree.get("url")
            or f"https://www.youtube.com/watch?v={entree['id']}")


def telecharger(url, suivi=None, entrees=None, titre=None, dossier=None):
    """Télécharge une vidéo ou une playlist. Renvoie un résumé (dict).
    Avec `entrees` (+ titre, dossier) : télécharge directement cette liste
    (ex. recherches « ytsearch1:… » du top du jour) sans analyser `url`.
    Lève une exception si le lien est illisible."""
    journal = Journal(suivi=suivi)
    if suivi:
        suivi.maj(etat="Analyse du lien", debut=time.time())

    if entrees is not None:
        ids_chaine = None
    else:
        # 1) Analyse rapide : titre, type, liste des vidéos, chaîne propriétaire
        ydl = yt_dlp.YoutubeDL(options_communes(journal) | {"extract_flat": "in_playlist"})
        try:
            info = ydl.extract_info(url, download=False)
        finally:
            fermer(ydl)
        if not info:
            raise RuntimeError(journal.erreurs[-1] if journal.erreurs else "lien illisible")

        titre = info.get("title") or "Sans titre"
        est_playlist = info.get("_type") == "playlist"
        if est_playlist:
            entrees = [e for e in (info.get("entries") or []) if e]
            dossier = os.path.join(DOSSIER, nom_dossier(titre))
            ids_chaine = {info.get("channel_id"), info.get("uploader_id")} - {None}
            if SEULEMENT_MA_CHAINE and not ids_chaine:
                raise RuntimeError("impossible d'identifier la chaîne propriétaire de la playlist")
        else:
            entrees = [info]
            dossier = os.path.join(DOSSIER, DOSSIER_TITRES)
            ids_chaine = None   # vidéo seule : pas de filtre
    os.makedirs(dossier, exist_ok=True)
    journal.prefixe = f"[{titre[:40]}] "
    if suivi:
        suivi.maj(titre=titre, total=len(entrees))

    # 2) Filtre « seulement ma chaîne »
    ignorees = {}

    def filtre(info_video, *, incomplete=False):
        if not (SEULEMENT_MA_CHAINE and ids_chaine):
            return None
        ids_video = {info_video.get("channel_id"), info_video.get("uploader_id")} - {None}
        if not ids_video:
            return None if incomplete else "chaîne inconnue"
        if ids_video & ids_chaine:
            return None
        ignorees[info_video.get("id")] = info_video.get("title") or info_video.get("id")
        if suivi:
            suivi.maj(ignorees=len(ignorees))
        return "vidéo d'une autre chaîne"

    # 3) Téléchargement vidéo par vidéo (permet un pourcentage global fiable)
    telechargees = set()

    def note_fin(d):
        if d["status"] == "finished":
            telechargees.add(d.get("info_dict", {}).get("id"))

    opts = options_communes(journal) | options_format() | {
        "outtmpl": os.path.join(dossier, "%(title)s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "match_filter": filtre,
        "windowsfilenames": True,
        "download_archive": os.path.join(dossier, ".deja_telecharges.txt"),  # reprise
        "progress_hooks": [note_fin] + ([suivi.hook_progression] if suivi else []),
        "postprocessor_hooks": [suivi.hook_conversion] if suivi else [],
    }

    log(f"=== {titre} ({len(entrees)} vidéo(s)) -> {dossier}")
    ydl = yt_dlp.YoutubeDL(opts)
    session_essais = 0    # musiques tentées avec la session actuelle
    session_neuve = True  # aucune réussite depuis la dernière nouvelle session

    def nouvelle_session(raison):
        nonlocal ydl, session_essais, session_neuve
        log(f"[{titre}] Nouvelle session YouTube ({raison}).")
        fermer(ydl)
        ydl = yt_dlp.YoutubeDL(opts)
        session_essais, session_neuve = 0, True

    try:
        echecs = {}   # musiques en erreur : on les saute et on continue
        serie = []    # index des échecs consécutifs
        pauses = 0
        reessai_jusqua = -1   # musiques en cours de nouvel essai : ne relancent pas de blocage
        robot = []            # index des échecs « not a bot » consécutifs
        index_ok = set()      # musiques réussies (téléchargées ou déjà présentes)
        bloque = False
        annule = False
        i = 0
        while i < len(entrees):
            if suivi and suivi.annule:
                annule = True
                break
            entree = entrees[i]
            cle = entree.get("id") or i
            if suivi:
                suivi.debut_video(i, entree.get("title") or entree.get("id"))
            if SESSION_MAX and session_essais >= SESSION_MAX:
                nouvelle_session(f"préventif, après {SESSION_MAX} musiques")
            nb_erreurs, nb_ok = len(journal.erreurs), len(telechargees)
            try:
                ydl.extract_info(url_video(entree), download=True)
            except Exception as e:  # noqa: BLE001
                if suivi and suivi.annule:
                    annule = True
                    break
                journal.error(f"ERREUR : {e}")
            if suivi and suivi.annule:   # annulé pendant la conversion : on s'arrête là
                annule = True
                if entree.get("id") in telechargees:
                    index_ok.add(i)
                break
            if len(telechargees) > nb_ok or len(journal.erreurs) > nb_erreurs:
                session_essais += 1   # les musiques déjà présentes ne comptent pas
            if len(journal.erreurs) > nb_erreurs and entree.get("id") not in telechargees:
                nettoyer_restes(dossier, entree.get("id"))
                echecs[cle] = entree.get("title") or entree.get("id") or "?"
                if i > reessai_jusqua:
                    serie.append(i)
                if suivi:
                    suivi.maj(erreurs=len(echecs))
                # Blocage anti-robot : inutile d'insister (nouvelle session / pause n'y font rien)
                if est_blocage_robot(journal.erreurs[-1]):
                    robot.append(i)
                    if ROBOT_SEUIL and len(robot) >= ROBOT_SEUIL:
                        for j in robot:   # pas de vrais échecs : elles seront retentées
                            echecs.pop(entrees[j].get("id") or j, None)
                        bloque = True
                        log(f"[{titre}] YouTube demande de confirmer qu'on n'est pas un robot : "
                            f"arrêt ({i + 1 - len(robot)}/{len(entrees)} traitées). Ajoute un fichier "
                            f"cookies.txt dans le dossier config pour débloquer.")
                        if suivi:
                            suivi.maj(erreurs=len(echecs), etat="Bloqué par YouTube (anti-robot)")
                        break
                    i += 1
                    continue
                robot = []
                if BLOCAGE_SEUIL and len(serie) >= BLOCAGE_SEUIL:
                    # Plusieurs échecs d'affilée : YouTube a sans doute bloqué la session.
                    # 1er recours : nouvelle session et on réessaie tout de suite.
                    # Si ça échoue encore avec une session neuve : courte pause.
                    if not session_neuve:
                        nouvelle_session(f"{len(serie)} échecs d'affilée")
                    elif pauses < BLOCAGE_MAX:
                        pauses += 1
                        reprise = datetime.now() + timedelta(minutes=BLOCAGE_PAUSE_MIN)
                        log(f"[{titre}] Toujours bloqué avec une nouvelle session : pause "
                            f"{pauses}/{BLOCAGE_MAX}, reprise à {reprise:%H:%M}.")
                        if suivi:
                            suivi.maj(etat=f"YouTube bloque : pause, reprise à {reprise:%H:%M}",
                                      vitesse=None, eta=None)
                        fin_pause = time.time() + BLOCAGE_PAUSE_MIN * 60
                        while time.time() < fin_pause and not (suivi and suivi.annule):
                            time.sleep(2)
                        nouvelle_session("après la pause")
                    else:
                        serie = []   # plus de pauses possibles : on saute simplement
                        i += 1
                        continue
                    # on réessaie les musiques de la série
                    for j in serie:
                        echecs.pop(entrees[j].get("id") or j, None)
                    if suivi:
                        suivi.maj(erreurs=len(echecs))
                    reessai_jusqua = serie[-1]
                    i = serie[0]
                    serie = []
                    continue
            else:
                serie = []
                robot = []
                index_ok.add(i)
                if entree.get("id") in telechargees:
                    session_neuve = False
            i += 1
        index_echecs = [j for j, e in enumerate(entrees) if (e.get("id") or j) in echecs]
        echecs = list(echecs.values())
    finally:
        fermer(ydl)
    telechargees.discard(None)
    if annule:
        nettoyer_annulation(dossier)
        log(f"[{titre}] annulé depuis Discord ({len(telechargees)} musique(s) déjà téléchargée(s)).")
    if suivi:
        suivi.maj(faites=len(entrees), video_frac=0.0, etat="Annulé" if annule else "Terminé")

    if echecs:
        log(f"[{titre}] {len(echecs)} musique(s) en erreur, sautée(s) :")
        for v in echecs:
            print("  -", v, flush=True)
    if ignorees:
        log(f"[{titre}] {len(ignorees)} vidéo(s) ignorée(s) (autre chaîne) :")
        for v in ignorees.values():
            print("  -", v, flush=True)
    resume = {
        "titre": titre,
        "dossier": dossier,
        "total": len(entrees),
        "telechargees": len(telechargees),
        "ignorees": len(ignorees),
        "erreurs": len(echecs),
        "echecs": echecs,
        "index_echecs": index_echecs,
        "bloque": bloque,
        "annule": annule,
        "index_ok": sorted(index_ok),
    }
    log(f"[{titre}] terminé : {resume['telechargees']} téléchargée(s), "
        f"{resume['ignorees']} ignorée(s), {resume['erreurs']} erreur(s).")
    return resume


# ---------------------------------------------------------------------------
# Mode ligne de commande (playlists.txt)
# ---------------------------------------------------------------------------
def traiter(url):
    try:
        telecharger(url)
    except Exception as e:  # noqa: BLE001
        log(f"Erreur sur {url} : {e}")


def lire_playlists():
    urls = []
    if os.environ.get("PLAYLISTS"):
        urls += re.split(r"[\s,]+", os.environ["PLAYLISTS"])
    if os.path.isfile(FICHIER_PLAYLISTS):
        with open(FICHIER_PLAYLISTS, encoding="utf-8") as f:
            urls += [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
    return list(dict.fromkeys(u for u in urls if u))   # sans doublons, ordre conservé


def une_passe():
    urls = lire_playlists()
    if not urls:
        log(f"Aucune playlist trouvée. Ajoute des URL dans {FICHIER_PLAYLISTS}.")
        return
    log(f"{len(urls)} lien(s) à traiter, format {FORMAT}, destination {DOSSIER}")
    with ThreadPoolExecutor(max_workers=PLAYLISTS_EN_PARALLELE) as pool:
        list(pool.map(traiter, urls))
    log("Passe terminée.")


if __name__ == "__main__":
    if FORMAT not in ("m4a", "mp3", "mp4"):
        sys.exit(f"FORMAT invalide : {FORMAT} (attendu : m4a, mp3 ou mp4)")
    verifier_dossier()

    while True:
        une_passe()
        if INTERVALLE_HEURES <= 0:
            break
        log(f"Prochaine vérification dans {INTERVALLE_HEURES} h.")
        time.sleep(INTERVALLE_HEURES * 3600)
