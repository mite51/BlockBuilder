# Block Builder

A local Windows application for building a versioned library of stylized terrain blocks. Python desktop controls use PySide6; every visible mesh tile and the wizard's mesh editor use Qt Quick 3D.

## Launch

Run **Open Block Builder.ps1** from this directory, or:

```powershell
python -m block_builder
```

Requires Windows and Python 3.12. From a fresh checkout:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-block-builder.txt
python -m block_builder
```

To inspect a separate demonstration library without API calls:

```powershell
python -m block_builder --demo
```

Demo assets are simple local fixtures marked `Demo`. They are written under `output/block_builder_demo/`, independently of the production library. They are not generated terrain artwork.

To open another library without changing preferences:

```powershell
python -m block_builder --library C:\path\to\cube_asset
```

## First use

1. Open **Settings → Connections**. Enter OpenAI and Meshy API keys and save. Use **Test connection** to check credentials without launching a generation. Existing keys are never displayed; blank fields preserve them.
2. Review **Generation** defaults. The default inference model is `gpt-6-astra`; the image model is configured separately. Mesh geometry defaults to `meshy-7.1`, while multiview retexturing explicitly uses `meshy-7`, as required by Meshy's current retexture API.
3. Click the upper-left **Create new block** tile and name the asset.
4. Describe it or choose one of the 23 terrain presets. Add optional source images. Save a description revision, highlight it, and choose **Use this result**.
5. Continue through preview, four-view sheet, split views, mesh, texture, and placement bounds. **Generate** creates an alternative. **Use this result** selects it. **Continue** requires a selected compatible result.
6. Finish with **Complete block**. The new revision appears in the library with an interactive 3D preview.

Each generation button starts a provider task using your account. There is no automatic paid batch generation. Each stage retains earlier results. Import buttons let you supply existing preview/sheet PNGs or static embedded GLBs, including for offline testing.

## Review and correction

- **Image stages:** edit the prompt, add feedback, or check **Edit the highlighted generation** to use an earlier result as an image reference. The turnaround uses the selected preview and asks for four views with no labels.
- **Split:** adjust the four source-pixel crop rectangles. Preview them, then save and select a split revision. All crops use one scale; white snow and pale material pixels are retained.
- **Mesh/texture:** orbit by dragging, zoom with the wheel, switch to front/side/top views, and toggle wireframe. Mesh and texture attempts have independent histories.
- **Bounds:** edit the logical cube's center, uniform side length, and XYZ rotation. **Guess placement cube** proposes a surface-sampling estimate. The mint overlay stays visible even through overhanging geometry. Use the 3 × 3 neighbor preview to inspect spacing. Save and select the corrected bounds revision.
- **History:** selecting different upstream work makes incompatible downstream results stale without deleting them. **Restore this input chain** restores an earlier result's ancestors. Details opens that attempt's folder.

The normalized placement cube is `[-0.5, +0.5]` on each axis, with a center pivot, Y up, X right, and Z front. Geometry can extend outside the cube. Normalization wraps the original GLB scene with a uniform transform; geometry buffers, embedded textures, and material definitions are preserved.

## Storage

The default library is `cube_asset/` beneath this project. Settings can choose a different library without moving existing assets.

```text
cube_asset/asset_name/
  asset.json
  sources/                       # Original and working reference images
  stages/<stage>/<attempt_id>/   # Metadata, prompts, inputs, outputs, API records
  completed/<revision_id>/
    block.glb
    bounds.json
    selection.json
    thumbnail.png
  logs/events.jsonl
  .cache/                        # Disposable bounds-preview GLBs
```

Requests contain hashes in place of embedded base64 payloads; their source artifacts and input lineage remain local. Provider response IDs, task IDs, settings, usage fields when returned, output hashes, errors, and downloaded textures are retained. Keys and Authorization headers are not stored in these files.

Preferences use Qt settings. API keys use native Windows Credential Manager under `BlockBuilder/openai` and `BlockBuilder/meshy`. `OPENAI_API_KEY` and `MESHY_API_KEY` can override stored credentials for development. Settings indicates active environment overrides; removing a stored key does not remove an environment variable.

## Resume and failure handling

Remote jobs run outside the UI thread. Closing the wizard keeps jobs running. Closing the application pauses local monitoring after recording state; the remote provider may continue processing.

On reopening, select an interrupted attempt and choose **Resume job**. A known remote ID is polled again; it is not submitted again. Failed downloads can be retried by resuming the same task. If a submission timed out before an ID arrived, it is marked **submission_unknown**: check the provider and use **Attach provider job ID**, or deliberately start a new generation. The application never automatically repeats ambiguous creation requests.

Completed revisions remain available offline. Editing a completed block creates a draft; the published revision remains intact until another completion.

## Validation and current limits

Run the automated checks:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest tests/block_builder -q -p no:cacheprovider
python -m block_builder --demo --smoke-test
```

Tests cover selection/lineage restoration, restart recovery, ambiguous requests, image cropping and shared scale, file-integrity checks, normalized transforms, retained overhang, API payloads, settings, and the full offline wizard. UI screenshots are saved under `output/block_builder_checks/`.

The API integration is implemented and tested with mocked responses. A real OpenAI → Meshy run still requires your configured keys and account/model access. No paid requests were made during initial development because no keys were configured.

- Image-generation consistency and Meshy's reconstruction quality still require user review. The tool cannot guarantee exact correspondence among AI-generated views or seamless joins between arbitrary blocks.
- Static, self-contained, uncompressed triangle GLBs are supported. Sparse/compressed positions, skins, animations, morph targets, and external buffers/images are rejected with an actionable message.
- Bounds estimation is intentionally approximate. Orientation and logical cube dimensions are user-correctable; vertices are not clipped or stretched independently by axis.
- Transparent ice materials depend on provider output and Qt's material support. No real ice generation has yet been verified.
- The history UI currently shows the first image returned by a generation; additional returned images are retained in its attempt folder.
- Library discovery reads this application's `asset.json` folders. Bring an external mesh into a block using the mesh/texture import controls.

The desktop launcher is supplied; a packaged installer is not yet included.

## Repository contents

- `block_builder/`: application code and Qt Quick 3D viewer.
- `block_builder/templates/`: the 23 terrain prompt presets shipped with the application.
- `tests/block_builder/`: offline core, provider, job, and UI tests.
- `BLOCK_BUILDER_PLAN.md`: the original MVP design and implementation plan.

Generated assets, API records, credentials, and test outputs are excluded from Git. Keep local asset libraries backed up separately.
