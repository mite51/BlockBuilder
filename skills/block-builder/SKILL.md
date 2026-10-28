---
name: block-builder
description: "Create and revise stylized cubic terrain assets through native image generation, label-free four-view sheets, Meshy geometry and textures, and corrected placement bounds. Use for the block-asset creation workflow or resuming its saved generations, not for building a new application."
---

# Block Builder

Run the block-building workflow in this conversation. Use the host's built-in
image-generation tool for images. Use the bundled helpers only for deterministic
file operations, Meshy tasks, and GLB normalization. Do not create another agent
loop, inference client, or frontend to perform this workflow.

## Start or resume

- Infer the asset name, material, references, and requested stopping point from
  the user. Default to `<workspace>/cube_asset/<asset_name>/`; honor an existing
  library or explicit location. Run `status` before resuming an asset. Do not
  create a replacement asset merely because this conversation is new.
- For a library request, run `list`. The existing Block Builder Qt app can show
  the interactive 3D grid and perform manual bounds correction; see
  [review and bounds](references/review.md). It is optional, not an image provider.
- The normal flow is **description → preview → four-view sheet → split → mesh →
  texture → bounds → complete**. A request for only one stage stays within that
  scope. The user's description is already input approval; save and select it
  without asking them to approve their own words again.
- By default, show a generated result and wait for the user's selection before
  consuming it in the next generation. This is the requested review workflow.
  If they already chose a result, or explicitly delegated selection, proceed
  without reconfirming. Never treat elapsed time as a selection.

Read [commands](references/commands.md) when performing local operations. Use
`scripts/block.py` with an absolute asset path. Helpers retain the desktop app's
asset schema, so existing assets and completed revisions remain usable there.

## Image stages: use this harness

1. Apply the available `imagegen` skill. Use the built-in image-generation tool;
   do not call an OpenAI image/Responses endpoint or ask for an OpenAI API key.
   If the host lacks image generation, preserve progress and explain that limit;
   do not silently switch to an API fallback.
2. Run `prepare-image` before invoking the tool. It returns a saved prompt and
   local reference paths for this new attempt. Inspect local reference images
   before passing them to the tool. The `assets/preview.txt` and
   `assets/turnaround.txt` templates expand the selected material description;
   adapt the material details when useful, preserving the camera/style rules.
   `assets/materials.json` contains the 23 existing material presets.
3. Generate one preview candidate per attempt. For a turnaround, use the
   **accepted preview as an image reference**. The tool may also receive the
   original style references; identify their roles explicitly. For edits, use
   `--edit-attempt` and state which aspects must stay fixed.
4. Call `record-image` immediately with the actual returned local output path.
   Save every attempt, including rejected generations. Copy the tool's output;
   do not move or overwrite its original file. Record provider/model identity
   only if the tool actually returned it; do not label the pixels as Astra output.
5. Inspect the result, show it in chat with a short attempt identifier, and
   record the user's choice using `select`. A critique or a new candidate does
   not automatically replace an accepted selection. Mark a failed tool call
   using `fail`; a new invocation gets a new attempt ID.

## Four views and splitting

The sheet is square, with equal 2×2 cells and **no printed labels or text**:

| Position | Metadata name | Camera and visible faces |
| --- | --- | --- |
| Upper left | main | Elevated front-right: top, front, right |
| Upper right | left | Elevated front-left: top, front, left |
| Lower left | back | Low rear-left: bottom, back, left |
| Lower right | right | Low rear-right: bottom, back, right |

Require a single physical asset with corresponding shared-face details, matching
scale, readable chunky texture, white background, no cast/contact/floor shadows,
and sufficient underside fill. Check that lower views really show the underside.
Do not claim a prompt guarantees geometric consistency.

Use Pillow through `split` for the explicitly requested crop operation. Inspect
all four crops for cut-off clods/roots, unequal framing, unwanted text, and wrong
views before offering them for selection. Keep the same pixel scale across views.
Do not use white-pixel deletion for background removal: it destroys snow and
pale stones. Use imagegen for visual/content corrections, then save a new attempt.

## Geometry, texture, and bounds

Read [Meshy](references/meshy.md) only when entering a Meshy stage. The accepted
four crops feed geometry generation; texture generation uses the accepted mesh
in a separate task. Credentials are needed only for these stages. Do not block
image work on a missing Meshy key.

Use one submission for each authorized attempt. Poll the stored task ID; keep
status checks separate from creation. On an ambiguous submission, reconcile its
ID rather than automatically starting another potentially paid task. A request
to generate the selected next stage authorizes that submission; do not add a
second permission gate. Do not run extra alternatives without the user's request
or an explicitly delegated iteration budget.

Read [review and bounds](references/review.md) before inspecting a mesh or setting
placement bounds. The logical cube is independent of rendered geometry. Suggest
bounds, show the candidate, and let the user correct them. Normalization keeps a
centered unit cube and preserves protruding clods, roots, UVs, and embedded maps.
Complete only after all required selections are current and the bounds have been
accepted, unless the user explicitly delegated that choice.

## Revisions and handoff

- Every attempt records its selected inputs. Rerunning a stage preserves earlier
  outputs. Changing an upstream selection makes incompatible descendants stale;
  do not quietly combine them. Restore an old input chain only at the user's
  request, or select/regenerate compatible descendants.
- Use the exact stored paths and IDs after a restart. Failed/pending results are
  not selections. Never fabricate success for a missing image, mesh, or texture.
- Close the desktop viewer before skill writes to the same library. The helper
  checks the library lock; do not bypass it or terminate the user's app.
- At a review checkpoint, report the candidate, saved location, and the next
  selection needed. At completion, link the normalized GLB and bounds JSON and
  state which tools were actually used. Do not call an image-only draft a
  completed 3D asset.
