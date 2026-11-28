#!/bin/sh
# Indexe dans Audio Station les musiques que le bot vient d'ajouter (voir indexation.py).
#
# À planifier dans DSM : Panneau de configuration > Planificateur de tâches > Créer >
# Tâche planifiée > Script défini par l'utilisateur, utilisateur « root », toutes les minutes,
# commande : sh /volume1/<chemin du projet>/synology/indexer.sh
#
# À ADAPTER si besoin (chemins vus depuis le NAS, pas depuis le conteneur) :
LISTE="${LISTE:-/volume1/homes/Maxime/docker/yt-music/config/a_indexer.txt}"   # = /config/a_indexer.txt du conteneur
MUSIC="${MUSIC:-/volume1/music}"                                               # = /music du conteneur

[ -s "$LISTE" ] || exit 0
lot="$LISTE.en_cours"
mv "$LISTE" "$lot" || exit 1   # le bot recrée la liste pour les fichiers suivants

sort -u "$lot" | while IFS= read -r fichier; do
    reel="$MUSIC/${fichier#/music/}"
    [ -f "$reel" ] && synoindex -a "$reel"
done
rm -f "$lot"
