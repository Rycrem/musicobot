FROM python:3.12-slim

# ffmpeg pour l'extraction audio / la fusion vidéo
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# Deno : runtime JavaScript désormais requis par yt-dlp pour YouTube
COPY --from=denoland/deno:bin-2.9.7 /deno /usr/local/bin/deno

# Versions figées pour des builds reproductibles. Si YouTube casse yt-dlp,
# monter YTDLP_VERSION (https://pypi.org/project/yt-dlp/) puis reconstruire.
ARG YTDLP_VERSION=2026.8.19
ARG DISCORDPY_VERSION=2.7.1
RUN pip install --no-cache-dir "yt-dlp[default]==${YTDLP_VERSION}" "discord.py==${DISCORDPY_VERSION}"

# Dossier personnel inscriptible par n'importe quel utilisateur : Deno et yt-dlp
# y mettent leur cache (sinon Deno échoue et YouTube renvoie des erreurs 403)
ENV PYTHONUNBUFFERED=1 \
    HOME=/home/app \
    DENO_DIR=/home/app/.cache/deno
RUN mkdir -p /home/app/.cache && chmod -R 1777 /home/app
WORKDIR /app
COPY playlist.py bot.py top.py entrypoint.sh config/bot.conf.example ./
# Les fichiers copiés depuis le NAS gardent ses permissions (souvent illisibles
# pour l'utilisateur du dossier music) : on les rend lisibles par tous.
RUN sed -i 's/\r$//' entrypoint.sh \
 && chmod 755 /app entrypoint.sh \
 && chmod 644 playlist.py bot.py top.py bot.conf.example

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "bot.py"]
