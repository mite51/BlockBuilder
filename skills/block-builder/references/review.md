# 3D review and placement bounds

The existing Block Builder desktop app remains useful as a **local viewer**.
Generation in this skill happens in chat, not through the desktop's image API.
The helper uses the same `asset.json`/stage schema, so it can share a library.

When the user wants the interactive 3D grid or manual bounds controls, locate
the existing BlockBuilder checkout (in this project it is `C:/Vibe/BlockBuilder`)
and run:

```powershell
python <skill>/scripts/block.py viewer --repo <checkout> --root <asset-library>
```

Pass the actual checkout and library rather than assuming the example paths.
This opens the existing application and makes no generation request. Every
visible mesh tile has an independent 3D view. Open the block to inspect its
mesh/texture, then the Placement bounds stage for numeric controls, the mint
cube overlay, orthographic views, and the 3×3 neighbor preview.

User changes saved/selected in that UI are real asset revisions. After the user
closes it, reread `status` before continuing. Close the viewer before helper
writes to the same library; it holds a library lock. Do not kill it to clear the
lock. Do not initiate image generation through its OpenAI settings for this
skill workflow.

If the app or Qt is unavailable, the file/CLI stages still work. Link the GLB
for an available viewer and show the numeric bounds candidate for text-based
correction. If a live 3D grid is specifically required, disclose that viewer
dependency rather than presenting thumbnails as live 3D. Do not build another
viewer during an asset-generation request.

## Logical cube versus mesh

The logical placement cube is not the full geometry bounding box. Small clods,
stones, and roots may extend beyond it. The default guess uses area-weighted
surface samples and robust coordinate extents; it cannot establish the user's
intended orientation or contact planes with certainty.

The canonical cell is right-handed, centered at the origin, with Y up, X right,
Z front, and bounds `[-0.5,+0.5]` on all three axes. Keep a uniform scale. Given
source-space center `c`, side length `s > 0`, and orientation `R`, transform a
point by `Rᵀ(p-c)/s`. The helper wraps the GLB scene root with that transform,
preserving the geometry buffers, UVs, embedded textures, and material settings.

Review front/side/top and the underside. Show neighboring instances at one-cell
spacing to assess gaps and overlap when the viewer is available. When changing
the source-space cell size, a smaller placement cube makes the normalized mesh
larger relative to its cell and increases protrusion. Do not clamp protrusions
or independently rescale axes to conceal an unsuitable reconstruction.

Keep guessed and corrected bounds as separate attempts. Record the user's
choice; then complete the asset. A previously completed revision remains usable
while new attempts are being reviewed. Distinguish approved placement bounds
from a claim that arbitrary generated block faces tile seamlessly.

The bundled geometry helper supports static, self-contained, uncompressed
triangle GLBs. Report unsupported compressed, sparse, animated, skinned, or
externally referenced models explicitly; request a suitable export rather than
pretending normalization succeeded.
