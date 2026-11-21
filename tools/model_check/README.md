# model_check: finding clipping on a 3D character by rendering it

The owner's request (19.11): render the model from many angles, with no animation, and watch how the textures change, so that clipping can be found by a program.

This tool generalises the "magenta skin check" from `D:\vvf-offload\anime3d\scene10\scene10.py`. That check painted the skin that should be covered in magenta and looked for magenta in the render. Here every part of the model goes into a layer (skin < inner clothes < trousers < outer clothes < hair, ...). Each layer is rendered on its own with its depth, so the program can see where an inner layer comes in front of an outer one. It also checks every view a second time at +0.5° to find shimmering.

## Run

```
# one go: checks free memory, runs Blender headless, then the analysis
python tools/model_check/run_check.py --model D:\vvf-offload\anime3d\mio_rigged.blend --out D:\vvf-offload\model_check\mio

# or the two steps by hand
blender -b --factory-startup -P tools/model_check/render_views.py -- --model X.blend --out DIR [--poses poses.json] [--res 512]
python tools/model_check/analyze.py DIR
```

Use the repo `.venv` Python. The analysis needs only numpy and Pillow and runs on Python 3.6+.
Put output under `D:\vvf-offload\model_check\...`, not in the repo. Mio at the default settings writes about 0.5 GB.

### render_views.py options
| option | default | |
|---|---|---|
| `--model` | | `.blend`, `.glb/.gltf`, `.vrm` (imported as glTF), `.fbx` |
| `--poses` | `default` | `poses_default.json` (rest, arms_up, deep_crouch, twist), `rest`, or your own JSON |
| `--only-poses` | | comma list of pose names |
| `--hints` | | layer hints JSON (below) |
| `--res` | 512 | square image size, analysis resolution |
| `--az` / `--el` | 24 / `-15,15,45` | azimuth count / elevations in degrees (write `--el=-15,15` when the list starts with a minus); azimuth 0 = the character faces the camera |
| `--jitter` | 0.5 | azimuth step in degrees for the second frame (shimmer tests) |
| `--tex-samples` | 8 | EEVEE samples for the beauty frames |
| `--max-subsurf` | 2 | caps Subdivision render levels |
| `--exclude` | | object name patterns to leave out (floor, props) |
| `--keep-outlines`, `--no-beauty`, `--threads`, `--fov` | | |

`run_check.py` adds `--blender PATH` (default: `$MODEL_CHECK_BLENDER`, then the 4.5 build on D:), `--min-free-gb 4`
(it waits while free commit memory is lower than this) and `--skip-render` (analysis only).
`analyze.py` takes `--eps-mm`, `--min-poke-px`, `--min-zfight-px`, `--tex-thr` and `--no-images`.

## How it works

**Render (Blender 4.5, `render_views.py`)**
1. Load the model and drop all actions and NLA, so there is no animation. Switch off inverted-hull outlines (a flipped Solidify modifier or one named "outline"). Cap subsurf.
2. Give each part, meaning one (object, material) pair, an id through `pass_index`, and give it a layer by name (`layers.py`). If a mesh holds parts from different compared layers (VRoid's `Body` holds skin, tops, bottoms and shoes), it is split by material first, so each layer can be rendered alone.
3. Pose the character with `poses.py`. Poses are rig-independent: roles such as `L_UpperArm` resolve to `J_Bip_L_UpperArm`, `upper_arm.L` or `mixamorig:LeftArm`, and directions are given in the character's own left/forward/up frame.
4. For each view (24 azimuths × 3 elevations, square 512 px):
   - Cycles, 1 sample, all parts: per-pixel part id (from the IndexOB and IndexMA passes), a back-face flag (a `Backfacing` AOV), and distance (from the Position pass). It renders at θ and again at θ+0.5°.
   - Cycles, one compared layer at a time: depth, part id and back-face flag of that layer.
   - EEVEE with shadows off: two beauty frames, at θ and θ+0.5°.

   Cycles is used for the data passes because it needs no material override and respects alpha cut-outs (`pass_alpha_threshold`). The loops run layer by layer, so persistent data only moves the camera between renders.

**Analysis (`analysis.py`, `analyze.py`)**, for every view:
- **Poke-through.** For each (inner, outer) layer pair, look inside the outer silhouette, away from depth steps. Flag pixels where the inner layer is closer to the camera than the outer one by more than eps (default 0.06% of the model height, about 0.9 mm on Mio) and less than 5% of the height. Also required: the outer pixel is a front face (an opening shows back faces), and the inner part is the one actually visible in the full render (a clip hidden behind an arm does not count in that view). Each blob gets two numbers:
  - *enclosure*: the share of the ring around it where the outer layer is on top;
  - *crossing*: the share of that ring where the inner surface carries on just behind the outer one, meaning the two surfaces really intersect.

  A blob with enclosure ≥ 0.5 and crossing ≥ 0.35 is a `poke_through`. Anything else is a `contact` at info level, for example a hand resting on the jacket, whose edge is a depth step.
- **Z-fighting.** The two layers lie within eps of each other over an area (morphological opening, at least 12 px).
- **ID shimmer.** Compare the part-id maps at θ and θ+0.5°. Flag a pixel if its id does not occur in the 3×3 neighbourhood of the other frame, if it is an isolated single-pixel id, or if more than 40% of a 7×7 window changed. A 1-px edge band at the silhouette is removed. Speckles are grouped (dilation by 2) and kept from 8 pixels up.
- **Texture flicker.** The same test on the EEVEE beauty colours: a colour outside the min..max of its neighbourhood by more than 0.12, or a dense area of changes. A sub-pixel move cannot explain either.
- Each finding gets its parts (object/material), area, 3D location (unprojected from the distance map) and nearest bone. Findings of one pose are clustered across views by type, layer pair and 3D distance (6% of height).
- **Severity.** `high` means a poke seen from 3 or more views and at least 25 px. `medium` means seen from 2 views or large. Then `low`. A contact is `info`. **Score** = 100 − 12 per high − 4 per medium − 1 per low. Clean means no high or medium finding.

**Output** under `DIR/report/`:
- `report.json`: score, per-pose scores, clusters with severity, area, parts, location, nearest bone, worst views and overlay paths, plus notes (parts that show mostly back faces, i.e. flipped normals).
- `summary.txt`: the same as a table.
- `overlays/<pose>__<view>.png`: the beauty frame next to a heatmap of the worst views, with the clusters circled and numbered.
- `contact_<pose>.png`: all views of the pose with hotspots tinted. Each cluster's worst view is boxed.

Colours: red = poke-through, yellow = z-fight, cyan = id shimmer, magenta = texture flicker. Orange (contact) is only in the overlays.

## Layers and hints

Layers are matched by keyword on the material name, then the object name. The most specific layer wins (see `CATALOG` in `layers.py`):
`ignore` (outline…), `hair_acc`, `face_detail`, `eyes`, `hair`, `footwear`, `accessory`, `inner`, `bottoms`, `outer`, `skin`, `other`.
Only the pairs in `PAIRS` are compared. Examples: skin < everything worn; inner < bottoms < outer < hair; outer < accessory;
eyes/face_detail < hair; hair < hair_acc. Eyes against face skin is not compared, because the eye socket is by design.

Hints JSON (`--hints`):
```json
{"assign": {"Body/Tops_*": "inner", "Cape": "outer", "*/Outline": "ignore"},
 "extra_pairs": [["outer", "cape"]],
 "pairs": null}
```
`assign` keys are fnmatch patterns over `object/material` or `object`. `pairs`, when given, replaces the default list.

## Poses JSON
```json
{"poses": [
  {"name": "rest", "bones": []},
  {"name": "arms_up", "bones": [
     {"bone": "L_UpperArm", "aim": [0.30, 0.10, 0.95]},
     {"bone": "spine", "rot": {"axis": "up", "deg": 25}}]}]}
```
`aim` points the bone along [left, forward, up]. `rot` turns it about an axis of that frame (right-hand rule: +deg about `forward` raises the left arm). Entries are applied in order, and children follow. Bones that are not found are reported in `warnings`.

## Tests

`tests/test_model_check.py` covers the pure parts: labelling and morphology, poke, contact, back-face, hidden-poke and z-fight cases on synthetic depth maps, shimmer and flicker, clustering and score, layer names (VRoid and Mio) and bone aliases.
The end-to-end test builds `synth_scene.py` (skin sphere in a jacket shell, plus a knee sphere poking 3 cm through it, plus a coplanar sticker on a box). It checks that both defects come out `high` and that the clean variant scores 100. It runs only when `MODEL_CHECK_BLENDER` points to `blender.exe`:
```
$env:MODEL_CHECK_BLENDER='D:\vvf-offload\tools\blender\blender-4.5.15-windows-x64\blender.exe'
.venv\Scripts\python -m pytest tests/test_model_check.py
```

## Validation (19.11)

| model | render | analysis | result |
|---|---|---|---|
| synth_dirty (knee through jacket + coplanar sticker) | 36 s | 3 s | score 67: poke_through high (Knee/Skin through Jacket, 28 views), zfight high (Sticker/Board, gap 0.00 mm), plus id_shimmer and texture_flicker on the sticker |
| synth_clean | 36 s | 3 s | score 100, clean |
| mio_rigged.blend (custom 19-bone rig), 4 poses × 72 views | 18.7 min | 1.5 min | trousers through the hoodie hem at both hips in every pose, worst in deep_crouch/twist; thighs through hoodie front and pocket and through the sleeves in deep_crouch; sleeve ends through the coral cuffs; trousers/hem z-fight (gap ~0.5 mm); stripe-texture crawl at the wrists; sole/welt speckle at grazing angles |
| heroine2_rigged.blend (VRoid J_Bip, 14:06 version) | 5.2 min | 1.5 min | thigh Gear sunk into the trousers; Gear vs jacket/sleeves at the shoulders (fine if the harness is meant to be under the jacket: hint it as inner); Under shows ~70% back faces (flipped normals?) |

Outputs: D:\vvf-offload\model_check\{synth,mio,heroine2}\report\.

## Known limits

- **Layers only.** Two parts in the same layer are not depth-compared, for example a sleeve against the hoodie body. Intra-layer z-fighting is still caught by the shimmer tests.
- **Visible clipping only.** A poke deep inside a fold, or hidden in every view, is not reported. That is intended.
- **Cycles ray tracing.** Exactly coplanar faces in Cycles give z-fight speckle, and EEVEE's depth-buffer z-fighting (with production `clip_start/clip_end`) is only seen in the beauty frames. The analysis camera uses tight clip planes, so production z-fighting caused by an extreme clip range is *not* reproduced. The depth test (gap < eps) is what flags such surfaces.
- **Penetration is capped.** It is measured along the camera ray and capped at 5% of the height. Anything farther in front counts as simply being in front.
- **Splitting can shift borders.** Splitting a mixed-layer mesh by material can move subsurf borders slightly; only meshes whose materials belong to different compared layers are split.
- **Aim direction.** `aim` turns the limb's direction toward the next joint of the chain (UpperArm → LowerArm head,
  LowerLeg → Foot, Foot → toe), falling back to head → tail. Before 21.11 it used head → tail, and on glTF-imported
  VRoid models (tails along the node's +Y) legs were turned upside down; .vrm scores from before then are wrong.
- **Pose rotations.** `aim` uses the shortest rotation, so a bone's twist is not controlled (use `rot` about the bone's axis for that). IK constraints in a rig can override the FK pose.
- **Mixed causes.** Texture flicker also flags EEVEE z-fighting, and its attribution is the majority part under the blob.

## Animation checks (21.11)

The pose check above has no motion. `anim_dump.py` + `anim_checks.py` check a keyed shot frame by frame.
Every check has a negative control in `tests/test_anim_checks.py`: a deliberately broken input it must catch.

```
# inside Blender, after the shot builder has keyed everything (no render needed):
import anim_dump as AD
AD.dump(out_dir, arm, [body, face, hair], frames, cuts=[...], floor=[env objects], contacts=[...], props=[...])
# then, with numpy only:
python tools/model_check/anim_analyze.py out_dir            # anim_report.json + anim_summary.txt
python tools/model_check/anim_analyze.py out_dir --negative freeze|leak|foot_spin   # break the real dump on purpose
```

| check | what fails | default limit |
|---|---|---|
| `jumps_bones`, `jumps_vertices` | a bone head / vertex steps more than 3 cm and 3x its usual moving step around it (90th percentile of ±8 frames; drawings on twos count as usual), or over 25 cm; cuts skipped | 3 cm, 3x |
| `rotation_jumps` | foot / toe / hand turns between two frames: over 60° = error; over 20° and 3x its usual turn = warning | 60° / 20° |
| `bend_plane` | knee and elbow fold about their hinge (the axis comes from the rest pose and follows the upper bone, so a thigh twist turns the plane with it) and to the right side; for knees also the knee distance from the plane "hip → ankle along the foot" | 30° off plane, 10° reversed, knee 8 cm |
| `floor_contact` | a foot vertex, a knee-end shin vertex or the knee joint is inside a step / slab (a ray up hits the surface from behind; measured in Blender) | 1 cm |
| `hand_contact` | where the shot says the hand holds a prop (weight ≥ 0.5): gap to the prop over 2 cm or deeper than 1.2 cm inside it; hand vertices as `clip_check.Character` selects them | 2 cm / 1.2 cm |
| `normals_<mesh>` | normals more than 40° away from the rest normal carried by the skinning, on more than 0.3% of the vertices of a frame (dents, chips). Meshes with shape keys (Face) are reported only. `skin_selfcheck_p99_cm` compares our numpy skinning with Blender's evaluated mesh | 40°, 0.3% |
| `import_twist` | on the first frame a deform bone is twisted about its own axis (toward its child joint) by more than 90° against rest; the top deform bone (hips: facing) is skipped | 90° |
| `static` | something that should move has a range under 1 cm over the shot; a still run of 8 s is a warning | 1 cm |
| `weight_leak` | rotating a deform bone ±45° (two cross axes and its own) moves vertices outside its zone (nearest bone segment not in its chain) farther than 7 cm / 0.35 bone lengths from its joint by more than 1.5 cm | 1.5 cm |
| `attached_props` | a prop on a socket bone drifts (2 cm) or turns (15°) against it, or its axis is off the socket's (30°) | |

The stairs pilot adapter (copies the build script, runs the dump instead of the render loop, does not touch the
pilot's `work_*` folders): `D:vf-offload\series_unlucky\pilot_testnim_checkun_dump.py v2|v4`.

## Flicker video (for people)

`python tools/model_check/flicker_video.py <run dir> <title>` writes `<run dir>/video_flicker/frames/*.jpg`:
on the left, two beauty frames blink (θ and θ+0.5°); on the right, red marks where the colour changed more than a
half-degree turn can explain. Then:
`ffmpeg -framerate 4 -i <run dir>/video_flicker/frames/%05d.jpg -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" -pix_fmt yuv420p flicker.mp4`.
Check: two half-overlapping cubes (coplanar faces) light up a whole strip (~12k px per view); our characters at rest show tens of px.

## Light orbit (21.11): how clean is the cel shadow on the face

The key light goes round the head (`--angles 72` = 5° steps, at `--elev 20`°) for every facial expression
(VRoid `Fcl_ALL_*`, mouth A, eyes closed: 8 by default). Per frame the shader AOV `npr_lit` (1 = lit side of the cel
shadow; the NPR looks in `D:\vvf-offload\anime3d\npr` write it) and a region code (face skin / face decals / hair /
other parts) are rendered with EEVEE at 1 sample, hair hidden (`--hide`). A scene where nothing writes `npr_lit` gets a
reference AOV (the scene lamps through Diffuse → Shader to RGB, lit above `--lit-thr`).

```
# the rig (Blender), then the numbers
blender -b --factory-startup -P tools/model_check/light_orbit.py -- --out DIR --setup D:\vvf-offload\anime3d\npr\orbit_setup.py ^
        --setup-args "--char hero --look flat" [--beauty] [--full] [--lamp-test]
python tools/model_check/light_report.py DIR          # -> DIR/report/light_orbit.json, summary.txt, masks_sheet.png
# or a model straight away: --model X.vrm ; tests: --synth
```

Metrics (`lit_analysis.py`, numpy only), shadow = face region with `npr_lit < 0.5`, decal pixels filled from the
neighbouring skin:
- **components**: 8-connected shadow pieces per frame (a sliver smaller than `--tiny` touching the 2-px band along the
  face outline is not counted: a terminator lying on the silhouette breaks into pieces in any renderer);
- **tiny_share / tiny_area**: share of the pieces (and of the shadow area) smaller than 0.1 % of the face;
- **perim_ratio**: sub-pixel contour length of all pieces / perimeter of the convex hull of the shadow (a disc 1.03,
  a crescent ~1.1–1.2, speckle far above);
- **IoU** of each expression with the neutral face at the same angle (also without the eye/mouth zone);
- **nesting**: from the front to the back the shadow may only grow, on the way back only shrink; a neighbour pair is
  nested when < 0.2 % of the face breaks that;
- `--lamp-test`: lamp power ×2 and an orange lamp every 30°, IoU with the base lamp (a shader that only uses the light
  direction gives 1.0); `--full`: neutral face, everything visible, per-part components (hair, sleeves...).

Negative controls: `--noise-normals 0.4` (random face normals: a normals-driven shader must score badly) and
`--noise-map 0.2` (noise in the `NPR_FaceMap_*` threshold maps). `tests/test_light_orbit.py` checks the metrics on
synthetic spheres (clean = 1 piece, nested, IoU 1; speckle, a jumping shadow, a sliding expression are caught) and,
with `MODEL_CHECK_BLENDER`, runs the rig on the synthetic face clean vs noisy.
