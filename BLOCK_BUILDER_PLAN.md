# Block Builder — MVP implementation plan

Planning date: 2026-10-27. Target: a local Windows desktop application in Python and Qt, using OpenAI Astra, Pillow, and Meshy. This document is a plan; no application or paid generation jobs have been started.

## 1. Product scope

Open into an asset library with one interactive 3D viewport per visible completed block. Keep the first, upper-left tile reserved for **Create New Block**. Clicking it opens a resumable wizard:

Describe → Preview → Four-view sheet → Split views → Mesh → Texture → Bounds → Complete.

Every stage supports multiple attempts, reviewing history, selecting a result, and explicitly continuing. Closing the wizard preserves progress. Existing assets reopen at any stage. An existing completed revision stays usable while a new revision is being prepared.

Include a Settings window accessible from both the library and wizard. Keep this first version focused on generating and reviewing individual block assets; terrain editing, automated batches, engine plugins, LOD generation, and collaboration are later work.

## 2. Stack and responsibilities

| Component | Proposed responsibility |
| --- | --- |
| Python | Application services, workflow state, persistence, background jobs, provider integration |
| PySide6 / Qt Quick Controls | Library, wizard, settings, histories, inspectors |
| Qt Quick 3D | Independent card viewports, mesh review, bounds editing, tiled placement preview |
| OpenAI Responses API with `gpt-6-astra` | Interpret descriptions and references, prepare editable prompts, inspect generated images and suggest corrections |
| OpenAI image-generation tool | Produce preview and turnaround images under Astra orchestration; configure and record the image model separately |
| Pillow | Read image metadata, normalize orientation, split sheets, pad/resize crops, save PNGs and thumbnails |
| Meshy API | Generate geometry from four views, then generate textures for the selected geometry |
| NumPy and a mesh utility library such as trimesh | Inspect geometry, propose placement bounds, validate normalization transforms |
| OS credential store via a keyring adapter | Store API keys separately from project files |

Use Qt Quick 3D's runtime GLB loading for the viewer. Its documented runtime loader supports glTF assets. Validate real Meshy outputs early, including texture maps and ice materials. [Qt runtime-loader example](https://doc.qt.io/qt-6/qtquick3d-simpleruntimeloader-example.html)

Keep provider adapters behind narrow interfaces so model/version changes do not alter wizard logic. Pin a tested dependency set during implementation.

## 3. Settings window

Provide these groups:

- **API connections:** masked OpenAI and Meshy key fields; Save, Replace, Remove, and Test Connection; clear connection/model-access status. A connection check must not launch a paid image or mesh generation.
- **Models:** Astra inference model, image-generation model, Meshy geometry model, and texture mode/model. Start with verified combinations and report incompatible settings.
- **Library:** configurable root, defaulting to `cube_asset/` beneath the repository; Open Folder and Rescan actions. Switching roots changes libraries; it does not silently move assets.
- **Generation defaults:** image resolution/quality where supported, mesh detail target, texture resolution and PBR preferences, and a conservative concurrency limit.
- **Viewport:** background, lighting, automatic rotation, and preview quality.

Store secrets in the Windows credential store. Store non-secret preferences in Qt settings. Environment variables may provide explicit development overrides, with their presence shown without displaying the value. Never copy keys or Authorization headers into request history, logs, manifests, or exported assets.

Distinguish missing credentials, invalid credentials, network failure, and unavailable model. Permit offline library browsing and local bounds editing. Changes to defaults affect new jobs; every existing attempt retains a snapshot of its original settings.

## 4. Library screen

- The first tile is always **Create New Block**, including under filtering.
- Each visible completed asset has its own live 3D view, orbit/zoom controls, name, and revision/status indicator.
- Use the same lighting and initial camera for comparison.
- Double-click an asset to inspect it or reopen its wizard.
- Show unfinished drafts in the same library with a status and their latest available image or mesh, so work is easy to resume.
- Read existing assets from the configured library root; keep an optional local index disposable and rebuildable from asset manifests.
- Lazy-load visible models and release offscreen resources. Idle cards should render on demand; animation is optional. Do not substitute static thumbnails for the requested visible 3D views.
- Use cached thumbnails only during loading and for assets that have no mesh yet.

Prototype the gallery early using representative textured models. Set a measured performance budget on the user's machine before adding optimization complexity.

## 5. Wizard stages

All stages use a common layout: step list, current result viewer, editable inputs/settings, generation history, and **Generate Again**, **Use This Result**, **Back**, and **Continue** controls. Local editing stages use **Save Revision** in place of generation.

### A. Describe

Inputs: asset name, material description, optional reference images, and an optional preset from the existing 23 terrain prompts.

Copy source images into the asset folder immediately. Keep their originals and normalized working copies. Astra may propose a structured block specification and an editable image prompt. The user can edit both before generating.

Separate immutable visual rules from material-specific details: chunky cubic silhouette, hand-painted style, protrusion allowance, background, lighting, and camera layout. Source/reference images have explicit roles so a style reference is not confused with the target block identity.

Deliverable: a selected description revision, reference list, and prompt specification.

### B. Preview

Generate one elevated front-right three-quarter image using the description and any selected references. Show attempts in a thumbnail history with full-size comparison. Support both a fresh attempt and an edit of a selected attempt with feedback.

The accepted preview becomes the identity reference for the turnaround, rather than relying only on the same text prompt.

Deliverable: selected preview PNG plus its prompt, settings, provider metadata, and source dependencies.

### C. Four-view sheet

Adapt the existing turnaround prompt template, preserving the four camera angles and material consistency instructions. Remove all requests to draw labels and replace them with **no text, labels, arrows, borders, or watermarks anywhere in the image**.

Generate a square image with four equal cells and safe internal margins:

| Cell | Internal view identifier | Required view |
| --- | --- | --- |
| Upper left | main | Elevated front-right; top, front, right |
| Upper right | left | Elevated front-left; top, front, left |
| Lower left | back | Low rear-left; bottom, back, left |
| Lower right | right | Low rear-right; bottom, back, right |

Identifiers are metadata and UI labels only. They must not be painted into the image.

Use the selected preview as an image reference. Require the same physical asset, consistent shared-face details, matching scale, pure white background, and no cast/contact/floor shadows.

Offer advisory Astra review for wrong camera angles, missing undersides, extra blocks, inconsistent details, and visible text. The user reviews the sheet before continuing. AI images can still disagree across views; a consistency prompt or automated review cannot guarantee physical correspondence.

Deliverable: selected sheet PNG and the exact expanded template.

### D. Split and verify views

Use Pillow to split the selected sheet into four cells in the documented order. Preserve the original sheet. Handle actual image dimensions explicitly, including odd dimensions.

Present all four crops and editable crop rectangles. Allow padding and a shared resize policy; retain the original framing and avoid scaling each block independently. Do not rotate or mirror crops automatically.

Use white backgrounds by default. Do not erase white pixels to remove backgrounds: that would damage snow and pale stones. Any later masking feature needs foreground-aware masks and explicit review.

Deliverable: four selected PNGs and a versioned crop specification containing rectangles, padding, resize operations, output dimensions, and source-sheet identity.

### E. Generate mesh

Submit the accepted crops to Meshy's multi-image endpoint with texture generation disabled. Show job status, then display the downloaded geometry in a neutral material with optional wireframe.

Allow new attempts with editable detail settings. Review silhouette, top/bottom interpretation, fragments, holes, and missing surface forms. Meshy generates a model of the pictured object; it does not guarantee exact cube proportions or topology.

Deliverable: selected geometry GLB and associated files, request/response metadata, remote task ID, and geometry statistics.

### F. Generate texture

Texture the selected mesh in a separate Meshy task. Prefer the accepted four views as texture references when the configured model supports that mode. Review the resulting textured asset under consistent neutral lighting.

Keep texture histories tied to the exact input geometry. Download all returned relevant materials and maps. Check whether the returned textured model changes orientation, transforms, or geometry; do not assume it is interchangeable with the input GLB. The bounds stage works on the actual selected textured artifact.

Provide a single-reference or text-guided mode as an explicit alternative, not a silent fallback. Preserve supported PBR outputs. Ice transparency and transmission need visual verification in the actual viewer and may require later material tuning.

Deliverable: selected textured GLB, texture maps, provider metadata, and geometry lineage.

### G. Set normalized placement bounds

Distinguish the **rendered mesh bounds** from the **logical placement cube**. Roots, clods, and stones may extend past the logical cube.

1. Let the user confirm the mesh orientation and top direction.
2. Propose a cube using area-weighted surface sampling, robust face/extent estimates, and a common side length. Do not use raw vertex density as a proxy for the intended surface.
3. Overlay an editable wireframe cube. Provide center, rotation, and uniform side-length controls, plus numeric values and reset.
4. Offer front/side/top orthographic views and a tiled preview with adjacent instances one cell apart.
5. Show protrusion distances and visible gaps/overlaps as review information.
6. Save the user's confirmed placement transform as its own revision.

Proposed canonical coordinates: right-handed, Y up, X right, Z front; placement cube spans `[-0.5, +0.5]` on all axes, with a center pivot and unit cell size. Physical cell size remains separate metadata.

For a point `p`, confirmed center `c`, orientation matrix `R`, and positive cube side length `s`, normalize using `p_normalized = transpose(R) * (p - c) / s`. Apply this to the complete scene consistently, preserving material bindings and valid normal transforms.

Do not clamp or trim protruding geometry. Preserve the original model and save the transform and resulting actual mesh bounds alongside a normalized export. Default to uniform scaling so a bad reconstruction is not silently stretched into a cube.

Automatic bounds are a starting guess. Irregular roots and rounded surfaces make user correction essential. Placement bounds also do not guarantee seamless tiling between arbitrary generated faces.

### H. Complete

Show the selected lineage from description through bounds. Verify local files and selected dependencies, then create an immutable completed revision with a normalized GLB, bounds metadata, and thumbnail.

Atomically update the asset's current completed-revision pointer and refresh its library tile. Reopening and editing creates a draft without replacing the last completed revision until the user finishes again.

## 6. Generation history and dependency rules

Model the workflow as a dependency graph, presented as a linear wizard.

Each attempt gets an immutable ID and folder. Its record includes:

- Stage, timestamp, status, source attempt IDs, and input file hashes.
- Exact prompt, template version, source references, and settings snapshot.
- Provider/model IDs, remote task/response IDs, and available usage information.
- Sanitized request and response data, error details, and generated-file hashes.
- User feedback and any relationship to an earlier edited attempt.

Save output artifacts once; selection is a separate pointer. Generating another attempt does not change selection automatically.

If the user selects a different upstream attempt, keep all descendants but mark incompatible results as based on a previous selection. Require regeneration or an explicitly compatible selection before completing the new chain. Returning to an earlier chain should restore its usable descendants. A display-name-only edit does not invalidate geometry.

Bounds depend on the selected textured model. A texture replacement may reuse bounds only after geometry/transform equivalence is verified; otherwise require review.

Support pending, submitting, running, succeeded, failed, submission-unknown, and interrupted states. Selection/confirmation is distinct from remote success.

## 7. On-disk asset contract

Interpret `/cube_asset/asset_name` as a folder beneath the configured library root, not the Windows drive root.

Example:

```text
cube_asset/
  coarse_dirt/
    asset.json
    sources/
      ref_<id>_original.png
      ref_<id>_working.png
    stages/
      description/<attempt_id>/
      preview/<attempt_id>/
      turnaround/<attempt_id>/
      split/<attempt_id>/
      mesh/<attempt_id>/
      texture/<attempt_id>/
      bounds/<attempt_id>/
    completed/
      <revision_id>/
        block.glb
        bounds.json
        selection.json
        thumbnail.png
    logs/
      events.jsonl
```

Each stage-attempt folder contains `attempt.json`, applicable prompt/spec files, sanitized provider records, and all outputs needed to understand or reuse that attempt. Split attempts contain four PNGs; mesh and texture attempts contain their GLBs and maps; bounds attempts contain the proposed and confirmed transforms.

`asset.json` contains a schema version, stable asset UUID, display name, safe folder slug, current selections, workflow status, and completed-revision pointer. Store relative paths for portability. Handle name collisions with a suffix and keep display-name changes separate from folder identity.

Use atomic manifest writes and temporary downloads renamed only after completion. Recover orphaned attempt folders after a crash. Retain original requests for provenance without claiming nondeterministic services can reproduce identical outputs.

## 8. Background jobs and provider contracts

Run network and image/mesh processing away from the Qt UI thread. Use a job service and bounded worker pool, reporting progress through Qt signals; UI objects remain on the UI thread.

Persist intent before submission and remote IDs immediately when received. On restart, reconcile known remote jobs and continue downloads. Retry polling and downloads with backoff. Do not blindly resubmit a creation request after an ambiguous timeout: record submission-unknown and reconcile using supported provider facilities before offering an explicit rerun.

Cancel local waits independently from remote cancellation. Only promise remote cancellation when the provider supports it. Late results remain attached to their originating attempt and never replace a selected result.

Download provider artifacts promptly and retain local copies. A completed asset must open with the network disconnected. Meshy documents expiring signed download URLs, so URLs are not sufficient storage. [Meshy retention documentation](https://docs.meshy.ai/en/api/asset-retention)

## 9. Verified API direction

The following are documented capabilities checked during planning; account access and end-to-end behavior still need a real integration spike.

- Astra supports image input and the Responses image-generation tool. Configure `gpt-6-astra` for inference and a supported image model separately; persist both. [Astra model](https://developers.openai.com/api/docs/models/gpt-6-astra) and [image tool](https://developers.openai.com/api/docs/guides/tools-image-generation).
- Meshy accepts one to four images at `POST /openapi/v1/multi-image-to-3d`; `should_texture: false` separates geometry from texturing. [Multi-image API](https://docs.meshy.ai/en/api/multi-image-to-3d).
- Retexturing uses `POST /openapi/v1/retexture`. The documented four-view option is `multiview_image_urls`, with the main view first, and currently requires explicit `ai_model: "meshy-7"`. It cannot be combined with the single-image or text-style fields. Use a successful compatible task ID or the selected model file as input; verify the geometry-to-texture handoff in the spike. [Retexture API](https://docs.meshy.ai/en/api/retexture).
- Pillow provides the crop and image operations needed for local splitting. [Pillow Image reference](https://pillow.readthedocs.io/en/stable/reference/Image.html).

Keep a capability table in the provider adapters rather than applying one shared Meshy model setting to both geometry and texture.

## 10. Suggested code organization

```text
block_builder/
  app.py
  ui/                 # QML library, wizard, stage views, settings
  controllers/        # Qt-facing controllers and models
  domain/             # Assets, attempts, selections, dependency rules
  storage/            # Manifests, atomic writes, recovery, library scan
  jobs/               # Persistent job records, workers, reconciliation
  providers/          # OpenAI and Meshy adapters
  imaging/            # Pillow normalization, crops, padding
  geometry/           # Inspection, bounds fitting, normalization
  viewport/           # Runtime loading, cameras, bounds gizmos
  templates/          # Preview and label-free turnaround templates
  settings/           # Preferences, credentials, connection checks
tests/
```

Keep business logic independent of QML so rerun rules, recovery, and normalization can be tested without launching the UI.

## 11. Build sequence and acceptance checks

| Milestone | Deliverable | Acceptance |
| --- | --- | --- |
| 1. Integration spike | One preview → label-free sheet → four crops → geometry → texture → local GLB viewer | Verify image access, Meshy handoff, underside interpretation, texture matching, and Qt rendering before building the full UI |
| 2. Foundation | Settings, credential storage, asset schema, histories, persistent job service, library skeleton | Credentials stay outside assets; failed/unknown jobs recover without silent duplicate submission; library opens offline |
| 3. Library and wizard shell | Live 3D grid, top-left creation tile, resumable stages and selection UI | Existing assets load; visible cards have independent 3D views; close/reopen preserves selections |
| 4. Image workflow | Description, preview, turnaround, editable split stage | User reruns/selects attempts; four correct label-free views are saved; upstream changes mark dependent work appropriately |
| 5. Mesh and texture workflow | Meshy jobs, download recovery, geometry and textured comparison | Several mesh/texture attempts coexist and selected results preserve their exact input lineage |
| 6. Bounds and completion | Suggested cube, manual controls, neighbor preview, normalized output | Known test transforms normalize correctly; protrusions survive; completed GLB opens offline and appears in grid |
| 7. MVP hardening | Packaging, actionable failures, restart recovery, real material checks | End-to-end coarse dirt, rooted dirt, and snow/ice cases; interrupted jobs recover; a completed revision survives later draft edits |

Use mocked providers for most workflow tests and a small deliberate set of real API runs for integration. Test crop ordering and dimensions, stale dependency handling, atomic writes/recovery, scene transforms, material preservation, and adjacent-cell placement.

The first shipped milestone is one reliable, resumable asset workflow with saved alternatives. Additional features can build on the same asset and attempt contracts.
