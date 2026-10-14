# Bot musique pour NAS UGREEN

Bot Discord qui télécharge de la musique depuis YouTube directement sur un NAS (UGREEN ou tout NAS avec Docker), dans le dossier `music`.
Tout se pilote avec des commandes Discord ; le bot tourne dans Docker en permanence et redémarre avec le NAS.

> Guide complet pas à pas (installation, Discord, dépannage) : **[Guide-bot-musique.pdf](Guide-bot-musique.pdf)**

## Fonctionnalités

- **`/add <lien>`** : télécharge une vidéo ou une playlist YouTube entière (audio m4a avec titre, artiste et pochette intégrés).
- **`/artiste <nom> [nombre]`** : toute la discographie d'un artiste (albums, EP, singles, sans doublons), ou ses N titres les plus populaires. Le bot propose d'abord les artistes trouvés sur Deezer dans une liste (nombre d'albums, de titres et de fans) ; une fois le bon choisi, il annonce le téléchargement dans le salon avec la photo de l'artiste. Un clic sur la réaction ❌ (ou le bouton « Annuler le téléchargement ») l'arrête ; si le dossier de l'artiste a été créé par ce téléchargement, il est supprimé. Le bot doit avoir le droit « Ajouter des réactions » dans le salon.
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
