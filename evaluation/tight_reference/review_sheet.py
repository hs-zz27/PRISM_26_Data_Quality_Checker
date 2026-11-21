"""Build the review sheet for the mentor: his example photos, original outlines next to the redrawn references.

Only people kept as references in review.json are shown. Writes review_sheet/redrawn_review.pdf (via headless Chrome).

  .venv/bin/python -B evaluation/tight_reference/review_sheet.py
"""
import json, subprocess
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TR = HERE
OUT = HERE / "review_sheet"
OUT.mkdir(exist_ok=True)
from sheet_tools import CHROME, CSS, label, outline, uri  # noqa: E402
review = json.loads((TR / "review.json").read_text())["people"]
kit = json.loads((TR / "kit/kit_manifest.json").read_text())
PHOTOS = [p for p in kit["photos"] if p["category"] == "mentor_sheet"]
LETTERS = "ABCDEFGHIJ"
APPROVED = {1236789: "the fire performer", 431398: "the bride"}

sections = []
for entry in PHOTOS:
    old = json.loads((TR / "kit" / entry["file"]).read_text()); new = json.loads((TR / "drawn" / entry["file"]).read_text())
    photo = Image.open(TR / "kit" / entry["image"]).convert("RGB")
    refs = [cid for cid in entry["coco_annotations"] if review[str(cid)]["verdict"] == "reference"]
    letters = {cid: LETTERS[i] for i, cid in enumerate(refs)}
    def whole(doc, colour):
        img = photo.resize((photo.width * 2, photo.height * 2), Image.LANCZOS); d = ImageDraw.Draw(img)
        for cid in refs:
            for s in doc["shapes"]:
                if s["description"] == f"COCO annotation {cid}": outline(d, s["points"], 2, 0, 0, colour, 4)
        for cid in refs:
            pts = np.vstack([s["points"] for s in doc["shapes"] if s["description"] == f"COCO annotation {cid}"]) * 2
            top = pts[pts[:, 1].argmin()]  # the label sits just above the person's highest point
            label(d, (top[0] - 14, top[1]), letters[cid], 30, img.size)
        return img
    crops = ""
    for cid in refs:
        shapes = {name: [s["points"] for s in doc["shapes"] if s["description"] == f"COCO annotation {cid}"] for name, doc in (("old", old), ("new", new))}
        pts = np.vstack([np.asarray(p) for v in shapes.values() for p in v]); x0, y0 = max(0, pts[:, 0].min() - 12), max(0, pts[:, 1].min() - 12)
        x1, y1 = min(photo.width, pts[:, 0].max() + 12), min(photo.height, pts[:, 1].max() + 12); sc = min(4.0, 330 / max(x1 - x0, y1 - y0))
        pair = []
        for name, colour in (("old", (255, 255, 255)), ("new", (40, 220, 110))):
            c = photo.crop((int(x0), int(y0), int(x1), int(y1))).resize((round((x1 - x0) * sc), round((y1 - y0) * sc)), Image.LANCZOS); d = ImageDraw.Draw(c)
            for p in shapes[name]: outline(d, p, sc, int(x0), int(y0), colour, 2)
            pair.append(uri(c))
        note = f" ({APPROVED[cid]}: you already marked this one tight)" if cid in APPROVED else ""
        crops += f'<div class="person"><h3>Person {letters[cid]}{note}</h3><div class="pair"><figure><img src="{pair[0]}"><figcaption>Before: the original outline</figcaption></figure><figure><img src="{pair[1]}"><figcaption>After: redrawn to follow the body</figcaption></figure></div></div>'
    sections.append(f'''<section><h2>Photo {len(sections) + 1}</h2>
<div class="pair"><figure><img src="{uri(whole(old, (255, 255, 255)))}"><figcaption>Before: the original outlines (white)</figcaption></figure>
<figure><img src="{uri(whole(new, (40, 220, 110)))}"><figcaption>After: the redrawn outlines (green)</figcaption></figure></div>
{crops}</section>''')

intro = f'''<section class="first"><h1>Redrawn tight outlines for your review</h1>
<p>You said the fire performer and the bride are drawn tightly enough and the other outlines on our example sheet are not. We redrew the outlines to follow each person's visible body closely, so we can test our tool against outlines you agree are tight. The fire performer and the bride are redrawn too and marked, so you can compare them with the originals you approved.</p>
<p>Could you tell us which redrawn outlines are tight enough, and which are not? Your answer becomes the standard our tool is tested against.</p>
<p>This sheet shows your example photos with the original outline next to the redrawn one, first for the whole photo and then person by person. Only the visible part of each person is outlined; a person partly hidden behind someone is drawn in separate pieces.</p>
<p>The redrawn outlines were made with a segmentation model and checked by us one by one. People who are too small, too dark or too hidden to outline reliably are not included.</p></section>'''
html = f'<!doctype html><html><head><meta charset="utf-8"><title>Redrawn outlines for review</title><style>{CSS}</style></head><body>{intro}{"".join(sections)}</body></html>'
(OUT / "redrawn_review.html").write_text(html, encoding="utf-8")
pdf = OUT / "redrawn_review.pdf"
subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf}", (OUT / "redrawn_review.html").as_uri()], check=True, capture_output=True, timeout=180)
print("people:", sum(1 for p in PHOTOS for cid in p["coco_annotations"] if review[str(cid)]["verdict"] == "reference"), "photos:", len(PHOTOS), "->", pdf)
