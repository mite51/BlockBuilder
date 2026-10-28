# Meshy stages

Only enter these stages when the user requests 3D generation or authorizes
continuing the full block workflow after choosing the image inputs. Keep each
submission tied to one saved attempt. The skill itself is not permission for
unlimited variations or external publication.

## Credentials

`credentials` reports only whether a Meshy key exists. The helper reads
`MESHY_API_KEY`, falling back on Windows to the existing app's
`BlockBuilder/meshy` generic credential. No OpenAI key is read or used for images.
If missing, tell the user to set the Meshy key locally or use the existing app's
Settings. Do not ask them to paste it in chat or pass it on a command line. Save
image-stage progress and continue any independent work.

## Requests

Defaults, checked against Meshy documentation on 2026-10-28:

```json
{
  "mesh_model": "meshy-7.1",
  "geometry_resolution": "standard",
  "polycount": 30000,
  "texture_model": "meshy-7",
  "texture_mode": "multiview",
  "texture_resolution": "2k",
  "enable_pbr": true
}
```

Geometry uses four accepted crops at `POST /openapi/v1/multi-image-to-3d`, with
`should_texture: false` and GLB output. The main crop is first. Texturing uses
`POST /openapi/v1/retexture` with the selected local GLB and accepted crops.
For files too large for a model data URI, a known Meshy task ID may be used.

The retexture API currently requires explicit `meshy-7` for
`multiview_image_urls`; don't copy the geometry model into that field. Multiview
cannot be combined with `text_style_prompt` or `image_style_url`. The helper's
`texture_mode` selects exactly one mode: `multiview`, `image` (main crop), or
`text` (1–800 characters from `--prompt-file`). UVs are preserved when the input
has them. Recheck official docs before changing models or handling a capability
error; report incompatibility instead of silently changing the user's choice.

Official sources: [multi-image geometry](https://docs.meshy.ai/en/api/multi-image-to-3d)
and [retexture](https://docs.meshy.ai/en/api/retexture).

## Retry and review boundaries

- `mesh`/`texture` submits once. `poll` retrieves the stored ID and never creates
  another task. Wait between status checks and back off if nothing changes.
- `submission_unknown`, or `submitting` without an ID after interruption, means
  the provider might already be processing/charging. Reconcile the actual ID
  with `attach-job`; do not blindly rerun. If the outcome cannot be determined,
  explain it and obtain the user's choice before another submission.
- Poll/download failures retain the ID and can be resumed. Provider failures
  are recorded as failures. A new generation is a separate deliberate attempt.
- Download the GLB, thumbnail, and returned texture maps into the asset. Provider
  URLs are temporary, not the asset library. Request/response records are local
  with embedded payloads represented by hashes; keys and headers are excluded.
- Review the geometry before texturing, and the actual textured output before
  bounds. Inspect the underside, connected silhouette, root protrusions, stray
  fragments, and material response. Do not equate remote success with quality.

No hosted polling service or background agent is needed. The conversation
orchestrates finite submissions, checks, review, and selection.
