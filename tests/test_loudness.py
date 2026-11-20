"""Tests du lissage du volume, avec un vrai ffmpeg (ignorés s'il n'est pas disponible)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loudness  # noqa: E402
import normaliser_bibliotheque  # noqa: E402

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
loudness.FFMPEG = FFMPEG or "ffmpeg"


def fabriquer(chemin, lufs, duree=8, freq=440):
    """Crée un morceau de test (sinus + bruit léger) dont le niveau est d'environ [lufs] LUFS."""
    volume_db = lufs + 27.7   # décalage mesuré de cette source (sinus + bruit rose mixés)
    ext = os.path.splitext(chemin)[1]
    codec = ["-c:a", "libmp3lame"] if ext == ".mp3" else ["-c:a", "aac"]
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duree}:sample_rate=44100",
                    "-f", "lavfi", "-i", f"anoisesrc=d={duree}:c=pink:a=0.05:r=44100",
                    "-filter_complex", f"[0][1]amix=inputs=2,volume={volume_db}dB", *codec, "-b:a", "128k", chemin],
                   check=True)


def etiqueter(chemin):
    """Tags typiques de la bibliothèque : titre, artiste, album, pochette et un tag personnalisé."""
    if chemin.endswith(".mp3"):
        from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TXXX
        t = ID3()
        t.add(TIT2(encoding=3, text="Titre"))
        t.add(TPE1(encoding=3, text="Artiste"))
        t.add(TALB(encoding=3, text="Album"))
        t.add(TXXX(encoding=3, desc="DEEZER_ID", text="12345"))
        t.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=b"\xff\xd8\xff\xe0JPEGDATA"))
        t.save(chemin, v2_version=3)
    else:
        from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
        m = MP4(chemin)
        m["\xa9nam"] = ["Titre"]
        m["\xa9ART"] = ["Artiste"]
        m["\xa9alb"] = ["Album"]
        m["----:com.apple.iTunes:DEEZER_ID"] = [MP4FreeForm(b"12345")]
        m["covr"] = [MP4Cover(b"\xff\xd8\xff\xe0JPEGDATA", imageformat=MP4Cover.FORMAT_JPEG)]
        m.save()


def tags(chemin):
    if chemin.endswith(".mp3"):
        from mutagen.id3 import ID3
        t = ID3(chemin)
        return (str(t["TIT2"]), str(t["TPE1"]), str(t["TALB"]), str(t["TXXX:DEEZER_ID"]), bytes(t["APIC:"].data))
    from mutagen.mp4 import MP4
    t = MP4(chemin).tags
    return (t["\xa9nam"][0], t["\xa9ART"][0], t["\xa9alb"][0],
            bytes(t["----:com.apple.iTunes:DEEZER_ID"][0]), bytes(t["covr"][0]))


def niveau(chemin):
    return float(loudness.mesurer(chemin)["i"])


def lire(chemin):
    with open(chemin, "rb") as f:
        return f.read()


@unittest.skipUnless(FFMPEG, "ffmpeg indisponible")
class TestLissage(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dossier, True)

    def chemin(self, nom, volume_db, tag=True):
        c = os.path.join(self.dossier, nom)
        fabriquer(c, volume_db)
        if tag:
            etiqueter(c)
        return c

    def test_morceau_trop_faible_remonte_a_la_cible(self):
        for ext in (".m4a", ".mp3"):
            with self.subTest(ext):
                c = self.chemin("faible" + ext, -30)
                self.assertLess(niveau(c), -25)
                r = loudness.lisser(c)
                self.assertEqual(r.statut, "lisse", r.message)
                self.assertAlmostEqual(niveau(c), -14.0, delta=1.5)

    def test_morceau_trop_fort_redescend(self):
        c = self.chemin("fort.m4a", -8)
        r = loudness.lisser(c)
        self.assertEqual(r.statut, "lisse", r.message)
        self.assertAlmostEqual(niveau(c), -14.0, delta=1.5)

    def test_tags_et_pochette_conserves(self):
        for ext in (".m4a", ".mp3"):
            with self.subTest(ext):
                c = self.chemin("tags" + ext, -30)
                avant = tags(c)
                self.assertEqual(loudness.lisser(c).statut, "lisse")
                self.assertEqual(tags(c), avant)

    def test_duree_conservee(self):
        from mutagen import File
        c = self.chemin("duree.m4a", -30)
        avant = File(c).info.length
        loudness.lisser(c)
        self.assertAlmostEqual(File(c).info.length, avant, delta=0.3)

    def test_deux_morceaux_de_volumes_differents_finissent_au_meme_niveau(self):
        a = self.chemin("a.m4a", -34)
        b = self.chemin("b.m4a", -9)
        loudness.lisser(a)
        loudness.lisser(b)
        self.assertLess(abs(niveau(a) - niveau(b)), 1.5)

    def test_deja_au_bon_niveau_n_est_pas_reecrit(self):
        c = self.chemin("ok.m4a", -30)
        loudness.lisser(c)
        octets = lire(c)
        mtime = os.stat(c).st_mtime_ns
        r = loudness.lisser(c)
        self.assertEqual(r.statut, "deja")
        self.assertEqual(lire(c), octets)
        self.assertEqual(os.stat(c).st_mtime_ns, mtime)

    def test_essai_a_blanc_ne_modifie_rien(self):
        c = self.chemin("blanc.m4a", -30)
        octets = lire(c)
        r = loudness.lisser(c, appliquer=False)
        self.assertEqual(r.statut, "a_lisser")
        self.assertEqual(lire(c), octets)
        self.assertEqual([n for n in os.listdir(self.dossier) if n.startswith(".")], [])

    def test_fichier_corrompu_ou_silencieux_laisse_intact(self):
        faux = os.path.join(self.dossier, "faux.m4a")
        with open(faux, "wb") as f:
            f.write(b"ceci n'est pas de l'audio")
        self.assertEqual(loudness.lisser(faux).statut, "ignore")
        self.assertEqual(lire(faux), b"ceci n'est pas de l'audio")
        silence = os.path.join(self.dossier, "silence.m4a")
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=d=5",
                        "-c:a", "aac", silence], check=True)
        self.assertEqual(loudness.lisser(silence).statut, "ignore")

    def test_echec_ffmpeg_laisse_l_original(self):
        c = self.chemin("echec.m4a", -30)
        octets = lire(c)
        ancien = loudness._executer

        def faux(args, timeout=900):   # la mesure marche, l'encodage échoue
            if "-c:a" in args:
                return subprocess.CompletedProcess(args, 1, "", "erreur simulée")
            return ancien(args, timeout)
        loudness._executer = faux
        try:
            r = loudness.lisser(c)
        finally:
            loudness._executer = ancien
        self.assertEqual(r.statut, "echec")
        self.assertEqual(lire(c), octets)
        self.assertEqual([n for n in os.listdir(self.dossier) if n.startswith(".lissage")], [])

    def test_le_gain_est_plafonne_pour_ne_jamais_saturer(self):
        # Un fond très faible avec des claquements forts : monter jusqu'à -14 LUFS ferait saturer les pics.
        c = os.path.join(self.dossier, "pics.m4a")
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "aevalsrc='if(lt(mod(t,1),0.002),0.5,0.01*sin(2*PI*220*t))':d=10:s=44100",
                        "-c:a", "aac", "-b:a", "192k", c], check=True)
        avant = loudness.mesurer(c)
        r = loudness.lisser(c)
        self.assertEqual(r.statut, "lisse", r.message)
        apres = loudness.mesurer(c)
        self.assertLessEqual(apres["peak"], loudness.PIC_DEFAUT + 0.2)
        self.assertGreater(apres["i"], avant["i"])
        self.assertLess(apres["i"], -14.0)   # plafonné par les pics : la cible n'est pas atteinte, c'est voulu

    def test_gain_pour(self):
        self.assertAlmostEqual(loudness.gain_pour({"i": -24.0, "peak": -10.0}, -14.0, -1.5), 8.0)   # plafonné par le pic
        self.assertAlmostEqual(loudness.gain_pour({"i": -24.0, "peak": -30.0}, -14.0, -1.5), 10.0)
        self.assertAlmostEqual(loudness.gain_pour({"i": -8.0, "peak": -0.2}, -14.0, -1.5), -6.0)

    def test_format_non_gere(self):
        c = os.path.join(self.dossier, "x.flac")
        open(c, "wb").close()
        self.assertEqual(loudness.lisser(c).statut, "ignore")


@unittest.skipUnless(FFMPEG, "ffmpeg indisponible")
class TestScript(unittest.TestCase):
    def setUp(self):
        self.racine = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.racine, True)
        os.makedirs(os.path.join(self.racine, "Artiste", "Album"))
        os.makedirs(os.path.join(self.racine, ".telechargement"))
        self.a = os.path.join(self.racine, "Artiste", "Album", "01.m4a")
        fabriquer(self.a, -30)
        etiqueter(self.a)
        self.b = os.path.join(self.racine, "Artiste", "Album", "02.mp3")
        fabriquer(self.b, -9)
        etiqueter(self.b)
        self.cache = os.path.join(self.racine, ".telechargement", "x.m4a")
        fabriquer(self.cache, -30)

    def test_par_defaut_c_est_un_essai_a_blanc(self):
        avant = {c: lire(c) for c in (self.a, self.b, self.cache)}
        self.assertEqual(normaliser_bibliotheque.main(["--dossier", self.racine]), 0)
        for c, octets in avant.items():
            self.assertEqual(lire(c), octets)

    def test_appliquer_lisse_tout_sauf_les_dossiers_caches(self):
        cache_avant = lire(self.cache)
        tags_avant = (tags(self.a), tags(self.b))
        self.assertEqual(normaliser_bibliotheque.main(["--dossier", self.racine, "--appliquer"]), 0)
        for c in (self.a, self.b):
            self.assertAlmostEqual(niveau(c), -14.0, delta=1.5)
        self.assertEqual((tags(self.a), tags(self.b)), tags_avant)
        self.assertEqual(lire(self.cache), cache_avant)   # .telechargement ignoré

    def test_sauvegarde_et_relance_sans_effet(self):
        sauv = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, sauv, True)
        original = lire(self.a)
        normaliser_bibliotheque.main(["--dossier", self.racine, "--appliquer", "--sauvegarde", sauv])
        self.assertEqual(lire(os.path.join(sauv, "Artiste", "Album", "01.m4a")), original)
        apres = lire(self.a)
        normaliser_bibliotheque.main(["--dossier", self.racine, "--appliquer"])   # deuxième passe : rien à faire
        self.assertEqual(lire(self.a), apres)

    def test_limite(self):
        normaliser_bibliotheque.main(["--dossier", self.racine, "--appliquer", "--limite", "1", "--parallele", "1"])
        lisses = [abs(niveau(c) + 14) <= 1.5 for c in (self.a, self.b)]
        self.assertEqual(sum(lisses), 1)


if __name__ == "__main__":
    unittest.main()
