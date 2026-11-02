# Short demo (about 3 minutes)

The finished result is `sample_output/sample_video.mp4` (landscape) and `sample_output/sample_video_vertical.mp4`.
`sample_output/preview_contact_sheet.jpg` shows 9 frames from the video.

## Live walk-through

1. **Web UI** - `python app.py`, open http://127.0.0.1:5000.
2. Click **Load sample listing** (fills the form and the 6 bundled photos), then **Create video**.
   Progress is shown live (script -> voice-over -> audio -> rendering). The player appears with downloads for MP4,
   captions (.srt) and the script (.json). The table under the player lists the exact on-screen text and voice-over per scene.
3. Switch **Format** to *Vertical 9:16* and create again, to get a Reels / WhatsApp status cut.
4. **Graceful errors** - clear the name and press Create: the form lists what is missing. Upload a text file renamed to
   `.jpg`: it is rejected with a message. Remove the price: the price scene disappears and a warning is shown.
5. **CLI** - `python -m pvg --input sample_data/property.json --dry-run` prints the script without rendering.
   `python -m pvg --input sample_data/property_minimal.json --quality draft` shows a listing with only a name.
6. **Trust** - open `pvg/script.py::check_facts` and `tests/test_pvg.py::TestScript` to see how invented numbers,
   claims and names are caught.

## Talking points (approach, decisions, limits)

* Problem: sales teams need one promo video per listing, fast, and **claims must be accurate**.
* Solution: validated input -> fact-grounded script -> scene planner -> Ken-Burns/overlay renderer -> ffmpeg MP4.
* Deterministic templates ensure zero hallucination risk and 100% offline generation.
* Robust to messy data: bad images skipped, missing fields degrade scene by scene, no crashes.
* Limits: robotic offline voice, English only, demo-grade web server.
