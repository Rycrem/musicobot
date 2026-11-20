#!/usr/bin/env python3
"""
Lisse le volume de toute une bibliothèque déjà présente sur le NAS (voir loudness.py).

Par défaut c'est un ESSAI À BLANC : rien n'est modifié, le script liste ce qu'il changerait. Ajoute --appliquer
pour réécrire les fichiers. Relançable à volonté : les fichiers déjà au bon niveau sont sautés, donc on peut
l'interrompre (Ctrl+C) et reprendre plus tard.

Sur le NAS (le conteneur a le dossier music monté sur /music) :
    docker compose run --rm playlists python normaliser_bibliotheque.py                 # essai à blanc
    docker compose run --rm playlists python normaliser_bibliotheque.py --appliquer     # pour de bon

Options : --cible -14 (LUFS) · --pic -1.5 (dB) · --limite N (s'arrête après N fichiers à traiter) ·
          --sauvegarde DOSSIER (garde une copie de chaque original avant de le remplacer) ·
          --dossier /music · --parallele 2
"""
import argparse
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import loudness


def fichiers(racine):
    for dossier, sous, noms in os.walk(racine):
        sous[:] = sorted(d for d in sous if not d.startswith("."))   # .telechargement, caches...
        for nom in sorted(noms):
            if not nom.startswith(".") and nom.lower().endswith(loudness.EXTENSIONS):
                yield os.path.join(dossier, nom)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dossier", default=os.environ.get("DOSSIER", "/music"))
    p.add_argument("--cible", type=float, default=loudness.CIBLE_DEFAUT, help="niveau visé en LUFS (défaut -14)")
    p.add_argument("--pic", type=float, default=loudness.PIC_DEFAUT, help="pic maximal en dB (défaut -1.5)")
    p.add_argument("--appliquer", action="store_true", help="réécrit les fichiers (sinon : essai à blanc)")
    p.add_argument("--limite", type=int, default=0, help="s'arrête après ~N fichiers à traiter (0 = tous)")
    p.add_argument("--sauvegarde", help="dossier où copier chaque original avant de le remplacer")
    p.add_argument("--parallele", type=int, default=2, help="fichiers traités en même temps (défaut 2)")
    a = p.parse_args(argv)

    if not os.path.isdir(a.dossier):
        print(f"Dossier introuvable : {a.dossier}")
        return 2
    mode = "RÉÉCRITURE" if a.appliquer else "ESSAI À BLANC (rien n'est modifié ; --appliquer pour réécrire)"
    print(f"{mode} : {a.dossier}, cible {a.cible} LUFS, pic {a.pic} dB")

    verrou = threading.Lock()
    arret = threading.Event()
    compte = {"lisse": 0, "a_lisser": 0, "deja": 0, "ignore": 0, "echec": 0}
    traites = 0

    def traiter(chemin):
        nonlocal traites
        if arret.is_set():   # limite atteinte : on ne commence plus de fichier
            return chemin, None
        if a.appliquer and a.sauvegarde:
            rel = os.path.relpath(chemin, a.dossier)
            copie = os.path.join(a.sauvegarde, rel)
            os.makedirs(os.path.dirname(copie), exist_ok=True)
            if not os.path.exists(copie):
                shutil.copy2(chemin, copie)
        r = loudness.lisser(chemin, a.cible, a.pic, appliquer=a.appliquer)
        if r.statut in ("lisse", "a_lisser"):
            with verrou:
                traites += 1
                if a.limite and traites >= a.limite:
                    arret.set()   # avec plusieurs fichiers en parallèle, quelques-uns de plus peuvent déjà tourner
        return chemin, r

    tous = list(fichiers(a.dossier))
    print(f"{len(tous)} fichier(s) audio")
    try:
        with ThreadPoolExecutor(max_workers=max(1, a.parallele)) as pool:
            attente = []
            for chemin in tous:
                attente.append(pool.submit(traiter, chemin))
            for futur in as_completed(attente):
                chemin, r = futur.result()
                if r is None:
                    continue
                rel = os.path.relpath(chemin, a.dossier)
                with verrou:
                    compte[r.statut] += 1
                    if r.statut in ("lisse", "a_lisser"):
                        fin = f" -> {r.apres:.1f}" if r.apres is not None else ""
                        print(f"  {'lissé  ' if r.statut == 'lisse' else 'à lisser'} {r.avant:6.1f} LUFS{fin}  {rel}")
                    elif r.statut == "echec":
                        print(f"  ÉCHEC   {rel} : {r.message}")
    except KeyboardInterrupt:
        print("\nInterrompu : les fichiers déjà lissés le restent, relance pour continuer.")
    print(f"Terminé : {compte['lisse']} lissé(s), {compte['a_lisser']} à lisser, {compte['deja']} déjà au bon niveau, "
          f"{compte['ignore']} ignoré(s), {compte['echec']} échec(s).")
    return 1 if compte["echec"] else 0


if __name__ == "__main__":
    sys.exit(main())
