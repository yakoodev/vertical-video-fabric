/**
 * «Новый холст»: клип без исходного видео (app/canvas.py) — длительность 5–180 с и
 * анимированный фон; дальше слои, музыка и рендер как у обычного клипа. Создаёт
 * POST /api/canvas-clips и сразу открывает редактор (editor_url).
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ApiError } from "@/api/client";
import { clipPlansApi } from "@/api/clipPlans";
import { useToast } from "@/components/Toast";
import { DEFAULT_BACKGROUND, type Background } from "@/motion";
import { BackgroundPicker } from "@/pages/workspace/BackgroundPicker";

// Как app/canvas.py: MIN_DURATION_SEC, DEFAULT_DURATION_SEC и максимум куска.
const MIN_SEC = 5;
const MAX_SEC = 180;
const DEFAULT_SEC = 15;

export function NewCanvas({ onCreated, disabled }: { onCreated?: () => void; disabled?: boolean }) {
  const toast = useToast();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [duration, setDuration] = useState(DEFAULT_SEC);
  const [title, setTitle] = useState("");
  const [bg, setBg] = useState<Background>(DEFAULT_BACKGROUND);

  // Набранное руками приводим к 5–180 с, как сервер.
  const sec = Math.min(MAX_SEC, Math.max(MIN_SEC, Math.round(duration) || DEFAULT_SEC));

  const create = useMutation({
    mutationFn: () => clipPlansApi.createCanvas({ duration: sec, background: bg, title: title.trim() }),
    onSuccess: (plan) => {
      toast.success("Холст создан — добавляйте картинки, текст и музыку");
      setOpen(false);
      onCreated?.();
      navigate(plan.editor_url);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось создать холст"),
  });

  return (
    <>
      <button
        type="button"
        className={`btn${open ? " active" : ""}`}
        disabled={disabled || create.isPending}
        aria-expanded={open}
        title="Ролик без видео: анимированный фон, картинки, титры и музыка (промо, анонс)"
        onClick={() => setOpen((v) => !v)}
      >
        🎨 Новый холст
      </button>
      {open ? (
        <div className="canvas-new">
          <div className="canvas-new-row">
            <label className="field">
              <span>Название</span>
              <input
                className="input"
                placeholder="Холст"
                maxLength={100}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <label className="field range canvas-new-dur">
              <span>Длительность · {sec} с</span>
              <input
                type="range"
                min={MIN_SEC}
                max={MAX_SEC}
                step={1}
                value={sec}
                onChange={(e) => setDuration(Number(e.target.value))}
              />
            </label>
            <input
              className="input canvas-new-sec"
              type="number"
              min={MIN_SEC}
              max={MAX_SEC}
              step={1}
              value={duration}
              aria-label="Длительность, с"
              onChange={(e) => setDuration(Number(e.target.value))}
              onBlur={() => setDuration(sec)}
            />
          </div>
          <div className="li-cap">Фон — его можно сменить и в редакторе («Фон холста»)</div>
          <BackgroundPicker value={bg} onChange={setBg} />
          <div className="canvas-new-row">
            <span className="muted li-note lay-grow">Видео не нужно: весь кадр — фон, сверху слои.</span>
            <button type="button" className="btn ghost" onClick={() => setOpen(false)} disabled={create.isPending}>
              Отмена
            </button>
            <button type="button" className="btn primary" disabled={create.isPending} onClick={() => create.mutate()}>
              {create.isPending ? "Создаю…" : `Создать холст · ${sec} с`}
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}
