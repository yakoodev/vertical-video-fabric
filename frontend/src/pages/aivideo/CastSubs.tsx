// ИИ-видео: персонажи и локации.
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { aivApi, type AivCharacter, type AivLocation, type Overview } from "@/api/aivideo";
import { useToast } from "@/components/Toast";
import { TtsVoicePicker, type TtsVoiceValue } from "@/components/TtsVoicePicker";
import { EmptyState } from "@/components/ui";
import { errText, useInvalidate } from "./shared";
import { StageButton, Versions } from "./StageControls";

// --------------------------------------------------------------------------- //
// Персонажи
// --------------------------------------------------------------------------- //

export function CharactersSub({ o }: { o: Overview }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const [form, setForm] = useState({ name: "", identity_card: "", origin: "invented" });
  const add = useMutation({
    mutationFn: () => aivApi.addCharacter(o.project.id, form as never),
    onSuccess: () => {
      setForm({ name: "", identity_card: "", origin: "invented" });
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не добавлено")),
  });
  return (
    <div className="series-col">
      <section className="panel series-panel">
        <div className="series-panel-head">
          <h3>Новый персонаж</h3>
          <StageButton projectId={o.project.id} stage="refs" label="Листы и референсы: оценить" />
        </div>
        <div className="series-row">
          <input className="input" placeholder="Имя" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <select className="input" value={form.origin} onChange={(e) => setForm({ ...form, origin: e.target.value })}>
            <option value="invented">придуманный</option>
            <option value="owner">мой витубер/стример (свои картинки)</option>
          </select>
          <button className="btn sm" disabled={!form.name || !form.identity_card || add.isPending} onClick={() => add.mutate()}>
            ＋ Добавить
          </button>
        </div>
        <textarea
          className="input"
          rows={2}
          placeholder="Карта личности: возраст, волосы, глаза, рост и пропорции, 2–3 цвета hex, отличительная деталь"
          value={form.identity_card}
          onChange={(e) => setForm({ ...form, identity_card: e.target.value })}
        />
      </section>
      {o.characters.length ? (
        o.characters.map((c) => <CharacterCard key={c.id} c={c} projectId={o.project.id} />)
      ) : (
        <EmptyState title="Персонажей нет" hint="Добавьте или дайте сценаристу завести их." />
      )}
    </div>
  );
}

function CharacterCard({ c, projectId }: { c: AivCharacter; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const [card, setCard] = useState(c.identity_card);
  const [physics, setPhysics] = useState({ constraints: c.constraints ?? "", stand_in: c.stand_in ?? "" });
  const [voice, setVoice] = useState<TtsVoiceValue & { sfx?: string }>({
    ...c.voice,
    model: c.voice.model ?? "",
    voice: c.voice.voice ?? "",
    style: c.voice.style ?? "",
  });
  const [speech, setSpeech] = useState({ speech: c.speech ?? "voice", speech_captions: c.speech_captions ?? true });
  const [look, setLook] = useState({ name: "", outfit_text: "", beat: "" });
  const save = useMutation({
    mutationFn: () => aivApi.editCharacter(c.id, { identity_card: card, voice, ...speech, ...physics } as never),
    onSuccess: () => {
      toast.success("Сохранено (зависимые кадры — «устарело»)");
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не сохранено")),
  });
  const addLook = useMutation({
    mutationFn: () => aivApi.addLook(c.id, look),
    onSuccess: () => {
      setLook({ name: "", outfit_text: "", beat: "" });
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не добавлено")),
  });
  const upload = useMutation({
    mutationFn: (f: File) => aivApi.uploadCharacterRef(c.id, f),
    onSuccess: () => invalidate(),
    onError: (e) => toast.error(errText(e, "Не загружено")),
  });
  const items = [`character:${c.id}`, ...c.looks.map((l) => `look:${l.id}`)];
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>
          {c.name} <span className="muted mono">{c.slug}</span> {c.origin === "owner" ? <span className="chip">свой</span> : null}
        </h3>
        <div className="series-row">
          <StageButton projectId={projectId} stage="refs" items={items} label="Листы персонажа: оценить" />
          {/* «Сохранить» — в шапке карточки, как у плана: сохраняет всё (карта, физика, речь, голос). */}
          <button className="btn sm" disabled={save.isPending} onClick={() => save.mutate()}>
            Сохранить
          </button>
        </div>
      </div>
      <textarea className="input" rows={2} value={card} onChange={(e) => setCard(e.target.value)} />
      <div className="series-row">
        <input
          className="input wide"
          placeholder="физика: «плоский диск на полу, без рук и ног, ровно один» — дословно в кадры, видео и проверку"
          value={physics.constraints}
          onChange={(e) => setPhysics({ ...physics, constraints: e.target.value })}
        />
        <select className="input" value={physics.stand_in} title="Манекен в блокинге" onChange={(e) => setPhysics({ ...physics, stand_in: e.target.value })}>
          <option value="">манекен: по карте</option>
          <option value="human">человек (капсула)</option>
          <option value="disc">плоский диск (пылесос)</option>
          <option value="quadruped">зверь на четырёх лапах</option>
          <option value="small">маленький (шар)</option>
        </select>
      </div>
      <div className="series-row">
        <select
          className="input"
          value={speech.speech}
          title="Как персонаж «говорит» в сценарии и озвучке"
          onChange={(e) => setSpeech({ ...speech, speech: e.target.value as AivCharacter["speech"] })}
        >
          <option value="voice">говорит голосом</option>
          <option value="sfx">не говорит — звуки (писк по эмоции)</option>
          <option value="none">молчит</option>
        </select>
        {speech.speech === "sfx" ? (
          <label className="aiv-check" title="Показывать текст его реплик («Бип-бип?..») субтитрами">
            <input type="checkbox" checked={speech.speech_captions} onChange={(e) => setSpeech({ ...speech, speech_captions: e.target.checked })} />{" "}
            подписи к звукам
          </label>
        ) : null}
      </div>
      {speech.speech === "voice" ? (
        <div className="series-row">
          <TtsVoicePicker value={voice} emptyLabel="TTS: Polza (по умолчанию, платно)" showModel onChange={(next) => setVoice({ ...voice, ...next })} />
        </div>
      ) : null}
      <div className="series-section-head">
        <b>Листы и референсы</b>
        <label className="btn ghost sm">
          ⬆ Своя картинка
          <input type="file" accept="image/*" hidden onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
        </label>
      </div>
      <Versions refs={c.refs} projectId={projectId} />
      <div className="series-section">
        <b>Облики по сюжету</b>
        {c.looks.map((l) => (
          <div key={l.id} className="aiv-look">
            <span>
              <b>{l.name}</b> <span className="muted">{l.slug}</span> — {l.outfit_text} {l.beat ? <i className="muted">({l.beat})</i> : null}
            </span>
            <Versions refs={l.refs} projectId={projectId} />
          </div>
        ))}
        <div className="series-row">
          <input className="input" placeholder="Облик" value={look.name} onChange={(e) => setLook({ ...look, name: e.target.value })} />
          <input className="input wide" placeholder="одежда/состояние" value={look.outfit_text} onChange={(e) => setLook({ ...look, outfit_text: e.target.value })} />
          <input className="input" placeholder="где в сюжете" value={look.beat} onChange={(e) => setLook({ ...look, beat: e.target.value })} />
          <button className="btn sm" disabled={!look.name || !look.outfit_text || addLook.isPending} onClick={() => addLook.mutate()}>
            ＋ Облик
          </button>
        </div>
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Локации
// --------------------------------------------------------------------------- //

export function LocationsSub({ o }: { o: Overview }) {
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const [form, setForm] = useState({ name: "", description: "" });
  const add = useMutation({
    mutationFn: () => aivApi.addLocation(o.project.id, form),
    onSuccess: () => {
      setForm({ name: "", description: "" });
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не добавлено")),
  });
  return (
    <div className="series-col">
      <section className="panel series-panel">
        <div className="series-row">
          <input className="input" placeholder="Локация" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input className="input wide" placeholder="описание: что, время суток, свет" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <button className="btn sm" disabled={!form.name || add.isPending} onClick={() => add.mutate()}>
            ＋ Добавить
          </button>
          <StageButton projectId={o.project.id} stage="refs" items={o.locations.map((l) => `location:${l.id}`)} label="Плиты локаций: оценить" />
        </div>
      </section>
      {o.locations.map((l) => (
        <LocationCard key={l.id} l={l} projectId={o.project.id} />
      ))}
    </div>
  );
}

function LocationCard({ l, projectId }: { l: AivLocation; projectId: number }) {
  const toast = useToast();
  const invalidate = useInvalidate(projectId);
  const [desc, setDesc] = useState(l.description);
  const save = useMutation({
    mutationFn: () => aivApi.editLocation(l.id, { description: desc }),
    onSuccess: () => invalidate(),
    onError: (e) => toast.error(errText(e, "Не сохранено")),
  });
  return (
    <section className="panel series-panel">
      <div className="series-panel-head">
        <h3>
          {l.name} <span className="muted mono">{l.slug}</span>
        </h3>
        <button className="btn sm" disabled={desc === l.description || save.isPending} onClick={() => save.mutate()}>
          Сохранить
        </button>
      </div>
      <textarea className="input" rows={2} value={desc} onChange={(e) => setDesc(e.target.value)} />
      <Versions refs={l.refs} projectId={projectId} />
    </section>
  );
}
