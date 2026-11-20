"""
Lissage du volume : ramène chaque musique au même niveau sonore (norme EBU R128, ffmpeg « loudnorm »).

Les audios de YouTube n'ont pas tous le même volume. Deux passes : la première mesure le niveau du fichier, la
seconde applique un simple gain pour atteindre la cible, sans écraser la dynamique (mode linéaire, sans « pompage »).
Le fichier est réencodé une fois (AAC ou MP3 à 192 kb/s) ; titre, artiste, album, pochette et tous les autres tags
sont recopiés à l'identique.

Sûr par construction : le résultat est écrit à côté, contrôlé (durée, niveau), puis seulement alors il remplace
l'original. Au moindre doute l'original n'est pas touché. Idempotent : un fichier déjà au bon niveau est laissé tel quel.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from mutagen import File as FichierAudio
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp4 import MP4

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"

CIBLE_DEFAUT = -14.0   # LUFS : le niveau de YouTube et Spotify
PIC_DEFAUT = -1.5      # dB (true peak) : marge contre l'écrêtage à la conversion
LRA_DEFAUT = 11.0
TOLERANCE = 1.0        # LU : en dessous, le fichier est considéré comme déjà au bon niveau
BITRATE = "192k"
EXTENSIONS = (".m4a", ".mp3")


@dataclass
class Resultat:
    statut: str          # a_lisser | lisse | deja | ignore | echec
    avant: float | None = None   # LUFS mesurés avant
    apres: float | None = None   # LUFS mesurés après (si fichier réécrit)
    message: str = ""


def _executer(args, timeout=900):
    return subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-y", *args],
                          capture_output=True, text=True, timeout=timeout)


def mesurer(chemin, cible=CIBLE_DEFAUT, pic=PIC_DEFAUT, lra=LRA_DEFAUT):
    """Mesures loudnorm du fichier (dict de chaînes), ou None si illisible ou silencieux."""
    r = _executer(["-i", chemin, "-vn", "-af", f"loudnorm=I={cible}:TP={pic}:LRA={lra}:print_format=json",
                   "-f", "null", "-"])
    if r.returncode != 0:
        return None
    blocs = re.findall(r"\{[^{}]*\}", r.stderr)
    if not blocs:
        return None
    try:
        m = json.loads(blocs[-1])
        if float(m["input_i"]) == float("-inf"):
            return None
        float(m["input_tp"]), float(m["input_lra"]), float(m["input_thresh"]), float(m["target_offset"])
    except (ValueError, KeyError):
        return None
    return m


def _copier_tags(source, destination):
    """Recopie tous les tags (et la pochette) de [source] vers [destination], deux fichiers de même format."""
    ext = os.path.splitext(source)[1].lower()
    if ext == ".mp3":
        try:
            ID3(source).save(destination, v2_version=3)
        except ID3NoHeaderError:
            pass
    else:
        ancien = MP4(source)
        nouveau = MP4(destination)
        if nouveau.tags is None:
            nouveau.add_tags()
        for cle, valeur in (ancien.tags or {}).items():
            nouveau.tags[cle] = valeur
        nouveau.save()


def lisser(chemin, cible=CIBLE_DEFAUT, pic=PIC_DEFAUT, lra=LRA_DEFAUT, appliquer=True):
    """Lisse le volume de [chemin]. Avec appliquer=False, ne fait que mesurer (essai à blanc)."""
    ext = os.path.splitext(chemin)[1].lower()
    if ext not in EXTENSIONS:
        return Resultat("ignore", message=f"format {ext or '?'} non géré")
    try:
        m = mesurer(chemin, cible, pic, lra)
    except subprocess.TimeoutExpired:
        return Resultat("echec", message="mesure trop longue")
    if m is None:
        return Resultat("ignore", message="illisible ou silencieux")
    avant = float(m["input_i"])
    if abs(avant - cible) <= TOLERANCE and float(m["input_tp"]) <= pic + 0.5:
        return Resultat("deja", avant=avant)
    if not appliquer:
        return Resultat("a_lisser", avant=avant)

    try:
        info = FichierAudio(chemin).info
        duree, freq = info.length, int(getattr(info, "sample_rate", 44100) or 44100)
    except Exception as e:  # noqa: BLE001
        return Resultat("echec", avant=avant, message=f"lecture impossible : {e}")

    dossier = os.path.dirname(os.path.abspath(chemin))
    # Fichier temporaire à côté de l'original (même disque : le remplacement est atomique), caché du reste.
    fd, tmp = tempfile.mkstemp(prefix=".lissage-", suffix=ext, dir=dossier)
    os.close(fd)
    try:
        filtre = (f"loudnorm=I={cible}:TP={pic}:LRA={lra}:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
                  f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
                  f":offset={m['target_offset']}:linear=true")
        codec = ["-c:a", "libmp3lame"] if ext == ".mp3" else ["-c:a", "aac"]
        # loudnorm travaille à 192 kHz en interne : on remet la fréquence d'origine.
        r = _executer(["-i", chemin, "-vn", "-map", "0:a:0", "-map_metadata", "-1", "-af", filtre,
                       "-ar", str(freq), *codec, "-b:a", BITRATE, tmp])
        if r.returncode != 0:
            return Resultat("echec", avant=avant, message="ffmpeg a échoué : " + (r.stderr.strip().splitlines() or ["?"])[-1])
        _copier_tags(chemin, tmp)
        # Contrôles avant de remplacer l'original : durée identique, niveau atteint.
        nouvelle = FichierAudio(tmp).info.length
        if abs(nouvelle - duree) > max(1.5, duree * 0.01):
            return Resultat("echec", avant=avant, message=f"durée anormale ({nouvelle:.1f} s au lieu de {duree:.1f} s)")
        controle = mesurer(tmp, cible, pic, lra)
        if controle is None:
            return Resultat("echec", avant=avant, message="résultat illisible")
        apres = float(controle["input_i"])
        if abs(apres - cible) > 2.0:   # loudnorm n'a pas pu atteindre la cible (dynamique trop grande, par exemple)
            return Resultat("echec", avant=avant, apres=apres, message=f"niveau final {apres:.1f} LUFS, trop loin de la cible")
        shutil.copymode(chemin, tmp)
        os.replace(tmp, chemin)
        tmp = None
        return Resultat("lisse", avant=avant, apres=apres)
    except subprocess.TimeoutExpired:
        return Resultat("echec", avant=avant, message="encodage trop long")
    except Exception as e:  # noqa: BLE001 — l'original n'a pas été touché
        return Resultat("echec", avant=avant, message=str(e))
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
