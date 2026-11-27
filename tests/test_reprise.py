"""Reprise de la file de téléchargement après un redémarrage (reprise.py), sans Discord ni yt-dlp.
Lancer : python -m unittest discover tests"""
import os
import sys
import tempfile
import unittest
from functools import partial
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reprise  # noqa: E402


def telecharger_chanson(chanson, suivi=None):
    return chanson


def telecharger_artiste(artiste, nombre=0, suivi=None):
    return artiste, nombre


def ranger_bibliotheque(suivi=None):
    return None


MOTEUR = SimpleNamespace(telecharger_chanson=telecharger_chanson, telecharger_artiste=telecharger_artiste)


def tache(url, action=None, **kw):
    base = dict(url=url, auteur_id=42, auto=False, nom="", action=action, reprises=0)
    return SimpleNamespace(**(base | kw))


class Description(unittest.TestCase):
    def test_lien_sans_action(self):
        self.assertEqual({}, reprise.decrire(None))
        self.assertIsNone(reprise.reconstruire({}, MOTEUR))

    def test_chanson_aller_retour(self):
        fiche = {"id": 7, "title": "Halo", "artist": {"name": "Depeche Mode"}}
        d = reprise.decrire(partial(telecharger_chanson, fiche))
        self.assertEqual({"fonction": "telecharger_chanson", "args": [fiche]}, d)
        action = reprise.reconstruire(d, MOTEUR)
        self.assertEqual(fiche, action())

    def test_artiste_avec_nombre(self):
        d = reprise.decrire(partial(telecharger_artiste, {"id": 3}, 10))
        self.assertEqual(({"id": 3}, 10), reprise.reconstruire(d, MOTEUR)())

    def test_action_non_reprenable(self):
        self.assertIsNone(reprise.decrire(partial(ranger_bibliotheque)))          # /ranger : à relancer à la main
        self.assertIsNone(reprise.decrire(partial(telecharger_chanson, object())))  # argument non sérialisable
        self.assertIsNone(reprise.decrire(lambda suivi: None))

    def test_action_inconnue_a_la_relecture(self):
        self.assertIs(False, reprise.reconstruire({"fonction": "os.system", "args": ["x"]}, MOTEUR))
        self.assertIs(False, reprise.reconstruire({"fonction": "telecharger_album", "args": []}, MOTEUR))  # absente du moteur


class Fichier(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.chemin = os.path.join(self.dir.name, "file.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_ecriture_lecture_et_fichier_supprime_quand_vide(self):
        en_cours = tache("chanson:7", partial(telecharger_chanson, {"id": 7}), nom="DM - Halo")
        attente = tache("https://youtube.com/playlist?list=PL1", auto=True)
        lot = reprise.entrees([en_cours, attente, tache("ranger:bibliotheque", partial(ranger_bibliotheque))], en_cours)
        self.assertEqual(2, len(lot))   # /ranger n'est pas gardé
        reprise.ecrire(self.chemin, lot)
        lu = reprise.lire(self.chemin)
        self.assertEqual(["chanson:7", "https://youtube.com/playlist?list=PL1"], [e["url"] for e in lu])
        self.assertTrue(lu[0]["en_cours"])
        self.assertFalse(lu[1]["en_cours"])
        self.assertTrue(lu[1]["auto"])
        reprise.ecrire(self.chemin, [])
        self.assertFalse(os.path.exists(self.chemin))
        reprise.ecrire(self.chemin, [])   # déjà absent : sans erreur

    def test_fichier_absent_ou_abime(self):
        self.assertEqual([], reprise.lire(self.chemin))
        for contenu in ("", "{pas du json", '{"a": 1}', '[1, "x", {"url": ""}, {"url": "u"}]'):
            with open(self.chemin, "w", encoding="utf-8") as f:
                f.write(contenu)
            self.assertEqual([], reprise.lire(self.chemin), contenu)


class Reprises(unittest.TestCase):
    def test_une_tache_en_attente_ne_compte_pas_de_tentative(self):
        r = reprise.a_reprendre([{"url": "a", "action": {}, "reprises": 0, "en_cours": False}])
        self.assertEqual(0, r[0][1])

    def test_une_tache_en_cours_compte_une_tentative(self):
        r = reprise.a_reprendre([{"url": "a", "action": {}, "reprises": 1, "en_cours": True}])
        self.assertEqual(2, r[0][1])

    def test_abandon_apres_trop_de_tentatives(self):
        messages = []
        e = {"url": "a", "nom": "Depeche Mode", "action": {}, "reprises": reprise.REPRISES_MAX, "en_cours": True}
        self.assertEqual([], reprise.a_reprendre([e], messages.append))
        self.assertEqual(1, len(messages))
        # en attente, jamais lancée : reprise quel que soit le compteur
        e["en_cours"] = False
        self.assertEqual(1, len(reprise.a_reprendre([e])))


if __name__ == "__main__":
    unittest.main()
