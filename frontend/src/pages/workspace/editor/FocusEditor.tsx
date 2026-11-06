import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { segmentsApi } from "@/api/segments";
import { qk } from "@/api/keys";
import { type AiSegment, type FocusPoint } from "@/api/types";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { formatDuration } from "@/components/ui";

// Manual focus track editor: drop point-of-interest keyframes at the playhead so
// the smart 9:16 reframe follows the subject. Pairs with the LLM auto-track.
export function FocusEditor({
  segment,
  sourceId,
  videoRef,
}: {
  segment: AiSegment;
  sourceId: string;
  videoRef: React.RefObject<HTMLVideoElement>;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [points, setPoints] = useState<FocusPoint[]>(segment.focus ?? []);
  const [x, setX] = useState(0.5);
  const segDur = Math.max(0.1, segment.end_sec - segment.start_sec);

  useEffect(() => {
    setPoints(segment.focus ?? []);
  }, [segment.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: (pts: FocusPoint[]) => segmentsApi.setFocus(segment.id, pts),
    onSuccess: () => {
      toast.success("Точки фокуса сохранены");
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить фокус"),
  });

  const detect = useMutation({
    mutationFn: () => segmentsApi.autofocusOne(segment.id),
    onSuccess: (seg) => {
      setPoints(seg.focus ?? []);
      toast.success(`Детектор: ${seg.focus?.length ?? 0} точек`);
      qc.invalidateQueries({ queryKey: qk.source(sourceId) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Детектор не сработал"),
  });

  const addAtPlayhead = () => {
    const cur = videoRef.current?.currentTime ?? segment.start_sec;
    const t = Math.min(segDur, Math.max(0, cur - segment.start_sec));
    const next = [...points.filter((p) => Math.abs(p.t - t) > 0.05), { t: Number(t.toFixed(2)), x }].sort((a, b) => a.t - b.t);
    setPoints(next);
  };

  return (
    <div className="focus-ed">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <strong style={{ fontSize: 13 }}>{segment.title}</strong>
        <span className="muted" style={{ fontSize: 12 }}>
          {points.length ? `${points.length} точек` : "следует за центром"}
        </span>
      </div>
      <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
        Поставьте видео на нужный момент, задайте горизонтальный центр и добавьте точку. Между точками камера движется плавно.
      </p>
      <label className="field">
        <span style={{ display: "flex", justifyContent: "space-between" }}>
          <span>Центр по горизонтали</span>
          <span className="mono">{Math.round(x * 100)}%</span>
        </span>
        <input type="range" min={0} max={1} step={0.01} value={x} onChange={(e) => setX(Number(e.target.value))} />
      </label>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          className="btn sm"
          disabled={detect.isPending}
          title="Перезаписать фокус этого сегмента детектором (лица/движение)"
          onClick={() => detect.mutate()}
        >
          {detect.isPending ? "Детект…" : "Детектор"}
        </button>
        <button className="btn sm" onClick={addAtPlayhead}>
          + Точка на текущем кадре
        </button>
        <button className="btn primary sm" disabled={save.isPending} onClick={() => save.mutate(points)}>
          {save.isPending ? "…" : "Сохранить фокус"}
        </button>
        {points.length ? (
          <button className="btn ghost sm" onClick={() => setPoints([])}>
            Очистить
          </button>
        ) : null}
      </div>
      {points.length ? (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {points.map((p, i) => (
            <button
              key={i}
              className="focus-chip"
              title="Перейти / удалить точку"
              onClick={() => {
                if (videoRef.current) videoRef.current.currentTime = segment.start_sec + p.t;
                setX(p.x);
              }}
            >
              <span className="mono">{formatDuration(p.t)}</span> · {Math.round(p.x * 100)}%
              <span
                className="focus-chip-x"
                onClick={(e) => {
                  e.stopPropagation();
                  setPoints((prev) => prev.filter((_, idx) => idx !== i));
                }}
              >
                ×
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
