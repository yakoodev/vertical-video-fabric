# vrm_springs — hair and cloth springs for VRoid VRM 0.x

Rebuilds `extensions.VRM.secondaryAnimation` of a VRoid export without touching how it looks, and checks the result on
the site's own 3D-preview solver (three-vrm 3.5.5 through `frontend/src/pages/models3d/viewer/vrmRig.ts`).

What the fix does (`springs.py`):
- Hair chains get a class from where their root sits on the head: `front` (fringe), `side` (locks by the cheeks),
  `crown` (lies flat on the scalp) or `back`. Each chain becomes its own bone group.
- The root moves one joint lower. The segment on the scalp stays on the head, so the top cannot sag.
- The fringe and side locks are near-rigid, so they cannot enter the face. The back hair keeps its weight and damping.
- Tied hair (ponytail, pigtails, bun) gets its own chain under the head. The vertices of the tail are re-weighted to
  that chain, so the bunch swings as one and the scalp hair stays put. The tail is set in `profiles.json`: a tie
  point, an axis and a radius.
- Collider spheres are fitted inside the body on the head, neck, upper chest and shoulders. They are mirrored left and
  right. A group only uses spheres that clear its rest tails. The hair hit radius is the rest gap to the face spheres,
  so nothing is pushed at rest.
- Cloth (hood, strings, hems, sleeves) gets damping only. Strings, bust and hems keep their own weight.
- Only these change: the JSON, the new joints and the skin weights of tied hair. Positions, normals, UVs, morphs,
  materials and textures keep their bytes. `batch.py` and the tests verify this.

The check (`sim/hairsim.ts` + `sim/run_sim.mjs` in headless Chrome, `analyze.py`) measures:
- swing of the spring-driven hair in the head frame;
- sag against the modelled shape;
- frames with hair more than 2 mm inside the face skin or eyes;
- frames with hair carried by the springs from outside to more than 8 mm inside the body or clothes. The hood and
  collar shell is excluded, and so is what the rigid pose itself tucks in;
- cloth tips in the body;
- `tools/model_check/anim_checks.jumps` on every spring tail, jitter at rest, and NaN.

Negative controls: `fix_springs --break floppy|unstable`.

```
python -m tools.vrm_springs.batch fix | metrics | report | videos | sheet
python -m tools.vrm_springs.fix_springs IN.vrm OUT.vrm --profile tools/vrm_springs/profiles.json
node tools/vrm_springs/sim/run_sim.mjs still --packs DIR --models a.vrm,b.vrm --clip clips/seq.json --at 2.6 --debug 1 --out p
```

`--debug 1` draws the offending vertices in red.
