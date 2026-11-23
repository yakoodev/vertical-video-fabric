import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "@/api/client";
import { assets3dApi, assets3dKeys, type AnimClip, type AnimEntry, type AssetKind } from "@/api/assets3d";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Icon } from "@/components/Icon";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading } from "@/components/ui";
import { projectPath } from "@/lib/paths";
import { clock, etaSlow, exprLabel, mb, num, sortExpressions } from "./labels";
import { DEFAULT_OPTIONS, useViewer, type ViewerOptions } from "./useViewer";
import type { ViewPreset } from "./viewer/stage";
import "@/styles/models3d.css";

/**
 * Окно просмотра одной 3D-модели: /p/:projectId/3d/:assetId — прямая ссылка, её можно переслать.
 * Вращать, приближать, сдвигать (мышь и пальцы), виды, свет, фон, каркас, сведения о модели; у персонажей VRM —
 * выражения лица, поза, дыхание, физика волос и «Анимации» с полосой времени.
 */

const VIEWS: [ViewPreset, string][] = [["front", "Спереди"], ["threeQuarter", "¾"], ["side", "Сбоку"], ["back", "Сзади"]];
const SPEEDS = [0.25, 0.5, 1];
const KIND_OPTIONS: [AssetKind, string][] = [["character", "Персонажи"], ["prop", "Реквизит"], ["environment", "Окружение"]];

function Seg<T extends string | number | boolean>({ value, options, onChange, label }: {
  value: T; options: [T, string][]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="m3d-seg" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={String(v)} type="button" className={v === value ? "on" : ""} aria-pressed={v === value} onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

export function Model3dPage() {
  const params = useParams();
  const projectId = Number(params.projectId);
  const assetId = Number(params.assetId);
  const navigate = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const asset = useQuery({ queryKey: assets3dKeys.one(assetId), queryFn: () => assets3dApi.get(assetId), enabled: assetId > 0 });
  const isVrm = !!asset.data?.is_vrm;
  const anims = useQuery({ queryKey: assets3dKeys.anims, queryFn: assets3dApi.anims, enabled: isVrm, staleTime: 300_000 });
  const [opts, setOpts] = useState<ViewerOptions>(DEFAULT_OPTIONS);
  const set = <K extends keyof ViewerOptions>(k: K, v: ViewerOptions[K]) => setOpts((o) => ({ ...o, [k]: v }));
  const v = useViewer(asset.data, opts);
  const [clipId, setClipId] = useState("");
  const [clipBusy, setClipBusy] = useState(false);
  const cache = useRef(new Map<string, AnimClip>());
  const [confirmDel, setConfirmDel] = useState(false);
  const thumbTried = useRef(false);

  const clipEntry = useMemo(() => anims.data?.clips.find((c) => c.id === clipId), [anims.data, clipId]);
  const grouped = useMemo(() => {
    const out: { group: string; main: AnimEntry[] }[] = [];
    const more: AnimEntry[] = [];
    for (const g of anims.data?.groups ?? []) {
      const items = (anims.data?.clips ?? []).filter((c) => c.group === g);
      const main = items.filter((c) => c.curated);
      if (main.length) out.push({ group: g, main });
      more.push(...items.filter((c) => !c.curated));
    }
    return { out, more };
  }, [anims.data]);

  // Первая обложка карточки — снимок этого окна (если у модели своей нет).
  useEffect(() => {
    const a = asset.data;
    if (v.phase !== "ready" || !a || a.has_thumb || thumbTried.current) return;
    thumbTried.current = true;
    const t = window.setTimeout(async () => {
      const blob = await v.snapshot();
      if (!blob) return;
      try {
        await assets3dApi.thumb(a.id, blob);
        qc.invalidateQueries({ queryKey: assets3dKeys.list(a.project_id) });
      } catch {
        // обложка — не главное: без неё карточка покажет значок
      }
    }, 600);
    return () => window.clearTimeout(t);
  }, [v.phase, asset.data, v, qc]);

  const chooseClip = async (id: string) => {
    setClipId(id);
    if (!id) {
      v.setClip(null, false);
      return;
    }
    const entry = anims.data?.clips.find((c) => c.id === id);
    const move = entry?.root === "move";
    setOpts((o) => ({ ...o, move, playing: true, loop: entry?.loop ?? true, expression: o.expression }));
    let clip = cache.current.get(id);
    if (!clip) {
      setClipBusy(true);
      try {
        clip = await assets3dApi.anim(id);
        cache.current.set(id, clip);
      } catch (e) {
        toast.error(e instanceof ApiError ? e.message : "анимация не загрузилась");
        setClipBusy(false);
        setClipId("");
        return;
      }
      setClipBusy(false);
    }
    v.setClip(clip, move, entry?.loop ?? true);
  };

  const toggleMove = (move: boolean) => {
    set("move", move);
    const clip = cache.current.get(clipId);
    if (clip) v.setClip(clip, move, opts.loop);
  };

  const patch = useMutation({
    mutationFn: (body: { name?: string; kind?: AssetKind }) => assets3dApi.patch(assetId, body),
    onSuccess: (a) => {
      qc.setQueryData(assets3dKeys.one(assetId), a);
      qc.invalidateQueries({ queryKey: assets3dKeys.list(projectId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "не сохранилось"),
  });
  const remove = useMutation({
    mutationFn: () => assets3dApi.remove(assetId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: assets3dKeys.list(projectId) });
      navigate(projectPath(projectId, "models3d"));
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "не удалилось"),
  });

  const makeCover = async () => {
    const blob = await v.snapshot();
    if (!blob) return;
    try {
      await assets3dApi.thumb(assetId, blob);
      qc.invalidateQueries({ queryKey: assets3dKeys.list(projectId) });
      toast.success("Обложка карточки — этот вид");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "не сохранилось");
    }
  };

  const copyLink = async () => {
    const url = `${window.location.origin}/p/${projectId}/3d/${assetId}`;
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Ссылка на модель скопирована");
    } catch {
      window.prompt("Ссылка на модель", url);
    }
  };

  if (asset.isLoading) return <Loading />;
  if (asset.isError || !asset.data) return <ErrorState error={asset.error} onRetry={() => asset.refetch()} />;
  const a = asset.data;
  const info = a.info ?? {};
  const pct = v.progress.total ? Math.min(100, Math.round((v.progress.done / v.progress.total) * 100)) : 0;
  const net = a.preview_gz_bytes || a.preview_bytes;
  const maxTex = (list?: { width: number | null; height: number | null }[]) =>
    Math.max(0, ...(list ?? []).map((i) => Math.max(i.width ?? 0, i.height ?? 0)));
  const exprs = sortExpressions(v.expressions);

  return (
    <div className="m3d-page">
      <div className="m3d-head">
        <Link className="m3d-back" to={projectPath(projectId, "models3d")}>
          <Icon name="arrow" size={16} /> Все модели
        </Link>
        <h1>{a.name}</h1>
        <span className="m3d-kind">{a.kind_label}</span>
        <div className="m3d-head-acts">
          <button className="btn sm" onClick={copyLink} title="Прямая ссылка на это окно — можно переслать">
            <Icon name="send" size={15} /> Ссылка
          </button>
        </div>
      </div>

      <div className={`m3d-layout${isVrm ? " has-timeline" : ""}`}>
        {/* Шкала анимации — под картинкой, а не поверх: на нижнем ракурсе она закрывала ноги персонажа. */}
        <div className={`m3d-canvas-wrap${isVrm && clipId ? " has-tl" : ""}`}>
          <div className="m3d-canvas" ref={v.hostRef} />
          {a.status !== "ready" ? (
            <div className="m3d-overlay">
              <strong>{a.status === "failed" ? "Модель не открылась" : "Готовлю модель к просмотру…"}</strong>
              {a.error ? <span>{a.error}</span> : null}
            </div>
          ) : v.phase === "loading" || v.phase === "parsing" ? (
            <div className="m3d-overlay">
              <strong>{v.phase === "parsing" ? "Собираю модель…" : `Загружаю модель · ${pct} %`}</strong>
              <div className="m3d-bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                <span style={{ width: `${v.phase === "parsing" ? 100 : pct}%` }} />
              </div>
              <span className="muted">
                {mb(v.progress.done)} из {mb(v.progress.total || net)} · на медленной связи {etaSlow(net)}
              </span>
            </div>
          ) : v.phase === "error" ? (
            <div className="m3d-overlay">
              <strong>Модель не загрузилась</strong>
              <span>{v.error}</span>
            </div>
          ) : null}
          <div className="m3d-views">
            {VIEWS.map(([key, label]) => (
              <button key={key} className="btn sm" onClick={() => v.view(key)}>{label}</button>
            ))}
            <button className="btn sm" onClick={v.reset} title="Вернуть камеру как было">Сбросить</button>
            <button className={`btn sm${opts.turntable ? " is-on" : ""}`} aria-pressed={opts.turntable}
              onClick={() => set("turntable", !opts.turntable)} title="Модель медленно поворачивается">
              Автоповорот
            </button>
          </div>
          {isVrm && clipId ? (
            <div className="m3d-timeline">
              <button className="btn sm m3d-play" onClick={() => {
                if (v.ended) v.restart();
                set("playing", v.ended ? true : !opts.playing);
              }} aria-label={opts.playing && !v.ended ? "Пауза" : "Играть"}>
                {opts.playing && !v.ended ? "❚❚" : "▶"}
              </button>
              <input type="range" min={0} max={v.clipDuration || 0} step={0.01} value={Math.min(v.clipTime, v.clipDuration)}
                onChange={(e) => v.seek(Number(e.target.value))} aria-label="Время анимации" />
              <span className="mono m3d-time">{clock(v.clipTime)} / {clock(v.clipDuration)}</span>
              <Seg label="Скорость" value={opts.speed} onChange={(s) => set("speed", s)}
                options={SPEEDS.map((s) => [s, s === 1 ? "1×" : `${String(s).replace(".", ",")}×`])} />
              <label className="m3d-check"><input type="checkbox" checked={opts.loop} onChange={(e) => set("loop", e.target.checked)} /> По кругу</label>
              <label className="m3d-check" title="Персонаж уходит с места, камера идёт за ним">
                <input type="checkbox" checked={opts.move} onChange={(e) => toggleMove(e.target.checked)} /> С перемещением
              </label>
            </div>
          ) : null}
        </div>

        <aside className="m3d-side">
          {isVrm ? (
            <section className="m3d-sec">
              <h3>Персонаж</h3>
              <label className="m3d-field">
                <span>Анимации</span>
                <select value={clipId} onChange={(e) => chooseClip(e.target.value)} disabled={!v.vrmReady || anims.isLoading}>
                  <option value="">— стоит на месте —</option>
                  {grouped.out.map((g) => (
                    <optgroup key={g.group} label={g.group}>
                      {g.main.map((c) => <option key={c.id} value={c.id}>{c.ru}</option>)}
                    </optgroup>
                  ))}
                  {grouped.more.length ? (
                    <optgroup label="ещё">
                      {grouped.more.map((c) => <option key={c.id} value={c.id}>{c.group}: {c.ru}</option>)}
                    </optgroup>
                  ) : null}
                </select>
              </label>
              {clipBusy ? <p className="m3d-note">Загружаю анимацию…</p> : null}
              {clipEntry ? (
                <p className="m3d-note">
                  Откуда: {clipEntry.source || "—"}<br />
                  Можно использовать: {clipEntry.license || "—"}
                </p>
              ) : null}
              <label className="m3d-field">
                <span>Лицо</span>
                <select value={opts.expression} onChange={(e) => set("expression", e.target.value)} disabled={!exprs.length}>
                  <option value="">Как есть</option>
                  {exprs.map((n) => <option key={n} value={n}>{exprLabel(n)}</option>)}
                </select>
              </label>
              <label className="m3d-check"><input type="checkbox" checked={opts.autoBlink} onChange={(e) => set("autoBlink", e.target.checked)} /> Моргает сам</label>
              {!clipId ? (
                <>
                  <div className="m3d-field">
                    <span>Поза</span>
                    <Seg label="Поза" value={opts.pose} onChange={(p) => set("pose", p)}
                      options={[["relaxed", "Руки опущены"], ["tpose", "Т-поза"]]} />
                  </div>
                  <label className="m3d-check"><input type="checkbox" checked={opts.breathing} onChange={(e) => set("breathing", e.target.checked)} /> Дышит</label>
                </>
              ) : null}
              <p className="m3d-note">Волосы и одежда двигаются сами, как в роликах.</p>
            </section>
          ) : null}

          <section className="m3d-sec">
            <h3>Свет и фон</h3>
            <Seg label="Свет" value={opts.light} onChange={(l) => set("light", l)} options={[["studio", "Студия"], ["toon", "Мультяшный"]]} />
            <Seg label="Фон" value={opts.background} onChange={(b) => set("background", b)} options={[["dark", "Тёмный"], ["light", "Светлый"]]} />
            <label className="m3d-check"><input type="checkbox" checked={opts.wireframe} onChange={(e) => set("wireframe", e.target.checked)} /> Показать сетку модели</label>
          </section>

          <section className="m3d-sec">
            <h3>О модели</h3>
            <dl className="m3d-facts">
              <dt>Треугольников</dt><dd>{num(info.triangles)}</dd>
              <dt>Вершин</dt><dd>{num(info.vertices)}</dd>
              <dt>Материалов</dt><dd>{num(info.materials)}</dd>
              <dt>Текстур</dt><dd>{num(info.textures)}{maxTex(info.images) ? ` · до ${maxTex(info.images)} px` : ""}</dd>
              {isVrm ? <><dt>Выражений лица</dt><dd>{num(info.expressions?.length)}</dd></> : null}
              {isVrm ? <><dt>Пружинок волос и одежды</dt><dd>{num(info.spring_bones)}</dd></> : null}
              <dt>Исходный файл</dt><dd>{mb(a.size_bytes)}</dd>
              <dt>Для просмотра</dt><dd>{mb(a.preview_bytes)}{maxTex(info.preview_images) ? ` · текстуры до ${maxTex(info.preview_images)} px` : ""}</dd>
              <dt>Скачивается</dt><dd>{mb(net)}</dd>
              {info.meta?.author ? <><dt>Автор</dt><dd>{info.meta.author}</dd></> : null}
              <dt>Файл</dt><dd className="m3d-file">{a.original_filename}</dd>
            </dl>
          </section>

          <section className="m3d-sec">
            <h3>Карточка</h3>
            <label className="m3d-field">
              <span>Название</span>
              <input className="input" defaultValue={a.name} key={a.name}
                onBlur={(e) => e.target.value.trim() && e.target.value !== a.name && patch.mutate({ name: e.target.value })} />
            </label>
            <label className="m3d-field">
              <span>Группа</span>
              <select value={a.kind} onChange={(e) => patch.mutate({ kind: e.target.value as AssetKind })}>
                {KIND_OPTIONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
            <div className="m3d-acts">
              <button className="btn sm" onClick={makeCover} disabled={v.phase !== "ready"}>Сделать обложкой</button>
              <a className="btn sm" href={a.urls.original} download={a.original_filename}>Скачать файл модели</a>
              <button className="btn sm danger" onClick={() => setConfirmDel(true)}><Icon name="trash" size={15} /> Удалить</button>
            </div>
          </section>
        </aside>
      </div>
      <ConfirmDialog open={confirmDel} title={`Удалить «${a.name}»?`} body="Модель и её файлы уйдут из проекта."
        busy={remove.isPending} onConfirm={() => remove.mutate()} onCancel={() => setConfirmDel(false)} />
    </div>
  );
}
