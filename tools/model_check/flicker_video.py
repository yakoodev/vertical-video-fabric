import os, sys, numpy as np
sys.path.insert(0, r'C:\Users\Yakoo\source\repos\vertical-video-fabric')
from tools.model_check import analysis as A
from PIL import Image, ImageDraw, ImageFont
root = sys.argv[1]; title = sys.argv[2]
out = os.path.join(root, 'video_flicker', 'frames'); os.makedirs(out, exist_ok=True)
for f in os.listdir(out): os.remove(os.path.join(out, f))
ft = ImageFont.truetype('arial.ttf', 26); fs = ImageFont.truetype('arial.ttf', 20)
names = {'rest': 'стоя', 'arms_up': 'руки вверх', 'deep_crouch': 'присед', 'twist': 'скрутка'}
p = A.default_params(1.6, 0.004)
def load(f): return np.asarray(Image.open(f).convert('RGBA')).astype(np.float32) / 255
n = 0; tot = {}
for pose in ('rest', 'arms_up', 'deep_crouch', 'twist'):
    for az in range(0, 360, 15):
        key = f'e+15_a{az:03d}'
        fa = os.path.join(root, f'pose_{pose}', 'beauty', key + '.png'); fb = fa.replace('.png', '_j.png')
        if not (os.path.exists(fa) and os.path.exists(fb)): continue
        a, b = load(fa), load(fb)
        ca, aa = A.composite(a); cb, ab = A.composite(b)
        sil = A.dilate((aa < 0.995) | (ab < 0.995), 1)
        v = np.maximum(A.range_violation(ca, cb, 1), A.range_violation(cb, ca, 1)); v[sil] = 0
        m = v > p['tex_thr']
        heat = ca.mean(2, keepdims=True).repeat(3, 2) * 0.55
        soft = np.clip(v / 0.12, 0, 1)[..., None]; heat = heat * (1 - soft * 0.6) + np.array([1, 0.6, 0]) * soft * 0.6
        heat[A.dilate(m, 1)] = [1, 0.1, 0.1]
        H, W = a.shape[:2]
        imA = Image.fromarray((ca * 255).astype(np.uint8)); imB = Image.fromarray((cb * 255).astype(np.uint8))
        imH = Image.fromarray((np.clip(heat, 0, 1) * 255).astype(np.uint8))
        cnt = int(m.sum()); tot[pose] = tot.get(pose, 0) + cnt
        reps = 8 if cnt > 30 else 2
        for r in range(reps):
            c = Image.new('RGB', (W * 2 + 30, H + 100), (18, 18, 18))
            c.paste(imA if r % 2 == 0 else imB, (10, 56)); c.paste(imH, (W + 20, 56))
            d = ImageDraw.Draw(c)
            d.text((10, 8), f'{title} · {names[pose]} · {az}°', fill=(230, 230, 230), font=ft)
            d.text((W + 20, 8), f'мерцание: {cnt} px', fill=(255, 90, 90) if cnt > 30 else (150, 150, 150), font=ft)
            d.text((10, H + 62), 'слева мигают 2 кадра: ракурс и он же +0.5°.  справа красным — где цвет прыгнул сильнее, чем от поворота', fill=(200, 200, 200), font=fs)
            c.save(os.path.join(out, f'{n:05d}.jpg'), quality=88); n += 1
print(n, tot)
