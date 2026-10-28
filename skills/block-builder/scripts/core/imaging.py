from pathlib import Path
from PIL import Image, ImageOps
from .storage import write_json

VIEWS = ('main', 'left', 'back', 'right')


def quadrants(size):
    w, h = size
    if min(w, h) < 4:
        raise ValueError('The sheet is too small.')
    return [(0, 0, w//2, h//2), (w//2, 0, w, h//2),
            (0, h//2, w//2, h), (w//2, h//2, w, h)]


def split_sheet(source, target, rectangles=None, padding=16, output_size=1024):
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as original:
        sheet = ImageOps.exif_transpose(original).convert('RGB')
    rectangles = rectangles or quadrants(sheet.size)
    if len(rectangles) != 4 or padding < 0 or output_size < 32:
        raise ValueError('Provide four crop rectangles and valid padding/output size.')
    crops = []
    for rect in rectangles:
        x0, y0, x1, y1 = map(int, rect)
        if not (0 <= x0 < x1 <= sheet.width and 0 <= y0 < y1 <= sheet.height):
            raise ValueError('Crop rectangles must lie inside the sheet with positive dimensions.')
        crops.append(sheet.crop((x0,y0,x1,y1)))
    # All views use the same pixel scale, including when crop sizes differ.
    canvas_size = max(max(c.size) for c in crops) + padding * 2
    outputs = {}
    for name, crop in zip(VIEWS, crops):
        canvas = Image.new('RGB', (canvas_size, canvas_size), 'white')
        canvas.paste(crop, ((canvas_size-crop.width)//2, (canvas_size-crop.height)//2))
        canvas.resize((output_size, output_size), Image.Resampling.LANCZOS).save(target / f'{name}.png')
        outputs[name] = f'{name}.png'
    write_json(target / 'crops.json', {'source_size': list(sheet.size), 'rectangles': rectangles,
                                     'padding': padding, 'output_size': output_size,
                                     'shared_scale': output_size / canvas_size, 'order': VIEWS})
    outputs['crops'] = 'crops.json'
    return outputs
