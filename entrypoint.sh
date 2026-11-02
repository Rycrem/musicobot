#!/bin/sh
# Lance le programme avec le même propriétaire que le dossier music du NAS,
# pour que les fichiers téléchargés t'appartiennent (pas besoin de connaître ton UID).
# PUID / PGID dans docker-compose.yml permettent de forcer une valeur.
echo "yt-music : image version 12"
if [ "$(id -u)" = "0" ]; then
  # Rend le code lisible quel que soit l'utilisateur (permissions héritées du NAS)
  chmod -R a+rX /app 2>/dev/null
  PUID="${PUID:-$(stat -c %u /music 2>/dev/null)}"
  PGID="${PGID:-$(stat -c %g /music 2>/dev/null)}"
  echo "Propriétaire du dossier music : UID=${PUID} GID=${PGID}"
  if setpriv --reuid="${PUID:-0}" --regid="${PGID:-0}" --clear-groups deno --version >/dev/null 2>&1; then
    echo "Deno OK (déchiffrement YouTube disponible)"
  else
    echo "ATTENTION : Deno ne fonctionne pas, YouTube risque de renvoyer des erreurs 403."
  fi
  if [ -n "$PUID" ] && [ "$PUID" != "0" ]; then
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
  fi
  echo "ATTENTION : dossier music appartenant à root, les fichiers seront créés par root."
else
  echo "Conteneur lancé sans root (UID $(id -u)) : retire la ligne 'user:' du docker-compose.yml."
fi
exec "$@"
