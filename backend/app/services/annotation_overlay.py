"""Shared annotation drawing aid. Original image bytes and geometry stay unchanged."""
from io import BytesIO

from PIL import Image, ImageDraw


def annotation_overlay(image, objects, format):
    with Image.open(BytesIO(image)) as source:
        canvas = source.convert('RGB')
    draw = ImageDraw.Draw(canvas)
    offset = 1 if format == 'penn_fudan' else 0

    def visible(x, y):
        return (max(0, min(canvas.width-1, x)), max(0, min(canvas.height-1, y)))

    for obj in objects:
        box = obj.get('bbox')
        if box and obj.get('bbox_origin') != 'derived_from_polygon':
            left, top, right, bottom = box
            if left <= right and top <= bottom:
                draw.rectangle([visible(left-offset, top-offset), visible(right-offset, bottom-offset)],
                               outline='#ffdd00', width=2)
        for polygon in obj.get('polygons', []):
            if len(polygon) >= 3:
                draw.line([visible(*point) for point in polygon] + [visible(*polygon[0])],
                          fill='#00ffff', width=2)
        if box:
            draw.text(visible(box[0]-offset, box[1]-offset-12), f"#{obj['object_id']}",
                      fill='#ffdd00', stroke_width=1, stroke_fill='black')
    output = BytesIO()
    canvas.save(output, format='PNG')
    return output.getvalue()
