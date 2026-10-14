"""
Bot Discord : télécharge sur le NAS les vidéos / playlists envoyées avec /add.

  /add <url>   ajoute un lien (vidéo ou playlist) à la file de téléchargement
  /update      affiche l'avancement : pourcentage global de la playlist,
               vidéo en cours, vitesse, temps restant estimé et file d'attente
  /suivre <url>  vérifie une playlist automatiquement (toutes les VERIF_HEURES)
                 et télécharge ses nouvelles musiques
  /suivies     liste les playlists suivies
  /retirer <n> arrête de suivre une playlist
  /verifier    force la vérification immédiate des playlists suivies
  /help        liste des commandes
  /top         télécharge tout de suite le top du jour (France, USA, Monde) ;
               fait aussi automatiquement chaque soir à TOP_HEURE
  /artiste <nom> [nombre]  télécharge toute la discographie d'un artiste
               (ou seulement ses N titres les plus populaires) : le bot propose
               les artistes trouvés dans une liste, puis annonce le téléchargement
               avec la photo de l'artiste (❌ ou bouton pour l'annuler)

Configuration : /config/bot.conf (voir le fichier modèle).
Les liens sont traités un par un, dans l'ordre d'arrivée.
"""
import asyncio
import itertools
import json
import os
import re
import shutil
import time
import urllib.parse
from dataclasses import dataclass, replace

import discord
from discord import app_commands

import playlist as dl
import top

CHEMIN_CONFIG = os.environ.get("BOT_CONFIG", "/config/bot.conf")
INTERVALLE_DIRECT = max(3, int(os.environ.get("INTERVALLE_DIRECT", "5")))  # s entre 2 mises à jour de /update
FICHIER_SUIVIES = os.environ.get("FICHIER_SUIVIES", "/config/playlists_suivies.json")
VERIF_HEURES = max(0.25, float(os.environ.get("VERIF_HEURES", "6")))   # vérification auto des playlists suivies
PAUSE_ROBOT_HEURES = float(os.environ.get("PAUSE_ROBOT_HEURES", "3"))  # pause si YouTube bloque (anti-robot)
TOP_HEURE = os.environ.get("TOP_HEURE", "21:00").strip()   # heure du top du jour ("" = désactivé)
EMOJI_ANNULER = "❌"   # icône des boutons et options d'annulation


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def lire_config():
    conf = {}
    try:
        # utf-8-sig : accepte les fichiers enregistrés avec le Bloc-notes Windows (BOM)
        with open(CHEMIN_CONFIG, encoding="utf-8-sig") as f:
            for ligne in f:
                ligne = ligne.strip()
                if not ligne or ligne.startswith("#") or "=" not in ligne:
                    continue
                cle, val = ligne.split("=", 1)
                conf[cle.strip().upper()] = val.strip().strip('"').strip("'")
    except FileNotFoundError:
        dl.log(f"Fichier de configuration introuvable : {CHEMIN_CONFIG} "
               "(vérifie que le dossier config est bien à côté du docker-compose.yml)")
    except PermissionError:
        dl.log(f"Pas le droit de lire {CHEMIN_CONFIG} : vérifie les permissions du dossier config "
               f"(le bot tourne avec l'UID {os.getuid()}).")
    return conf


def creer_config_si_absente():
    """Premier lancement : crée config/bot.conf à partir du modèle bot.conf.example
    (le vrai bot.conf, qui contient le token, n'est jamais versionné dans git)."""
    if os.path.exists(CHEMIN_CONFIG):
        return
    for modele in (CHEMIN_CONFIG + ".example", "/app/bot.conf.example"):
        if os.path.isfile(modele):
            try:
                shutil.copyfile(modele, CHEMIN_CONFIG)
                dl.log(f"{CHEMIN_CONFIG} créé à partir du modèle : remplis-le (token Discord…).")
            except OSError as e:
                dl.log(f"Impossible de créer {CHEMIN_CONFIG} ({e}) : copie bot.conf.example "
                       "en bot.conf dans le dossier config et remplis-le.")
            return


def attendre_config():
    """Tant que le token n'est pas rempli, on attend (pas de redémarrages en boucle)."""
    creer_config_si_absente()
    while True:
        conf = lire_config()
        if conf.get("DISCORD_TOKEN"):
            return conf
        dl.log(f"DISCORD_TOKEN vide dans {CHEMIN_CONFIG} : remplis-le, "
               "le bot démarrera tout seul (vérification chaque minute).")
        time.sleep(60)


def liste_ids(texte):
    return {int(x) for x in re.findall(r"\d+", texte or "")}


# ---------------------------------------------------------------------------
# Mise en forme
# ---------------------------------------------------------------------------
def barre(pct, largeur=20):
    plein = round(pct / 100 * largeur)
    return "█" * plein + "░" * (largeur - plein)


def debit(octets_s):
    if not octets_s:
        return "—"
    for unite in ("o/s", "Ko/s", "Mo/s", "Go/s"):
        if octets_s < 1024:
            return f"{octets_s:.1f} {unite}"
        octets_s /= 1024
    return f"{octets_s:.1f} To/s"


def duree(secondes):
    if secondes is None:
        return "—"
    secondes = int(secondes)
    h, reste = divmod(secondes, 3600)
    m, s = divmod(reste, 60)
    return f"{h} h {m:02d} min" if h else (f"{m} min {s:02d} s" if m else f"{s} s")


def court(texte, n=80):
    texte = texte or "—"
    return texte if len(texte) <= n else texte[: n - 1] + "…"


def nombre_lisible(n):
    for seuil, unite in ((1_000_000, "M"), (1_000, "k")):
        if n >= seuil:
            return f"{n / seuil:.1f}".replace(".0", "").replace(".", ",") + f" {unite}"
    return str(n)


def identite_artiste(a):
    """Vrai nom d'un artiste solo, « Groupe » pour un groupe (d'après MusicBrainz), sinon None."""
    if a.get("type") == "Group":
        return "Groupe"
    vrai = a.get("vrai_nom")
    if vrai and top.cle_titre(vrai) != top.cle_titre(a["name"]):
        return vrai
    return None


def details_artiste(a):
    """« Claire Pommet · 12 albums · ~140 titres · 1,2 M fans »."""
    details = [identite_artiste(a)] if identite_artiste(a) else []
    details.append(f"{a.get('nb_album', 0)} album(s)")
    if a.get("nb_titres"):
        details.append(f"~{a['nb_titres']} titres")
    if a.get("nb_fan"):
        details.append(f"{nombre_lisible(a['nb_fan'])} fans")
    return " · ".join(details)


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------
@dataclass(eq=False)
class Tache:
    numero: int
    url: str
    auteur_id: int
    salon: discord.abc.Messageable | None
    suivi: dl.Suivi
    auto: bool = False   # lancée par la vérification automatique
    action: object = None   # fonction(suivi) à exécuter à la place de dl.telecharger(url)
    inter: object = None    # commande Discord d'origine (réponse cachée possible 15 min)
    t_inter: float = 0.0
    annonce: object = None  # message public avec la photo de l'artiste (/artiste)
    nom: str = ""           # nom lisible (artiste, top…) affiché dans /update
    embed: object = None    # embed de cette annonce, mis à jour à la fin


class Bot(discord.Client):
    def __init__(self, conf):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        gid = conf.get("GUILD_ID", "")
        self.guild_id = int(gid) if gid.isdigit() else None
        brut = conf.get("UTILISATEURS_AUTORISES", "").strip()
        self.tout_le_monde = brut == "*"
        self.autorises = liste_ids(brut)
        self.salons = liste_ids(conf.get("SALONS_AUTORISES"))
        self.reveil = asyncio.Event()   # signale au travailleur qu'il y a du travail
        self.pause_jusqua: float | None = None   # file en pause (blocage anti-robot YouTube)
        self.attente: list[Tache] = []
        self.en_cours: Tache | None = None
        self.numeros = itertools.count(1)
        self._travailleur = None
        self._directs: dict[int, asyncio.Task] = {}   # /update en direct, un par utilisateur
        self._verif: asyncio.Task | None = None
        self.prochaine_verif: float | None = None
        self.suivies: list[dict] = self.charger_suivies()
        sid = conf.get("SALON_TOP", "")
        self.salon_top_id = int(sid) if sid.isdigit() else None
        self.prochain_top: float | None = None
        self._top: asyncio.Task | None = None
        self.annonces: dict[int, Tache] = {}   # id du message d'annonce -> tâche (bouton Annuler)

    async def setup_hook(self):
        if self.guild_id:   # commandes disponibles immédiatement sur ce serveur
            serveur = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=serveur)
            await self.tree.sync(guild=serveur)
        else:               # commandes globales : jusqu'à 1 h avant d'apparaître
            await self.tree.sync()
        self._travailleur = asyncio.create_task(self.travailleur())
        self._verif = asyncio.create_task(self.boucle_verif())
        if TOP_HEURE:
            self._top = asyncio.create_task(self.boucle_top())

    async def on_ready(self):
        dl.log(f"Bot connecté : {self.user} — dossier de destination {dl.DOSSIER}, format {dl.FORMAT}")

    def refus(self, inter: discord.Interaction):
        """Renvoie un message d'erreur si l'utilisateur n'a pas le droit, sinon None."""
        if self.salons and inter.channel_id not in self.salons:
            return "Cette commande n'est pas autorisée dans ce salon."
        if self.tout_le_monde:   # UTILISATEURS_AUTORISES=* : aucune restriction
            ok = True
        elif self.autorises:
            ok = inter.user.id in self.autorises
        else:   # rien de configuré : seul le propriétaire du serveur
            ok = inter.guild is not None and inter.user.id == inter.guild.owner_id
        return None if ok else "Tu n'es pas autorisé à utiliser ce bot."

    # ---------------- Playlists suivies ----------------
    @staticmethod
    def charger_suivies():
        try:
            with open(FICHIER_SUIVIES, encoding="utf-8") as f:
                donnees = json.load(f)
            return [d for d in donnees if isinstance(d, dict) and d.get("url")]
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as e:
            dl.log(f"Lecture de {FICHIER_SUIVIES} impossible : {e}")
            return []

    def sauver_suivies(self):
        """Écrit la liste (fichier temporaire puis renommage : jamais de fichier à moitié écrit)."""
        try:
            tmp = FICHIER_SUIVIES + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.suivies, f, ensure_ascii=False, indent=2)
            os.replace(tmp, FICHIER_SUIVIES)
            return True
        except OSError as e:
            dl.log(f"Écriture de {FICHIER_SUIVIES} impossible : {e}")
            return False

    def taches(self):
        return ([self.en_cours] if self.en_cours else []) + self.attente

    @staticmethod
    def nom_tache(tache):
        return tache.suivi.photo()["titre"] or tache.nom or tache.url

    async def ajouter_tache(self, url, auteur_id, salon, auto=False, action=None, inter=None):
        """Ajoute un lien à la file. Renvoie (position, tâche) : position 0 = démarre tout
        de suite ; (None, None) s'il est déjà en cours ou en attente."""
        taches = self.taches()
        if any(t.url == url for t in taches):
            return None, None
        tache = Tache(next(self.numeros), url, auteur_id, salon, dl.Suivi(url), auto, action,
                      inter, time.time() if inter else 0.0)
        self.attente.append(tache)
        self.reveil.set()
        return len(taches), tache

    # ---------------- Annulation (/artiste) ----------------
    async def annuler_tache(self, tache):
        """Annule une tâche : retirée de la file si elle attend, sinon arrêtée au plus vite
        (la musique en cours est interrompue). Renvoie un texte pour l'utilisateur."""
        if tache in self.attente:
            self.attente.remove(tache)
            await self.maj_annonce(tache, "Annulé avant le début du téléchargement.", 0xED4245)
            return "Téléchargement annulé (il n'avait pas encore commencé)."
        if tache is self.en_cours:
            if tache.suivi.annule:
                return "Annulation déjà en cours…"
            tache.suivi.annuler()
            tache.suivi.maj(etat="Annulation en cours…")
            await self.maj_annonce(tache, "Annulation en cours…", 0xFEE75C, garder_bouton=True)
            return "Annulation demandée : le téléchargement s'arrête."
        return "Ce téléchargement est déjà terminé."

    async def maj_annonce(self, tache, statut, couleur, garder_bouton=False):
        """Met à jour la fiche d'une tâche /artiste (statut, couleur). Message caché : Discord
        n'autorise sa modification que pendant 15 min après la commande."""
        if tache.annonce is None or tache.embed is None:
            return
        tache.embed.color = couleur
        tache.embed.set_field_at(len(tache.embed.fields) - 1, name="Statut", value=statut, inline=False)
        kw = {} if garder_bouton else {"view": None}
        if not garder_bouton:
            self.annonces.pop(tache.annonce.id, None)
        try:
            await tache.annonce.edit(embed=tache.embed, **kw)
        except discord.HTTPException:
            pass   # message supprimé entre-temps

    async def salon_de(self, salon_id):
        if not salon_id:
            return None
        salon = self.get_channel(salon_id)
        if salon is None:
            try:
                salon = await self.fetch_channel(salon_id)
            except discord.HTTPException:
                return None
        return salon

    async def verifier_suivies(self, auto=True, auteur_id=0, salon=None, inter=None):
        """Met chaque playlist suivie dans la file (les musiques déjà présentes sont sautées).
        Renvoie (ajoutées, déjà dans la file)."""
        ajoutees = deja = 0
        for p in list(self.suivies):
            cible = salon or await self.salon_de(p.get("salon_id"))
            pos, _ = await self.ajouter_tache(p["url"], auteur_id or p.get("auteur_id", 0), cible, auto,
                                              inter=inter)
            if pos is None:
                deja += 1
            else:
                ajoutees += 1
        if self.suivies:
            dl.log(f"Vérification des playlists suivies ({'auto' if auto else 'forcée'}) : "
                   f"{ajoutees} ajoutée(s) à la file, {deja} déjà en cours.")
        return ajoutees, deja

    async def boucle_verif(self):
        await self.wait_until_ready()
        attente = 120   # première vérification 2 min après le démarrage
        while True:
            self.prochaine_verif = time.time() + attente
            await asyncio.sleep(attente)
            try:
                await self.verifier_suivies(auto=True)
            except Exception as e:  # noqa: BLE001
                dl.log(f"Erreur pendant la vérification automatique : {e}")
            attente = VERIF_HEURES * 3600

    # ---------------- Top du jour ----------------
    async def lancer_tops(self, auto=True, auteur_id=0, salon=None, inter=None):
        """Ajoute un téléchargement par top (France, USA, Monde) à la file."""
        if salon is None:   # top automatique : annonce seulement si SALON_TOP est rempli
            salon = await self.salon_de(self.salon_top_id)
        ajoutes = 0
        for nom in top.noms_tops():
            action = (lambda n: (lambda suivi: top.telecharger_top(n, suivi)))(nom)
            _, tache = await self.ajouter_tache(f"top:{nom}", auteur_id, salon, auto, action, inter)
            if tache is not None:
                tache.nom = f"Top {nom}"
                ajoutes += 1
        dl.log(f"Top du jour ({'auto' if auto else 'forcé'}) : {ajoutes} classement(s) ajouté(s) à la file.")
        return ajoutes

    @staticmethod
    def secondes_avant(heure_txt):
        """Secondes jusqu'à la prochaine occurrence de HH:MM (heure locale, TZ du conteneur)."""
        h, m = (int(x) for x in heure_txt.split(":"))
        maintenant = time.localtime()
        cible = time.mktime(maintenant[:3] + (h, m, 0) + maintenant[6:8] + (-1,))
        if cible <= time.time() + 1:
            cible += 24 * 3600
        return cible - time.time()

    async def boucle_top(self):
        await self.wait_until_ready()
        if not re.fullmatch(r"\d{1,2}:\d{2}", TOP_HEURE):
            dl.log(f"TOP_HEURE invalide : {TOP_HEURE!r} (format attendu HH:MM) — top du jour désactivé.")
            return
        while True:
            attente = self.secondes_avant(TOP_HEURE)
            self.prochain_top = time.time() + attente
            await asyncio.sleep(attente)
            try:
                await self.lancer_tops(auto=True)
            except Exception as e:  # noqa: BLE001
                dl.log(f"Erreur pendant le top du jour : {e}")
            await asyncio.sleep(60)   # évite un double déclenchement dans la même minute

    # ---------------- Affichage /update ----------------
    def construire_embed(self):
        """Renvoie (embed, actif) : actif = il reste quelque chose en cours ou en attente."""
        tache = self.en_cours
        embed = discord.Embed(title="Téléchargement", color=0x5865F2)
        if not tache and not self.attente:
            embed.description = "Aucun téléchargement en cours."
            return embed, False
        if not tache and self.pause_jusqua:
            embed.title = "En pause : YouTube bloque le NAS"
            embed.description = (f"Reprise <t:{int(self.pause_jusqua)}:R>, ou dès qu'un fichier "
                                 f"`cookies.txt` est ajouté dans le dossier config.")
        if tache:
            p = tache.suivi.photo()
            embed.title = court(p["titre"] or "Analyse du lien…", 250)
            if p["url"].startswith(("http://", "https://")):   # pas pour top:… / artiste:…
                embed.url = p["url"]
            elif p["url"].startswith("artiste:"):
                embed.url = f"https://www.deezer.com/artist/{p['url'].split(':')[1]}"
            elif p["url"].startswith("chanson:"):
                embed.url = f"https://www.deezer.com/track/{p['url'].split(':')[1]}"
            embed.description = f"`{barre(p['pourcentage'])}` **{p['pourcentage']:.1f} %**\n{p['etat']}"
            if p["total"] > 1:
                embed.add_field(name="Musiques",
                                value=f"{min(p['faites'] + 1, p['total'])} / {p['total']}")
            if p["video_titre"]:
                embed.add_field(name="Musique en cours",
                                value=f"{court(p['video_titre'])}\n{p['video_pct']:.0f} %",
                                inline=False)
            embed.add_field(name="Vitesse", value=debit(p["vitesse"]))
            if p["debut"]:
                ecoule = time.time() - p["debut"]
                embed.add_field(name="Écoulé", value=duree(ecoule))
                if p["pourcentage"] >= 2:
                    reste = ecoule * (100 - p["pourcentage"]) / p["pourcentage"]
                    embed.add_field(name="Reste (estimation)", value=duree(reste))
            if p["ignorees"] or p["erreurs"]:
                embed.add_field(name="Ignorées / en erreur",
                                value=f"{p['ignorees']} / {p['erreurs']}")
        elif not self.pause_jusqua:
            embed.description = "Démarrage du prochain lien…"
        if self.attente:
            lignes = [f"{i}. {court(t.nom, 60)}" if t.nom else f"{i}. <{t.url}>"
                      for i, t in enumerate(self.attente[:5], 1)]
            if len(self.attente) > 5:
                lignes.append(f"… et {len(self.attente) - 5} autre(s)")
            embed.add_field(name=f"File d'attente ({len(self.attente)})",
                            value=court("\n".join(lignes), 1024), inline=False)
        embed.set_footer(text=f"En direct · mis à jour toutes les {INTERVALLE_DIRECT} s · "
                              f"{time.strftime('%H:%M:%S')}")
        return embed, True

    async def suivre_en_direct(self, inter, vue):
        """Met à jour la réponse privée de /update (et ses boutons d'annulation) toutes les
        INTERVALLE_DIRECT secondes. Discord n'autorise à modifier une réponse privée que
        15 min : on s'arrête avant."""
        fin = time.time() + 14 * 60
        try:
            while True:
                await asyncio.sleep(INTERVALLE_DIRECT)
                embed, actif = self.construire_embed()
                expire = time.time() >= fin
                if not actif:
                    embed.description = "Tous les téléchargements sont terminés."
                    embed.set_footer(text=f"Terminé · {time.strftime('%H:%M:%S')}")
                elif expire:
                    embed.set_footer(text="Suivi en direct arrêté (limite Discord de 15 min) : "
                                          "refais /update pour continuer")
                try:
                    await inter.edit_original_response(
                        embed=embed, view=vue.actualiser() if actif and not expire else None)
                except discord.NotFound:
                    return   # réponse fermée par l'utilisateur
                except discord.HTTPException as e:
                    dl.log(f"Mise à jour de /update impossible : {e}")
                if not actif or expire:
                    vue.stop()
                    return
        except asyncio.CancelledError:
            vue.stop()
            try:
                embed, _ = self.construire_embed()
                embed.set_footer(text="Suivi en direct arrêté : voir le /update plus récent")
                await inter.edit_original_response(embed=embed, view=None)
            except discord.HTTPException:
                pass
            raise

    @staticmethod
    def date_cookies():
        try:
            return os.path.getmtime(dl.FICHIER_COOKIES)
        except OSError:
            return None

    async def attendre_fin_blocage(self):
        """Pause de la file après un blocage anti-robot : reprend après PAUSE_ROBOT_HEURES,
        ou dès qu'un fichier cookies.txt est ajouté / modifié."""
        cookies_avant = self.date_cookies()
        while self.pause_jusqua and time.time() < self.pause_jusqua:
            await asyncio.sleep(30)
            if self.date_cookies() != cookies_avant and self.date_cookies() is not None:
                dl.log("Nouveau fichier cookies.txt détecté : reprise des téléchargements.")
                break
        self.pause_jusqua = None

    async def prevenir(self, tache, texte):
        """Message de fin pour la personne qui a lancé le téléchargement :
        - message caché dans le salon si la commande date de moins de 15 min
          (au-delà, Discord n'autorise plus de réponse cachée) ;
        - sinon message privé.
        Top du jour automatique (pas d'auteur) : dans SALON_TOP s'il est configuré."""
        if tache.inter is not None and time.time() - tache.t_inter < 14 * 60:
            try:
                await tache.inter.followup.send(texte, ephemeral=True)
                return
            except discord.HTTPException as e:
                dl.log(f"Réponse cachée impossible ({e}) : envoi en message privé.")
        try:
            if tache.auteur_id:
                user = self.get_user(tache.auteur_id) or await self.fetch_user(tache.auteur_id)
                await user.send(texte)
            elif tache.salon is not None:
                await tache.salon.send(texte)
        except discord.HTTPException as e:
            dl.log(f"Message de fin non envoyé ({e}). Si c'est un MP : autorise les messages "
                   "privés des membres du serveur dans tes paramètres Discord.")

    async def travailleur(self):
        while True:
            while not self.attente:
                self.reveil.clear()
                await self.reveil.wait()
            if self.pause_jusqua:
                await self.attendre_fin_blocage()
                if not self.attente:
                    continue
            tache = self.attente.pop(0)
            self.en_cours = tache
            msg = None
            try:
                if tache.action:
                    r = await asyncio.to_thread(tache.action, tache.suivi)
                else:
                    r = await asyncio.to_thread(dl.telecharger, tache.url, tache.suivi)
                if r.get("bloque"):
                    # YouTube bloque l'IP du NAS : on remet le lien en tête de file et on met
                    # tout en pause (les musiques déjà faites seront sautées à la reprise).
                    self.en_cours = None
                    reprise = replace(tache, numero=next(self.numeros), suivi=dl.Suivi(tache.url))
                    self.attente.insert(0, reprise)
                    if tache.annonce is not None:   # le ❌ / bouton visent maintenant la reprise
                        self.annonces[tache.annonce.id] = reprise
                    self.pause_jusqua = time.time() + PAUSE_ROBOT_HEURES * 3600
                    cookies = "présent mais sans doute expiré" if self.date_cookies() else "absent"
                    texte = (f"YouTube bloque le NAS (« confirme que tu n'es pas un robot ») pendant "
                             f"**{court(r['titre'])}** : {r['telechargees']} musique(s) téléchargée(s) "
                             f"avant le blocage.\nTous les téléchargements sont en pause jusqu'à "
                             f"<t:{int(self.pause_jusqua)}:t>, ou dès que tu ajoutes un fichier "
                             f"`cookies.txt` dans le dossier config (actuellement : {cookies}). "
                             f"Ils reprendront ensuite là où ils se sont arrêtés.")
                    dl.log(f"Blocage anti-robot : file en pause {PAUSE_ROBOT_HEURES:g} h.")
                    await self.prevenir(tache, texte)
                    await self.maj_annonce(reprise, "En pause : YouTube bloque le NAS, reprise "
                                           f"<t:{int(self.pause_jusqua)}:R>.", 0xFEE75C,
                                           garder_bouton=True)
                    continue
                if r.get("annule"):
                    effaces = (" Le dossier de l'artiste, créé par ce téléchargement, a été supprimé."
                               if r.get("supprime") else
                               f" {r['telechargees']} musique(s) déjà téléchargée(s) conservée(s).")
                    msg = f"Annulé : **{court(r['titre'])}**.{effaces}"
                    await self.maj_annonce(tache, "Annulé." + effaces, 0xED4245)
                    continue
                # mémorise le titre des playlists suivies (affiché par /suivies)
                for p in self.suivies:
                    if p["url"] == tache.url and p.get("titre") != r["titre"]:
                        p["titre"] = r["titre"]
                        self.sauver_suivies()
                ecoule = duree(time.time() - (tache.suivi.debut or time.time()))
                deja = max(0, r["total"] - r["telechargees"] - r["ignorees"] - r["erreurs"])
                if tache.auto and not r["telechargees"] and not r["erreurs"]:
                    dl.log(f"Vérification auto : rien de nouveau dans {r['titre']}.")
                else:
                    debut = (f"{'Top du jour' if tache.url.startswith('top:') else 'Vérification automatique'}"
                             f" : **{court(r['titre'])}**"
                             if tache.auto else
                             f"Terminé : **{court(r['titre'])}** en {ecoule}")
                    msg = (f"{debut}\n"
                           f"{r['telechargees']} musique(s) téléchargée(s) · {deja} déjà présente(s) · "
                           f"{r['erreurs']} en erreur · {r['ignorees']} ignorée(s)\n"
                           f"Dossier : `{os.path.relpath(r['dossier'], dl.DOSSIER)}`")
                    if r["echecs"]:
                        liste = "\n".join(f"- {court(t, 70)}" for t in r["echecs"][:10])
                        if len(r["echecs"]) > 10:
                            liste += f"\n… et {len(r['echecs']) - 10} autre(s)"
                        msg += f"\nMusiques en erreur (sautées) :\n{liste}"
                    msg = court(msg, 1990)
                await self.maj_annonce(
                    tache, f"Terminé ✅ {r['telechargees']} musique(s) téléchargée(s) · "
                           f"{deja} déjà présente(s) · {r['erreurs']} en erreur", 0x57F287)
            except Exception as e:  # noqa: BLE001
                dl.log(f"Échec sur {tache.url} : {e}")
                qui = "Vérification automatique" if tache.auto else "Erreur"
                msg = f"{qui} : échec du téléchargement de <{tache.url}> : {court(str(e), 300)}"
                await self.maj_annonce(tache, f"Échec : {court(str(e), 200)}", 0xED4245)
            finally:
                self.en_cours = None
            if msg:
                await self.prevenir(tache, msg)


async def repondre(inter, contenu=None, **kw):
    """Toutes les réponses du bot sont privées : seule la personne qui a tapé la commande
    les voit (et la commande elle-même n'apparaît pas dans le salon)."""
    return await inter.response.send_message(contenu, ephemeral=True, **kw)


async def repondre_suite(inter, contenu=None, **kw):
    return await inter.followup.send(contenu, ephemeral=True, **kw)


NB_PROPOSITIONS = 5   # artistes / chansons proposés dans la liste de /artiste et /chanson


def embed_artiste(a, nombre, demandeur):
    """Fiche (message caché) d'un téléchargement d'artiste, façon Requestrr (photo en grand)."""
    embed = discord.Embed(title=a["name"], url=a.get("link") or f"https://www.deezer.com/artist/{a['id']}",
                          color=0x5865F2,
                          description=(f"Téléchargement de ses **{nombre}** titres les plus populaires"
                                       if nombre else "Téléchargement de **toute sa discographie**"))
    image = a.get("picture_xl") or a.get("picture_big")
    if image:
        embed.set_image(url=image)
    if identite_artiste(a):
        embed.add_field(name="Groupe" if a.get("type") == "Group" else "Vrai nom",
                        value="Oui" if a.get("type") == "Group" else identite_artiste(a))
    embed.add_field(name="Albums", value=str(a.get("nb_album", "—")))
    if a.get("nb_titres"):
        embed.add_field(name="Titres", value=f"~{a['nb_titres']}")
    if a.get("nb_fan"):
        embed.add_field(name="Fans Deezer", value=nombre_lisible(a["nb_fan"]))
    if a.get("youtube"):
        embed.add_field(name="YouTube", value=f"[Chaîne YouTube]({a['youtube']})", inline=False)
    else:   # chaîne inconnue de MusicBrainz : lien de recherche
        recherche = urllib.parse.quote_plus(a["name"])
        embed.add_field(name="YouTube", inline=False,
                        value=f"[Rechercher sur YouTube](https://www.youtube.com/results?search_query={recherche})")
    embed.add_field(name="Statut", value="—", inline=False)   # toujours le dernier champ
    embed.set_footer(text=f"Demandé par {demandeur}")
    return embed


class ChoixArtiste(discord.ui.View):
    """Liste déroulante des artistes trouvés (+ « Annuler la demande »)."""

    def __init__(self, bot, inter, artistes, nombre):
        super().__init__(timeout=10 * 60)
        self.bot, self.inter, self.nombre = bot, inter, nombre
        self.artistes = {str(a["id"]): a for a in artistes}
        options = [discord.SelectOption(label=court(a["name"], 100), value=str(a["id"]),
                                        description=court(details_artiste(a), 100))
                   for a in artistes]
        options.append(discord.SelectOption(label="Annuler la demande", value="annuler",
                                            emoji=EMOJI_ANNULER))
        self.liste = discord.ui.Select(placeholder="Choisis le bon artiste…", options=options)
        self.liste.callback = self.choisi
        self.add_item(self.liste)

    async def choisi(self, inter: discord.Interaction):
        self.stop()
        valeur = self.liste.values[0]
        if valeur == "annuler":
            return await inter.response.edit_message(content="Demande annulée.", view=None)
        a = self.artistes[valeur]
        cle = f"artiste:{a['id']}:{self.nombre}"
        if any(t.url == cle for t in self.bot.taches()):
            return await inter.response.edit_message(
                content=f"**{a['name']}** est déjà en cours ou dans la file.", view=None)
        await inter.response.edit_message(
            content=f"Artiste choisi : **{a['name']}**. Les musiques déjà téléchargées sont sautées ; "
                    "`/update` pour suivre l'avancement.", view=None)
        # L'annonce est publiée avant de lancer le téléchargement : elle peut ainsi être
        # mise à jour même s'il se termine tout de suite (tout déjà présent).
        embed = embed_artiste(a, self.nombre, inter.user.display_name)
        embed.set_field_at(len(embed.fields) - 1, name="Statut", value="Préparation…", inline=False)
        try:
            annonce = await inter.followup.send(embed=embed, ephemeral=True, wait=True)
        except discord.HTTPException as e:
            dl.log(f"Annonce de l'artiste impossible : {e}")
            annonce = None
        action = lambda suivi: top.telecharger_artiste(a, self.nombre, suivi)  # noqa: E731
        position, tache = await self.bot.ajouter_tache(cle, inter.user.id, inter.channel,
                                                       action=action, inter=self.inter)
        if tache is not None:
            tache.nom = a["name"] + (f" (top {self.nombre})" if self.nombre else "")
        if position is None:   # lancé entre-temps par quelqu'un d'autre
            if annonce is not None:
                await annonce.delete()
            return
        if annonce is None:
            return
        tache.annonce, tache.embed = annonce, embed
        self.bot.annonces[annonce.id] = tache
        embed.set_field_at(len(embed.fields) - 1, name="Statut", inline=False,
                           value="Téléchargement lancé." if position == 0
                           else f"Dans la file d'attente (position {position}).")
        if annonce.id in self.bot.annonces:   # pas déjà terminé (sinon l'annonce est à jour)
            try:
                await annonce.edit(embed=embed, view=AnnulerTelechargement(self.bot, tache))
            except discord.HTTPException as e:
                dl.log(f"Mise à jour de l'annonce impossible : {e}")

    async def on_timeout(self):
        try:
            await self.inter.edit_original_response(content="Demande expirée : refais `/artiste`.",
                                                    view=None)
        except discord.HTTPException:
            pass


class AnnulerTelechargement(discord.ui.View):
    """Bouton « Annuler le téléchargement » sous l'annonce publique."""

    def __init__(self, bot, tache):
        super().__init__(timeout=None)
        self.bot, self.tache = bot, tache

    @discord.ui.button(label="Annuler le téléchargement", style=discord.ButtonStyle.danger,
                       emoji=EMOJI_ANNULER)
    async def annuler(self, inter: discord.Interaction, _bouton: discord.ui.Button):
        if inter.user.id != self.tache.auteur_id:
            return await repondre(inter, "Seule la personne qui a demandé ce téléchargement peut l'annuler.")
        # self.tache peut avoir été remplacée (reprise après un blocage YouTube)
        tache = self.bot.annonces.get(inter.message.id, self.tache)
        await repondre(inter, await self.bot.annuler_tache(tache))


class ControleUpdate(discord.ui.View):
    """Boutons de /update : annuler le téléchargement en cours, retirer un lien de la file.
    Toute personne autorisée à utiliser le bot peut annuler (pas seulement le demandeur)."""

    def __init__(self, bot):
        super().__init__(timeout=15 * 60)
        self.bot = bot
        self.cible = None   # tâche en cours au moment de l'affichage

    def actualiser(self):
        """Reconstruit les éléments selon l'état actuel de la file ; renvoie la vue."""
        self.clear_items()
        self.cible = self.bot.en_cours
        if self.cible is not None and not self.cible.suivi.annule:
            bouton = discord.ui.Button(style=discord.ButtonStyle.danger, emoji=EMOJI_ANNULER,
                                       label=court(f"Annuler : {self.bot.nom_tache(self.cible)}", 80))
            bouton.callback = self.annuler_en_cours
            self.add_item(bouton)
        if self.bot.attente:
            options = [discord.SelectOption(label=court(f"{i}. {self.bot.nom_tache(t)}", 100),
                                            value=str(t.numero))
                       for i, t in enumerate(self.bot.attente[:25], 1)]
            liste = discord.ui.Select(placeholder="Retirer de la file d'attente…", options=options)
            liste.callback = lambda inter: self.retirer(inter, liste.values[0])
            self.add_item(liste)
        return self

    async def apres(self, inter, texte):
        embed, actif = self.bot.construire_embed()
        await inter.response.edit_message(embed=embed, view=self.actualiser() if actif else None)
        await repondre_suite(inter, texte)

    async def annuler_en_cours(self, inter: discord.Interaction):
        if (erreur := self.bot.refus(inter)):
            return await repondre(inter, erreur)
        if self.cible is None or self.cible is not self.bot.en_cours:
            return await self.apres(inter, "Ce téléchargement est déjà terminé.")
        dl.log(f"Annulation demandée depuis /update : {self.cible.url}")
        await self.apres(inter, await self.bot.annuler_tache(self.cible))

    async def retirer(self, inter: discord.Interaction, numero):
        if (erreur := self.bot.refus(inter)):
            return await repondre(inter, erreur)
        tache = next((t for t in self.bot.attente if str(t.numero) == numero), None)
        if tache is None:
            return await self.apres(inter, "Ce lien n'est plus dans la file d'attente.")
        dl.log(f"Retiré de la file depuis /update : {tache.url}")
        nom = court(self.bot.nom_tache(tache), 80)
        await self.bot.annuler_tache(tache)
        await self.apres(inter, f"Retiré de la file d'attente : **{nom}**")


def details_chanson(c):
    """« Album · 3:45 » (ce que Deezer fournit)."""
    details = []
    if c.get("album", {}).get("title"):
        details.append(c["album"]["title"])
    if c.get("duration"):
        details.append(f"{c['duration'] // 60}:{c['duration'] % 60:02d}")
    return " · ".join(details) or "—"


class ChoixChanson(discord.ui.View):
    """Liste déroulante des chansons trouvées (+ « Annuler la demande »)."""

    def __init__(self, bot, inter, chansons):
        super().__init__(timeout=10 * 60)
        self.bot, self.inter = bot, inter
        self.chansons = {str(c["id"]): c for c in chansons}
        options = [discord.SelectOption(label=court(f"{c['artist']['name']} - {c['title']}", 100),
                                        value=str(c["id"]), description=court(details_chanson(c), 100))
                   for c in chansons]
        options.append(discord.SelectOption(label="Annuler la demande", value="annuler",
                                            emoji=EMOJI_ANNULER))
        self.liste = discord.ui.Select(placeholder="Choisis la bonne chanson…", options=options)
        self.liste.callback = self.choisi
        self.add_item(self.liste)

    async def choisi(self, inter: discord.Interaction):
        self.stop()
        valeur = self.liste.values[0]
        if valeur == "annuler":
            return await inter.response.edit_message(content="Demande annulée.", view=None)
        c = self.chansons[valeur]
        nom = f"{c['artist']['name']} - {c['title']}"
        action = lambda suivi: top.telecharger_chanson(c, suivi)  # noqa: E731
        position, tache = await self.bot.ajouter_tache(f"chanson:{c['id']}", inter.user.id, inter.channel,
                                                       action=action, inter=self.inter)
        if tache is not None:
            tache.nom = nom
        if position is None:
            texte = f"**{court(nom)}** est déjà en cours ou dans la file."
        elif position == 0:
            texte = f"Téléchargement lancé : **{court(nom)}**\nUtilise `/update` pour suivre l'avancement."
        else:
            texte = f"Ajouté à la file (position {position}) : **{court(nom)}**"
        await inter.response.edit_message(content=texte, view=None)

    async def on_timeout(self):
        try:
            await self.inter.edit_original_response(content="Demande expirée : refais `/chanson`.",
                                                    view=None)
        except discord.HTTPException:
            pass


def enregistrer_commandes(bot: Bot):
    @bot.tree.command(name="help", description="Liste des commandes du bot")
    async def aide(inter: discord.Interaction):
        embed = discord.Embed(title="Commandes du bot", color=0x5865F2,
                              description="Les musiques déjà présentes sur le NAS sont toujours sautées.")
        embed.add_field(name="Télécharger", inline=False, value=(
            "`/add <lien>` — une vidéo ou une playlist YouTube\n"
            "`/chanson <titre>` — une chanson à partir de son nom (choix dans une liste)\n"
            "`/artiste <nom> [nombre]` — toute la discographie d'un artiste, "
            "ou ses N titres les plus populaires (choix dans une liste, bouton pour annuler)\n"
            "`/top` — le top 10 du jour France, USA et Monde (aussi automatique "
            f"chaque soir{f' à {TOP_HEURE}' if TOP_HEURE else ''})"))
        embed.add_field(name="Suivre l'avancement", inline=False, value=(
            "`/update` — avancement en direct (pourcentage, musique en cours, file d'attente), "
            "avec un bouton pour annuler le téléchargement en cours ou retirer un lien de la file"))
        embed.add_field(name="Playlists suivies", inline=False, value=(
            f"`/suivre <lien>` — vérifie la playlist toutes les {VERIF_HEURES:g} h et télécharge "
            "ses nouvelles musiques\n"
            "`/suivies` — liste des playlists suivies et prochaines vérifications\n"
            "`/retirer <numéro>` — arrête de suivre une playlist (numéro de `/suivies`)\n"
            "`/verifier` — vérifie tout de suite les playlists suivies"))
        embed.add_field(name="Si YouTube bloque", inline=False, value=(
            "Les téléchargements se mettent en pause. Dépose un fichier `cookies.txt` "
            "dans le dossier config du NAS : ils reprennent automatiquement."))
        embed.set_footer(text=f"Musiques enregistrées en {dl.FORMAT} dans le dossier music du NAS")
        await repondre(inter, embed=embed)

    @bot.tree.command(name="add", description="Télécharger une vidéo ou une playlist sur le NAS")
    @app_commands.describe(url="Lien de la vidéo ou de la playlist")
    @app_commands.guild_only()
    async def add(inter: discord.Interaction, url: str):
        if (erreur := bot.refus(inter)):
            return await repondre(inter, erreur)
        url = url.strip().strip("<>")
        if not re.match(r"^https?://\S+$", url):
            return await repondre(inter, "Ce n'est pas un lien valide.")
        position, _ = await bot.ajouter_tache(url, inter.user.id, inter.channel, inter=inter)
        if position is None:
            return await repondre(inter, "Ce lien est déjà en cours ou dans la file.")
        if position == 0:
            texte = f"Téléchargement lancé : <{url}>\nUtilise `/update` pour suivre l'avancement."
        else:
            texte = f"Ajouté à la file (position {position}) : <{url}>"
        await repondre(inter, texte)

    @bot.tree.command(name="update", description="Voir l'avancement du téléchargement")
    @app_commands.guild_only()
    async def update(inter: discord.Interaction):
        if (erreur := bot.refus(inter)):
            return await repondre(inter, erreur)
        embed, actif = bot.construire_embed()
        if not actif:
            return await repondre(inter, embed=embed)
        vue = ControleUpdate(bot)
        await repondre(inter, embed=embed, view=vue.actualiser())
        precedent = bot._directs.get(inter.user.id)
        if precedent and not precedent.done():
            precedent.cancel()
        bot._directs[inter.user.id] = asyncio.create_task(bot.suivre_en_direct(inter, vue))

    @bot.tree.command(name="suivre",
                      description="Vérifier une playlist automatiquement et télécharger ses nouvelles musiques")
    @app_commands.describe(url="Lien de la playlist à suivre")
    @app_commands.guild_only()
    async def suivre(inter: discord.Interaction, url: str):
        if (erreur := bot.refus(inter)):
            return await repondre(inter, erreur)
        url = url.strip().strip("<>")
        if not re.match(r"^https?://\S+$", url):
            return await repondre(inter, "Ce n'est pas un lien valide.")
        if any(p["url"] == url for p in bot.suivies):
            return await repondre(inter, "Cette playlist est déjà suivie.")
        bot.suivies.append({"url": url, "titre": None, "salon_id": inter.channel_id,
                            "auteur_id": inter.user.id, "ajout": time.strftime("%Y-%m-%d %H:%M")})
        if not bot.sauver_suivies():
            bot.suivies.pop()
            return await repondre(
                inter,                 "Impossible d'enregistrer la playlist : le bot n'a pas le droit d'écrire dans le "
                "dossier config. Vérifie ses permissions sur le NAS.")
        position, _ = await bot.ajouter_tache(url, inter.user.id, inter.channel, inter=inter)
        texte = (f"Playlist suivie : <{url}>\n"
                 f"Elle sera vérifiée toutes les {VERIF_HEURES:g} h ; les nouvelles musiques seront "
                 f"téléchargées automatiquement et annoncées en message privé.")
        if position is not None:
            texte += ("\nPremier téléchargement lancé." if position == 0
                      else f"\nPremier téléchargement ajouté à la file (position {position}).")
        await repondre(inter, texte)

    @bot.tree.command(name="suivies", description="Lister les playlists vérifiées automatiquement")
    @app_commands.guild_only()
    async def suivies(inter: discord.Interaction):
        if (erreur := bot.refus(inter)):
            return await repondre(inter, erreur)
        if not bot.suivies:
            return await repondre(
                inter,                 "Aucune playlist suivie. Ajoute-en une avec `/suivre <url>`.")
        lignes = [f"**{i}.** [{court(p.get('titre') or 'Titre inconnu (pas encore vérifiée)', 60)}]"
                  f"(<{p['url']}>)" for i, p in enumerate(bot.suivies, 1)]
        embed = discord.Embed(title=f"Playlists suivies ({len(bot.suivies)})", color=0x5865F2,
                              description=court("\n".join(lignes), 4000))
        if bot.prochaine_verif:
            embed.add_field(name="Prochaine vérification",
                            value=f"<t:{int(bot.prochaine_verif)}:R> (toutes les {VERIF_HEURES:g} h)")
        if bot.prochain_top:
            embed.add_field(name="Top du jour", value=f"<t:{int(bot.prochain_top)}:R> (chaque jour à {TOP_HEURE})")
        embed.set_footer(text="/retirer <numéro> pour arrêter d'en suivre une · /verifier pour forcer")
        await repondre(inter, embed=embed)

    @bot.tree.command(name="retirer", description="Arrêter de suivre une playlist")
    @app_commands.describe(numero="Numéro de la playlist dans /suivies")
    @app_commands.guild_only()
    async def retirer(inter: discord.Interaction, numero: int):
        if (erreur := bot.refus(inter)):
            return await repondre(inter, erreur)
        if not 1 <= numero <= len(bot.suivies):
            return await repondre(
                inter,                 f"Numéro invalide : choisis entre 1 et {len(bot.suivies)} (voir `/suivies`).")
        p = bot.suivies.pop(numero - 1)
        if not bot.sauver_suivies():
            bot.suivies.insert(numero - 1, p)
            return await repondre(
                inter,                 "Impossible d'enregistrer la modification (droits d'écriture du dossier config).")
        await repondre(
                inter,             f"Playlist retirée du suivi : **{court(p.get('titre') or p['url'])}**\n"
            "Les musiques déjà téléchargées restent sur le NAS.")

    @bot.tree.command(name="top", description="Télécharger maintenant le top 10 du jour (France, USA, Monde)")
    @app_commands.guild_only()
    async def top_du_jour(inter: discord.Interaction):
        if (erreur := bot.refus(inter)):
            return await repondre(inter, erreur)
        ajoutes = await bot.lancer_tops(auto=False, auteur_id=inter.user.id, salon=inter.channel,
                                        inter=inter)
        if not ajoutes:
            return await repondre(
                inter,                 "Le top du jour est déjà en cours ou dans la file. Utilise `/update` pour le suivre.")
        texte = (f"Top du jour lancé : {ajoutes} classement(s) ({', '.join(top.noms_tops())}) "
                 f"ajouté(s) à la file. Les musiques déjà téléchargées sont sautées.")
        if bot.prochain_top:
            texte += f"\nProchain top automatique : <t:{int(bot.prochain_top)}:R>."
        texte += "\nUtilise `/update` pour suivre l'avancement."
        await repondre(inter, texte)

    @bot.tree.command(name="artiste", description="Télécharger toutes les musiques d'un artiste")
    @app_commands.describe(nom="Nom de l'artiste",
                           nombre="Seulement ses N titres les plus populaires (vide = toute la discographie)")
    @app_commands.guild_only()
    async def artiste(inter: discord.Interaction, nom: str, nombre: int = 0):
        if (erreur := bot.refus(inter)):
            return await repondre(inter, erreur)
        nombre = max(0, nombre)
        await inter.response.defer(thinking=True, ephemeral=True)
        try:
            trouves = await asyncio.to_thread(top.chercher_artistes, nom.strip(), NB_PROPOSITIONS)
        except Exception as e:  # noqa: BLE001
            return await repondre_suite(inter, f"Recherche impossible sur Deezer : {court(str(e), 200)}")
        if not trouves:
            return await repondre_suite(inter, f"Aucun artiste trouvé pour « {court(nom, 60)} ».")
        quoi = f"ses {nombre} titres les plus populaires" if nombre else "toute sa discographie"
        vue = ChoixArtiste(bot, inter, trouves, nombre)
        await repondre_suite(inter, f"Quel artiste veux-tu télécharger ({quoi}) ?", view=vue)

    @bot.tree.command(name="chanson", description="Télécharger une chanson à partir de son nom")
    @app_commands.describe(titre="Titre de la chanson (ajoute l'artiste pour affiner, ex. « Daft Punk One More Time »)")
    @app_commands.guild_only()
    async def chanson(inter: discord.Interaction, titre: str):
        if (erreur := bot.refus(inter)):
            return await repondre(inter, erreur)
        await inter.response.defer(thinking=True, ephemeral=True)
        try:
            trouvees = await asyncio.to_thread(top.chercher_chansons, titre.strip(), NB_PROPOSITIONS)
        except Exception as e:  # noqa: BLE001
            return await repondre_suite(inter, f"Recherche impossible sur Deezer : {court(str(e), 200)}")
        if not trouvees:
            return await repondre_suite(
                inter, f"Aucune chanson trouvée pour « {court(titre, 60)} ». Essaie avec le nom de "
                       "l'artiste, ou donne directement le lien YouTube avec `/add`.")
        await repondre_suite(inter, "Quelle chanson veux-tu télécharger ?",
                             view=ChoixChanson(bot, inter, trouvees))

    @bot.tree.command(name="verifier", description="Vérifier tout de suite les playlists suivies")
    @app_commands.guild_only()
    async def verifier(inter: discord.Interaction):
        if (erreur := bot.refus(inter)):
            return await repondre(inter, erreur)
        if not bot.suivies:
            return await repondre(
                inter,                 "Aucune playlist suivie. Ajoute-en une avec `/suivre <url>`.")
        ajoutees, deja = await bot.verifier_suivies(auto=False, auteur_id=inter.user.id, inter=inter,
                                                    salon=inter.channel)
        texte = f"Vérification lancée : {ajoutees} playlist(s) ajoutée(s) à la file."
        if deja:
            texte += f" {deja} déjà en cours ou en attente."
        texte += "\nUtilise `/update` pour suivre l'avancement."
        await repondre(inter, texte)


if __name__ == "__main__":
    dl.verifier_dossier()
    configuration = attendre_config()
    bot = Bot(configuration)
    enregistrer_commandes(bot)
    dl.log("Connexion à Discord…")
    try:
        # log_handler par défaut : les erreurs de connexion s'affichent dans les journaux
        bot.run(configuration["DISCORD_TOKEN"])
    except discord.LoginFailure:
        dl.log("Token refusé par Discord : recopie le token (Developer Portal > Bot > Reset Token) "
               "dans bot.conf, sans espace ni guillemet, puis redémarre le conteneur.")
        time.sleep(300)   # évite de redémarrer en boucle
    except discord.PrivilegedIntentsRequired:
        dl.log("Discord refuse les intents demandés : vérifie les réglages de l'onglet Bot.")
        time.sleep(300)
