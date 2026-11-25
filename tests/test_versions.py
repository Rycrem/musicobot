"""Tests du choix de la version d'un titre (original, a cappella, instrumental, live…), sans réseau ni yt-dlp.
Lancer : python -m unittest discover tests   (nécessite aussi mutagen)"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DOSSIER", "/tmp/music")
try:
    import yt_dlp  # noqa: F401
except ImportError:   # le moteur de téléchargement n'est pas nécessaire pour tester le choix d'une version
    stub = types.ModuleType("yt_dlp")
    stub.utils = types.ModuleType("yt_dlp.utils")
    stub.utils.DownloadCancelled = type("DownloadCancelled", (Exception,), {})
    sys.modules.update({"yt_dlp": stub, "yt_dlp.utils": stub.utils})

import bibliotheque  # noqa: E402
import top  # noqa: E402

LINKIN = {"id": 92, "name": "Linkin Park"}


def piste(id_, titre, album_id, album_titre, artiste=LINKIN, duree=190):
    return {"id": id_, "title": titre, "artist": artiste, "album": {"id": album_id, "title": album_titre},
            "duration": duree}


# Réponse réelle de Deezer pour « linkin park the emptiness machine » : l'original (le single) est 1er,
# mais l'a cappella est sur un ALBUM (From Zero: A Cappellas), que le bot préférait à un single.
DEEZER = [
    piste(2978105041, "The Emptiness Machine", 1, "The Emptiness Machine"),
    piste(4175528112, "The Emptiness Machine - Unshatter Film Soundtrack (Live)", 3, "Unshatter Film Soundtrack (Live)", duree=195),
    piste(3191709331, "The Emptiness Machine (A Cappella)", 2, "From Zero: A Cappellas", duree=185),
    piste(3234807761, "The Emptiness Machine (Instrumental)", 4, "From Zero: A Cappellas + Instrumentals"),
    piste(3243700041, "The Emptiness Machine (Linkin Park Cover)", 5, "The Emptiness Machine (Linkin Park Cover)",
          artiste={"id": 7, "name": "The Guitarist"}, duree=201),
]

ALBUMS = {
    1: {"record_type": "single", "release_date": "2024-09-05", "title": "The Emptiness Machine", "artist": LINKIN},
    2: {"record_type": "album", "release_date": "2025-02-14", "title": "From Zero: A Cappellas", "artist": LINKIN},
    3: {"record_type": "album", "release_date": "2025-01-01", "title": "Unshatter Film Soundtrack (Live)", "artist": LINKIN},
    4: {"record_type": "album", "release_date": "2025-02-14", "title": "From Zero: A Cappellas + Instrumentals", "artist": LINKIN},
}


class VariantesTest(unittest.TestCase):
    def test_les_graphies_de_a_cappella_sont_reconnues(self):
        for titre in ("The Emptiness Machine (A Cappella)", "Song (Acapella)", "Song [A Capella]", "Song - A Cappella",
                      "Song (Acappella Version)", "SONG (A CAPPELLA)"):
            self.assertIn("acapella", bibliotheque.variantes(titre), titre)
            self.assertTrue(top.autre_version(titre), titre)

    def test_une_version_normale_n_est_pas_une_variante(self):
        for titre in ("The Emptiness Machine", "Capella Grey - Gyalis", "Cappella", "Chapelle", "A Capital Idea"):
            self.assertNotIn("acapella", bibliotheque.variantes(titre), titre)

    def test_les_variantes_deja_connues_le_restent(self):
        self.assertTrue(top.autre_version("The Emptiness Machine (Instrumental)"))
        self.assertTrue(top.autre_version("Song (Live at Wembley)"))
        self.assertFalse(top.autre_version("The Emptiness Machine"))
        self.assertFalse(top.autre_version("Song - Remastered 2011"))


class ChoixDeLaVersionTest(unittest.TestCase):
    def setUp(self):
        self._anciens = (top.api, bibliotheque.album)
        top.api = lambda chemin: {"data": list(DEEZER)} if chemin.startswith("search/track") else {"nb_fan": 1000}
        bibliotheque.album = lambda album_id: ALBUMS.get(album_id, {"record_type": "single", "release_date": "2024-01-01"})

    def tearDown(self):
        top.api, bibliotheque.album = self._anciens

    def test_l_original_est_propose_en_premier_pas_l_a_cappella(self):
        trouvees = top.chercher_chansons("linkin park the emptiness machine", 5)
        self.assertEqual(2978105041, trouvees[0]["id"])
        self.assertEqual("The Emptiness Machine", trouvees[0]["title"])

    def test_sans_demande_l_a_cappella_n_est_pas_proposee_a_la_place_de_l_original(self):
        ids = [c["id"] for c in top.chercher_chansons("linkin park the emptiness machine", 5)]
        self.assertEqual(1, ids.count(2978105041))
        self.assertNotEqual(3191709331, ids[0])

    def test_si_on_demande_l_a_cappella_on_la_propose(self):
        ids = [c["id"] for c in top.chercher_chansons("linkin park the emptiness machine a cappella", 5)]
        self.assertIn(3191709331, ids)      # l'a cappella, pas le live qui est le premier « autre version » de la liste
        self.assertNotIn(4175528112, ids)

    def test_si_on_demande_le_live_on_a_le_live(self):
        ids = [c["id"] for c in top.chercher_chansons("linkin park the emptiness machine live", 5)]
        self.assertIn(4175528112, ids)
        self.assertNotIn(3191709331, ids)


if __name__ == "__main__":
    unittest.main()
