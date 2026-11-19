"""Tests de l'API HTTP, sans Discord ni yt-dlp : un faux bot et un faux moteur Deezer.
Lancer : python -m unittest discover tests   (nécessite aiohttp, installé avec discord.py)"""
import os
import sys
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

TOKEN = "t" * 40
ENTETE = {"Authorization": f"Bearer {TOKEN}"}


class FauxSuivi:
    def __init__(self, titre="Daft Punk - One More Time", pct=42.0):
        self.titre, self.pct = titre, pct

    def photo(self):
        return {"titre": self.titre, "etat": "Téléchargement", "total": 1, "faites": 0,
                "video_titre": "One More Time", "pourcentage": self.pct, "vitesse": 1_500_000,
                "eta": 12, "erreurs": 0}


class FauxBot:
    def __init__(self):
        self.en_cours = None
        self.attente = []
        self.terminees = []
        self.pause_jusqua = None
        self.numero = 0
        self.annulees = []

    def taches(self):
        return ([self.en_cours] if self.en_cours else []) + self.attente

    @staticmethod
    def nom_tache(t):
        return t.suivi.titre or t.nom

    async def ajouter_tache(self, url, auteur_id, salon, auto=False, action=None, inter=None):
        if any(t.url == url for t in self.taches()):
            return None, None
        self.numero += 1
        t = SimpleNamespace(numero=self.numero, url=url, nom="", suivi=FauxSuivi(url), auto=auto,
                            action=action, auteur_id=auteur_id, salon=salon)
        position = len(self.taches())
        (self.attente if self.en_cours else self.attente).append(t)
        return position, t

    async def annuler_tache(self, t):
        self.annulees.append(t.numero)
        return "Annulation demandée."


class FauxMoteur:
    def __init__(self):
        self.recherches = []

    def chercher_chansons(self, q, limite):
        self.recherches.append(("chansons", q, limite))
        return [{"id": 3135556, "title": "One More Time", "artist": {"name": "Daft Punk"},
                 "album": {"title": "Discovery", "cover_medium": "https://c/x.jpg"}, "duration": 320,
                 "fans_artiste": 4_000_000, "preview": "https://p/x.mp3"}]

    def chercher_artistes(self, q, limite):
        return [{"id": 27, "name": "Daft Punk", "nb_album": 12, "nb_titres": 140, "nb_fan": 4_000_000,
                 "picture_medium": "https://p/a.jpg", "type": "Group"}]

    def api(self, chemin):
        if chemin.endswith("/999"):
            return {"error": {"type": "DataException"}}
        if chemin.startswith("track/"):
            return {"id": int(chemin.split("/")[1]), "title": "One More Time", "artist": {"name": "Daft Punk"}}
        return {"id": int(chemin.split("/")[1]), "name": "Daft Punk"}

    def telecharger_chanson(self, c, suivi):
        return {}

    def telecharger_artiste(self, a, nombre, suivi):
        return {}


class ApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api.PAUSE_ECHEC = 0
        self.bot, self.moteur = FauxBot(), FauxMoteur()
        self.client = TestClient(TestServer(api.creer_app(self.bot, TOKEN, self.moteur)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_jeton_obligatoire(self):
        for entetes in ({}, {"Authorization": "Bearer mauvais"}, {"Authorization": TOKEN},
                        {"Authorization": f"Basic {TOKEN}"}):
            r = await self.client.get("/api/sante", headers=entetes)
            self.assertEqual(401, r.status, entetes)

    async def test_jeton_exige_sur_toutes_les_routes(self):
        for methode, chemin in [("get", "/api/chansons?q=abc"), ("post", "/api/chansons"), ("get", "/api/artistes?q=abc"),
                                ("post", "/api/artistes"), ("post", "/api/liens"), ("get", "/api/file"),
                                ("delete", "/api/taches/1")]:
            r = await getattr(self.client, methode)(chemin)
            self.assertEqual(401, r.status, chemin)
        self.assertEqual([], self.moteur.recherches, "aucune recherche sans jeton")

    async def test_sante(self):
        r = await self.client.get("/api/sante", headers=ENTETE)
        self.assertEqual({"ok": True, "version": api.VERSION_API, "pause": False}, await r.json())

    async def test_recherche_chansons(self):
        r = await self.client.get("/api/chansons", params={"q": "daft punk one more time"}, headers=ENTETE)
        self.assertEqual(200, r.status)
        c = (await r.json())["resultats"][0]
        self.assertEqual({"id": "3135556", "titre": "One More Time", "artiste": "Daft Punk", "album": "Discovery",
                          "pochette": "https://c/x.jpg", "duree": 320, "fans": 4_000_000, "apercu": "https://p/x.mp3",
                          "lien": "https://www.deezer.com/track/3135556"}, c)

    async def test_recherche_invalide(self):
        for q in ("", "a", "x" * 101):
            r = await self.client.get("/api/chansons", params={"q": q}, headers=ENTETE)
            self.assertEqual(400, r.status, q)

    async def test_recherche_artistes(self):
        r = await self.client.get("/api/artistes", params={"q": "daft"}, headers=ENTETE)
        a = (await r.json())["resultats"][0]
        self.assertEqual("Daft Punk", a["nom"])
        self.assertTrue(a["groupe"])
        self.assertEqual(140, a["titres"])

    async def test_telecharger_chanson_puis_doublon(self):
        r = await self.client.post("/api/chansons", json={"id": "3135556"}, headers=ENTETE)
        self.assertEqual(202, r.status)
        corps = await r.json()
        self.assertEqual("Daft Punk - One More Time", corps["nom"])
        self.assertEqual("chanson:3135556", self.bot.attente[0].url)
        again = await self.client.post("/api/chansons", json={"id": 3135556}, headers=ENTETE)
        self.assertEqual(409, again.status)

    async def test_identifiants_refuses(self):
        for corps in ({}, {"id": "abc"}, {"id": "1; rm -rf"}, {"id": "../x"}, {"id": "1" * 20}):
            r = await self.client.post("/api/chansons", json=corps, headers=ENTETE)
            self.assertEqual(400, r.status, corps)
        r = await self.client.post("/api/chansons", data="pas du json", headers=ENTETE)
        self.assertEqual(400, r.status)
        self.assertEqual([], self.bot.attente)

    async def test_chanson_inconnue_de_deezer(self):
        r = await self.client.post("/api/chansons", json={"id": "999"}, headers=ENTETE)
        self.assertEqual(404, r.status)

    async def test_artiste_nombre_de_titres(self):
        ok = await self.client.post("/api/artistes", json={"id": "27", "nombre": 10}, headers=ENTETE)
        self.assertEqual(202, ok.status)
        self.assertEqual("artiste:27:10", self.bot.attente[0].url)
        for nombre in (-1, 201, "dix", True, 2.5):
            r = await self.client.post("/api/artistes", json={"id": "28", "nombre": nombre}, headers=ENTETE)
            self.assertEqual(400, r.status, nombre)

    async def test_lien(self):
        ok = await self.client.post("/api/liens", json={"url": "https://youtu.be/abc"}, headers=ENTETE)
        self.assertEqual(202, ok.status)
        for url in ("", "ftp://x", "javascript:alert(1)", "https://a b", "https://x/" + "a" * 600):
            r = await self.client.post("/api/liens", json={"url": url}, headers=ENTETE)
            self.assertEqual(400, r.status, url)

    async def test_file_et_historique(self):
        await self.client.post("/api/chansons", json={"id": "3135556"}, headers=ENTETE)
        self.bot.en_cours = self.bot.attente.pop(0)
        self.bot.terminees.append({"numero": 0, "nom": "x", "statut": "termine", "fin": int(time.time())})
        r = await self.client.get("/api/file", headers=ENTETE)
        corps = await r.json()
        t = corps["taches"][0]
        self.assertTrue(t["en_cours"])
        self.assertEqual(42.0, t["pourcentage"])
        self.assertEqual("One More Time", t["morceau"])
        self.assertEqual("termine", corps["terminees"][0]["statut"])
        self.assertIsNone(corps["pause_jusqua"])

    async def test_annulation(self):
        await self.client.post("/api/chansons", json={"id": "3135556"}, headers=ENTETE)
        r = await self.client.delete("/api/taches/1", headers=ENTETE)
        self.assertEqual(200, r.status)
        self.assertEqual([1], self.bot.annulees)
        self.assertEqual(404, (await self.client.delete("/api/taches/42", headers=ENTETE)).status)
        self.assertEqual(400, (await self.client.delete("/api/taches/abc", headers=ENTETE)).status)

    async def test_corps_trop_gros(self):
        r = await self.client.post("/api/liens", data=b"x" * 20_000, headers=ENTETE)
        self.assertEqual(413, r.status)


class DemarrageTest(unittest.IsolatedAsyncioTestCase):
    async def test_sans_jeton_rien_ne_demarre(self):
        self.assertIsNone(await api.demarrer(FauxBot(), {}, FauxMoteur(), lambda *_: None))

    async def test_jeton_trop_court_refuse(self):
        messages = []
        self.assertIsNone(await api.demarrer(FauxBot(), {"API_TOKEN": "court"}, FauxMoteur(), messages.append))
        self.assertIn("trop court", messages[0])


if __name__ == "__main__":
    unittest.main()
