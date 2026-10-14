#!/bin/sh
# Mise à jour automatique au démarrage du conteneur.
# Le dossier du projet sur le NAS est monté sur /code : s'il s'agit d'un dépôt
# git, on récupère les nouveautés de la branche MAJ_BRANCHE puis on lance le
# code à jour. Tes fichiers (config/bot.conf, cookies.txt…) sont ignorés par
# git et ne sont jamais modifiés ; si un fichier suivi a été modifié à la main
# sur le NAS, la mise à jour est annulée plutôt que de l'écraser.
CODE=/code
BRANCHE="${MAJ_BRANCHE:-main}"
DEPOT="${MAJ_DEPOT:-https://github.com/Rycrem/musicobot.git}"
IMAGE=/usr/local/share/yt-music

if [ "${MAJ_AUTO:-1}" != "1" ] || [ "$(id -u)" != "0" ] || [ ! -f "$CODE/bot.py" ]; then
  exec /app/entrypoint.sh "$@"
fi

# git tourne avec le propriétaire du dossier du projet, pour que les fichiers
# restent modifiables depuis le NAS
PROPRIO="$(stat -c %u "$CODE")"
GROUPE="$(stat -c %g "$CODE")"
g() {
  setpriv --reuid="$PROPRIO" --regid="$GROUPE" --clear-groups \
    env HOME=/tmp git -c safe.directory='*' -C "$CODE" "$@"
}

echo "Mise à jour : branche $BRANCHE de $DEPOT"
if [ ! -d "$CODE/.git" ]; then
  # Dossier copié depuis un zip : on en fait un dépôt git sans toucher aux fichiers
  g init -q -b "$BRANCHE" && g remote add origin "$DEPOT" \
    && g fetch -q origin "$BRANCHE" && g reset -q "origin/$BRANCHE" \
    && g branch -q --set-upstream-to="origin/$BRANCHE" \
    || echo "Mise à jour : initialisation du dépôt impossible"
  # Fichiers du projet absents du dossier (ex. config/bot.conf.example) : on les remet
  g ls-files -d 2>/dev/null | while IFS= read -r f; do
    g checkout -q -- "$f" && echo "Mise à jour : fichier manquant remis : $f"
  done
fi

g remote set-url origin "$DEPOT" 2>/dev/null || g remote add origin "$DEPOT"
AVANT="$(g rev-parse --short HEAD 2>/dev/null)"
if ! g fetch -q origin "$BRANCHE"; then
  echo "Mise à jour : GitHub injoignable, on garde la version actuelle ($AVANT)"
elif g merge -q --ff-only "origin/$BRANCHE" >/tmp/maj.log 2>&1; then
  APRES="$(g rev-parse --short HEAD)"
  if [ "$AVANT" = "$APRES" ]; then
    echo "Mise à jour : déjà à jour ($APRES)"
  else
    echo "Mise à jour : $AVANT -> $APRES"
    g log --oneline "$AVANT..$APRES" 2>/dev/null | sed 's/^/  /'
  fi
else
  echo "Mise à jour ANNULÉE, rien n'a été écrasé :"
  sed 's/^/  /' /tmp/maj.log
  echo "  Mets tes modifications dans la branche $BRANCHE sur GitHub, ou annule-les sur le NAS."
fi
MODIFS="$(g status --porcelain --untracked-files=no 2>/dev/null)"
[ -n "$MODIFS" ] && printf 'Fichiers modifiés à la main sur le NAS :\n%s\n' "$MODIFS"

# Code à jour copié dans l'image (droits lisibles quel que soit l'utilisateur)
for f in "$CODE"/*.py "$CODE/entrypoint.sh" "$CODE/config/bot.conf.example"; do
  [ -f "$f" ] && cp "$f" /app/
done
sed -i 's/\r$//' /app/entrypoint.sh
chmod 755 /app/entrypoint.sh && chmod 644 /app/*.py /app/bot.conf.example

# Nouvelles versions de yt-dlp / discord.py : installées sans reconstruire
if [ -f "$CODE/requirements.txt" ] && ! cmp -s "$CODE/requirements.txt" "$IMAGE/requirements.txt"; then
  echo "Mise à jour : installation des nouvelles versions de requirements.txt"
  if pip install -q --no-cache-dir --root-user-action=ignore -r "$CODE/requirements.txt"; then
    cp "$CODE/requirements.txt" "$IMAGE/requirements.txt"
  else
    echo "Mise à jour : échec de pip, on garde les versions actuelles"
  fi
fi

for f in Dockerfile maj-code.sh; do
  if [ -f "$CODE/$f" ] && ! cmp -s "$CODE/$f" "$IMAGE/$f"; then
    echo "ATTENTION : $f a changé, reconstruis le projet pour l'appliquer (le bot démarre quand même)."
  fi
done

exec /app/entrypoint.sh "$@"
