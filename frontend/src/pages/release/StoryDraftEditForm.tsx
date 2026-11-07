import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { type ReleaseScriptDraft } from "@/api/releases";
import { storiesApi } from "@/api/stories";
import { useToast } from "@/components/Toast";

const TITLE_MAX = 60;

/** Слова подряд: так сервис решает, подходят ли биты автора к тексту (app/stories_gen_script.py, _word_list). */
const wordsOf = (text: string) => text.toLowerCase().replace(/ё/g, "е").match(/[a-zа-я0-9]+/g)?.join(" ") ?? "";

/**
 * Правка черновика: название и текст по фразе на строку. Черновик остаётся черновиком, ничего не запускается и не тратится.
 * Биты автора привязаны к словам: другие слова их заменяют, и форма говорит об этом до сохранения (app/stories_gen_script.py, edit_draft).
 */
export function StoryDraftEditForm({ draft, onDone }: { draft: ReleaseScriptDraft; onDone: (saved: boolean) => void }) {
  const toast = useToast();
  const startText = draft.sentences.map((s) => s.text).join("\n");
  const [title, setTitle] = useState(draft.title);
  const [text, setText] = useState(startText);
  const beatsGo = draft.author_beats && wordsOf(text) !== wordsOf(draft.beats.map((b) => b.text).join(" "));
  const ready = title.trim() !== "" && text.trim() !== "";
  const changed = title.trim() !== draft.title || text.trim() !== startText;
  const save = useMutation({
    mutationFn: () => storiesApi.editDraft(draft.episode_id, { title, text, replace_beats: beatsGo }),
    onSuccess: () => {
      toast.success(beatsGo ? "Сохранено, биты разбиты заново. Сценарий не запущен" : "Сохранено. Сценарий не запущен");
      onDone(true);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить"),
  });
  return (
    <form
      className="rel-draft-edit"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready && changed) save.mutate();
      }}
    >
      <label className="field">
        <span>Название, которое видят зрители</span>
        <input className="input" value={title} maxLength={Math.max(TITLE_MAX, draft.title.length)} onChange={(e) => setTitle(e.target.value)} />
        {draft.title_on_frame ? <small className="muted">Оно появится на первом кадре видео.</small> : null}
      </label>
      <label className="field">
        <span>Текст рассказчика</span>
        <textarea className="input" rows={Math.min(14, draft.sentences.length + 3)} value={text} onChange={(e) => setText(e.target.value)} />
        <small className="muted">Каждая фраза с новой строки. Сцены и надписи на экране останутся у своих фраз.</small>
      </label>
      {beatsGo ? (
        <p className="rel-draft-warn" role="status">
          Слова изменились, и биты ниже ({draft.beats.length}) больше не подойдут. При сохранении они пропадут вместе со всем, что в них записано: где, кто, что на экране, камера, звук. Текст разобьётся на биты заново, по фразам.
        </p>
      ) : null}
      <div className="series-row">
        <button type="submit" className="btn primary" disabled={!ready || !changed || save.isPending}>
          {save.isPending ? "Сохраняю…" : beatsGo ? "Сохранить и заменить биты" : "Сохранить"}
        </button>
        <button type="button" className="btn ghost" disabled={save.isPending} onClick={() => onDone(false)}>
          Отмена
        </button>
      </div>
    </form>
  );
}
