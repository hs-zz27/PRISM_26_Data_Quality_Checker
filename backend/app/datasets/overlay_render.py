"""Draws annotation overlays for people to inspect. Display only.

Nothing here reads or writes annotation data. Geometry arrives as plain numbers, is
multiplied by one uniform preview scale, and is drawn exactly where the source says. It
is never tightened, normalized, repaired or replaced. If part of it cannot be drawn
safely it is left out of the picture and the reason is returned so the report can say so.

Layout (all separate from each other):
    [ image panel ][ object list ]     numbered markers sit on the image
    [ how to read + diagnostics + not-drawn notes, wrapped ]

Safety limits: coordinates beyond +-MAX_COORD are not drawn, lines are clipped to the
panel analytically (O(1) per segment, so huge values cannot cause long loops), and the
output size is validated before any canvas is allocated.
"""

from dataclasses import dataclass, field
from io import BytesIO
import math

from PIL import Image, ImageDraw, ImageFont

MAX_COORD = 1e7
MAX_VERTICES_PER_ELEMENT = 50_000
MAX_LISTED_OBJECTS = 80
MAX_DIAGNOSTIC_LINES = 60
TARGET_LONG_SIDE = 1000
MAX_AUTO_SCALE = 4.0
MAX_PANEL_PIXELS = 24_000_000
MAX_CANVAS_PIXELS = 36_000_000
MAX_CANVAS_SIDE = 12_000
SIDEBAR_WIDTH = 340
MIN_CANVAS_WIDTH = 760
LINE_WIDTH = 2
DASH, GAP = 9, 6
HALO = (20, 20, 20)
PALETTE = [(230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48), (145, 30, 180),
           (0, 170, 170), (240, 50, 230), (170, 190, 20), (250, 150, 180), (0, 100, 100),
           (170, 110, 40), (128, 0, 0)]


class OverlayTooLarge(ValueError):
    """The requested preview would be larger than the safety limits allow."""


@dataclass
class OverlayObject:
    number: int                      # display number only; not a source identifier
    label: str
    ident: str                       # source identifier text (or JSON pointer)
    box: tuple | None = None         # (x0, y0, x1, y1) exactly as stored
    box_origin: str | None = None    # "supplied" or "derived_from_polygon"
    polygons: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)   # [(code, message)]
    empty_reason: str | None = None


@dataclass
class ObjectOutcome:
    number: int
    status: str                      # drawn | drawn_clipped | outside_image | not_drawn
    notes: list = field(default_factory=list)


@dataclass
class RenderReport:
    scale: float
    panel_size: tuple
    canvas_size: tuple
    outcomes: list


def color_for(number: int):
    return PALETTE[(number - 1) % len(PALETTE)]


def _text_color(color):
    return (0, 0, 0) if 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2] > 150 else (255, 255, 255)


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def plan_scale(width: int, height: int, requested: float | None = None):
    """Decide the uniform preview scale from the DECODED size and validate the panel."""
    if width < 1 or height < 1:
        raise OverlayTooLarge("Image has no pixels.")
    if requested is not None and not (math.isfinite(requested) and requested > 0):
        raise OverlayTooLarge("Scale must be a positive finite number.")
    scale = requested if requested is not None else min(MAX_AUTO_SCALE, TARGET_LONG_SIDE / max(width, height))
    panel_w, panel_h = max(1, round(width * scale)), max(1, round(height * scale))
    if panel_w * panel_h > MAX_PANEL_PIXELS or max(panel_w, panel_h) > MAX_CANVAS_SIDE:
        raise OverlayTooLarge(f"Preview panel would be {panel_w}x{panel_h} pixels (limit "
                              f"{MAX_PANEL_PIXELS:,} pixels, {MAX_CANVAS_SIDE:,} per side).")
    return scale, panel_w, panel_h


def clip_segment(p, q, width, height):
    """Liang-Barsky clip of segment p-q to [0, width] x [0, height]; None if fully outside."""
    (x0, y0), (x1, y1) = p, q
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for edge, bound in ((-dx, x0), (dx, width - x0), (-dy, y0), (dy, height - y0)):
        if edge == 0:
            if bound < 0:
                return None
        else:
            ratio = bound / edge
            if edge < 0:
                if ratio > t1:
                    return None
                t0 = max(t0, ratio)
            else:
                if ratio < t0:
                    return None
                t1 = min(t1, ratio)
    return (x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy), (t0 > 0 or t1 < 1)


def _dash_pieces(a, b):
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length == 0:
        return []
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    pieces, position = [], 0.0
    while position < length:
        stop = min(position + DASH, length)
        pieces.append(((a[0] + ux * position, a[1] + uy * position), (a[0] + ux * stop, a[1] + uy * stop)))
        position += DASH + GAP
    return pieces


def _bad_coordinates(points):
    for x, y in points:
        if not (isinstance(x, (int, float)) and isinstance(y, (int, float))):
            return "contains a non-numeric coordinate"
        if not (math.isfinite(x) and math.isfinite(y)):
            return "contains a non-finite coordinate"
        if abs(x) > MAX_COORD or abs(y) > MAX_COORD:
            return f"has a coordinate beyond the preview limit (+-{MAX_COORD:g})"
    return None


def _prepare(obj: OverlayObject, sx, sy, width, height):
    """Turn one object's geometry into drawable pieces. Returns (pieces, outcome, bounds)."""
    elements = []     # (name, points, closed, dashed)
    if obj.box is not None:
        x0, y0, x1, y1 = obj.box
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]   # as supplied, even if inverted
        dashed = obj.box_origin == "derived_from_polygon"
        elements.append(("derived box" if dashed else "box", corners, True, dashed))
    for index, part in enumerate(obj.polygons, 1):
        elements.append((f"polygon part {index}", list(part), True, False))
    outcome = ObjectOutcome(obj.number, "not_drawn")
    pieces, all_x, all_y, clipped, visible = [], [], [], False, False
    if not elements:
        outcome.notes.append(obj.empty_reason or "The object has no box or polygon geometry.")
        return pieces, outcome, None
    for name, points, closed, dashed in elements:
        if len(points) > MAX_VERTICES_PER_ELEMENT:
            outcome.notes.append(f"{name} has {len(points):,} vertices, over the preview limit "
                                 f"({MAX_VERTICES_PER_ELEMENT:,}); kept in the report, not drawn.")
            continue
        problem = _bad_coordinates(points)
        if problem:
            outcome.notes.append(f"{name} {problem}; kept in the report, not drawn.")
            continue
        scaled = [(x * sx, y * sy) for x, y in points]
        ring = scaled + [scaled[0]] if closed and len(scaled) > 1 else scaled
        element_visible = False
        for a, b in zip(ring, ring[1:]):
            hit = clip_segment(a, b, width, height)
            if hit is None:
                continue
            element_visible, clipped = True, clipped or hit[2]
            segments = _dash_pieces(hit[0], hit[1]) if dashed else [(hit[0], hit[1])]
            pieces.extend((s, dashed) for s in segments)
            all_x += [hit[0][0], hit[1][0]]
            all_y += [hit[0][1], hit[1][1]]
        if element_visible:
            visible = True
        else:
            outcome.notes.append(f"{name} lies entirely outside the image area.")
    if visible:
        outcome.status = "drawn_clipped" if clipped else "drawn"
        if clipped:
            outcome.notes.append("Part of the geometry extends beyond the image edge; it is "
                                 "clipped at the edge in this preview only.")
        return pieces, outcome, (min(all_x), min(all_y), max(all_x), max(all_y))
    outcome.status = "outside_image" if not any("not drawn" in n for n in outcome.notes) else "not_drawn"
    return pieces, outcome, None


def wrap_text(text, font, width):
    lines = []
    for paragraph in str(text).split("\n"):
        current = ""
        for word in paragraph.split(" "):
            while font.getlength(word) > width and len(word) > 1:      # break very long words
                cut = len(word) - 1
                while cut > 1 and font.getlength(word[:cut]) > width:
                    cut -= 1
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:cut])
                word = word[cut:]
            trial = f"{current} {word}".strip()
            if font.getlength(trial) <= width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return [line for line in lines if line is not None]


def _fit(text, font, width):
    if font.getlength(text) <= width:
        return text
    while len(text) > 1 and font.getlength(text + "...") > width:
        text = text[:-1]
    return text + "..."


def _overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _place_badge(size, bounds, occupied, panel):
    """Pick a spot next to the object that avoids other badges; least overlap otherwise."""
    w, h = size
    x0, y0, x1, y1 = bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    candidates = [(x0, y0 - h - 1), (x0 + 1, y0 + 1), (x1 - w, y0 - h - 1), (x1 - w - 1, y0 + 1),
                  (x0, y1 + 1), (x0 + 1, y1 - h - 1), (cx - w / 2, cy - h / 2)]
    # Keep widening the search around the object until a free spot appears (leader lines
    # tie displaced numbers back to their object).
    steps = ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, -1), (0, 1), (-1, 0), (1, 0),
             (-2, -1), (2, -1), (-2, 1), (2, 1), (-1, -2), (1, -2), (-1, 2), (1, 2))
    for ring in range(1, 16):
        for dx, dy in steps:
            candidates.append((x0 + dx * ring * (w * 0.55 + 2), y0 + dy * ring * (h + 3) * 0.75))
    best = None
    for left, top in candidates:
        left = min(max(left, 0), max(panel[0] - w, 0))
        top = min(max(top, 0), max(panel[1] - h, 0))
        rect = (left, top, left + w, top + h)
        cost = sum(_overlap(rect, other) for other in occupied)
        if cost == 0:
            return rect
        if best is None or cost < best[0]:
            best = (cost, rect)
    return best[1]


def _ident_text(obj):
    return f"{obj.number}  {obj.label}  [{obj.ident}]"


def render_overlay(image_bytes: bytes, objects: list, *, scale: float | None = None,
                   synthetic: bool = False, declared_note: str | None = None,
                   show_vertices: bool = False):
    """Return (PIL image, RenderReport). Raises OverlayTooLarge before allocating big canvases."""
    with Image.open(BytesIO(image_bytes)) as probe:
        width, height = probe.size
    factor, panel_w, panel_h = plan_scale(width, height, scale)
    sx, sy = panel_w / width, panel_h / height          # from the DECODED size only

    prepared = [_prepare(obj, sx, sy, panel_w, panel_h) for obj in objects]
    outcomes = [p[1] for p in prepared]

    font, small, bold = _font(14), _font(13), _font(15)
    canvas_w = max(panel_w + SIDEBAR_WIDTH, MIN_CANVAS_WIDTH)
    text_w = canvas_w - 24

    flagged = {o.number for o in objects if o.diagnostics}
    derived = {o.number for o in objects if o.box_origin == "derived_from_polygon" and o.box is not None}

    # ---- bottom text block (laid out first so the canvas can be validated before allocating)
    blocks = []     # (text, font, color)
    if synthetic:
        blocks.append(("SYNTHETIC FIXTURE - not a real dataset.", bold, (160, 0, 0)))
    blocks.append(("How to read this preview", bold, (0, 0, 0)))
    blocks.append(("Solid lines: original geometry exactly as supplied in the source file.", small, (0, 0, 0)))
    blocks.append(("Dashed lines: geometry derived by this tool from supplied polygons "
                   "(a box from polygon vertices). It is not in the source file.", small, (0, 0, 0)))
    blocks.append(("Numbers on the image are preview numbers only, not source IDs. The list at the "
                   "right gives each object's class and source identifier. Colours match between "
                   "an object's lines, its number and its list entry.", small, (0, 0, 0)))
    blocks.append(("*  after a number: this object's box is derived (dashed).   "
                   "!  after a number: this object has diagnostics, listed below.", small, (0, 0, 0)))
    note = (f"Image shown at {factor:.2f}x ({width}x{height} decoded pixels). Coordinates are only "
            "multiplied by this scale; the declared image size is never used.")
    if declared_note:
        note += " " + declared_note
    blocks.append((note, small, (0, 0, 0)))
    diag_lines = []
    for obj in objects:
        for code, message in obj.diagnostics:
            diag_lines.append(f"#{obj.number} {obj.label} [{obj.ident}]: {code} - {message}")
    if diag_lines:
        blocks.append((f"Diagnostics ({len(diag_lines)}). A diagnostic is a structural fact, "
                       "not a quality verdict.", bold, (0, 0, 0)))
        for line in diag_lines[:MAX_DIAGNOSTIC_LINES]:
            blocks.append((line, small, (120, 0, 0)))
        if len(diag_lines) > MAX_DIAGNOSTIC_LINES:
            blocks.append((f"... {len(diag_lines) - MAX_DIAGNOSTIC_LINES} more in the text report.", small, (120, 0, 0)))
    omitted = [(o, n) for o, oc in zip(objects, outcomes) for n in oc.notes]
    if omitted:
        blocks.append(("Not drawn, or only partly drawn (still present in the report):", bold, (0, 0, 0)))
        for obj, text in omitted[:MAX_DIAGNOSTIC_LINES]:
            blocks.append((f"#{obj.number} {obj.label} [{obj.ident}]: {text}", small, (90, 60, 0)))

    wrapped = []
    for text, face, color in blocks:
        for line in wrap_text(text, face, text_w):
            wrapped.append((line, face, color))
    line_h = 19
    bottom_h = 12 + line_h * len(wrapped) + 8

    listed = objects[:MAX_LISTED_OBJECTS]
    row_h = 22
    sidebar_h = 34 + row_h * len(listed) + (row_h if len(objects) > len(listed) else 0)
    canvas_h = max(panel_h, sidebar_h) + bottom_h
    if (canvas_w * canvas_h > MAX_CANVAS_PIXELS or max(canvas_w, canvas_h) > MAX_CANVAS_SIDE):
        raise OverlayTooLarge(f"Preview would be {canvas_w}x{canvas_h} pixels (limit "
                              f"{MAX_CANVAS_PIXELS:,} pixels, {MAX_CANVAS_SIDE:,} per side).")

    # ---- allocate and draw
    with Image.open(BytesIO(image_bytes)) as source:
        base = source.convert("RGB")
    if (panel_w, panel_h) != base.size:
        base = base.resize((panel_w, panel_h), Image.LANCZOS)
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    canvas.paste(base, (0, 0))
    draw = ImageDraw.Draw(canvas)

    def stroke(pieces_list, halo):
        for obj, (pieces, _, _) in zip(objects, prepared):
            color = HALO if halo else color_for(obj.number)
            w = LINE_WIDTH + 2 if halo else LINE_WIDTH
            for (a, b), _dashed in pieces:
                draw.line([a, b], fill=color, width=w)

    # draw only inside the image panel: later text/lists never get geometry over them
    stroke(prepared, halo=True)
    stroke(prepared, halo=False)

    if show_vertices:
        for obj, (_, outcome, _) in zip(objects, prepared):
            if outcome.status in ("drawn", "drawn_clipped"):
                for part in obj.polygons:
                    if _bad_coordinates(part) is None and len(part) <= MAX_VERTICES_PER_ELEMENT:
                        for x, y in part:
                            px, py = x * sx, y * sy
                            if 0 <= px <= panel_w and 0 <= py <= panel_h:
                                draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill=color_for(obj.number),
                                             outline=HALO)

    # numbered markers: small objects first, so they keep the spots nearest to them
    occupied, order = [], sorted(
        (i for i, p in enumerate(prepared) if p[2] is not None),
        key=lambda i: (prepared[i][2][2] - prepared[i][2][0]) * (prepared[i][2][3] - prepared[i][2][1]))
    for i in order:
        obj, bounds = objects[i], prepared[i][2]
        text = f"{obj.number}{'*' if obj.number in derived else ''}{'!' if obj.number in flagged else ''}"
        box = draw.textbbox((0, 0), text, font=font)
        size = (box[2] - box[0] + 8, box[3] - box[1] + 6)
        rect = _place_badge(size, bounds, occupied, (panel_w, panel_h))
        occupied.append(rect)
        color = color_for(obj.number)
        touching = (rect[0] <= bounds[2] + 2 and rect[2] >= bounds[0] - 2 and
                    rect[1] <= bounds[3] + 2 and rect[3] >= bounds[1] - 2)
        if not touching:                      # displaced: tie it back with a leader line
            ax = min(max((rect[0] + rect[2]) / 2, bounds[0]), bounds[2])
            ay = min(max((rect[1] + rect[3]) / 2, bounds[1]), bounds[3])
            draw.line([((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2), (ax, ay)], fill=HALO, width=3)
            draw.line([((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2), (ax, ay)], fill=color, width=1)
        draw.rectangle(rect, fill=color, outline=HALO, width=1)
        draw.text((rect[0] + 4 - box[0], rect[1] + 3 - box[1]), text, fill=_text_color(color), font=font)

    # object list (right of the image)
    left = panel_w + 12
    draw.text((left, 8), "Objects (# = preview number)", fill=(0, 0, 0), font=bold)
    for row, obj in enumerate(listed):
        top = 34 + row * row_h
        color = color_for(obj.number)
        draw.rectangle((left, top + 2, left + 14, top + 16), fill=color, outline=HALO)
        marks = ("*" if obj.number in derived else "") + ("!" if obj.number in flagged else "")
        status = prepared[row][1].status
        tail = "" if status in ("drawn", "drawn_clipped") else "  (not drawn)"
        label = _fit(f"{obj.number}{marks}  {obj.label}  [{obj.ident}]{tail}", small,
                     canvas_w - left - 24)
        draw.text((left + 22, top), label, fill=(0, 0, 0), font=small)
    if len(objects) > len(listed):
        draw.text((left, 34 + row_h * len(listed)), f"... {len(objects) - len(listed)} more objects "
                  "(see the text report)", fill=(0, 0, 0), font=small)

    # bottom text block
    y = max(panel_h, sidebar_h) + 12
    draw.line([(0, y - 6), (canvas_w, y - 6)], fill=(180, 180, 180), width=1)
    for line, face, color in wrapped:
        draw.text((12, y), line, fill=color, font=face)
        y += line_h
    return canvas, RenderReport(factor, (panel_w, panel_h), (canvas_w, canvas_h), outcomes)
