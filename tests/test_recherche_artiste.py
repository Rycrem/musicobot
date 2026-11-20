"""Tests de la recherche d'artistes, sans réseau ni yt-dlp : un faux Deezer reproduit le cas « Angèle ».
Lancer : python -m unittest discover tests"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DOSSIER", "/tmp/music")
try:
    import yt_dlp  # noqa: F401
except ImportError:   # le moteur de téléchargement n'est pas nécessaire pour tester la recherche
    stub = types.ModuleType("yt_dlp")
    stub.utils = types.ModuleType("yt_dlp.utils")
    stub.utils.DownloadCancelled = type("DownloadCancelled", (Exception,), {})   # lue à l'import de playlist.py
    sys.modules.update({"yt_dlp": stub, "yt_dlp.utils": stub.utils})

import top  # noqa: E402


def artiste(id_, nom, fans):
    return {"id": id_, "name": nom, "nb_fan": fans, "nb_album": 1}


# Réponse réelle de Deezer pour « Angèle » : la vraie arrive 6e, après cinq homonymes minuscules.
DEEZER = [
    artiste(420808582, "Angele & Le Dauphin", 9),
    artiste(58827562, "Angèle", 92),
    artiste(61051842, "Angèle", 168),
    artiste(76309762, "Angele", 22),
    artiste(61051852, "Angèle", 19),
    artiste(1042268, "Angèle", 1_763_950),
    artiste(15678, "Ángeles del Infierno", 112_859),
    artiste(102727, "Angèle Dubeau", 20_441),
    artiste(116523, "Los Angeles Azules", 1_525_613),
]

# Réponse réelle de Deezer pour « Adèle » : Adele (15,5 M de fans) arrive 7e, au milieu d'homonymes et de noms composés.
ADELE = [
    artiste(5203567, "Adèle & Zalem", 2005),
    artiste(5673798, "Adele", 12_522),
    artiste(76792372, "Adele & The Chandeliers", 867),
    artiste(61817012, "Adele", 497),
    artiste(69171232, "Adèle & Robin", 3887),
    artiste(76152522, "Adele & Andy", 383),
    artiste(75798, "Adele", 15_531_853),
    artiste(4027733, "A. Dele", 17),
    artiste(2710031, "Adele & Glenn", 148),
    artiste(7016369, "Adele", 639),
    artiste(55936502, "Adèle Castillon", 102_225),
    artiste(51991422, "Mortelle Adèle", 64_272),
    artiste(306607671, "ADÉLA", 31_733),
    artiste(180115117, "Adele-Hörbücher", 119),
]


class RechercheArtisteTest(unittest.TestCase):
    def setUp(self):
        self.demandes = []
        self.reponse = DEEZER

        def faux_api(chemin):
            self.demandes.append(chemin)
            return {"data": list(self.reponse)}

        self._anciens = (top.api, top.nombre_titres, top.completer_musicbrainz)
        top.api = faux_api
        top.nombre_titres = lambda a: 10
        top.completer_musicbrainz = lambda artistes: None

    def tearDown(self):
        top.api, top.nombre_titres, top.completer_musicbrainz = self._anciens

    def ids(self, nom, limite=5):
        return [a["id"] for a in top.chercher_artistes(nom, limite)]

    def test_la_vraie_angele_arrive_en_premier(self):
        trouves = top.chercher_artistes("Angèle", 5)
        self.assertEqual(1042268, trouves[0]["id"])
        self.assertEqual("Angèle", trouves[0]["name"])

    def test_on_demande_plus_de_resultats_que_ceux_gardes(self):
        top.chercher_artistes("Angèle", 5)
        self.assertIn(f"limit={top.POOL_ARTISTES}", self.demandes[0])
        self.assertGreaterEqual(top.POOL_ARTISTES, 25)

    def test_le_nombre_de_propositions_est_respecte(self):
        self.assertEqual(5, len(top.chercher_artistes("Angèle", 5)))
        self.assertEqual(2, len(top.chercher_artistes("Angèle", 2)))

    def test_adele_malgre_l_accent_et_le_septieme_rang_de_deezer(self):
        self.reponse = ADELE
        self.assertEqual(75798, self.ids("Adèle")[0])
        self.assertEqual(75798, self.ids("adele")[0])

    def test_les_artistes_connus_passent_devant_les_homonymes_minuscules(self):
        # Adèle Castillon (102 k fans) et Mortelle Adèle (64 k) devant les « Adele » à 497 et 639 fans.
        self.reponse = ADELE
        self.assertEqual([75798, 55936502, 51991422, 5673798, 69171232], self.ids("Adèle"))

    def test_angele_dubeau_remonte_devant_les_homonymes_sans_public(self):
        self.assertEqual([1042268, 102727, 61051842, 58827562, 76309762], self.ids("angele"))

    def test_un_artiste_dont_le_nom_ne_correspond_pas_est_relegue_meme_tres_connu(self):
        # Los Angeles Azules (1,5 M de fans) ne doit jamais passer devant un artiste dont le nom correspond à « Angèle ».
        self.assertNotIn(116523, self.ids("Angèle", 7))
        ordre = self.ids("Angèle", 20)
        correspondants = [1042268, 102727, 61051842, 58827562, 76309762, 61051852, 420808582]
        self.assertTrue(all(ordre.index(i) < ordre.index(116523) for i in correspondants))

    def test_le_nombre_de_titres_n_est_calcule_que_pour_les_artistes_gardes(self):
        appels = []
        top.nombre_titres = lambda a: appels.append(a["id"]) or 10
        top.chercher_artistes("Angèle", 3)
        self.assertEqual(3, len(appels))

    def test_un_nombre_de_fans_absent_ne_plante_pas(self):
        self.reponse = [artiste(1, "Zed", None), artiste(2, "Zed", 50)]
        self.assertEqual([2, 1], self.ids("Zed"))

    def test_egalite_de_score_ordre_de_deezer_conserve(self):
        self.reponse = [artiste(1, "Zed", 10), artiste(2, "Zed", 10), artiste(3, "Zed", 10)]
        self.assertEqual([1, 2, 3], self.ids("Zed"))


if __name__ == "__main__":
    unittest.main()
