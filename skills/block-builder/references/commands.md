# Local commands

`scripts/block.py` is a finite operations helper, not an inference harness. Run
it with the session's Python. `--help` and `<command> --help` describe arguments.
Pillow handles image copying/cropping; NumPy handles GLB transforms; httpx handles
Meshy. They are listed in `scripts/requirements.txt`. Install only if missing.
No OpenAI SDK, OpenAI API key, or Qt dependency is required for these operations.

In PowerShell, set paths explicitly (replace examples with actual returned paths):

```powershell
$blockHelper = 'C:\Users\mite5\.codex\skills\block-builder\scripts\block.py'
$blockLibrary = 'C:\Vibe\BlockBuilder\cube_asset'
python $blockHelper list --root $blockLibrary
python $blockHelper new --root $blockLibrary --name 'Coarse Dirt'
$blockAsset = 'C:\Vibe\BlockBuilder\cube_asset\coarse_dirt'
```

Do not assume the slug: `new` returns its actual `asset` path, adding a suffix on
name collisions. All mutation commands emit JSON, including attempt IDs, status,
compatibility, and absolute output paths. `status` is read-only:

```powershell
python $blockHelper status --asset $blockAsset
python $blockHelper materials
python $blockHelper describe --asset $blockAsset --preset 'Coarse Dirt'
```

For custom descriptions, write a UTF-8 file and pass `--text-file`. Add original
references with repeated `--reference <path>` arguments. Source originals and
normalized working copies are saved inside the asset. Select the description
attempt that represents the user's supplied brief:

```powershell
python $blockHelper select --asset $blockAsset --stage description --attempt <returned-id>
```

## Native image generation

```powershell
python $blockHelper prepare-image --asset $blockAsset --stage preview
```

This creates an attempt **without generating an image**. Read its `prompt_file`,
inspect the returned `reference_images`, and call the built-in image generator.
For a revised prompt, supply `--prompt-file` with a UTF-8 file. Use repeated
`--reference` for extra inputs, and `--edit-attempt <id>` for an existing image.
The helper snapshots referenced files so their provenance is preserved.

After the image tool returns a real local file:

```powershell
python $blockHelper record-image --asset $blockAsset --stage preview --attempt <prepared-id> --image <tool-output-path>
```

Optional `--metadata <json-file>` saves actual tool-returned metadata. Do not put
keys, headers, or assumed model names into it. A finished attempt cannot be
overwritten. Once the user chooses it:

```powershell
python $blockHelper select --asset $blockAsset --stage preview --attempt <chosen-id>
python $blockHelper prepare-image --asset $blockAsset --stage turnaround
```

The turnaround's identity reference is automatically copied from the selected
preview. Invoke imagegen again, then `record-image --stage turnaround`, review,
and `select --stage turnaround`. Do not start Meshy using the unsplit sheet.

For failed image calls:

```powershell
python $blockHelper fail --asset $blockAsset --stage preview --attempt <id> --reason 'Image tool returned no image'
```

## Crops

```powershell
python $blockHelper split --asset $blockAsset --padding 16 --size 1024
```

The order is main, left, back, right. For custom framing use `--spec-file`:

```json
{
  "rectangles": [[0,0,1024,1024],[1024,0,2048,1024],[0,1024,1024,2048],[1024,1024,2048,2048]],
  "padding": 16,
  "output_size": 1024
}
```

Coordinates are `[left, top, right, bottom]` in the actual source dimensions.
Do not reuse the example dimensions blindly. Inspect the returned PNGs, then
select the split attempt. Padding uses one common resize factor across all views.

## Meshy

See [Meshy reference](meshy.md) before using these commands:

```powershell
python $blockHelper credentials
python $blockHelper mesh --asset $blockAsset
python $blockHelper poll --asset $blockAsset --stage mesh --attempt <id>
```

Each `poll` makes one status request and downloads results if ready; it never
submits another task. Wait between polls (roughly 15–30 seconds, backing off on
unchanged progress). Keep the user informed during long operations; do not create
an automation unless requested. After review and selection of geometry:

```powershell
python $blockHelper texture --asset $blockAsset
python $blockHelper poll --asset $blockAsset --stage texture --attempt <id>
```

`mesh` and `texture` accept `--settings <json-file>` for an explicit settings
snapshot, and `--prompt-file` for text texture guidance. Valid setting keys and
model constraints are in the Meshy reference. A submission error is recorded in
the result JSON: always inspect `status` and `error`, not just the exit code.

For an existing provider task whose submission lost its local ID:

```powershell
python $blockHelper attach-job --asset $blockAsset --stage mesh --attempt <id> --job-id <actual-meshy-id>
```

Only attach a verified task corresponding to this attempt, then poll it. Do not
attach a different task as a retry. When a saved task ID exists, resume by polling.

## Bounds, selection, and completion

```powershell
python $blockHelper bounds --asset $blockAsset
```

This creates an unselected guessed placement-cube attempt. It returns a normalized
model and `bounds.json` for review. For corrections, write a new spec and rerun:

```json
{"center": [0,0,0], "size": 1.0, "rotation": [0,0,0]}
```

`center`/`size` are in the selected textured model's source coordinate system;
`rotation` is XYZ degrees. Use `bounds --spec-file <path>`. Every adjustment is a
new attempt. Select accepted bounds and finish:

```powershell
python $blockHelper select --asset $blockAsset --stage bounds --attempt <chosen-id>
python $blockHelper complete --asset $blockAsset
```

`complete` requires a compatible selected result at every stage, verifies stored
output hashes, and creates a new `completed/<revision_id>/` with `block.glb`,
`bounds.json`, `selection.json`, and a thumbnail. `--thumbnail` can supply an
actual rendered preview. The prior completed revision remains on disk.

`select --restore` explicitly restores an old attempt's input chain. Without
that flag, selecting an incompatible stale result fails. Use this only when the
user wants that older chain, not to conceal a dependency mismatch.

The helper uses the desktop library lock when Qt is available and an exclusive
file otherwise. Close other writers before mutating the same library. Never
delete a live lock. A crash with a stale fallback lock requires checking its
recorded process before cleanup.
