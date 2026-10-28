import re
from pathlib import Path

STYLE = ('Stylized 3D game asset, chunky cube silhouette, softly rounded irregular edges, '
         'hand-painted cartoon appearance, smooth shading, crisp broad sculpted detail. '
         'Readable chunky texture with sparse fine granules. Soft even studio lighting, '
         'pure white background, no cast shadows, contact shadows, floor shadows, or gradients. '
         'No text, labels, borders, arrows, or watermarks anywhere in the image.')

TURNAROUND = '''Create a square 2x2 turnaround sheet showing FOUR CAMERA VIEWS OF ONE IDENTICAL PHYSICAL ASSET.
Material: {description}
{style}
Use the supplied accepted preview as the identity reference. Preserve its design and material.
Upper left: elevated front-right three-quarter view, top, front and right faces visible.
Upper right: elevated front-left three-quarter view, top, front and left faces visible.
Lower left: LOW rear-left three-quarter view, camera BELOW the upright block looking upward;
clearly show BOTTOM, back and left faces, not the top.
Lower right: LOW rear-right three-quarter view, camera BELOW the upright block looking upward;
clearly show BOTTOM, back and right faces, not the top.
Every shared face retains the SAME clod shapes, stones, cracks, colors and other surface details
in the SAME positions as the camera rotates. Do not mirror or redesign any face. Features crossing
edges must connect coherently. Top matches upper views; bottom matches lower views; front matches
upper views; back matches lower views; left and right match their corresponding upper/lower views.
Keep all four blocks at the same scale, evenly spaced in four equal cells with generous white
internal margins for splitting. Adequate fill light makes undersides visible. No floor or shadows.
ABSOLUTELY NO VIEW LABELS OR OTHER TEXT. Camera names are instructions only, never draw them.'''


def preview_prompt(description):
    return f'Create one isolated chunky cubic block. Material: {description}\n{STYLE}\nSlightly elevated front-right three-quarter view showing top and two vertical faces. Generous margins.'


def turnaround_prompt(description):
    return TURNAROUND.format(description=description,style=STYLE)


def presets():
    path = Path(__file__).resolve().parent/'templates'/'terrain_block_turnaround_prompts.md'
    if not path.exists():
        return {}
    result = {}
    for name, body in re.findall(r'## ([^\n]+)\n(.*?)(?=\n## |\Z)',path.read_text(encoding='utf-8'),re.S):
        match = re.search(r'The block is made of (.*?)(?:\n\nView layout:)',body,re.S)
        if match:
            result[name.strip()] = 'The block is made of ' + match.group(1).strip()
    return result
