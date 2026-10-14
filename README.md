# Bot musique pour NAS UGREEN

Bot Discord qui télécharge de la musique depuis YouTube directement sur un NAS (UGREEN ou tout NAS avec Docker), dans le dossier `music`.
Tout se pilote avec des commandes Discord ; le bot tourne dans Docker en permanence et redémarre avec le NAS.

> Guide complet pas à pas (installation, Discord, dépannage) : **[Guide-bot-musique.pdf](Guide-bot-musique.pdf)**

## Fonctionnalités

- **`/add <lien>`** : télécharge une vidéo ou une playlist YouTube entière (audio m4a avec titre, artiste et pochette intégrés).
- **`/artiste <nom> [nombre]`** : toute la discographie d'un artiste (albums, EP, singles, sans doublons), ou ses N titres les plus populaires.
- **`/top`** : top 10 du jour France, USA et Monde (classements Deezer), aussi téléchargé automatiquement chaque soir.
- **`/suivre <lien>`** : suit une playlist ; ses nouvelles musiques sont téléchargées automatiquement (vérification toutes les 6 h).
- **`/update`** : avancement en direct (pourcentage global, musique en cours, vitesse, file d'attente).
- Musiques déjà présentes sautées, erreurs sautées sans arrêter la playlist.
- Gestion des limites de YouTube : nouvelle session automatique, pause et reprise en cas de blocage anti-robot, prise en charge d'un `cookies.txt`.
- Discret : toutes les commandes et réponses ne sont visibles que par la personne qui les utilise.

## Installation rapide

1. **Créer le bot Discord** sur [discord.com/developers/applications](https://discord.com/developers/applications) : *New Application* → onglet *Bot* → *Reset Token* (copie le token).
   Invite-le avec *OAuth2 → URL Generator* : scopes `bot` + `applications.commands`, permissions *Send Messages* et *Embed Links*.
2. **Récupérer le projet** sur le NAS (dans un dossier, par ex. `docker/yt-music`) :
   ```bash
   git clone https://github.com/Rycrem/musicobot.git yt-music
   ```
   ou télécharge le zip du repo et copie le dossier sur le NAS.
3. **Adapter `docker-compose.yml`** : le chemin du dossier music du NAS (partie gauche) :
   ```yaml
   volumes:
     - /volume1/partage/music:/music   # ← ton chemin
     - ./config:/config
   ```
4. **Lancer** : application Docker d'UGOS → *Projet → Créer* → choisir le dossier `yt-music` (ou `docker compose up -d --build` en SSH).
5. **Configurer** : au premier lancement, le bot crée `config/bot.conf` à partir de [`config/bot.conf.example`](config/bot.conf.example).
   Remplis au minimum `DISCORD_TOKEN` (et `GUILD_ID` pour que les commandes apparaissent tout de suite), puis redémarre le conteneur.
6. Dans Discord, tape **`/help`**.

Journaux attendus au démarrage :

```
yt-music : image version 10
Propriétaire du dossier music : UID=1000 GID=10
Deno OK (déchiffrement YouTube disponible)
Connexion à Discord…
Bot connecté : MonBot#1234 — dossier de destination /music, format m4a
```

## Configuration

| Fichier | Contenu |
|---|---|
| `config/bot.conf` | Token Discord, ID du serveur, personnes et salons autorisés, salon d'annonce du top. **Jamais versionné.** |
| `docker-compose.yml` | Chemin du dossier music et réglages : format (`m4a`/`mp3`/`mp4`), heure du top (`TOP_HEURE`), fréquence de vérification des playlists (`VERIF_HEURES`)… |
| `config/cookies.txt` | Optionnel : cookies d'un compte YouTube si YouTube bloque le NAS (« Sign in to confirm you're not a bot »). **Jamais versionné.** |

Les fichiers de `config/` qui contiennent des secrets ou des données locales sont exclus par le [`.gitignore`](.gitignore).

## Organisation des musiques

```
music/
├── <Nom de la playlist>/   /add (playlist) et /suivre
├── Titres/                 /add (vidéo seule)
├── Artistes/<Nom>/         /artiste
└── Top du jour/            /top et top automatique
```

## Structure du projet

| Fichier | Rôle |
|---|---|
| `bot.py` | Bot Discord : commandes, file d'attente, suivi en direct, tâches planifiées |
| `playlist.py` | Moteur de téléchargement (yt-dlp) : reprise, sessions, gestion des blocages |
| `top.py` | Top du jour et discographies via l'API publique de Deezer, recherche sur YouTube |
| `Dockerfile` | Image Python 3.12 + ffmpeg + Deno + yt-dlp + discord.py |
| `entrypoint.sh` | Démarrage avec le propriétaire du dossier music (droits des fichiers) |

## Mise à jour

Après un `git pull` (ou le remplacement de fichiers `.py`, `Dockerfile`, `entrypoint.sh`), il faut **reconstruire** l'image :

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

Les versions de yt-dlp, discord.py et Deno sont figées dans le `Dockerfile`. Pour mettre yt-dlp à jour (par exemple quand YouTube renvoie des erreurs), change `YTDLP_VERSION` dans le `Dockerfile`, ou sans le modifier :

```bash
docker compose build --no-cache --build-arg YTDLP_VERSION=2026.X.Y   # numéro pris sur pypi.org/project/yt-dlp
```

Une modification de `config/bot.conf` ou de `docker-compose.yml` demande seulement un redémarrage.

## Avertissement

Projet à usage personnel. Respecte les conditions d'utilisation de YouTube et les droits d'auteur : ne télécharge que des contenus que tu as le droit de conserver.
