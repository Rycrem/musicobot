"""Signalement des nouvelles musiques à l'indexeur du NAS (indexation.py), sans NAS.
Lancer : python -m unittest discover tests"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bibliotheque  # noqa: E402
import indexation  # noqa: E402


class Indexation(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.liste = os.path.join(self.dir.name, "a_indexer.txt")
        self.ancien = indexation.FICHIER_INDEX
        indexation.FICHIER_INDEX = self.liste

    def tearDown(self):
        indexation.FICHIER_INDEX = self.ancien
        self.dir.cleanup()

    def lignes(self):
        with open(self.liste, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_signaler_ajoute_une_ligne_par_fichier(self):
        indexation.signaler("/music/Renan Luce/Repenti/01 - Les voisines.m4a")
        indexation.signaler("/music/Renan Luce/Repenti/02 - Chien mouillé.m4a")
        self.assertEqual(["/music/Renan Luce/Repenti/01 - Les voisines.m4a",
                          "/music/Renan Luce/Repenti/02 - Chien mouillé.m4a"], self.lignes())

    def test_liste_desactivee_ou_inaccessible_sans_erreur(self):
        indexation.FICHIER_INDEX = ""
        indexation.signaler("/music/a.m4a")
        indexation.FICHIER_INDEX = os.path.join(self.dir.name, "absent", "liste.txt")
        indexation.signaler("/music/a.m4a")   # dossier inexistant : on ignore
        self.assertFalse(os.path.exists(self.liste))

    def test_deplacer_signale_un_nouveau_fichier_pas_un_doublon(self):
        source = os.path.join(self.dir.name, "attente.m4a")
        cible = os.path.join(self.dir.name, "music", "Artiste", "Album", "01 - Titre.m4a")
        with open(source, "wb") as f:
            f.write(b"x")
        self.assertTrue(bibliotheque.deplacer(source, cible))
        self.assertEqual([cible], self.lignes())
        # même musique déjà là : le doublon est supprimé, rien à indexer de plus
        with open(source, "wb") as f:
            f.write(b"x")
        self.assertFalse(bibliotheque.deplacer(source, cible))
        self.assertEqual([cible], self.lignes())


if __name__ == "__main__":
    unittest.main()
