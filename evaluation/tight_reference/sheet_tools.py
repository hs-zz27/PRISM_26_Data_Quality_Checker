"""Drawing and printing helpers shared by the review sheets for the mentor (review_sheet.py, verdict_sheet.py)."""
import base64
import io

from PIL import ImageFont

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"  # macOS; change for other systems
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def outline(draw, pts, scale, dx, dy, colour, width):
    q = [((x - dx) * scale, (y - dy) * scale) for x, y in pts]
    draw.line(q + [q[0]], fill=(12, 12, 12), width=width + 4, joint="curve"); draw.line(q + [q[0]], fill=colour, width=width, joint="curve")

def uri(img):
    b = io.BytesIO(); img.save(b, "JPEG", quality=88); return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()

def label(draw, xy, text, size, bounds):
    f = ImageFont.truetype(BOLD, size); l, t, r, b = draw.textbbox((0, 0), text, font=f)
    x = min(max(xy[0], 2), bounds[0] - (r - l) - 10); y = max(xy[1] - (b - t) - 10, 2)
    draw.rectangle([x, y, x + r - l + 8, y + b - t + 8], fill=(12, 12, 12)); draw.text((x + 4 - l, y + 4 - t), text, font=f, fill=(255, 255, 255))

CSS = """@page { size: A4; margin: 14mm; } body { font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt; color: #1c1c1c; margin: 0; line-height: 1.4; }
h1 { font-size: 20pt; margin: 0 0 6pt; } h2 { font-size: 14pt; margin: 0 0 6pt; } h3 { font-size: 10.5pt; margin: 6pt 0 3pt; }
section { break-before: page; } section.first { break-before: auto; }
.pair { display: flex; gap: 4mm; } figure { margin: 0; flex: 1; } figure img { width: 100%; display: block; border: 1px solid #bbb; }
figcaption { font-size: 8.8pt; color: #555; margin-top: 2pt; } .person { break-inside: avoid; display: inline-block; width: 49%; vertical-align: top; }
.person .pair figure img { max-height: 62mm; object-fit: contain; } p { margin: 0 0 6pt; }"""
