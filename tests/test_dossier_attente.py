"""Tests du dossier d'attente des téléchargements (hors de la bibliothèque) et du déplacement entre volumes.
Lancer : python -m unittest discover tests   (nécessite mutagen et aiohttp)"""
import errno
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DOSSIER", "/tmp/music")
try:
    import yt_dlp  # noqa: F401
except ImportError:   # le moteur de téléchargement n'est pas nécessaire ici
    stub = types.ModuleType("yt_dlp")
    stub.utils = types.ModuleType("yt_dlp.utils")
    stub.utils.DownloadCancelled = type("DownloadCancelled", (Exception,), {})
    sys.modules.update({"yt_dlp": stub, "yt_dlp.utils": stub.utils})

import bibliotheque  # noqa: E402
import playlist as dl  # noqa: E402


def ecrire(chemin, contenu=b"x"):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "wb") as f:
        f.write(contenu)
    return chemin


class ChoixDuDossierTest(unittest.TestCase):
    def test_variable_d_environnement_prioritaire(self):
        self.assertEqual("/ailleurs/attente", dl.choisir_dossier_attente({"DOSSIER_ATTENTE": " /ailleurs/attente "}))

    def test_par_defaut_dans_le_dossier_de_configuration(self):
        with tempfile.TemporaryDirectory() as config, \
                mock.patch.object(dl, "FICHIER_PLAYLISTS", os.path.join(config, "playlists.txt")):
            self.assertEqual(os.path.join(config, "attente"), dl.choisir_dossier_attente({}))

    def test_retour_a_l_ancien_emplacement_sans_dossier_de_configuration(self):
        with mock.patch.object(dl, "FICHIER_PLAYLISTS", "/dossier/qui/n/existe/pas/playlists.txt"):
            self.assertEqual(dl.DOSSIER_ATTENTE_ANCIEN, dl.choisir_dossier_attente({}))

    def test_retour_a_l_ancien_emplacement_si_config_non_inscriptible(self):
        with tempfile.TemporaryDirectory() as config, \
                mock.patch.object(dl, "FICHIER_PLAYLISTS", os.path.join(config, "playlists.txt")), \
                mock.patch("os.access", return_value=False):
            self.assertEqual(dl.DOSSIER_ATTENTE_ANCIEN, dl.choisir_dossier_attente({}))

    def test_l_emplacement_par_defaut_n_est_pas_dans_la_bibliotheque(self):
        # Sinon l'indexeur le voit : c'est tout l'objet du changement.
        with tempfile.TemporaryDirectory() as config, \
                mock.patch.object(dl, "FICHIER_PLAYLISTS", os.path.join(config, "playlists.txt")):
            attente = os.path.abspath(dl.choisir_dossier_attente({}))
        self.assertFalse(attente.startswith(os.path.abspath(dl.DOSSIER) + os.sep))


class DeplacerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = ecrire(os.path.join(self.tmp.name, "attente", "titre.m4a"), b"musique")
        self.cible = os.path.join(self.tmp.name, "music", "Artiste", "Album", "01 - Titre.m4a")

    def fichiers(self):
        return sorted(os.path.relpath(os.path.join(r, n), self.tmp.name) for r, _, noms in os.walk(self.tmp.name) for n in noms)

    def test_meme_volume(self):
        self.assertTrue(bibliotheque.deplacer(self.source, self.cible))
        self.assertEqual([os.path.join("music", "Artiste", "Album", "01 - Titre.m4a")], self.fichiers())

    def test_autre_volume_copie_puis_renomme_sans_laisser_de_fichier_partiel(self):
        vu = []
        vrai_replace = os.replace

        def replace_espion(src, dst):
            vu.append((os.path.basename(src), os.path.exists(dst)))   # la cible n'existe pas encore pendant la copie
            return vrai_replace(src, dst)

        with mock.patch("os.rename", side_effect=OSError(errno.EXDEV, "Invalid cross-device link")), \
                mock.patch("os.replace", side_effect=replace_espion):
            self.assertTrue(bibliotheque.deplacer(self.source, self.cible))
        self.assertEqual([os.path.join("music", "Artiste", "Album", "01 - Titre.m4a")], self.fichiers())
        with open(self.cible, "rb") as f:
            self.assertEqual(b"musique", f.read())
        self.assertEqual([(".01 - Titre.m4a.part", False)], vu)   # copié sous un nom caché, puis renommé

    def test_copie_interrompue_ne_laisse_rien_dans_la_bibliotheque_et_garde_la_source(self):
        def copie_qui_echoue(src, dst):
            ecrire(dst, b"mu")   # début de copie
            raise OSError(errno.ENOSPC, "No space left on device")

        with mock.patch("os.rename", side_effect=OSError(errno.EXDEV, "cross-device")), \
                mock.patch("shutil.copy2", side_effect=copie_qui_echoue):
            with self.assertRaises(OSError):
                bibliotheque.deplacer(self.source, self.cible)
        self.assertEqual([os.path.join("attente", "titre.m4a")], self.fichiers())   # ni partiel, ni demi-fichier

    def test_doublon_supprime_sans_copie(self):
        ecrire(self.cible, b"deja la")
        self.assertFalse(bibliotheque.deplacer(self.source, self.cible))
        self.assertEqual([os.path.join("music", "Artiste", "Album", "01 - Titre.m4a")], self.fichiers())


class DossiersParcourusTest(unittest.TestCase):
    def test_les_dossiers_d_attente_sont_parcourus_pour_ranger_les_restes(self):
        with tempfile.TemporaryDirectory() as tmp:
            music = os.path.join(tmp, "music")
            externe = os.path.join(tmp, "config", "attente")
            ecrire(os.path.join(music, "Artiste", "Album", "a.m4a"))
            ecrire(os.path.join(music, ".telechargement", "ancien.m4a"))
            ecrire(os.path.join(music, ".cache", "cache.m4a"))        # autre dossier caché : ignoré
            ecrire(os.path.join(music, "@eaDir", "x.m4a"))             # dossier système : ignoré
            ecrire(os.path.join(externe, "reste.m4a"))
            with mock.patch.object(dl, "DOSSIER", music), \
                    mock.patch.object(dl, "DOSSIER_ATTENTE_ANCIEN", os.path.join(music, ".telechargement")), \
                    mock.patch.object(dl, "DOSSIER_ATTENTE", externe):
                vus = sorted(n for _, noms in bibliotheque.dossiers_parcourus() for n in noms)
        self.assertEqual(["a.m4a", "ancien.m4a", "reste.m4a"], vus)


class ControleAuDemarrageTest(unittest.TestCase):
    def lancer(self, tmp, avec_ancien=None, avec_externe=None):
        music = os.path.join(tmp, "music")
        ancien = os.path.join(music, ".telechargement")
        externe = os.path.join(tmp, "config", "attente")
        os.makedirs(music)
        for dossier, contenu in ((ancien, avec_ancien), (externe, avec_externe)):
            if contenu is not None:
                os.makedirs(dossier, exist_ok=True)
                for nom in contenu:
                    ecrire(os.path.join(dossier, nom))
        journal = []
        with mock.patch.object(dl, "DOSSIER", music), mock.patch.object(dl, "DOSSIER_ATTENTE_ANCIEN", ancien), \
                mock.patch.object(dl, "DOSSIER_ATTENTE", externe), mock.patch.object(dl, "log", journal.append):
            bibliotheque.controler_dossiers_attente()
        return journal, ancien

    def test_ancien_dossier_vide_supprime(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal, ancien = self.lancer(tmp, avec_ancien=[])
            self.assertFalse(os.path.exists(ancien))
            self.assertTrue(any("supprimé" in l for l in journal))

    def test_ancien_dossier_avec_des_restes_est_garde_et_signale(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal, ancien = self.lancer(tmp, avec_ancien=["pink floyd.m4a", "scorpions.m4a", "note.txt"])
            self.assertTrue(os.path.isdir(ancien))
            self.assertTrue(any("2 musique(s) restée(s)" in l and "/ranger" in l for l in journal), journal)

    def test_restes_dans_le_nouveau_dossier_signales(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal, _ = self.lancer(tmp, avec_externe=["bigflo.m4a"])
            self.assertTrue(any("1 musique(s) restée(s)" in l for l in journal), journal)

    def test_rien_a_signaler(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal, _ = self.lancer(tmp)
            self.assertEqual(1, len(journal))   # seulement « Dossier d'attente des téléchargements : … »

    def test_ancien_emplacement_encore_utilise_n_est_pas_supprime(self):
        # Repli (pas de dossier de configuration) : l'ancien et le nouveau dossier sont le même.
        with tempfile.TemporaryDirectory() as tmp:
            music = os.path.join(tmp, "music")
            ancien = os.path.join(music, ".telechargement")
            os.makedirs(ancien)
            with mock.patch.object(dl, "DOSSIER", music), mock.patch.object(dl, "DOSSIER_ATTENTE_ANCIEN", ancien), \
                    mock.patch.object(dl, "DOSSIER_ATTENTE", ancien), mock.patch.object(dl, "log", lambda m: None):
                bibliotheque.controler_dossiers_attente()
            self.assertTrue(os.path.isdir(ancien))


if __name__ == "__main__":
    unittest.main()
