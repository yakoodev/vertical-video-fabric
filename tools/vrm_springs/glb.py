# -*- coding: utf-8 -*-
"""Minimal GLB / VRM 0.x reader-writer for rig edits: the JSON document, the binary chunk, accessors.

Only appends: new bufferViews and accessors go to the end of the binary chunk, existing ones keep their bytes and
indices, so meshes, textures and every VRM extension that points at nodes / materials stay valid.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

CTYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
CTYPE_OF = {np.dtype(v): k for k, v in CTYPE.items()}
NCOMP = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}
TYPE_OF = {1: 'SCALAR', 2: 'VEC2', 3: 'VEC3', 4: 'VEC4', 16: 'MAT4'}


class Glb:
    def __init__(self, doc: dict, bin_: bytes):
        self.json = doc
        self.bin = bytearray(bin_)

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls, path) -> 'Glb':
        data = Path(path).read_bytes()
        magic, _ver, total = struct.unpack_from('<III', data, 0)
        if magic != GLB_MAGIC:
            raise ValueError('%s: not a GLB/VRM' % path)
        off, doc, bin_ = 12, None, b''
        while off + 8 <= min(total, len(data)):
            ln, typ = struct.unpack_from('<II', data, off)
            chunk = data[off + 8: off + 8 + ln]
            if typ == CHUNK_JSON:
                doc = json.loads(chunk.decode('utf-8'))
            elif typ == CHUNK_BIN:
                bin_ = chunk
            off += 8 + ln
        if doc is None:
            raise ValueError('%s: no JSON chunk' % path)
        return cls(doc, bin_)

    def to_bytes(self) -> bytes:
        self.json['buffers'][0]['byteLength'] = len(self.bin)
        js = json.dumps(self.json, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        js += b' ' * (-len(js) % 4)
        bn = bytes(self.bin) + b'\0' * (-len(self.bin) % 4)
        total = 12 + 8 + len(js) + 8 + len(bn)
        return (struct.pack('<III', GLB_MAGIC, 2, total) + struct.pack('<II', len(js), CHUNK_JSON) + js
                + struct.pack('<II', len(bn), CHUNK_BIN) + bn)

    def save(self, path):
        Path(path).write_bytes(self.to_bytes())

    # ------------------------------------------------------------------ accessors
    def accessor(self, i: int) -> np.ndarray:
        a = self.json['accessors'][i]
        ct = CTYPE[a['componentType']]
        nc = NCOMP[a['type']]
        n = a['count']
        if 'bufferView' not in a:
            return np.zeros((n, nc), ct)
        bv = self.json['bufferViews'][a['bufferView']]
        off = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
        item = np.dtype(ct).itemsize * nc
        st = bv.get('byteStride', 0) or item
        if st == item:
            arr = np.frombuffer(self.bin, ct, count=n * nc, offset=off).reshape(n, nc)
        else:
            raw = np.frombuffer(self.bin, np.uint8, count=st * (n - 1) + item, offset=off)
            arr = np.lib.stride_tricks.as_strided(raw, (n, item), (st, 1)).copy().view(ct).reshape(n, nc)
        arr = arr.copy()
        if a.get('normalized'):
            arr = arr.astype(np.float32) / np.iinfo(ct).max
        return arr

    def add_accessor(self, arr: np.ndarray, target: int | None = None, minmax: bool = False) -> int:
        """Append arr (N, C) as a new tightly packed bufferView + accessor; -> accessor index."""
        arr = np.ascontiguousarray(arr)
        if arr.ndim == 1:
            arr = arr[:, None]
        self.bin += b'\0' * (-len(self.bin) % 4)
        off = len(self.bin)
        raw = arr.tobytes()
        self.bin += raw
        bv = {'buffer': 0, 'byteOffset': off, 'byteLength': len(raw)}
        if target:
            bv['target'] = target
        self.json['bufferViews'].append(bv)
        acc = {'bufferView': len(self.json['bufferViews']) - 1, 'byteOffset': 0,
               'componentType': CTYPE_OF[arr.dtype], 'count': int(arr.shape[0]), 'type': TYPE_OF[arr.shape[1]],
               'normalized': False}
        if minmax:
            acc['min'] = arr.min(0).tolist()
            acc['max'] = arr.max(0).tolist()
        self.json['accessors'].append(acc)
        return len(self.json['accessors']) - 1

    # ------------------------------------------------------------------ nodes
    def node_index(self, name: str) -> int:
        for i, n in enumerate(self.json['nodes']):
            if n.get('name') == name:
                return i
        raise KeyError(name)

    def parents(self) -> dict:
        return {c: i for i, n in enumerate(self.json['nodes']) for c in n.get('children', [])}

    def local_matrix(self, i: int) -> np.ndarray:
        n = self.json['nodes'][i]
        if 'matrix' in n:
            return np.array(n['matrix'], float).reshape(4, 4).T
        t = np.array(n.get('translation', [0, 0, 0]), float)
        x, y, z, w = n.get('rotation', [0, 0, 0, 1])
        s = np.array(n.get('scale', [1, 1, 1]), float)
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        M = np.eye(4)
        M[:3, :3] = R * s
        M[:3, 3] = t
        return M

    def world_matrices(self) -> list:
        par = self.parents()
        W = [None] * len(self.json['nodes'])

        def get(i):
            if W[i] is None:
                L = self.local_matrix(i)
                W[i] = (get(par[i]) @ L) if i in par else L
            return W[i]
        for i in range(len(W)):
            get(i)
        return W

    def world_positions(self) -> np.ndarray:
        return np.array([M[:3, 3] for M in self.world_matrices()])

    @property
    def vrm(self) -> dict:
        return self.json['extensions']['VRM']
