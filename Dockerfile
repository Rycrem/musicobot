FROM python:3.12-slim

# ffmpeg pour l'extraction audio / la fusion vidéo, git pour la mise à jour au démarrage
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata git \
 && rm -rf /var/lib/apt/lists/*

# Deno : runtime JavaScript désormais requis par yt-dlp pour YouTube
COPY --from=denoland/deno:bin-2.9.7 /deno /usr/local/bin/deno

# Versions de yt-dlp et discord.py : voir requirements.txt
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Dossier personnel inscriptible par n'importe quel utilisateur : Deno et yt-dlp
# y mettent leur cache (sinon Deno échoue et YouTube renvoie des erreurs 403)
ENV PYTHONUNBUFFERED=1 \
    HOME=/home/app \
    DENO_DIR=/home/app/.cache/deno
RUN mkdir -p /home/app/.cache && chmod -R 1777 /home/app
WORKDIR /app
COPY playlist.py bot.py top.py bibliotheque.py entrypoint.sh config/bot.conf.example ./
# Les fichiers copiés depuis le NAS gardent ses permissions (souvent illisibles
# pour l'utilisateur du dossier music) : on les rend lisibles par tous.
RUN sed -i 's/\r$//' entrypoint.sh \
 && chmod 755 /app entrypoint.sh \
 && chmod 644 playlist.py bot.py top.py bibliotheque.py bot.conf.example

# Copie de référence : maj-code.sh détecte ce qui a changé depuis la construction
COPY requirements.txt Dockerfile maj-code.sh /usr/local/share/yt-music/
RUN install -m 755 /usr/local/share/yt-music/maj-code.sh /usr/local/bin/maj-code.sh \
 && sed -i 's/\r$//' /usr/local/bin/maj-code.sh

ENTRYPOINT ["/usr/local/bin/maj-code.sh"]
CMD ["python", "bot.py"]
