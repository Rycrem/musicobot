"""Une recherche YouTube ne doit télécharger qu'UNE vidéo (la bonne), jamais les 5 résultats.
Lancer : python -m unittest discover tests   (nécessite mutagen et aiohttp)"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DOSSIER", "/tmp/music")
try:
    import yt_dlp  # noqa: F401
except ImportError:   # le moteur de téléchargement n'est pas nécessaire ici
    stub = types.ModuleType("yt_dlp")
    stub.utils = types.ModuleType("yt_dlp.utils")
    stub.utils.DownloadCancelled = type("DownloadCancelled", (Exception,), {})
    sys.modules.update({"yt_dlp": stub, "yt_dlp.utils": stub.utils})

import playlist as dl  # noqa: E402


class DoitChoisir(unittest.TestCase):
    def test_entree_du_top_avec_recherches(self):
        # forme des entrées de top.telecharger : YouTube Music d'abord, puis la recherche YouTube
        entree = {"url": "ytsearch5:Artiste - Titre audio", "title": "Artiste - Titre", "id": None,
                  "recherches": [("https://music.youtube.com/search?q=x#songs", lambda r, d: None, True),
                                 ("ytsearch5:Artiste - Titre audio", lambda r, d: None, False)]}
        self.assertTrue(dl.doit_choisir(entree))

    def test_entree_avec_choisir_seul(self):
        self.assertTrue(dl.doit_choisir({"url": "ytsearch5:x audio", "choisir": lambda r: None}))

    def test_lien_ou_video_telecharges_tels_quels(self):
        self.assertFalse(dl.doit_choisir({"url": "https://www.youtube.com/watch?v=abc", "id": "abc"}))
        self.assertFalse(dl.doit_choisir({"url": "https://www.youtube.com/watch?v=abc", "recherches": [], "choisir": None}))


if __name__ == "__main__":
    unittest.main()
