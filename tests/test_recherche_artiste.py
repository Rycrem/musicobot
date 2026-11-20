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
]


class RechercheArtisteTest(unittest.TestCase):
    def setUp(self):
        self.demandes = []

        def faux_api(chemin):
            self.demandes.append(chemin)
            return {"data": list(DEEZER)}

        self._anciens = (top.api, top.nombre_titres, top.completer_musicbrainz)
        top.api = faux_api
        top.nombre_titres = lambda a: 10
        top.completer_musicbrainz = lambda artistes: None

    def tearDown(self):
        top.api, top.nombre_titres, top.completer_musicbrainz = self._anciens

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

    def test_noms_exacts_d_abord_par_popularite_sans_tenir_compte_des_accents(self):
        ids = [a["id"] for a in top.chercher_artistes("angele", 5)]
        self.assertEqual([1042268, 61051842, 58827562, 76309762, 61051852], ids)

    def test_les_autres_artistes_suivent_dans_l_ordre_de_deezer(self):
        ids = [a["id"] for a in top.chercher_artistes("Angèle", 8)]
        self.assertEqual([420808582, 15678, 102727], ids[5:])

    def test_le_nombre_de_titres_n_est_calcule_que_pour_les_artistes_gardes(self):
        appels = []
        top.nombre_titres = lambda a: appels.append(a["id"]) or 10
        top.chercher_artistes("Angèle", 3)
        self.assertEqual(3, len(appels))

    def test_un_nombre_de_fans_absent_ne_plante_pas(self):
        top.api = lambda chemin: {"data": [artiste(1, "Zed", None), artiste(2, "Zed", 50)]}
        self.assertEqual([2, 1], [a["id"] for a in top.chercher_artistes("Zed", 5)])


if __name__ == "__main__":
    unittest.main()
