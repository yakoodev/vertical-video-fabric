// ИИ-видео, блокинг: 3D-макет плана в нашем движке сцен (0 ₽), композиция для ключевых кадров.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { aivApi, type Overview, type Shot } from "@/api/aivideo";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { EpisodePicker, errText, Frame, useEpisode, useInvalidate } from "./shared";
import { StageButton } from "./StageControls";

// Откуда раскладка блокинга (app/aivideo_blocking.py: source).
const BLOCKING_SOURCE: Record<string, string> = { auto: "авто по состоянию", gemini: "Gemini", agent: "агент", owner: "вручную" };

// --------------------------------------------------------------------------- //
// Блокинг: 3D-макет плана в нашем движке сцен (0 ₽) — композиция для ключевых кадров
// --------------------------------------------------------------------------- //

export function BlockingSub({ o }: { o: Overview }) {
  const ep = useEpisode(o);
  const s = ep.script;
  return (
    <div className="series-col">
      <section className="panel series-panel">
        <div className="series-row">
          <EpisodePicker o={o} id={ep.id} setId={ep.setId} />
          {s ? <StageButton projectId={o.project.id} stage="blocking" scriptId={s.id} label="Блокинг серии (0 ₽): оценить" /> : null}
        </div>
        <span className="muted">
          Gemini раскладывает планы простыми манекенами (капсула с «носом» — человек, плоский диск — робот-пылесос, коробки —
          мебель), движок рисует старт и конец плана. Скриншот идёт первым референсом кадра: композиция, позы, места, ракурс.
        </span>
      </section>
      {s ? (
        <div className="series-col">
          {s.shots?.map((shot) => <BlockingShot key={shot.id} shot={shot} projectId={o.project.id} />)}
        </div>
      ) : null}
    </div>
  );
}

function BlockingShot({ shot, projectId }: { shot: Shot; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const qc = useQueryClient();
  const key = ["ai-video", "blocking", String(shot.id)];
  const q = useQuery({ queryKey: key, queryFn: () => aivApi.blocking(shot.id) });
  const [text, setText] = useState<string | null>(null);
  const done = (msg: string) => {
    toast.success(msg);
    setText(null);
    qc.invalidateQueries({ queryKey: key });
    invalidate();
  };
  const save = useMutation({
    mutationFn: () => {
      let spec: unknown;
      try {
        spec = JSON.parse(text ?? "{}");
      } catch {
        throw new Error("раскладка — JSON: {room, objects, start: {actors, props}, end: {…}}");
      }
      return aivApi.setBlocking(shot.id, { spec });
    },
    onSuccess: () => done(`План ${shot.idx}: блокинг отрисован (0 ₽)`),
    onError: (e) => toast.error(e instanceof Error && !(e instanceof ApiError) ? e.message : errText(e, "Не сохранено")),
  });
  const rewrite = useMutation({
    mutationFn: () => aivApi.setBlocking(shot.id, { rewrite: true }),
    onSuccess: () => done(`План ${shot.idx}: раскладка пересоздана`),
    onError: (e) => toast.error(errText(e, "Не пересоздано")),
  });
  const b = q.data;
  const startKf = shot.start_frame ?? null;
  const endKf = shot.end_frame ?? null;
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <b>
          План {shot.idx} · {shot.action || "—"}
        </b>
        <span className="muted">
          {b ? `раскладка: ${BLOCKING_SOURCE[b.source] ?? b.source}${b.outdated ? " (устарела — состояние плана поменялось)" : ""}` : ""}
          {b?.legend ? ` · ${b.legend}` : ""}
        </span>
      </div>
      <div className="aiv-frames">
        <Frame ref_={b?.frames.start ?? shot.blocking_frames?.start} label="блокинг: старт" empty="не отрисован" />
        <Frame ref_={startKf} label="кадр: старт" empty="кадра нет" />
        {shot.needs_motion ? (
          <>
            <Frame ref_={b?.frames.end ?? shot.blocking_frames?.end} label="блокинг: конец" empty="не отрисован" />
            <Frame ref_={endKf} label="кадр: конец" empty="кадра нет" />
          </>
        ) : null}
      </div>
      <div className="series-row">
        <button className="btn ghost sm" disabled={rewrite.isPending} onClick={() => rewrite.mutate()}
                title="Gemini заново разложит план по состоянию и концу плана (без ключа — по состоянию)">
          ↻ Пересоздать раскладку
        </button>
      </div>
      {/* JSON раскладки нужен редко (правка вручную) — свёрнут, чтобы три плана не были тремя простынями. */}
      <details className="aiv-more" open={text != null}>
        <summary>Править раскладку вручную (JSON)</summary>
        <label className="field">
          <span>Сантиметры: x вправо, z к камере; facing 0 — в камеру, 90 — вправо</span>
          <textarea className="input mono" rows={6} value={text ?? (b ? JSON.stringify(b.spec, null, 1) : "")}
                    onChange={(e) => setText(e.target.value)} />
        </label>
        <div className="series-row">
          <button className="btn sm" disabled={save.isPending || text == null} onClick={() => save.mutate()}>
            Сохранить и отрисовать
          </button>
        </div>
      </details>
    </section>
  );
}
