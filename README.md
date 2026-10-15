# Bot musique pour NAS UGREEN

Bot Discord qui télécharge de la musique depuis YouTube directement sur un NAS (UGREEN ou tout NAS avec Docker), dans le dossier `music`.
Tout se pilote avec des commandes Discord ; le bot tourne dans Docker en permanence et redémarre avec le NAS.

> Guide complet pas à pas (installation, Discord, dépannage) : **[Guide-bot-musique.pdf](Guide-bot-musique.pdf)**

## Fonctionnalités

- **`/add <lien>`** : télécharge une vidéo ou une playlist YouTube entière (audio m4a avec titre, artiste et pochette intégrés).
- **`/artiste <nom> [nombre]`** : toute la discographie d'un artiste (albums, EP, singles, sans doublons), ou ses N titres les plus populaires. Le bot propose d'abord les artistes trouvés sur Deezer dans une liste (vrai nom pour les artistes solo, nombre d'albums, de titres et de fans) ; une fois le bon choisi, il affiche une fiche du téléchargement (visible de toi seul) avec la photo de l'artiste et un lien vers sa chaîne YouTube. Le bouton « Annuler le téléchargement » l'arrête ; si le dossier de l'artiste a été créé par ce téléchargement, il est supprimé. Le vrai nom et la chaîne YouTube viennent de MusicBrainz, quand l'artiste y est référencé.
- **`/top`** : top 10 du jour France, USA et Monde (classements Deezer), aussi téléchargé automatiquement chaque soir.
- **`/suivre <lien>`** : suit une playlist ; ses nouvelles musiques sont téléchargées automatiquement (vérification toutes les 6 h).
- **`/ranger`** : range les musiques déjà présentes par artiste et album (voir [Organisation des musiques](#organisation-des-musiques)).
- **`/update`** : avancement en direct (pourcentage global, musique en cours, vitesse, file d'attente) ; un bouton annule le téléchargement en cours et une liste permet de retirer un lien de la file d'attente.
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
   ou télécharge le zip du repo, décompresse-le et copie le dossier sur le NAS (le bot en fera un dépôt git au premier démarrage).
   Si tu reprends une ancienne installation, copie seulement tes fichiers (`bot.conf`, `cookies.txt`, `playlists_suivies.json`…) dans le nouveau dossier `config` : ne remplace pas le dossier entier, `config/bot.conf.example` est nécessaire à la construction.
3. **Adapter `docker-compose.yml`** : le chemin du dossier music du NAS (partie gauche) :
   ```yaml
   volumes:
     - /volume1/partage/music:/music   # ← ton chemin
     - ./config:/config
     - .:/code                         # ne pas toucher : sert à la mise à jour
   ```
4. **Lancer** : application Docker d'UGOS → *Projet → Créer* → choisir le dossier `yt-music` (ou `docker compose up -d --build` en SSH).
5. **Configurer** : au premier lancement, le bot crée `config/bot.conf` à partir de [`config/bot.conf.example`](config/bot.conf.example).
   Remplis au minimum `DISCORD_TOKEN` (et `GUILD_ID` pour que les commandes apparaissent tout de suite), puis redémarre le conteneur.
6. Dans Discord, tape **`/help`**.

Journaux attendus au démarrage :

```
Mise à jour : branche main de https://github.com/Rycrem/musicobot.git
Mise à jour : déjà à jour (a609f16)
yt-music : image version 11
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

Les musiques sont rangées par artiste et album ; seul le top du jour garde son dossier :

```
music/
├── <Artiste>/
│   ├── <Album>/
│   │   ├── 01 - <Titre>.m4a
│   │   └── 02 - <Titre>.m4a
│   └── <Titre d'un single>.m4a
├── Top du jour/            /top et top automatique : <Artiste> - <Titre>.m4a
└── Non classés/            musiques non reconnues sur Deezer
```

- Chaque musique est reconnue sur Deezer : directement pour `/top` et `/artiste`, d'après le titre de la vidéo pour `/add` et les playlists suivies. Ses tags sont réécrits (titre, artiste, artiste de l'album, album, numéro de piste, année, genre, pochette de l'album) : Audio Station, Plex ou Jellyfin les classent correctement. Quand un morceau existe en plusieurs versions, c'est celle de l'album d'origine qui est retenue (plutôt qu'un single ou une compilation).
- Un single n'a pas de dossier d'album : il est directement dans le dossier de l'artiste (ses tags gardent le nom et la pochette du single).
- Le top du jour reste une catégorie à part (« Par dossier » dans Audio Station), avec les mêmes tags : ses musiques apparaissent aussi sous le bon artiste et le bon album.
- Une musique déjà présente dans la bibliothèque (même artiste, album et titre) n'est pas gardée en double.
- Une vidéo non reconnue (remix inconnu de Deezer, live, son de niche…) va dans `Non classés/`, avec les informations de YouTube.
- **`/ranger`** fait la même chose pour les musiques déjà sur le NAS (anciens dossiers `Titres/`, `Top du jour/`, `Artistes/`, playlists…). Les musiques non reconnues restent à leur place et sont listées ; les dossiers vidés sont supprimés. À lancer une fois après la mise à jour, puis quand tu veux.
- Les registres des musiques déjà téléchargées sont communs à toute la bibliothèque (`music/.deja_telecharges*.txt`) ; ceux des anciens dossiers y sont repris automatiquement au démarrage.

## Structure du projet

| Fichier | Rôle |
|---|---|
| `bot.py` | Bot Discord : commandes, file d'attente, suivi en direct, tâches planifiées |
| `playlist.py` | Moteur de téléchargement (yt-dlp) : reprise, sessions, gestion des blocages |
| `top.py` | Top du jour et discographies via l'API publique de Deezer, recherche sur YouTube |
| `bibliotheque.py` | Reconnaissance sur Deezer, tags (mutagen) et rangement par artiste / album, `/ranger` |
| `Dockerfile` | Image Python 3.12 + ffmpeg + Deno + git + yt-dlp + discord.py |
| `entrypoint.sh` | Démarrage avec le propriétaire du dossier music (droits des fichiers) |
| `maj-code.sh` | Mise à jour depuis GitHub à chaque démarrage du conteneur |
| `requirements.txt` | Versions de yt-dlp et discord.py |

## Mise à jour

**Il suffit de redémarrer le conteneur** (application Docker d'UGOS → *Conteneur* → *Redémarrer*, ou `docker compose restart` en SSH).
À chaque démarrage, le bot récupère les nouveautés de GitHub (branche `MAJ_BRANCHE` du `docker-compose.yml`) puis lance le code à jour. Les journaux indiquent ce qui a changé :

```
Mise à jour : branche ugreen de https://github.com/Rycrem/musicobot.git
Mise à jour : 4d18dda -> 635af0f
  635af0f Nouvelle commande /radio
```

- **Tes fichiers ne sont jamais écrasés** : `config/bot.conf`, `cookies.txt`, `playlists_suivies.json` et les registres `.deja_telecharges*` sont ignorés par git. Si un fichier du projet a été modifié à la main sur le NAS et que la mise à jour le touche, elle est annulée (rien n'est écrasé) et les journaux l'indiquent.
- **Nouvelle version de yt-dlp ou discord.py** (`requirements.txt`) : installée automatiquement au démarrage.
- **Changement du `Dockerfile`** (rare : ffmpeg, Deno, Python) : les journaux affichent « reconstruis le projet ». Dans UGOS → *Projet* → *yt-music* → reconstruire, ou en SSH :
  ```bash
  docker compose up -d --build
  ```
- **Changement du `docker-compose.yml`** : il est mis à jour sur le NAS, mais ne s'applique qu'en relançant le projet (pas seulement le conteneur).
- Pour désactiver la mise à jour : `MAJ_AUTO: "0"`. Sans accès à GitHub, le bot démarre avec la version déjà présente.

Pour mettre yt-dlp à jour (par exemple quand YouTube renvoie des erreurs), monte sa version dans [`requirements.txt`](requirements.txt) (numéro pris sur [pypi.org/project/yt-dlp](https://pypi.org/project/yt-dlp/)) puis redémarre.

### Branche `ugreen` : réglages propres à ton NAS

Le NAS peut suivre une branche dédiée, `ugreen`, pour y mettre ce qui ne concerne que lui (chemin du dossier music, réglages…) sans toucher à `main`.

- Chaque modification de `main` est **fusionnée automatiquement dans `ugreen`** (GitHub Actions, [`fusion-ugreen.yml`](.github/workflows/fusion-ugreen.yml)). La branche est créée au premier passage. En cas de conflit, rien n'est forcé : une issue est ouverte.
- Sur `ugreen`, dans `docker-compose.yml` : ton chemin music et `MAJ_BRANCHE: ugreen`.
- Sur le NAS : `git clone -b ugreen https://github.com/Rycrem/musicobot.git yt-music` (ou le zip de la branche `ugreen`).

### Branches du dépôt

- Les modifications passent par une PR depuis une branche `features/<nom-de-la-feature>`.
- Une branche est supprimée automatiquement une fois sa PR fusionnée ([`nettoyage-branches.yml`](.github/workflows/nettoyage-branches.yml)). Pour nettoyer les anciennes : onglet *Actions* → *Nettoyage des branches fusionnées* → *Run workflow*.

## Avertissement

Projet à usage personnel. Respecte les conditions d'utilisation de YouTube et les droits d'auteur : ne télécharge que des contenus que tu as le droit de conserver.
