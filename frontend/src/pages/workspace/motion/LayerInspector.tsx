/**
 * Инспектор выделенного слоя: содержимое, стиль, положение с ключевыми кадрами,
 * время и анимации. Каждая правка отдаёт новый слой целиком — компонент
 * редактора кладёт его в список слоёв, а автосохранение и Ctrl+Z работают как
 * для остальных настроек клипа.
 */
import { useEffect, useState } from "react";
import type { CaptionWord, Layer } from "@/motion";
import type { MotionFont } from "@/api/motion";
import { LAYER_ICON, layerTitle } from "@/pages/workspace/motion/edit";
import type { PickedPoint } from "@/pages/workspace/motion/TrackPanel";
import { SceneInspector } from "@/pages/workspace/motion/SceneInspector";
import { AudioSection } from "./inspector/AudioSection";
import { CaptionsSection } from "./inspector/CaptionsSection";
import { CalloutSection, ChartSection, NameplateSection, SocialSection } from "./inspector/DataSections";
import { FxSection, ParticlesSection } from "./inspector/EffectSections";
import { Section } from "./inspector/fields";
import { MediaSection, ProgressSection, ShapeSection } from "./inspector/MediaSections";
import { PositionSection } from "./inspector/PositionSection";
import { CounterSection, EmojiSection, TextSection } from "./inspector/TextSections";
import { AnimationSection, ShadowSection, TimeSection } from "./inspector/TimingSections";

type Props = {
  layer: Layer;
  /** Слова клипа (для субтитров-слоя: подсветка слов и эмодзи над словами). */
  words?: CaptionWord[];
  fonts: MotionFont[];
  /** Текущее время клипа (для ◆ ключей), читается периодически. */
  now: () => number;
  clipDuration: number;
  onChange: (next: Layer) => void;
  onDelete: () => void;
  onDuplicate: () => void;
  onSeek: (clipSec: number) => void;
  /** У источника хромакей: слой можно положить «за персонажа» (`behind`). */
  keyed?: boolean;
  /** У клипа есть наезды камеры: слою есть смысл «ехать с камерой» (`followCamera`). */
  hasCamera?: boolean;
  /** Ширина опорного кадра формата разметки слоёв (px): 1080, у 16:9 — 1920 (предел «Ширины»). */
  refWidth?: number;
  /** Клип (для трекинга «Следовать за…»); нет — кнопка неактивна. */
  planId?: number;
  /** Взвести выбор точки кликом по превью (трекинг ручной точки / нужного лица). */
  onPickPoint?: (cb: ((p: PickedPoint) => void) | null) => void;
};

export function LayerInspector({ layer, words = [], fonts, now, clipDuration, onChange, onDelete, onDuplicate, onSeek, keyed = false, hasCamera = false, refWidth = 1080, planId, onPickPoint }: Props) {
  // Время перечитываем несколько раз в секунду: от него зависят ◆ и значения
  // анимированных свойств, а весь редактор ради этого перерисовывать не нужно.
  const [t, setT] = useState(now());
  useEffect(() => {
    const id = window.setInterval(() => setT(now()), 200);
    return () => window.clearInterval(id);
  }, [now]);
  const patchProps = (patch: Record<string, unknown>) => onChange({ ...layer, props: { ...layer.props, ...patch } } as Layer);

  return (
    <div className="li">
      <div className="li-head">
        <span className="li-ico">{LAYER_ICON[layer.type]}</span>
        <input
          className="input li-name"
          value={layer.name ?? ""}
          placeholder={layerTitle(layer)}
          onChange={(e) => onChange({ ...layer, name: e.target.value || undefined })}
        />
        <button type="button" className="btn ghost sm" title={layer.hidden ? "Показать" : "Скрыть"} onClick={() => onChange({ ...layer, hidden: !layer.hidden })}>
          {layer.hidden ? "🙈" : "👁"}
        </button>
        <button type="button" className="btn ghost sm" title={layer.locked ? "Разблокировать" : "Заблокировать на кадре"} onClick={() => onChange({ ...layer, locked: !layer.locked })}>
          {layer.locked ? "🔒" : "🔓"}
        </button>
        <button type="button" className="btn ghost sm" title="Дублировать (Ctrl+D)" onClick={onDuplicate}>
          ⧉
        </button>
        <button type="button" className="btn ghost sm" title="Удалить (Del)" onClick={onDelete}>
          🗑
        </button>
      </div>

      {layer.type === "text" ? <TextSection layer={layer} fonts={fonts} refWidth={refWidth} patchProps={patchProps} /> : null}

      {layer.type === "fx" ? <FxSection layer={layer} patchProps={patchProps} /> : null}

      {layer.type === "particles" ? <ParticlesSection layer={layer} patchProps={patchProps} /> : null}

      {layer.type === "captions" ? <CaptionsSection layer={layer} words={words} onChange={onChange} onSeek={onSeek} patchProps={patchProps} /> : null}

      {layer.type === "counter" ? <CounterSection layer={layer} fonts={fonts} refWidth={refWidth} patchProps={patchProps} /> : null}

      {layer.type === "emoji" ? <EmojiSection layer={layer} patchProps={patchProps} /> : null}

      {layer.type === "image" || layer.type === "video" ? <MediaSection layer={layer} patchProps={patchProps} /> : null}

      {layer.type === "shape" ? <ShapeSection layer={layer} patchProps={patchProps} /> : null}

      {layer.type === "progress" ? <ProgressSection layer={layer} refWidth={refWidth} patchProps={patchProps} /> : null}

      {layer.type === "scene" ? (
        <Section title="3D-сцена">
          <SceneInspector layer={layer} onChange={onChange} />
        </Section>
      ) : null}

      {layer.type === "chart" ? <ChartSection layer={layer} patchProps={patchProps} /> : null}

      {layer.type === "callout" ? <CalloutSection layer={layer} refWidth={refWidth} patchProps={patchProps} /> : null}

      {layer.type === "nameplate" ? <NameplateSection layer={layer} patchProps={patchProps} /> : null}

      {layer.type === "social" ? <SocialSection layer={layer} refWidth={refWidth} patchProps={patchProps} /> : null}

      {layer.type === "audio" ? <AudioSection layer={layer} patchProps={patchProps} /> : null}

      {layer.type !== "audio" ? (
        <PositionSection layer={layer} t={t} now={now} keyed={keyed} hasCamera={hasCamera} planId={planId} onChange={onChange} onPickPoint={onPickPoint} />
      ) : null}

      <TimeSection layer={layer} t={t} clipDuration={clipDuration} onChange={onChange} onSeek={onSeek} />

      {layer.type !== "audio" ? <AnimationSection layer={layer} onChange={onChange} /> : null}

      {layer.type !== "audio" && layer.type !== "fx" ? <ShadowSection layer={layer} onChange={onChange} /> : null}
    </div>
  );
}
