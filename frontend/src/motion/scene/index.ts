/** Движок сцен: программный 3D/2D-рендер на 2D-холсте (docs/motion/scene-engine.md). */
export { renderScene, evalCamera, sceneImageRefs, SCENE_LIMITS, NODE_TYPES, SHADINGS, CAMERA_TYPES, PARTICLE3D_KINDS, type SceneSpec, type SceneStats, type SceneRenderOptions } from "./render";
export { validateScene, formatSceneProblems, type SceneCheck, type SceneProblem } from "./validate";
export { compileExpr, exprError, EXPR_FUNCS, EXPR_VARS, type ExprVars } from "./expr";
export { evalNum, evalVec, evalColor, springProgress, parseColor } from "./value";
export { boxMesh, sphereMesh, icosphereMesh, cylinderMesh, torusMesh, planeMesh, latheMesh, extrudeMesh, parsePath, presetShape, triangulate, PRESET_SHAPES, type Mesh } from "./geometry";
export { compose, mul, apply, lookAt, focalFor, projectCam, identity, type Vec3, type ViewCamera } from "./math";
