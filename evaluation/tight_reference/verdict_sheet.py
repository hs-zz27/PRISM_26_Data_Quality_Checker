"""Build a sheet asking the mentor to judge more of COCO's outlines as tight or not, to set the tolerance from.

His verdicts on 21 outlines (two tight) put the tolerance at about 0.47% of the diagonal with under a pixel to
spare (RESULTS.md). This sheet shows the 28 other people whose reference is kept in review.json, with COCO's
outline only (white, as on his first sheet; no machine outline beside it to lean on), numbered in kit order,
not in the order the measure ranks them. His two tight outlines open the sheet as the yardstick.

Writes review_sheet/coco_outline_verdicts.pdf and review_sheet/coco_outline_verdicts_key.json (number -> COCO
annotation id, to copy his answers into evaluation/boundary_fit/sir_labels.json).

  .venv/bin/python -B evaluation/tight_reference/verdict_sheet.py
"""
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from sheet_tools import CHROME, CSS, label, outline, uri

HERE = Path(__file__).resolve().parent
OUT = HERE / "review_sheet"
YARDSTICK = {1236789: ("000000258388", "the fire performer"), 431398: ("000000476514", "the bride")}
WHITE = (255, 255, 255)
EXTRA_CSS = """section.photo { break-before: auto; break-inside: avoid; margin: 0 0 6mm; }
section.single { display: inline-block; width: 31.5%; margin-right: 1.5%; vertical-align: top; }
section.single .card { width: 100%; } .yardstick img { max-height: 92mm; width: auto; max-width: 100%; }
.whole img { max-height: 66mm; width: auto; max-width: 100%; } .cards { display: flex; flex-wrap: wrap; gap: 3mm; }
.card { width: 31.5%; break-inside: avoid; } .card img { width: 100%; max-height: 62mm; object-fit: contain; display: block; }
.answer { font-size: 9.5pt; margin: 3pt 0 8pt; } .box { display: inline-block; width: 9pt; height: 9pt;
border: 1.2px solid #333; vertical-align: -1pt; margin: 0 4pt 0 0; }
table.answers { border-collapse: collapse; width: 100%; } table.answers td, table.answers th { border: 1px solid #999;
padding: 3pt 5pt; font-size: 9.5pt; text-align: left; } table.answers td.n { width: 8%; } table.answers td.c { width: 16%; }"""


def shapes_of(document, cid):
    return [s["points"] for s in document["shapes"] if s.get("description") == f"COCO annotation {cid}"]


def crop(photo, polygons, size=330):
    pts = np.vstack([np.asarray(p) for p in polygons])
    x0, y0 = max(0, pts[:, 0].min() - 12), max(0, pts[:, 1].min() - 12)
    x1, y1 = min(photo.width, pts[:, 0].max() + 12), min(photo.height, pts[:, 1].max() + 12)
    scale = min(4.0, size / max(x1 - x0, y1 - y0))
    image = photo.crop((int(x0), int(y0), int(x1), int(y1))).resize(
        (round((x1 - x0) * scale), round((y1 - y0) * scale)), Image.LANCZOS)
    draw = ImageDraw.Draw(image)
    for polygon in polygons:
        outline(draw, polygon, scale, int(x0), int(y0), WHITE, 2)
    return uri(image)


def whole(photo, numbered: dict):
    """The photo at twice its size with every numbered person's outline and number."""
    image = photo.resize((photo.width * 2, photo.height * 2), Image.LANCZOS)
    draw = ImageDraw.Draw(image)
    for polygons in numbered.values():
        for polygon in polygons:
            outline(draw, polygon, 2, 0, 0, WHITE, 4)
    for number, polygons in numbered.items():
        pts = np.vstack([np.asarray(p) for p in polygons]) * 2
        top = pts[pts[:, 1].argmin()]
        label(draw, (top[0] - 14, top[1]), str(number), 30, image.size)
    return uri(image)


def people_to_judge(kit: Path, review: dict):
    """[(photo entry, [coco ids])] in kit order: kept references outside the mentor's own sheet."""
    manifest = json.loads((kit / "kit_manifest.json").read_text(encoding="utf-8"))
    out = []
    for entry in manifest["photos"]:
        if entry["category"] == "mentor_sheet":
            continue
        ids = [cid for cid in entry["coco_annotations"] if review.get(str(cid), {}).get("verdict") == "reference"]
        if ids:
            out.append((entry, ids))
    return out


def main(kit: Path = HERE / "kit", review_path: Path = HERE / "review.json"):
    review = json.loads(review_path.read_text(encoding="utf-8"))["people"]
    groups = people_to_judge(kit, review)
    key, sections, number = {}, [], 0
    for entry, ids in groups:
        document = json.loads((kit / entry["file"]).read_text(encoding="utf-8"))
        photo = Image.open(kit / entry["image"]).convert("RGB")
        numbered = {}
        for cid in ids:
            number += 1
            numbered[number] = shapes_of(document, cid)
            key[number] = {"coco_annotation": cid, "photo": entry["file"][:-5]}
        people = "".join(
            f'<div class="card"><h3>Outline {n}</h3><img src="{crop(photo, polygons)}">'
            f'<p class="answer"><span class="box"></span>Tight enough<br><span class="box"></span>Not tight enough'
            f'<br>Where, if not: ____________</p></div>' for n, polygons in numbered.items())
        overview = (f'<figure class="whole"><img src="{whole(photo, numbered)}"><figcaption>The numbers show which '
                    f'outline is which.</figcaption></figure>' if len(numbered) > 1 else "")
        kind = "photo" if len(numbered) > 1 else "photo single"
        sections.append(f'<section class="{kind}"><h2>Photo {len(sections) + 1}</h2>{overview}'
                        f'<div class="cards">{people}</div></section>')
    yardstick = ""
    for cid, (stem, name) in YARDSTICK.items():
        document = json.loads((kit / f"{stem}.json").read_text(encoding="utf-8"))
        photo = Image.open(kit / f"{stem}.jpg").convert("RGB")
        yardstick += (f'<figure class="yardstick"><img src="{crop(photo, shapes_of(document, cid), 480)}">'
                      f'<figcaption>{name.capitalize()}: you marked this outline tight</figcaption></figure>')
    rows = "".join(f'<tr><td class="n">{n}</td><td class="c"><span class="box"></span></td>'
                   f'<td class="c"><span class="box"></span></td><td></td></tr>' for n in key)
    intro = (f'<section class="first"><h1>Are these outlines tight enough?</h1>'
             f'<p>On our example sheet you marked two outlines tight, the fire performer and the bride, and the other '
             f'19 not tight. Our tool now measures how far an outline lies from the person\'s visible edge, and it '
             f'agrees with all of your verdicts. But two tight examples are too few to set how far is too far.</p>'
             f'<p>Could you mark each of the {len(key)} numbered outlines below as <b>tight enough</b> or <b>not tight '
             f'enough</b>, the same way you judged the example sheet? Where one is not tight, a word on where helps '
             f'(for example "loose at the feet"). The table on the last page is enough for the answers.</p>'
             f'<p>These are original outlines from the COCO dataset, in white, on {len(groups)} photos. Only the visible '
             f'part of each person is outlined; a person partly hidden behind someone may be drawn in separate pieces. '
             f'The two outlines you approved are shown here as the yardstick.</p>'
             f'<div class="pair">{yardstick}</div></section>')
    answers = (f'<section><h2>Answers</h2><table class="answers"><tr><th>Outline</th><th>Tight enough</th>'
               f'<th>Not tight enough</th><th>Where, if not</th></tr>{rows}</table></section>')
    OUT.mkdir(exist_ok=True)
    html = (f'<!doctype html><html><head><meta charset="utf-8"><title>Outline verdicts</title>'
            f'<style>{CSS}\n{EXTRA_CSS}</style></head><body>{intro}<div style="break-before: page">{"".join(sections)}</div>{answers}</body></html>')
    page = OUT / "coco_outline_verdicts.html"
    page.write_text(html, encoding="utf-8")
    (OUT / "coco_outline_verdicts_key.json").write_text(json.dumps(
        {"note": "Number on the verdict sheet -> COCO annotation id. Record the mentor's answers in "
                 "evaluation/boundary_fit/sir_labels.json.", "outlines": key}, indent=2) + "\n", encoding="utf-8")
    pdf = OUT / "coco_outline_verdicts.pdf"
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf}",
                    page.as_uri()], check=True, capture_output=True, timeout=180)
    print(f"{len(key)} outlines on {len(groups)} photos -> {pdf}")


if __name__ == "__main__":
    main()
