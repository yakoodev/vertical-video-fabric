"""HTTP-ручки 3D-серии (раздел «3D-серия» в материалах проекта и редактор серии).

* ``GET /api/projects/{id}/series3d`` — библиотека, настройки, серии со статусом, карточки голоса, бюджет
* ``PUT /api/projects/{id}/series3d/settings`` — движок, пресеты стиля/света, голос рассказчика, музыка, субтитры
* ``POST /api/projects/{id}/series3d/library`` {kind, slug?, name, …данные}, ``DELETE …/library/{kind}/{slug}``,
  ``POST …/library/seed`` {only?} — готовые персонажи и локации, ``POST …/vrm`` (multipart .vrm) — своя модель
* ``POST /api/projects/{id}/series3d/episodes`` {script | topic | plan, title?, cast?, location?, duration?}
* ``GET/DELETE /api/series3d/episodes/{eid}``, ``PUT …/plan`` {plan | changes, note?}, ``GET …/plan/{version}``,
  ``POST …/render`` {dry_run?} (оценка — в ответе), ``POST …/cancel``, ``GET …/estimate``
* ``GET /api/series3d/catalog`` — ракурсы, движения, игра, переходы, свет (с миниатюрами)
* ``GET /api/series3d/preset-thumb/{category}/{name}`` — миниатюра пресета из docs/3d/img

Решение по карточке платного голоса — общая ручка ИИ-видео ``POST /api/ai-video/approvals/{aid}/decide``.
"""


import re
import shutil
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app import s3d_service as SVC
from app import s3d_store as D
from app.s3d_plan import PlanError

DOCS_IMG = Path(__file__).resolve().parent.parent / "docs" / "3d" / "img"
MAX_VRM_BYTES = 80 * 1024 * 1024


def build_series3d_router(auth_dep: Any, get_store: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["Series 3D"])

    def call(fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(get_store(), *args, **kwargs)
        except PlanError as exc:
            raise HTTPException(status_code=400, detail={"error": "план с ошибками", "problems": exc.problems}) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except (ValueError, TypeError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/projects/{project_id}/series3d", summary="3D-серия: библиотека, серии, карточки")
    def overview(project_id: int, _auth: auth_dep) -> dict:
        return call(SVC.overview, project_id)

    @router.put("/api/projects/{project_id}/series3d/settings", summary="Настройки 3D-серии проекта")
    def settings(project_id: int, _auth: auth_dep, body: dict = Body(...)) -> dict:
        return call(D.save_settings, project_id, body)

    @router.post("/api/projects/{project_id}/series3d/library", summary="Персонаж, локация или вещь библиотеки")
    def save_item(project_id: int, _auth: auth_dep, body: dict = Body(...)) -> dict:
        kind = str(body.get("kind") or "")
        return call(D.save_item, project_id, kind, body, slug=body.get("slug"), name=body.get("name"))

    @router.delete("/api/projects/{project_id}/series3d/library/{kind}/{slug}", summary="Убрать из библиотеки")
    def delete_item(project_id: int, kind: str, slug: str, _auth: auth_dep) -> dict:
        return {"deleted": call(D.delete_item, project_id, kind, slug)}

    @router.post("/api/projects/{project_id}/series3d/library/seed", summary="Готовые персонажи и локации")
    def seed(project_id: int, _auth: auth_dep, body: dict = Body(default={})) -> dict:
        return {"added": call(D.seed_builtins, project_id, body.get("only"))}

    @router.post("/api/projects/{project_id}/series3d/vrm", summary="Своя VRM-модель")
    def upload_vrm(project_id: int, _auth: auth_dep, file: UploadFile = File(...)) -> dict:
        from app.s3d_render import vrm_dir

        name = Path(file.filename or "model.vrm").name
        if not name.lower().endswith(".vrm"):
            raise HTTPException(status_code=400, detail="нужен файл .vrm")
        from app.projects import get_project

        call(get_project, project_id, include_sources=False)
        stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(name).stem)[:40] or "model"
        rel = f"uploads/p{int(project_id)}-{stem}-{uuid4().hex[:6]}.vrm"
        dest = vrm_dir() / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        if dest.stat().st_size > MAX_VRM_BYTES or dest.read_bytes()[:4] != b"glTF":
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="это не VRM (glTF-бинарник) или файл больше 80 МБ")
        return {"model": rel, "note": "модель загружена: укажите её персонажу в поле vrm.model"}

    @router.post("/api/projects/{project_id}/series3d/episodes", summary="Новая 3D-серия")
    def create(project_id: int, _auth: auth_dep, body: dict = Body(...)) -> dict:
        return call(SVC.create, project_id, script=str(body.get("script") or ""), topic=str(body.get("topic") or ""),
                    plan=body.get("plan"), title=str(body.get("title") or ""), cast=body.get("cast") or None,
                    location=body.get("location") or None, narrator=str(body.get("narrator") or "narrator"),
                    duration=float(body.get("duration") or 30), tone=str(body.get("tone") or ""), author="ui")

    @router.get("/api/series3d/episodes/{episode_id}", summary="3D-серия: план, версии, рендеры, проверки")
    def episode(episode_id: int, _auth: auth_dep) -> dict:
        return call(SVC.episode_full, episode_id)

    @router.delete("/api/series3d/episodes/{episode_id}", summary="Удалить 3D-серию (клип остаётся)")
    def delete_episode(episode_id: int, _auth: auth_dep) -> dict:
        return {"deleted": call(D.delete_episode, episode_id)}

    @router.put("/api/series3d/episodes/{episode_id}/plan", summary="Новая версия плана")
    def update_plan(episode_id: int, _auth: auth_dep, body: dict = Body(...)) -> dict:
        return call(SVC.update_plan, episode_id, plan=body.get("plan"), changes=body.get("changes"), author="ui",
                    note=str(body.get("note") or ""))

    @router.get("/api/series3d/episodes/{episode_id}/plan/{version}", summary="План серии одной версии")
    def plan_version(episode_id: int, version: int, _auth: auth_dep) -> dict:
        return {"version": version, "plan": call(D.plan_at, episode_id, version)}

    @router.get("/api/series3d/episodes/{episode_id}/estimate", summary="Сколько стоит рендер")
    def estimate(episode_id: int, _auth: auth_dep, dry_run: bool = False) -> dict:
        return call(SVC.estimate, episode_id, dry_run)

    @router.post("/api/series3d/episodes/{episode_id}/render", summary="Рендер серии (в очереди, по одному)")
    def render(episode_id: int, _auth: auth_dep, body: dict = Body(default={})) -> dict:
        return call(SVC.render, episode_id, dry_run=bool(body.get("dry_run")), requested_by="ui")

    @router.post("/api/series3d/episodes/{episode_id}/cancel", summary="Отменить рендер")
    def cancel(episode_id: int, _auth: auth_dep) -> dict:
        from app import s3d_render as R

        job = call(R.cancel, episode_id)
        return {"cancelled": bool(job), "job": SVC._job_brief(job)}

    @router.get("/api/series3d/catalog", summary="Что можно выбрать в плане серии")
    def catalog(_auth: auth_dep) -> dict:
        return SVC.catalogs()

    @router.get("/api/series3d/preset-thumb/{category}/{name}", summary="Миниатюра 3D-пресета", include_in_schema=False)
    def thumb(category: str, name: str, _auth: auth_dep) -> FileResponse:
        if not re.fullmatch(r"[a-z]+", category) or not re.fullmatch(r"[a-z0-9_]+", name):
            raise HTTPException(status_code=404, detail="нет миниатюры")
        path = DOCS_IMG / category / f"{name}.jpg"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="нет миниатюры")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    return router
