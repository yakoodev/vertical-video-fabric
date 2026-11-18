# matter.js — физика слоя `physics`

<https://github.com/liabru/matter-js>, npm `matter-js` **0.20.0** (2024-06-23), лицензия MIT; типы —
`@types/matter-js` 0.20.2 (MIT, только для проверки типов). Версии закреплены точно в `frontend/package.json`:
движок моушна (`frontend/src/motion/physics.ts`) бандлится с ней и в редактор (Vite), и в компоновщик
`node/motion-render.mjs` (esbuild; в образе — `NODE_PATH=/build/node_modules`, та же установка, что у редактора).
Код библиотеки не правится; детерминизм (свои sin/cos на время счёта, счётчик id с нуля) — снаружи, в
`physics.ts`.
