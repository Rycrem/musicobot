"""Signale à l'indexeur du NAS les musiques ajoutées à la bibliothèque.

Le bot écrit directement dans le dossier music : Audio Station ne le voit qu'à sa réindexation planifiée
(minuit). Un conteneur ne peut pas appeler `synoindex` : il note donc chaque nouveau fichier dans une liste
(FICHIER_INDEX, dans /config) que la tâche planifiée DSM `synology/indexer.sh` vide chaque minute."""
import os
import threading

FICHIER_INDEX = os.environ.get("FICHIER_INDEX", "/config/a_indexer.txt")
_verrou = threading.Lock()


def signaler(chemin):
    """Ajoute `chemin` (chemin dans le conteneur) à la liste des fichiers à indexer. Sans effet si FICHIER_INDEX est vide.
    Ne lève jamais : une liste illisible ne doit pas faire échouer un téléchargement."""
    if not FICHIER_INDEX or not chemin:
        return
    try:
        with _verrou, open(FICHIER_INDEX, "a", encoding="utf-8") as f:
            f.write(os.path.abspath(chemin) + "\n")
    except OSError:
        pass
