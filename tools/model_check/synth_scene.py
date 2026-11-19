# -*- coding: utf-8 -*-
"""Synthetic test scenes for the model check (Blender).

    blender -b --factory-startup -P tools/model_check/synth_scene.py -- --out DIR

Writes DIR/synth_dirty.blend and DIR/synth_clean.blend:
  Body (skin sphere r=0.25) inside Jacket (outer shell r=0.27, a 2 cm gap) -> clean pair
  Board (outer box) with a Sticker (accessory plane) on its front face
dirty: + Knee (skin sphere r=0.05 centred on the body surface at +X) pokes 3 cm through the jacket,
       the Sticker lies exactly in the box face plane (coplanar -> z-fight)
clean: no Knee, the Sticker floats 3 mm in front of the face.
"""
import argparse
import os
import sys

import bpy


def mat(name, rgb):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (rgb[0], rgb[1], rgb[2], 1)
    return m


def sphere(name, r, loc, m):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=r, location=loc)
    o = bpy.context.object
    o.name = name
    o.data.materials.append(m)
    bpy.ops.object.shade_smooth()
    return o


def build(dirty):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    skin = mat('Skin', (0.9, 0.7, 0.6))
    jacket = mat('Jacket', (0.2, 0.3, 0.7))
    panel = mat('JacketPanel', (0.3, 0.6, 0.3))
    sticker = mat('Sticker', (0.9, 0.8, 0.1))
    sphere('Body', 0.25, (0, 0, 0), skin)
    sphere('Jacket', 0.27, (0, 0, 0), jacket)
    if dirty:
        sphere('Knee', 0.05, (0.25, 0, 0), skin)
    bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0, 0, -0.55))
    b = bpy.context.object
    b.name = 'Board'
    b.data.materials.append(panel)
    # sticker on the front (-Y) face of the board, facing -Y (the default camera front)
    y = -0.15 if dirty else -0.153
    bpy.ops.mesh.primitive_plane_add(size=0.16, location=(0.02, y, -0.53), rotation=(1.5707963267948966, 0, 0))
    s = bpy.context.object
    s.name = 'Sticker'
    s.data.materials.append(sticker)
    # the plane's normal after +90 deg about X points -Y? make sure it faces the front camera
    s.data.update()
    n = s.matrix_world.to_3x3() @ s.data.polygons[0].normal
    if n.y > 0:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.flip_normals()
        bpy.ops.object.mode_set(mode='OBJECT')


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    for name, dirty in (('synth_dirty', True), ('synth_clean', False)):
        build(dirty)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(a.out, name + '.blend'))
        print('[synth] wrote', name)


if __name__ == '__main__':
    main()
