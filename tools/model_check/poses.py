# -*- coding: utf-8 -*-
"""Test poses for the model check, written rig-independently.

A pose file is JSON:
    {"poses": [
        {"name": "rest", "bones": []},
        {"name": "arms_up", "bones": [
            {"bone": "L_UpperArm", "aim": [0.35, 0.05, 0.93]},           # point the bone along a direction
            {"bone": "spine", "rot": {"axis": "up", "deg": 25}}           # rotate about a character axis
        ]}
    ]}
"bone" is a role (hips, spine, chest, upper_chest, neck, head, L_/R_ + Shoulder, UpperArm, LowerArm, Hand,
UpperLeg, LowerLeg, Foot) or an exact bone name. Directions and axes are in the character frame
[left, forward, up] (taken from the rig: left = R->L upper arm, up = hips->head), so the same file works for
VRoid (J_Bip_L_UpperArm), Rigify-style (upper_arm.L), Mixamo (mixamorig:LeftArm) and VRM humanoid names.
Entries are applied in order, each one in the current posed state (children follow their parents).

Pure Python: the resolution of role -> bone name is tested without Blender.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_POSES = os.path.join(HERE, 'poses_default.json')

# role -> candidate names (compared after normalisation: lower case, no separators). {S} = L/R side forms.
_ROLES = {
    'hips': ['hips', 'pelvis', 'jbipchips', 'mixamorighips', 'root_hips', 'hip'],
    'spine': ['spine', 'jbipcspine', 'mixamorigspine', 'spine01', 'spine1'],
    'chest': ['chest', 'jbipcchest', 'mixamorigspine1', 'spine02', 'spine2'],
    'upper_chest': ['upperchest', 'jbipcupperchest', 'mixamorigspine2', 'spine03', 'spine3'],
    'neck': ['neck', 'jbipcneck', 'mixamorigneck', 'neck01'],
    'head': ['head', 'jbipchead', 'mixamorighead'],
    'Shoulder': ['shoulder{s}', '{s}shoulder', 'jbip{s}shoulder', 'mixamorig{side}shoulder', '{side}shoulder',
                 'clavicle{s}', '{s}clavicle'],
    'UpperArm': ['upperarm{s}', 'upper_arm{s}', '{s}upperarm', 'jbip{s}upperarm', 'mixamorig{side}arm',
                 '{side}upperarm', '{side}arm', 'arm{s}', 'upperarm{s}'],
    'LowerArm': ['forearm{s}', 'lowerarm{s}', '{s}lowerarm', '{s}forearm', 'jbip{s}lowerarm',
                 'mixamorig{side}forearm', '{side}lowerarm', '{side}forearm'],
    'Hand': ['hand{s}', '{s}hand', 'jbip{s}hand', 'mixamorig{side}hand', '{side}hand'],
    'UpperLeg': ['thigh{s}', 'upperleg{s}', '{s}upperleg', '{s}thigh', 'jbip{s}upperleg', 'mixamorig{side}upleg',
                 '{side}upperleg', '{side}upleg', 'thigh{s}'],
    'LowerLeg': ['shin{s}', 'lowerleg{s}', 'calf{s}', '{s}lowerleg', '{s}shin', '{s}calf', 'jbip{s}lowerleg',
                 'mixamorig{side}leg', '{side}lowerleg', '{side}leg', 'knee{s}'],
    'Foot': ['foot{s}', '{s}foot', 'jbip{s}foot', 'mixamorig{side}foot', '{side}foot', 'ankle{s}'],
}
SIDED = {'Shoulder', 'UpperArm', 'LowerArm', 'Hand', 'UpperLeg', 'LowerLeg', 'Foot'}


def norm(name):
    return re.sub(r'[^a-z0-9]', '', (name or '').lower())


def _candidates(role):
    """'L_UpperArm' -> normalised candidate bone names, most specific first."""
    m = re.match(r'^([LR])_(\w+)$', role)
    if m and m.group(2) in SIDED:
        side, base = m.group(1), m.group(2)
        word = 'left' if side == 'L' else 'right'
        out = []
        for c in _ROLES[base]:
            out.append(norm(c.format(s=side.lower(), side=word)))
        return out
    if role in _ROLES and role not in SIDED:
        return [norm(c) for c in _ROLES[role]]
    return []


def resolve_bone(role, bone_names):
    """Exact bone name wins; otherwise the first candidate for the role that exists in the rig."""
    if role in bone_names:
        return role
    by_norm = {}
    for b in bone_names:
        by_norm.setdefault(norm(b), b)
    for c in _candidates(role):
        if c in by_norm:
            return by_norm[c]
    return None


def load_poses(path=None):
    with open(path or DEFAULT_POSES, encoding='utf-8') as f:
        data = json.load(f)
    poses = data['poses'] if isinstance(data, dict) else data
    for p in poses:
        p.setdefault('bones', [])
    return poses


AXES = {'left': (1.0, 0.0, 0.0), 'forward': (0.0, 1.0, 0.0), 'up': (0.0, 0.0, 1.0),
        'right': (-1.0, 0.0, 0.0), 'back': (0.0, -1.0, 0.0), 'down': (0.0, 0.0, -1.0)}


def axis_vec(axis):
    if isinstance(axis, str):
        return AXES[axis]
    return tuple(float(v) for v in axis)
