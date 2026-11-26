"""Tests de la détection « yt-dlp à mettre à jour » (calcul du lecteur YouTube en échec), sans réseau.
Lancer : python -m unittest discover tests   (nécessite yt-dlp)"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DOSSIER", tempfile.mkdtemp())

import playlist as dl  # noqa: E402

# Messages réels du journal du NAS (yt-dlp 2026.10.26, 25/11/2026)
CHALLENGE = ("[youtube] 0dpKit1Faaw: n challenge solving failed: Some formats may be missing. Ensure you have a "
             "supported JavaScript runtime and challenge solver script distribution installed.\n"
             "Please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the "
             "appropriate issue template.\nrequests = [JsChallengeRequest(type=<JsChallengeType.N: 'n'>, ...)]\n"
             "    at file:///app/$deno$stdin.js:59:36.")
SIGNATURE = "[youtube] 0dpKit1Faaw: Signature solving failed: Some formats may be missing."
FORMAT = ("ERROR: [youtube] 0dpKit1Faaw: Requested format is not available. "
          "Use --list-formats for a list of available formats")


class FauxYoutubeDL:
    """Imite yt-dlp quand YouTube a changé son lecteur : avertissements puis erreur, rien de téléchargé."""

    def __init__(self, opts):
        self.journal = opts["logger"]

    def add_post_processor(self, *a, **kw):
        pass

    def extract_info(self, url, download=True):
        self.journal.warning(CHALLENGE)
        self.journal.warning(SIGNATURE)
        self.journal.error(FORMAT)

    def close(self):
        pass


class Detection(unittest.TestCase):
    def test_messages(self):
        self.assertTrue(dl.est_ytdlp_perime(CHALLENGE))
        self.assertTrue(dl.est_ytdlp_perime(SIGNATURE))
        self.assertFalse(dl.est_ytdlp_perime(FORMAT))   # seul, ce message peut avoir d'autres causes
        self.assertFalse(dl.est_ytdlp_perime("Sign in to confirm you're not a bot"))

    def test_trace_deno_masquee(self):
        journal = dl.Journal()
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            journal.warning(CHALLENGE)
        self.assertTrue(journal.perime)
        self.assertEqual(len(sortie.getvalue().strip().splitlines()), 1)
        self.assertIn("yt-dlp à mettre à jour", sortie.getvalue())


class Arret(unittest.TestCase):
    def test_arret_sans_pause_et_musiques_retentees(self):
        entrees = [{"url": f"https://www.youtube.com/watch?v=video{n}", "id": f"video{n}", "title": f"Titre {n}"}
                   for n in range(10)]
        with tempfile.TemporaryDirectory() as dossier, \
                mock.patch.object(dl.yt_dlp, "YoutubeDL", FauxYoutubeDL), \
                mock.patch.object(dl, "RANGEMENT", None), \
                mock.patch.object(dl, "ARCHIVE", os.path.join(dossier, "archive.txt")), \
                mock.patch.object(dl.time, "sleep") as pause, \
                redirect_stdout(io.StringIO()) as sortie:
            r = dl.telecharger("essai", entrees=entrees, titre="Essai", dossier=dossier)
        self.assertTrue(r["perime"])
        self.assertFalse(r["bloque"])
        self.assertEqual(r["erreurs"], 0)          # pas de vrais échecs : retentées la fois suivante
        self.assertEqual(r["index_ok"], [])
        pause.assert_not_called()                  # ni pause anti-blocage…
        self.assertNotIn("Nouvelle session", sortie.getvalue())   # …ni nouvelle session
        self.assertIn(f"arrêt (0/{len(entrees)} traitées)", sortie.getvalue())


if __name__ == "__main__":
    unittest.main()
