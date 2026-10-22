import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { brandsApi, type Brand, type BrandInput } from "@/api/brands";
import { audioTracksApi, montageAssetsApi } from "@/api/assets";
import { motionApi } from "@/api/motion";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading } from "@/components/ui";
import { CAPTION_STYLES } from "@/motion";

// Новый бренд начинается с нейтральной формы — те же умолчания, что у normalize_brand.
const EMPTY: BrandInput = {
  name: "",
  font: "",
  colors: { text: "#ffffff", accent: "#ffd600", plate: "#0b0d14" },
  logo_asset_id: null,
  logo_corner: "top-right",
  caption_style: { style: "hormozi", font: "", color: null, activeColor: null, stroke: null, bg: null },
  hook_style: { font: "", color: null, size: null, plate: false, stroke: { color: "#000000", width: 10 }, uppercase: true },
  cta: { text: "", url_text: "", duration_sec: 3 },
  music_track_id: null,
  backdrop: { color: "#0b0d14", color2: "" },
  sfx_policy: "minimal",
};

function toInput(b: Brand): BrandInput {
  const { id: _id, created_at: _c, updated_at: _u, ...rest } = b;
  return rest;
}

function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="field" style={{ width: 96 }}>
      <span>{label}</span>
      <input type="color" className="lay-color" value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/** Схематичный кадр 9:16: хук, логотип, субтитры и призыв в цветах бренда. */
function BrandPreview({ b, logoUrl }: { b: BrandInput; logoUrl?: string }) {
  const font = b.font ? `'${b.font}', sans-serif` : undefined;
  const bg = b.backdrop.color2 ? `linear-gradient(${b.backdrop.color}, ${b.backdrop.color2})` : b.backdrop.color;
  const hookColor = b.hook_style.color || b.colors.accent;
  return (
    <div className="brand-preview" style={{ background: bg, fontFamily: font }}>
      <div
        className="brand-preview-hook"
        style={{
          color: hookColor,
          background: b.hook_style.plate ? b.colors.plate : "transparent",
          textTransform: b.hook_style.uppercase ? "uppercase" : "none",
          WebkitTextStroke: b.hook_style.stroke ? `1px ${b.hook_style.stroke.color}` : undefined,
        }}
      >
        Хук ролика
      </div>
      {logoUrl ? (
        <img className={`brand-preview-logo ${b.logo_corner}`} src={logoUrl} alt="" />
      ) : null}
      {b.cta.text || b.cta.url_text ? (
        <div className="brand-preview-cta" style={{ background: b.colors.plate, color: b.colors.text }}>
          {b.cta.text ? <div>{b.cta.text}</div> : null}
          {b.cta.url_text ? <div style={{ color: b.colors.accent }}>{b.cta.url_text}</div> : null}
        </div>
      ) : null}
      <div className="brand-preview-caps" style={{ color: b.caption_style.color || b.colors.text }}>
        слова <span style={{ color: b.caption_style.activeColor || b.colors.accent }}>субтитров</span>
      </div>
    </div>
  );
}

function BrandForm({ initial, id, onDone }: { initial: BrandInput; id: number | null; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [b, setB] = useState<BrandInput>(initial);
  const fonts = useQuery({ queryKey: ["motion", "fonts"], queryFn: motionApi.fonts, staleTime: Infinity });
  const assets = useQuery({ queryKey: qk.montageAssets, queryFn: montageAssetsApi.list, staleTime: 60_000 });
  const tracks = useQuery({ queryKey: qk.audioTracks, queryFn: audioTracksApi.list });
  const families = useMemo(
    () => Array.from(new Set((fonts.data ?? []).map((f) => f.family))).sort((a, c) => a.localeCompare(c)),
    [fonts.data],
  );
  const images = (assets.data ?? []).filter((a) => a.kind === "image" || a.kind === "gif");
  const logo = images.find((a) => a.id === b.logo_asset_id);
  const set = <K extends keyof BrandInput>(key: K, value: BrandInput[K]) => setB((prev) => ({ ...prev, [key]: value }));

  const save = useMutation({
    mutationFn: () => (id ? brandsApi.update(id, b) : brandsApi.create(b)),
    onSuccess: () => {
      toast.success(id ? "Бренд сохранён" : "Бренд создан");
      qc.invalidateQueries({ queryKey: qk.brands });
      onDone();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить бренд"),
  });

  return (
    <div className="panel brand-editor">
      <div style={{ display: "grid", gap: 12, minWidth: 0 }}>
        <strong>{id ? `Бренд «${initial.name}»` : "Новый бренд"}</strong>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: 1, minWidth: 180 }}>
            <span>Название</span>
            <input className="input" value={b.name} onChange={(e) => set("name", e.target.value)} placeholder="VTubika" />
          </label>
          <label className="field" style={{ width: 200 }}>
            <span>Шрифт</span>
            <select className="input" value={b.font} onChange={(e) => set("font", e.target.value)}>
              <option value="">По умолчанию</option>
              {families.map((f) => (
                <option key={f} value={f}>{f}</option>
              ))}
            </select>
          </label>
        </div>

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <ColorField label="Текст" value={b.colors.text} onChange={(v) => set("colors", { ...b.colors, text: v })} />
          <ColorField label="Акцент" value={b.colors.accent} onChange={(v) => set("colors", { ...b.colors, accent: v })} />
          <ColorField label="Плашка" value={b.colors.plate} onChange={(v) => set("colors", { ...b.colors, plate: v })} />
        </div>

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: 1, minWidth: 200 }}>
            <span>Логотип (из «Файлов для монтажа»)</span>
            <select
              className="input"
              value={b.logo_asset_id ?? 0}
              onChange={(e) => set("logo_asset_id", Number(e.target.value) || null)}
            >
              <option value={0}>Без логотипа</option>
              {images.map((a) => (
                <option key={a.id} value={a.id}>{a.label}</option>
              ))}
            </select>
          </label>
          <label className="field" style={{ width: 150 }}>
            <span>Угол</span>
            <select
              className="input"
              value={b.logo_corner}
              onChange={(e) => set("logo_corner", e.target.value as BrandInput["logo_corner"])}
            >
              <option value="top-right">Справа сверху</option>
              <option value="top-left">Слева сверху</option>
            </select>
          </label>
        </div>

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label className="field" style={{ width: 180 }}>
            <span>Субтитры: стиль</span>
            <select
              className="input"
              value={b.caption_style.style}
              onChange={(e) => set("caption_style", { ...b.caption_style, style: e.target.value })}
            >
              {CAPTION_STYLES.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </label>
          <label className="check" title="Обводка слов субтитров цветом плашки">
            <input
              type="checkbox"
              checked={Boolean(b.caption_style.stroke)}
              onChange={(e) =>
                set("caption_style", {
                  ...b.caption_style,
                  stroke: e.target.checked ? { color: b.colors.plate, width: 8 } : null,
                })
              }
            />
            <span>обводка</span>
          </label>
          <label className="check" title="Хук на плашке цвета бренда (вместо обводки)">
            <input
              type="checkbox"
              checked={b.hook_style.plate}
              onChange={(e) =>
                set("hook_style", {
                  ...b.hook_style,
                  plate: e.target.checked,
                  stroke: e.target.checked ? null : { color: "#000000", width: 10 },
                })
              }
            />
            <span>хук на плашке</span>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={b.hook_style.uppercase}
              onChange={(e) => set("hook_style", { ...b.hook_style, uppercase: e.target.checked })}
            />
            <span>хук капсом</span>
          </label>
        </div>

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <label className="field" style={{ flex: 1, minWidth: 160 }}>
            <span>Призыв в конце</span>
            <input
              className="input"
              value={b.cta.text}
              maxLength={60}
              placeholder="Каталог в профиле"
              onChange={(e) => set("cta", { ...b.cta, text: e.target.value })}
            />
          </label>
          <label className="field" style={{ flex: 1, minWidth: 140 }}>
            <span>Адрес (акцентом)</span>
            <input
              className="input"
              value={b.cta.url_text}
              maxLength={60}
              placeholder="vtubika.store"
              onChange={(e) => set("cta", { ...b.cta, url_text: e.target.value })}
            />
          </label>
          <label className="field" style={{ width: 90 }}>
            <span>Секунд</span>
            <input
              className="input"
              type="number"
              min={1.5}
              max={8}
              step={0.5}
              value={b.cta.duration_sec}
              onChange={(e) => set("cta", { ...b.cta, duration_sec: Number(e.target.value) || 3 })}
            />
          </label>
        </div>

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label className="field" style={{ flex: 1, minWidth: 180 }}>
            <span>Музыка</span>
            <select
              className="input"
              value={b.music_track_id ?? 0}
              onChange={(e) => set("music_track_id", Number(e.target.value) || null)}
            >
              <option value={0}>Не трогать</option>
              {(tracks.data ?? []).map((t) => (
                <option key={t.id} value={t.id}>{t.label}</option>
              ))}
            </select>
          </label>
          <label className="field" style={{ width: 170 }}>
            <span>Звуковые эффекты</span>
            <select
              className="input"
              value={b.sfx_policy}
              onChange={(e) => set("sfx_policy", e.target.value as BrandInput["sfx_policy"])}
            >
              <option value="minimal">Не больше двух</option>
              <option value="none">Без эффектов</option>
            </select>
          </label>
        </div>

        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <ColorField label="Фон (зелёнка)" value={b.backdrop.color} onChange={(v) => set("backdrop", { ...b.backdrop, color: v })} />
          {b.backdrop.color2 ? (
            <ColorField label="Низ градиента" value={b.backdrop.color2} onChange={(v) => set("backdrop", { ...b.backdrop, color2: v })} />
          ) : null}
          <label className="check" title="Фон за вырезанным персонажем — градиентом сверху вниз">
            <input
              type="checkbox"
              checked={Boolean(b.backdrop.color2)}
              onChange={(e) => set("backdrop", { ...b.backdrop, color2: e.target.checked ? b.colors.plate : "" })}
            />
            <span>градиент</span>
          </label>
        </div>

        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn primary" disabled={save.isPending || !b.name.trim()} onClick={() => save.mutate()}>
            {save.isPending ? "Сохранение…" : "Сохранить"}
          </button>
          <button className="btn ghost" onClick={onDone}>Отмена</button>
        </div>
      </div>
      <BrandPreview b={b} logoUrl={logo?.url} />
    </div>
  );
}

export function BrandsSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: qk.brands, queryFn: brandsApi.list });
  // null — форма закрыта, 0 — новый бренд, id — правка.
  const [editing, setEditing] = useState<number | null>(null);
  const remove = useMutation({
    mutationFn: (id: number) => brandsApi.remove(id),
    onSuccess: () => {
      toast.success("Бренд удалён");
      qc.invalidateQueries({ queryKey: qk.brands });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось удалить"),
  });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const current = editing ? q.data?.find((b) => b.id === editing) : undefined;

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <p className="muted" style={{ fontSize: 13, margin: 0, maxWidth: 680 }}>
        Бренд — стиль роликов одного заказчика: шрифт, цвета, логотип, призыв в конце, музыка. В монтаже клипа
        «Применить бренд» перекрасит субтитры и хук, поставит логотип и плашку-призыв на последние секунды.
      </p>
      {editing === null ? (
        <div>
          <button className="btn primary" onClick={() => setEditing(0)}>Новый бренд</button>
        </div>
      ) : (
        <BrandForm
          key={editing}
          id={editing || null}
          initial={current ? toInput(current) : EMPTY}
          onDone={() => setEditing(null)}
        />
      )}
      {!q.data?.length ? <span className="muted">Брендов нет</span> : null}
      {q.data?.map((b) => (
        <div key={b.id} className="ws-row">
          <span style={{ display: "flex", gap: 3 }}>
            {[b.colors.accent, b.colors.plate, b.colors.text].map((c, i) => (
              <span key={i} style={{ width: 16, height: 16, borderRadius: 4, background: c, border: "1px solid var(--border)" }} />
            ))}
          </span>
          <strong style={{ flex: 1 }}>{b.name}</strong>
          <span className="muted" style={{ fontSize: 12.5 }}>
            {[b.font || "шрифт по умолчанию", b.cta.text || b.cta.url_text ? `призыв ${b.cta.duration_sec} с` : "без призыва",
              b.logo_asset_id ? "логотип" : ""].filter(Boolean).join(" · ")}
          </span>
          <button className="btn ghost sm" onClick={() => setEditing(b.id)}>Изменить</button>
          <button className="btn ghost sm" onClick={() => remove.mutate(b.id)}>🗑</button>
        </div>
      ))}
    </div>
  );
}
