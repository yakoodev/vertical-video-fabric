import { useEffect, useState } from "react";
import { ApiError } from "@/api/client";
import type { CoverApi, CoverCandidate, MovieRequest, MovieResult } from "@/api/coverDocs";

/* Панели фоторедактора: сильные кадры ролика и превью «как в ролике» (app/cover_movie.py). */

export function CandidateStrip({ api, picked, onPick }: { api: CoverApi; picked: number | null; onPick: (t: number) => void }) {
  const [items, setItems] = useState<CoverCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = (refresh = false) => {
    setBusy(true);
    setError(null);
    api.candidates(refresh)
      .then((r) => setItems(r.items))
      .catch((err) => setError(err instanceof ApiError ? err.message : "Кадры не получились"))
      .finally(() => setBusy(false));
  };
  useEffect(() => load(), [api]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="cov-cands">
      <div className="cov-cands-head">
        <strong>Сильные кадры</strong>
        <button type="button" className="btn sm ghost" disabled={busy} onClick={() => load(true)}>{busy ? "Ищу…" : "Заново"}</button>
      </div>
      {error ? <p className="cov-warn">{error}</p> : null}
      {!items && !error ? <p className="muted">Смотрю ролик: резкость, герой в кадре, без размытых переходов…</p> : null}
      {items?.length ? (
        <div className="cov-cands-strip" role="listbox" aria-label="Сильные кадры ролика">
          {items.map((c) => (
            <button
              key={c.file}
              type="button"
              role="option"
              aria-selected={picked !== null && Math.abs(picked - c.t) < 0.05}
              className={picked !== null && Math.abs(picked - c.t) < 0.05 ? "on" : ""}
              onClick={() => onPick(c.t)}
              title={`${c.t.toFixed(1)} с`}
            >
              <img src={api.candidateUrl(c.file)} alt={`Кадр на ${c.t.toFixed(1)} с`} loading="lazy" />
              <span>{c.t.toFixed(1)} с</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

type FrameChoice = "best" | "picked" | "layer";

export function MoviePanel({ api, pickedT, layerSrc, hasVideo, busy, onBuilt, setBusy, onError, initial }: {
  api: CoverApi;
  initial?: Partial<MovieRequest>;
  pickedT: number | null;
  layerSrc: string | null;
  hasVideo: boolean;
  busy: boolean;
  onBuilt: (out: MovieResult) => void;
  setBusy: (on: boolean) => void;
  onError: (message: string) => void;
}) {
  const [word, setWord] = useState(initial?.big_word ?? "");
  const [top, setTop] = useState(initial?.top_line ?? "");
  const [sub, setSub] = useState(initial?.subline ?? "");
  const [style, setStyle] = useState<NonNullable<MovieRequest["style"]>>(initial?.style ?? "movie");
  const [side, setSide] = useState<"left" | "right">(initial?.side ?? "left");
  const [logo, setLogo] = useState(true);
  const [hud, setHud] = useState(true);
  const [frame, setFrame] = useState<FrameChoice>(layerSrc && !hasVideo ? "layer" : "best");
  const usable = (c: FrameChoice) => (c === "best" ? hasVideo : c === "picked" ? hasVideo && pickedT !== null : !!layerSrc);
  const build = async () => {
    setBusy(true);
    try {
      const body: MovieRequest = { big_word: word, top_line: top, subline: sub, style, side, logo, hud: hud ? "auto" : "off" };
      if (frame === "picked" && pickedT !== null) body.t = pickedT;
      if (frame === "layer" && layerSrc) body.frame = layerSrc;
      onBuilt(await api.movie(body));
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Превью не собралось");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="cov-movie" onSubmit={(e) => { e.preventDefault(); void build(); }}>
      <strong>Как в ролике</strong>
      <p className="muted">Кадр без интерфейса, ЧБ с огнём, надписи в стиле самого видео. Слоганы не нужны — слово из ролика.</p>
      <label className="field"><span>Строка сверху</span><input className="input" value={top} maxLength={60} placeholder="APEX LEGENDS" onChange={(e) => setTop(e.target.value)} /></label>
      <label className="field"><span>Главное слово</span><input className="input" value={word} maxLength={40} placeholder="BURN" onChange={(e) => setWord(e.target.value)} /></label>
      <label className="field"><span>Строка снизу</span><input className="input" value={sub} maxLength={80} placeholder="YOUR LOCAL POLITICIAN" onChange={(e) => setSub(e.target.value)} /></label>
      <div className="cov-seg" role="group" aria-label="Вид кадра">
        {([["movie", "ЧБ с акцентом"], ["color", "Цвет"], ["clean", "Без текста"]] as const).map(([id, name]) => (
          <button key={id} type="button" className={style === id ? "on" : ""} aria-pressed={style === id} onClick={() => setStyle(id)}>{name}</button>
        ))}
      </div>
      <div className="cov-seg" role="group" aria-label="Сторона текста">
        {([["left", "Текст слева"], ["right", "Текст справа"]] as const).map(([id, name]) => (
          <button key={id} type="button" className={side === id ? "on" : ""} aria-pressed={side === id} onClick={() => setSide(id)}>{name}</button>
        ))}
      </div>
      <label className="field">
        <span>Кадр</span>
        <select className="input" value={frame} onChange={(e) => setFrame(e.target.value as FrameChoice)}>
          <option value="best" disabled={!usable("best")}>Сильный кадр ролика</option>
          <option value="picked" disabled={!usable("picked")}>{pickedT !== null ? `Выбранный: ${pickedT.toFixed(1)} с` : "Выбранный во вкладке «Кадр»"}</option>
          <option value="layer" disabled={!usable("layer")}>Картинка выбранного слоя</option>
        </select>
      </label>
      <label className="cov-check"><input type="checkbox" checked={hud} onChange={(e) => setHud(e.target.checked)} /><span>Убрать интерфейс игры</span></label>
      <label className="cov-check"><input type="checkbox" checked={logo} onChange={(e) => setLogo(e.target.checked)} /><span>Логотип бренда проекта</span></label>
      <button type="submit" className="btn primary" disabled={busy || !usable(frame) || (style !== "clean" && !word.trim())}>
        {busy ? "Собираю…" : "Собрать превью"}
      </button>
      <p className="muted">Заменит все слои. «Отменить» вернёт прежние.</p>
    </form>
  );
}
