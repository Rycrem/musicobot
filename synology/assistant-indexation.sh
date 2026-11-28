#!/bin/sh
# Assistant interactif, à lancer DEPUIS TON MAC (dans un terminal) : vérifie le NAS en SSH,
# teste l'indexation Audio Station et te donne la commande exacte du Planificateur de tâches DSM.
# Il ne modifie rien sur le NAS sans te le demander. Usage : sh synology/assistant-indexation.sh
#
# Prérequis : SSH activé sur le NAS (DSM > Panneau de configuration > Terminal et SNMP).

demander() {   # demander "question" "défaut"  ->  $REPONSE
    if [ -n "$2" ]; then printf '%s [%s] : ' "$1" "$2"; else printf '%s : ' "$1"; fi
    read -r saisie
    REPONSE=${saisie:-$2}
}
oui() {        # oui "question"  ->  vrai si o / oui
    printf '%s [o/N] : ' "$1"
    read -r saisie
    case "$saisie" in o|O|oui|Oui|y|Y) return 0 ;; *) return 1 ;; esac
}
etape() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()    { printf '  \033[32m✔\033[0m %s\n' "$1"; }
ko()    { printf '  \033[31m✘\033[0m %s\n' "$1"; }
attendre() { printf '\n(Entrée pour continuer) '; read -r _; }

etape "1/6  Connexion au NAS"
demander "Adresse du NAS (IP ou nom, ex. 192.168.1.20)" ""
HOTE=$REPONSE
[ -n "$HOTE" ] || { echo "Adresse obligatoire."; exit 1; }
demander "Identifiant DSM" "Maxime"
UTIL=$REPONSE
demander "Port SSH" "22"
PORT=$REPONSE

SOCKET="${TMPDIR:-/tmp}/nas-indexation-$$"
nas() { ssh -p "$PORT" -o ControlMaster=auto -o ControlPath="$SOCKET" -o ControlPersist=300 "$UTIL@$HOTE" "$@"; }
nas_tty() { ssh -t -p "$PORT" -o ControlMaster=auto -o ControlPath="$SOCKET" -o ControlPersist=300 "$UTIL@$HOTE" "$@"; }
fin() { ssh -O exit -o ControlPath="$SOCKET" "$UTIL@$HOTE" 2>/dev/null; }
trap fin EXIT

echo "Connexion (ton mot de passe DSM sera demandé une seule fois)…"
if ! nas "echo connecté sur \$(hostname)"; then
    ko "Connexion impossible : SSH est-il activé sur le NAS, adresse et port exacts ?"
    exit 1
fi

etape "2/6  Dossiers"
demander "Dossier du projet yt-music sur le NAS" "/volume1/homes/$UTIL/docker/yt-music"
PROJET=$REPONSE
demander "Dossier de la musique sur le NAS" "/volume1/music"
MUSIC=$REPONSE
LISTE="$PROJET/config/a_indexer.txt"
SCRIPT="$PROJET/synology/indexer.sh"

verifier() {   # verifier "description" "test shell distant" "conseil si absent"
    if nas "$2" >/dev/null 2>&1; then ok "$1"; return 0; else ko "$1 — $3"; return 1; fi
}
verifier "Dossier du projet : $PROJET" "test -d '$PROJET'" "mauvais chemin ?" || exit 1
verifier "Dossier config du bot" "test -d '$PROJET/config'" "le bot a-t-il déjà démarré une fois ?"
verifier "Dossier de la musique : $MUSIC" "test -d '$MUSIC'" "mauvais chemin ? (ls /volume1)"
verifier "Script d'indexation : $SCRIPT" "test -f '$SCRIPT'" "redémarre le conteneur yt-music pour qu'il récupère la dernière version, puis relance l'assistant"
verifier "synoindex disponible" "test -x /usr/syno/bin/synoindex" "ce NAS n'est pas un Synology ?"

etape "3/6  Version du bot"
nas "cd '$PROJET' && git log --oneline -1 2>/dev/null" || ko "git illisible dans $PROJET"
echo "  (la liste de fichiers à indexer n'existe que depuis la PR #25 « Indexation immédiate »)"

etape "4/6  Liste des fichiers à indexer"
if nas "test -s '$LISTE'"; then
    echo "  $LISTE contient :"
    nas "tail -5 '$LISTE'" | sed 's/^/    /'
    echo "  (elle se vide à chaque passage de la tâche planifiée)"
elif nas "test -e '$LISTE'"; then
    ok "La liste existe et est vide : tout a été indexé, ou aucun téléchargement depuis."
else
    echo "  Pas de liste pour l'instant : normal tant qu'aucun titre n'a été téléchargé depuis la mise à jour."
fi

etape "5/6  Test de l'indexation (facultatif)"
echo "  Indique un fichier musique du NAS qui n'apparaît pas encore dans l'app (chemin complet vu du NAS,"
echo "  ex. $MUSIC/Artiste/Album/01 - Titre.m4a). Ta session demandera ton mot de passe pour sudo."
demander "Fichier à indexer (vide = passer)" ""
if [ -n "$REPONSE" ]; then
    FICHIER=$REPONSE
    if nas "test -f '$FICHIER'"; then
        if nas_tty "sudo /usr/syno/bin/synoindex -a '$FICHIER'; echo \"code retour synoindex : \$?\""; then
            echo "  → Attends ~30 s puis cherche ce titre dans Sillon. Il apparaît ? synoindex -a suffit."
        fi
    else
        ko "Fichier introuvable sur le NAS : $FICHIER"
    fi
fi

etape "6/6  Tâche planifiée DSM"
VARIABLES=""   # le script a des chemins par défaut : on ne les précise que s'ils diffèrent
[ "$MUSIC" = "/volume1/music" ] || VARIABLES="MUSIC=$MUSIC"
[ "$LISTE" = "/volume1/homes/Maxime/docker/yt-music/config/a_indexer.txt" ] || VARIABLES="LISTE=$LISTE $VARIABLES"
CMD="${VARIABLES:+$VARIABLES }sh $SCRIPT"
cat <<EOF

  Dans DSM : Panneau de configuration > Planificateur de tâches > Créer > Tâche planifiée > Script défini par l'utilisateur

    Général      : nom « Indexation musique », utilisateur root, coché « Activé »
    Calendrier   : Quotidien, début 00:00, toutes les 1 minute(s), dernière exécution 23:59
    Paramètres   : Exécuter la commande :

      $CMD

EOF
if oui "Lancer cette commande maintenant sur le NAS (sudo) pour vérifier qu'elle passe ?"; then
    nas_tty "sudo env LISTE='$LISTE' MUSIC='$MUSIC' sh '$SCRIPT'; echo \"code retour : \$?\""
fi
echo
echo "Terminé. Ensuite : télécharge un titre avec l'app, patiente ~1 minute, il doit apparaître."
