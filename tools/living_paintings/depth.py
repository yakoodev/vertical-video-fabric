# -*- coding: utf-8 -*-
"""Карта глубины картины: Depth Anything V2 Small (Apache-2.0), локально, GPU если есть.

python depth.py IMG OUT.npy [--model D:/vvf-offload/tools/depth_anything_v2_small]
Пишет OUT.npy (float32, размер картинки; больше = ближе, 0..1) и OUT.png (просмотр)."""
from __future__ import annotations

import argparse

import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForDepthEstimation


def depth(img_path: str, model_dir: str) -> np.ndarray:
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    proc = AutoImageProcessor.from_pretrained(model_dir)
    model = AutoModelForDepthEstimation.from_pretrained(model_dir).to(dev).eval()
    im = Image.open(img_path).convert("RGB")
    outs = []
    # две проходки: целиком и с отражением — усредняем, чтобы края были стабильнее
    for flip in (False, True):
        x = im.transpose(Image.FLIP_LEFT_RIGHT) if flip else im
        inp = proc(images=x, return_tensors="pt", size={"height": 770, "width": 770}, keep_aspect_ratio=True,
                   ensure_multiple_of=14, do_resize=True).to(dev)
        with torch.no_grad():
            d = model(**inp).predicted_depth
        d = torch.nn.functional.interpolate(d[:, None], size=im.size[::-1], mode="bicubic", align_corners=False)[0, 0]
        d = d.cpu().numpy()
        outs.append(d[:, ::-1] if flip else d)
    d = np.mean(outs, 0)
    lo, hi = np.percentile(d, 0.5), np.percentile(d, 99.5)
    return np.clip((d - lo) / (hi - lo + 1e-6), 0, 1).astype(np.float32)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("img"); ap.add_argument("out")
    ap.add_argument("--model", default="D:/vvf-offload/tools/depth_anything_v2_small")
    a = ap.parse_args()
    d = depth(a.img, a.model)
    np.save(a.out, d)
    Image.fromarray((d * 255).astype(np.uint8)).save(a.out.replace(".npy", ".png"))
    print("ok", d.shape)
