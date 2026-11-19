# -*- coding: utf-8 -*-
"""Layer catalogue for the model check: which part of a character is "under" which.

A *part* is one (object, material) pair. Each part gets a layer by name keywords (object name + material name),
or by explicit hints. Only the layer pairs listed in PAIRS are compared for poke-through / z-fighting:
the first layer of a pair is the inner one (it must stay behind), the second the outer one.

Pure Python (3.6+), no bpy: the Blender script and the tests both import it.
"""
import fnmatch
import json
import re

# (layer, keywords). Order matters: the first layer whose keyword matches wins, so the specific ones go first.
CATALOG = [
    ('ignore', ['outline', 'shadowcatcher', 'collider', 'bounds', 'helper']),
    ('hair_acc', ['hairpin', 'hair_pin', 'hairclip', 'hair_clip', 'hairband', 'ribbon', 'hat', 'cap', 'kanzashi',
                  'scrunchie', 'tiara', 'crown', 'headband', 'headphone', 'goggle', 'horn']),
    ('face_detail', ['brow', 'lash', 'eyeline', 'eyeliner', 'mouth', 'teeth', 'tooth', 'tongue', 'blush', 'cheek',
                     'lip']),
    ('eyes', ['iris', 'pupil', 'sclera', 'eyewhite', 'eye_white', 'highlight', 'eyeball', 'eye']),
    ('hair', ['hair', 'fringe', 'bang', 'ponytail', 'braid', 'ahoge', 'twintail', 'sideburn']),
    ('footwear', ['shoe', 'boot', 'sole', 'welt', 'sneaker', 'heel', 'sandal', 'loafer']),
    ('accessory', ['detail', 'decal', 'trim', 'patch', 'logo', 'print', 'pocket', 'button', 'zip', 'strap', 'belt',
                   'buckle', 'badge', 'pin', 'cuff', 'collar', 'tie', 'bow', 'lace', 'string', 'cord', 'glove',
                   'bracelet', 'necklace', 'choker', 'bag', 'sticker', 'accessory', 'acc_', 'gear', 'harness', 'holster',
                   'pouch', 'armor', 'armour', 'pad', 'buckle', 'chain', 'earring', 'watch']),
    ('inner', ['inner', 'under', 'shirt', 'blouse', 'tshirt', 't_shirt', 'tank', 'bra', 'sock', 'stocking', 'tights',
               'legging', 'innerwear', 'undershirt', 'camisole']),
    ('bottoms', ['trouser', 'pants', 'skirt', 'short', 'jeans', 'bottom', 'slacks', 'leg_cloth']),
    ('outer', ['hoodie', 'hood', 'jacket', 'coat', 'sweater', 'cardigan', 'vest', 'sleeve', 'tops', 'top_', 'onepiece',
               'dress', 'cloak', 'cape', 'parka', 'blazer', 'uniform', 'jersey', 'cloth', 'jumper', 'outer']),
    ('skin', ['skin', 'body', 'face', 'head', 'neck', 'hand', 'arm', 'leg', 'foot', 'feet', 'ear', 'nose', 'finger',
              'torso', 'chest', 'belly', 'nail']),
]
LAYERS = [name for name, _ in CATALOG] + ['other']

# inner -> outer pairs that are compared. A part of the inner layer must never be in front of the outer layer
# inside the outer layer's silhouette, and the two must not be near-coincident over an area.
PAIRS = [
    ('skin', 'inner'), ('skin', 'bottoms'), ('skin', 'outer'), ('skin', 'footwear'), ('skin', 'accessory'),
    ('skin', 'hair'), ('skin', 'face_detail'),
    ('inner', 'bottoms'), ('inner', 'outer'), ('inner', 'footwear'), ('inner', 'hair'), ('inner', 'accessory'),
    ('bottoms', 'outer'), ('bottoms', 'hair'), ('bottoms', 'accessory'),
    ('outer', 'hair'), ('outer', 'accessory'),
    ('eyes', 'hair'), ('eyes', 'face_detail'), ('face_detail', 'hair'),
    ('hair', 'hair_acc'),
]


def _tokens(text):
    """'Body_00_SKIN' -> 'body 00 skin'; 'HoodieDetails' -> 'hoodie details'; keeps keyword search simple."""
    s = re.sub(r'([a-z])([A-Z])', r'\1 \2', text or '')
    s = re.sub(r'\.\d+$', '', s)                     # Blender duplicate suffix .001
    return ' ' + re.sub(r'[^a-z0-9]+', ' ', s.lower()) + ' '


def _match(keywords, text):
    t = _tokens(text)
    flat = t.replace(' ', '')
    for k in keywords:
        k2 = k.replace('_', '')
        if k.endswith('_'):                          # prefix keyword such as 'acc_' / 'top_'
            if (' ' + k.rstrip('_') + ' ') in t:
                return k
            continue
        if len(k2) <= 3:                             # short words only as whole tokens ('arm', 'leg', 'cap', 'bow')
            if re.search(r'(^| )%s(s)?( |$)' % re.escape(k2), t.strip()):
                return k
            continue
        if k2 in flat:
            return k
    return None


def _layer_of(text):
    if not text:
        return None
    for layer, kws in CATALOG:
        if _match(kws, text):
            return layer
    return None


_RANK = {name: i for i, (name, _) in enumerate(CATALOG)}


def infer_layer(obj_name, mat_name):
    """Layer for one part. The material name decides (VRoid packs skin + clothes into one 'Body' mesh and the
    material says 'Tops_01_CLOTH'), with the object name as fallback. The object name only overrides when it names a
    more specific add-on layer: 'HairPins' + material 'PinYellow' -> hair_acc, 'HoodieDetails' + 'Hoodie' ->
    accessory. 'ignore' from either side wins."""
    m, o = _layer_of(mat_name), _layer_of(obj_name)
    if 'ignore' in (m, o):
        return 'ignore'
    if m is None:
        return o or 'other'
    if o in ('hair_acc', 'accessory') and _RANK[o] < _RANK[m]:
        return o
    return m

def load_hints(path_or_dict):
    """Hints JSON: {"assign": {"Hoodie": "outer", "Body/Tops_*": "outer", "*/Outline": "ignore"},
                    "pairs": [["skin", "outer"], ...],          # replaces PAIRS when given
                    "extra_pairs": [["outer", "cape"]]}         # added to PAIRS
    Keys of "assign" are fnmatch patterns over 'object/material' or just 'object'."""
    if path_or_dict is None:
        return {}
    if isinstance(path_or_dict, dict):
        return path_or_dict
    with open(path_or_dict, encoding='utf-8') as f:
        return json.load(f)


def assign_layers(parts, hints=None):
    """parts: list of (object_name, material_name). Returns {(obj, mat): layer}."""
    hints = hints or {}
    assign = hints.get('assign', {})
    out = {}
    for obj, mat in parts:
        key = '%s/%s' % (obj, mat or '')
        layer = None
        for pat, lay in assign.items():
            if fnmatch.fnmatchcase(key, pat) or ('/' not in pat and fnmatch.fnmatchcase(obj, pat)):
                layer = lay
                break
        out[(obj, mat)] = layer or infer_layer(obj, mat)
    return out


def layer_pairs(present, hints=None):
    """The (inner, outer) pairs to compare among the layers that are actually present."""
    hints = hints or {}
    pairs = [tuple(p) for p in hints.get('pairs', PAIRS)] + [tuple(p) for p in hints.get('extra_pairs', [])]
    seen, out = set(), []
    for a, b in pairs:
        if a in present and b in present and (a, b) not in seen:
            seen.add((a, b))
            out.append((a, b))
    return out
