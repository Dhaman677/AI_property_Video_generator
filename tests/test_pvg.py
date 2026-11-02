"""Run with:  python -m unittest discover -s tests -v"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pvg import InputError, PropertyDetails, RenderOptions, generate_video
from pvg.audio import generate_music, mix_tracks, narration_engine, SR
from pvg.images import load_images, match_images_to_features
from pvg.models import Feature
from pvg.pipeline import build_srt, validate_details
from pvg.script import check_facts, generate_script, speech_normalize, template_script
from pvg.video import probe_duration

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data" / "property.json"


def make_img(path: Path, size=(800, 600), color=(120, 160, 200)):
    im = Image.new("RGB", size, color)
    for i in range(0, size[0], 40):  # some texture so the quality check passes
        for j in range(0, size[1], 40):
            if (i + j) // 40 % 2:
                im.paste((color[0] // 2, color[1], 255 - color[2] // 2), (i, j, i + 40, j + 40))
    im.save(path)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def details(self, n_images=4, **kw):
        imgs = [make_img(self.tmp / f"photo{i}.jpg", color=(60 + i * 20, 100, 180 - i * 10)) for i in range(n_images)]
        d = PropertyDetails.from_dict({"name": "Test Villa", "location": "Pune", "price": "₹1.2 Cr",
                                       "property_type": "3 BHK villa", "features": ["Private garden", "Home theatre"],
                                       "cta_text": "Call us today", "contact": "+91 90000 00000"})
        d.images = imgs
        for k, v in kw.items():
            setattr(d, k, v)
        return d


class TestValidation(Base):
    def test_name_and_images_required(self):
        with self.assertRaises(InputError) as cm:
            validate_details(PropertyDetails())
        self.assertEqual(len(cm.exception.problems), 2)

    def test_missing_optional_fields_become_warnings(self):
        d = PropertyDetails(name="X")
        d.images = [self.tmp / "a.jpg"]
        warnings = validate_details(d)
        self.assertTrue(any("price" in w for w in warnings))
        self.assertTrue(any("features" in w for w in warnings))

    def test_json_errors_are_friendly(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{not json")
        with self.assertRaises(InputError) as cm:
            PropertyDetails.from_json_file(bad)
        self.assertIn("not valid JSON", str(cm.exception))
        with self.assertRaises(InputError):
            PropertyDetails.from_json_file(self.tmp / "missing.json")

    def test_sample_json_loads(self):
        d = PropertyDetails.from_json_file(SAMPLE)
        self.assertEqual(len(d.features), 5)
        self.assertEqual(len(d.images), 6)
        self.assertTrue(all(p.exists() for p in d.images))

    def test_text_is_cleaned(self):
        d = PropertyDetails.from_dict({"name": "  A\x00  B \n C ", "features": "x, y\nz"})
        self.assertEqual(d.name, "A B C")
        self.assertEqual([f.text for f in d.features], ["x", "y", "z"])

    def test_bad_aspect_rejected(self):
        with self.assertRaises(InputError):
            RenderOptions(aspect="4:3").size()


class TestImages(Base):
    def test_bad_images_are_skipped_with_warnings(self):
        good = make_img(self.tmp / "good.jpg")
        corrupt = self.tmp / "corrupt.jpg"
        corrupt.write_bytes(b"not an image")
        tiny = make_img(self.tmp / "tiny.png", size=(50, 50))
        txt = self.tmp / "notes.txt"
        txt.write_text("hi")
        imgs, warns = load_images([good, corrupt, tiny, txt, self.tmp / "missing.jpg", good])
        self.assertEqual(len(imgs), 1)
        joined = " ".join(warns)
        for needle in ("corrupt.jpg", "tiny.png", "notes.txt", "missing.jpg", "Duplicate"):
            self.assertIn(needle, joined)

    def test_more_than_eight_truncated(self):
        paths = [make_img(self.tmp / f"i{i}.jpg", color=(i * 20, 90, 150)) for i in range(10)]
        imgs, warns = load_images(paths)
        self.assertEqual(len(imgs), 8)
        self.assertTrue(any("only the first 8" in w for w in warns))

    def test_feature_image_matching(self):
        names = ["exterior", "kitchen", "swimming_pool", "bedroom"]
        imgs, _ = load_images([make_img(self.tmp / f"{n}.jpg", color=(40 + 30 * i, 90, 160)) for i, n in enumerate(names)])
        feats = [Feature("Swimming pool and clubhouse"), Feature("Modular kitchen"), Feature("Garden", image="bedroom.jpg")]
        m = match_images_to_features(feats, imgs)
        self.assertEqual([imgs[i].path.stem for i in m], ["swimming_pool", "kitchen", "bedroom"])


class TestScript(Base):
    def test_template_uses_only_supplied_facts(self):
        d = self.details()
        scenes = template_script(d)
        self.assertEqual([s.kind for s in scenes], ["title", "feature", "feature", "price", "cta"])
        self.assertEqual(check_facts(d, scenes), [])

    def test_fact_checker_catches_invented_claims(self):
        d = self.details()
        scenes = template_script(d)
        scenes[1].narration = "Just 5 minutes from the metro and airport, with a guaranteed discount."
        problems = " ".join(check_facts(d, scenes))
        self.assertIn("number '5'", problems)
        self.assertIn("metro", problems)
        self.assertIn("discount", problems)

    def test_fact_checker_catches_invented_proper_nouns(self):
        d = self.details()
        scenes = template_script(d)
        scenes[0].narration = "Introducing Test Villa in the heart of Koregaon Park."
        self.assertTrue(any("Koregaon" in p for p in check_facts(d, scenes)))

    def test_more_than_five_features_limited(self):
        d = self.details()
        d.features = [Feature(f"Feature {i}") for i in range(8)]
        scenes, warns, _ = generate_script(d)
        self.assertEqual(sum(s.kind == "feature" for s in scenes), 5)
        self.assertTrue(warns)

    def test_speech_normalisation(self):
        self.assertEqual(speech_normalize("₹85 Lakhs"), "85 Lakhs rupees")
        self.assertIn("B H K", speech_normalize("2 BHK"))
        self.assertIn("24 by 7", speech_normalize("24x7 security"))


class TestAudio(unittest.TestCase):
    def test_music_and_mix(self):
        m = generate_music(6.0)
        self.assertEqual(m.shape, (6 * SR, 2))
        self.assertLessEqual(abs(m).max(), 0.6)
        import numpy as np
        voice = np.ones(SR, dtype="float32") * 0.5
        mix = mix_tracks(6.0, [(1.0, voice)], m)
        self.assertEqual(mix.shape, (6 * SR, 2))
        self.assertLessEqual(abs(mix).max(), 0.96)


class TestSrt(unittest.TestCase):
    def test_srt_format(self):
        s = build_srt([(0.25, 3.5, "Hello"), (3661.5, 3663.0, "Late")])
        self.assertIn("00:00:00,250 --> 00:00:03,500", s)
        self.assertIn("01:01:01,500 --> 01:01:03,000", s)


class TestEndToEnd(Base):
    def test_video_is_created_without_audio(self):
        d = self.details(n_images=4)
        res = generate_video(d, self.tmp / "out" / "v.mp4", RenderOptions(quality="draft", narration=False, music=False))
        self.assertTrue(res.video_path.stat().st_size > 10_000)
        self.assertGreater(probe_duration(res.video_path), 10)
        self.assertTrue(res.srt_path.exists() and res.script_path.exists())

    @unittest.skipUnless(narration_engine(), "no TTS engine installed")
    def test_narration_and_vertical_format(self):
        d = self.details(n_images=2)  # fewer photos than scenes -> photos are re-used
        res = generate_video(d, self.tmp / "v.mp4", RenderOptions(aspect="9:16", quality="draft"))
        self.assertEqual(res.size[0] % 2, 0)
        self.assertLess(res.size[0], res.size[1])
        self.assertIsNotNone(res.narration)
        self.assertTrue(any("re-used" in w or "Images will be reused" in w for w in res.warnings))

    def test_many_images_become_gallery_scenes(self):
        d = self.details(n_images=8)
        d.features = [Feature("Private garden")]
        res = generate_video(d, self.tmp / "g.mp4", RenderOptions(quality="draft", narration=False, music=False))
        self.assertGreaterEqual(sum(s.kind == "gallery" for s in res.scenes), 3)

    def test_unusable_images_give_clear_error(self):
        d = self.details(n_images=0)
        d.images = [self.tmp / "nope.jpg"]
        with self.assertRaises(InputError) as cm:
            generate_video(d, self.tmp / "x.mp4", RenderOptions(quality="draft"))
        self.assertIn("nope.jpg", str(cm.exception))

    def test_partial_failure_still_renders(self):
        d = self.details(n_images=4)
        bad = self.tmp / "broken.jpg"
        bad.write_bytes(b"xx")
        d.images.append(bad)
        d.price = ""
        res = generate_video(d, self.tmp / "p.mp4", RenderOptions(quality="draft", narration=False, music=False))
        self.assertTrue(res.video_path.exists())
        self.assertTrue(any("broken.jpg" in w for w in res.warnings))
        self.assertNotIn("price", [s.kind for s in res.scenes])

    def test_missing_music_file_is_a_warning(self):
        d = self.details()
        res = generate_video(d, self.tmp / "m.mp4", RenderOptions(quality="draft", narration=False, music_file=str(self.tmp / "no.mp3")))
        self.assertTrue(any("music" in w.lower() for w in res.warnings))


class TestWeb(Base):
    def test_api_validation(self):
        from webapp.app import create_app
        c = create_app(self.tmp / "jobs").test_client()
        r = c.post("/api/generate", data={"name": ""})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(r.json["problems"]), 2)
        self.assertEqual(c.get("/api/jobs/" + "0" * 32).status_code, 404)
        self.assertEqual(c.get("/files/../../etc/passwd").status_code, 404)


if __name__ == "__main__":
    unittest.main()
