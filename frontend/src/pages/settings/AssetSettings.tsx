import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  audioTracksApi,
  bannersApi,
  ffmpegPresetsApi,
  subtitleProfilesApi,
  type AudioTrack,
  type Banner,
  type FfmpegPreset,
  type SubtitleProfile,
} from "@/api/assets";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useDeleteMutation } from "@/hooks/useDeleteMutation";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading } from "@/components/ui";

function UploadButton({ label, accept, busy, onPick }: { label: string; accept: string; busy: boolean; onPick: (f: File) => void }) {
  return (
    <label className="btn primary" style={{ cursor: busy ? "wait" : "pointer" }}>
      {busy ? "Загрузка…" : label}
      <input
        type="file"
        accept={accept}
        hidden
        disabled={busy}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onPick(f);
          e.target.value = "";
        }}
      />
    </label>
  );
}

export function RenderPresetsSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: qk.ffmpegPresets, queryFn: ffmpegPresetsApi.list });
  const del = useDeleteMutation<FfmpegPreset>({ listKey: qk.ffmpegPresets, mutationFn: ffmpegPresetsApi.remove, successMessage: () => "Пресет удалён" });
  const reframe = useMutation({
    mutationFn: ({ id, on }: { id: number; on: boolean }) => ffmpegPresetsApi.update(id, { smart_reframe: on ? 1 : 0 }),
    onMutate: async ({ id, on }) => {
      await qc.cancelQueries({ queryKey: qk.ffmpegPresets });
      const prev = qc.getQueryData<FfmpegPreset[]>(qk.ffmpegPresets);
      qc.setQueryData<FfmpegPreset[]>(qk.ffmpegPresets, (old) =>
        old?.map((p) => (p.id === id ? { ...p, smart_reframe: on ? 1 : 0 } : p)),
      );
      return { prev };
    },
    onError: (e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(qk.ffmpegPresets, ctx.prev);
      toast.error(e instanceof ApiError ? e.message : "Не удалось обновить пресет");
    },
    onSettled: () => qc.invalidateQueries({ queryKey: qk.ffmpegPresets }),
  });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>
        «Умный кадр» динамически следит за объектом при кадрировании широкого видео в 9:16 (нужны данные фокуса из анализа gemini/polza).
      </p>
      {!q.data?.length ? <span className="muted">Пресетов нет</span> : null}
      {q.data?.map((p) => (
        <div key={p.id} className="ws-row">
          <strong style={{ flex: 1 }}>{p.label}</strong>
          <span className="muted" style={{ fontSize: 12.5 }}>
            {p.output_width}×{p.output_height} · {Math.round(p.fps)}fps
          </span>
          <label className="check" title="Динамическое кадрирование по точке интереса">
            <input
              type="checkbox"
              checked={(p.smart_reframe ?? 1) === 1}
              onChange={(e) => reframe.mutate({ id: p.id, on: e.target.checked })}
            />
            <span style={{ fontSize: 12.5 }}>Умный кадр</span>
          </label>
          <button className="btn ghost sm" onClick={() => del.mutate(p.id)}>
            🗑
          </button>
        </div>
      ))}
    </div>
  );
}

export function SubtitlesSettings() {
  const q = useQuery({ queryKey: qk.subtitleProfiles, queryFn: subtitleProfilesApi.list });
  const del = useDeleteMutation<SubtitleProfile>({ listKey: qk.subtitleProfiles, mutationFn: subtitleProfilesApi.remove, successMessage: () => "Профиль удалён" });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {!q.data?.length ? <span className="muted">Профилей субтитров нет</span> : null}
      {q.data?.map((p) => (
        <div key={p.id} className="ws-row">
          <span style={{ width: 16, height: 16, borderRadius: 4, background: p.primary_color }} />
          <strong style={{ flex: 1 }}>{p.label}</strong>
          <span className="muted" style={{ fontSize: 12.5 }}>
            {p.font_family} · {p.font_size}px
          </span>
          <button className="btn ghost sm" onClick={() => del.mutate(p.id)}>
            🗑
          </button>
        </div>
      ))}
    </div>
  );
}

export function BannersSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const [label, setLabel] = useState("");
  const q = useQuery({ queryKey: qk.banners, queryFn: bannersApi.list });
  const del = useDeleteMutation<Banner>({ listKey: qk.banners, mutationFn: bannersApi.remove, successMessage: () => "Баннер удалён" });
  const upload = useMutation({
    mutationFn: (f: File) => bannersApi.upload(f, label.trim() || f.name),
    onSuccess: () => {
      toast.success("Баннер загружен");
      setLabel("");
      qc.invalidateQueries({ queryKey: qk.banners });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось загрузить"),
  });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div className="panel" style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", maxWidth: 560 }}>
        <input className="input" style={{ flex: 1, minWidth: 160 }} placeholder="Название баннера" value={label} onChange={(e) => setLabel(e.target.value)} />
        <UploadButton label="📤 Загрузить баннер" accept="image/*,video/*" busy={upload.isPending} onPick={(f) => upload.mutate(f)} />
      </div>
      <div className="card-grid">
        {q.data?.map((b) => (
          <div key={b.id} className="panel" style={{ display: "grid", gap: 8 }}>
            <img src={`/media/banners/${b.id}`} alt={b.label} style={{ width: "100%", borderRadius: 8, background: "#000" }} />
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <strong style={{ flex: 1 }}>{b.label}</strong>
              <span className="muted" style={{ fontSize: 12 }}>{b.position}</span>
              <button className="btn ghost sm" onClick={() => del.mutate(b.id)}>🗑</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function AudioSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const [label, setLabel] = useState("");
  const q = useQuery({ queryKey: qk.audioTracks, queryFn: audioTracksApi.list });
  const del = useDeleteMutation<AudioTrack>({ listKey: qk.audioTracks, mutationFn: audioTracksApi.remove, successMessage: () => "Трек удалён" });
  const upload = useMutation({
    mutationFn: (f: File) => audioTracksApi.upload(f, label.trim() || f.name),
    onSuccess: () => {
      toast.success("Трек загружен");
      setLabel("");
      qc.invalidateQueries({ queryKey: qk.audioTracks });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось загрузить"),
  });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div className="panel" style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", maxWidth: 560 }}>
        <input className="input" style={{ flex: 1, minWidth: 160 }} placeholder="Название трека" value={label} onChange={(e) => setLabel(e.target.value)} />
        <UploadButton label="📤 Загрузить музыку" accept="audio/*" busy={upload.isPending} onPick={(f) => upload.mutate(f)} />
      </div>
      <div style={{ display: "grid", gap: 10 }}>
        {q.data?.map((t) => (
          <div key={t.id} className="ws-row">
            <strong style={{ flex: 1 }}>{t.label}</strong>
            <span className="muted" style={{ fontSize: 12.5 }}>громкость {Math.round(t.volume * 100)}%</span>
            <audio src={`/media/audio-tracks/${t.id}`} controls style={{ height: 30 }} />
            <button className="btn ghost sm" onClick={() => del.mutate(t.id)}>🗑</button>
          </div>
        ))}
      </div>
    </div>
  );
}
