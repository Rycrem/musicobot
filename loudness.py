"""
Lissage du volume : ramène chaque musique au même niveau sonore (norme EBU R128, mesure ffmpeg « ebur128 »).

Les audios de YouTube n'ont pas tous le même volume. Une première lecture mesure le niveau du fichier, puis un simple gain
l'amène à la cible, sans toucher à la dynamique ; un limiteur retient les pics sous -1,5 dB quand il faut monter le volume
(il ne rabote jamais de plus de 4 dB). Le fichier lissé porte un tag LISSAGE_LUFS : il n'est jamais retraité.
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
from mutagen.id3 import ID3, ID3NoHeaderError, TXXX
from mutagen.mp4 import MP4, MP4FreeForm

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"

CIBLE_DEFAUT = -14.0   # LUFS : le niveau de YouTube et Spotify
PIC_DEFAUT = -1.5      # dB (true peak) : marge contre l'écrêtage à la conversion
TOLERANCE = 1.0        # LU : en dessous, le fichier est considéré comme déjà au bon niveau
BITRATE = "192k"
EXTENSIONS = (".m4a", ".mp3")
LIMITEUR_MAX = 4.0   # dB : de combien le limiteur peut rogner les pics pour atteindre la cible
MARGE_ENCODAGE = 1.0   # dB de marge sous le plafond pour le réencodage
PIC_MAX_FINAL = -0.3   # pic d'échantillon maximal accepté après réencodage (dBFS)
MARQUEUR = "LISSAGE_LUFS"   # tag laissé dans le fichier lissé : il n'est jamais retraité à la même cible


@dataclass
class Resultat:
    statut: str          # a_lisser | lisse | deja | ignore | echec
    avant: float | None = None   # LUFS mesurés avant
    apres: float | None = None   # LUFS mesurés après (si fichier réécrit)
    message: str = ""


def _executer(args, timeout=900):
    return subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-y", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def mesurer(chemin):
    """Niveau du fichier : {"i": loudness intégré en LUFS, "peak": pic d'échantillon en dBFS}, ou None si illisible
    ou silencieux. Filtre ebur128 : une simple lecture du fichier, bien plus légère que loudnorm (qui suréchantillonne
    à 192 kHz et ralentit beaucoup un NAS)."""
    r = _executer(["-i", chemin, "-vn", "-af", "ebur128=peak=sample:framelog=quiet", "-f", "null", "-"])
    if r.returncode != 0:
        return None
    resume = r.stderr[r.stderr.rfind("Summary:"):] if "Summary:" in r.stderr else ""
    i = re.search(r"\bI:\s+(-?[\d.]+|-inf)\s+LUFS", resume)
    pic = re.findall(r"\bPeak:\s+(-?[\d.]+|-inf)\s+dBFS", resume)
    if not i or not pic:
        return None
    try:
        niveau, crete = float(i.group(1)), float(pic[-1])
    except ValueError:
        return None
    if niveau == float("-inf") or niveau <= -70:
        return None
    return {"i": niveau, "peak": crete}


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


def _copier_proprietaire(source, destination):
    """Le fichier lissé garde le propriétaire et le groupe de l'original (utile quand le script tourne en root,
    par exemple depuis le terminal du conteneur : sinon le partage SMB du NAS ne pourrait plus le modifier)."""
    try:
        st = os.stat(source)
        os.chown(destination, st.st_uid, st.st_gid)
    except OSError:
        pass   # pas root : le fichier appartient déjà au bon utilisateur


def gain_pour(m, cible, pic):
    """Gain (dB) qui amène [m] à la cible. Si les pics le permettent il n'y a rien à rogner ; sinon un limiteur
    rabote les pics, mais d'au plus [LIMITEUR_MAX] dB (au-delà, on s'arrête avant la cible plutôt que d'écraser)."""
    marge = (pic - 0.5) - m["peak"]
    return min(m["i"] * -1 + cible, marge + LIMITEUR_MAX, 20.0)


def _marquer(chemin, cible):
    """Note dans le fichier la cible à laquelle il a été lissé."""
    valeur = f"{cible:g}"
    if chemin.lower().endswith(".mp3"):
        try:
            t = ID3(chemin)
        except ID3NoHeaderError:
            t = ID3()
        t.setall("TXXX:" + MARQUEUR, [TXXX(encoding=3, desc=MARQUEUR, text=valeur)])
        t.save(chemin, v2_version=3)
    else:
        f = MP4(chemin)
        if f.tags is None:
            f.add_tags()
        f.tags["----:com.apple.iTunes:" + MARQUEUR] = [MP4FreeForm(valeur.encode())]
        f.save()


def marque(chemin):
    """Cible à laquelle le fichier a déjà été lissé, ou None."""
    try:
        if chemin.lower().endswith(".mp3"):
            return float(str(ID3(chemin)["TXXX:" + MARQUEUR]))
        return float(bytes(MP4(chemin).tags["----:com.apple.iTunes:" + MARQUEUR][0]).decode())
    except Exception:  # noqa: BLE001 — pas de marqueur, ou fichier illisible
        return None


def lisser(chemin, cible=CIBLE_DEFAUT, pic=PIC_DEFAUT, appliquer=True):
    """Lisse le volume de [chemin]. Avec appliquer=False, ne fait que mesurer (essai à blanc)."""
    ext = os.path.splitext(chemin)[1].lower()
    if ext not in EXTENSIONS:
        return Resultat("ignore", message=f"format {ext or '?'} non géré")
    deja = marque(chemin)
    if deja is not None and abs(deja - cible) < 0.05:
        return Resultat("deja", message="déjà lissé")   # sans même mesurer : le rattrapage d'une bibliothèque est très rapide
    try:
        m = mesurer(chemin)
    except subprocess.TimeoutExpired:
        return Resultat("echec", message="mesure trop longue")
    if m is None:
        return Resultat("ignore", message="illisible ou silencieux")
    avant = m["i"]
    gain = gain_pour(m, cible, pic)
    if abs(gain) <= TOLERANCE:   # déjà au bon niveau (ou impossible de monter plus sans écrêter)
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
        codec = ["-c:a", "libmp3lame"] if ext == ".mp3" else ["-c:a", "aac"]
        filtre = f"volume={gain:.2f}dB"
        # Limiteur toujours actif : il retient les pics (même quand on baisse le volume, car certains titres très
        # compressés dépassent 0 dBFS une fois décodés) un cran sous le plafond, pour laisser de la marge au
        # réencodage AAC/MP3, qui peut ajouter ~1 dB de dépassement.
        filtre += f",alimiter=limit={10 ** ((pic - MARGE_ENCODAGE) / 20):.3f}:attack=3:release=40:level=0"
        r = _executer(["-i", chemin, "-vn", "-map", "0:a:0", "-map_metadata", "-1", "-af", filtre,
                       "-ar", str(freq), *codec, "-b:a", BITRATE, tmp])
        if r.returncode != 0:
            return Resultat("echec", avant=avant, message="ffmpeg a échoué : " + (r.stderr.strip().splitlines() or ["?"])[-1])
        _copier_tags(chemin, tmp)
        _marquer(tmp, cible)
        # Contrôles avant de remplacer l'original : durée identique, niveau atteint.
        nouvelle = FichierAudio(tmp).info.length
        if abs(nouvelle - duree) > max(1.5, duree * 0.01):
            return Resultat("echec", avant=avant, message=f"durée anormale ({nouvelle:.1f} s au lieu de {duree:.1f} s)")
        controle = mesurer(tmp)
        if controle is None:
            return Resultat("echec", avant=avant, message="résultat illisible")
        apres = controle["i"]
        # Le limiteur peut retenir un peu de volume (au plus LIMITEUR_MAX) ; au-delà, ou au-dessus, quelque chose cloche.
        attendu = avant + gain
        if not (attendu - (LIMITEUR_MAX if gain > 0 else 0) - 0.5 <= apres <= attendu + 1.5):
            return Resultat("echec", avant=avant, apres=apres, message=f"niveau final {apres:.1f} LUFS au lieu de {avant + gain:.1f}")
        if controle["peak"] > PIC_MAX_FINAL:   # saturation franche : jamais
            return Resultat("echec", avant=avant, apres=apres, message=f"pic final {controle['peak']:.1f} dBFS au-dessus du plafond")
        shutil.copymode(chemin, tmp)
        _copier_proprietaire(chemin, tmp)
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
