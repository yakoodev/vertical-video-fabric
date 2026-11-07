import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CAMERA_LABEL } from "@/api/aivideo";
import { ApiError } from "@/api/client";
import { navKeys } from "@/api/nav";
import { releaseKeys, type ReleaseScriptDraft } from "@/api/releases";
import { storiesApi, storiesKey } from "@/api/stories";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useToast } from "@/components/Toast";
import { formatDuration, plural } from "@/components/ui";

/**
 * Черновик выпуска: только сценарий и биты. Ничего не идёт и не тратится, пока владелец не нажмёт «Запустить»
 * (app/stories_gen.py, start_draft): дальше обычный конвейер, платное — обычной карточкой.
 */

const EXTRAS: [keyof ReleaseScriptDraft, string][] = [
  ["mistake", "Та самая ошибка"],
  ["cliffhanger", "Деталь финала"],
  ["part_title", "Следующая часть"],
  ["hook_words", "Слова на обложке"],
  ["viewer_question", "Вопрос зрителю"],
];

const secText = (sec: number) => `${sec.toFixed(1).replace(".", ",").replace(",0", "")} с`;

function BeatLine({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <span className="rel-beat-f">
      <b>{label}:</b> {value}
    </span>
  );
}

export function StoryDraftPanel({ draft, projectId, onDeleted }: { draft: ReleaseScriptDraft; projectId: number; onDeleted: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [asking, setAsking] = useState(false);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: releaseKeys.all });
    qc.invalidateQueries({ queryKey: navKeys.all });
    qc.invalidateQueries({ queryKey: storiesKey(projectId) });
  };
  const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
  const start = useMutation({
    mutationFn: () => storiesApi.startEpisode(draft.episode_id),
    onSuccess: () => {
      toast.success("Запущено. Если понадобится платное, сначала придёт карточка с суммой");
      refresh();
    },
    onError: (e) => toast.error(errorText(e, "Не удалось запустить")),
  });
  const remove = useMutation({
    mutationFn: () => storiesApi.deleteEpisode(draft.episode_id),
    onSuccess: () => {
      refresh();
      onDeleted();
    },
    onError: (e) => toast.error(errorText(e, "Не удалось удалить")),
  });
  const facts = [
    draft.est_sec ? `≈ ${formatDuration(Math.round(draft.est_sec))} чтения` : "",
    draft.sentences.length ? plural(draft.sentences.length, "фраза", "фразы", "фраз") : "",
    draft.part ? `часть ${draft.part}` : "",
    draft.tone,
  ].filter(Boolean);
  return (
    <>
      <section className="rel-card rel-draft-start" aria-label="Запуск">
        <div>
          <b>Пока это только сценарий</b>
          <p className="muted">Ничего не запущено и ничего не потрачено. После запуска сервис сам подберёт сцены, запишет голос и соберёт клип. Платное спросит отдельно.</p>
        </div>
        <div className="series-row">
          <button type="button" className="btn primary" disabled={start.isPending} onClick={() => start.mutate()}>
            {start.isPending ? "Запускаю…" : "Запустить"}
          </button>
          <button type="button" className="btn ghost" disabled={remove.isPending} onClick={() => setAsking(true)}>
            Удалить
          </button>
        </div>
      </section>
      <section className="panel series-panel rel-draft">
        <div className="rel-draft-h">
          <h2>Сценарий</h2>
          {facts.length ? <span className="muted">{facts.join(", ")}</span> : null}
        </div>
        {draft.logline ? <p className="rel-draft-log">{draft.logline}</p> : null}
        {draft.premise && draft.premise !== draft.logline ? <p className="muted rel-draft-premise">Завязка: {draft.premise}</p> : null}
        <div className="rel-draft-text">
          {draft.sentences.map((s, n) => (
            <p key={n}>
              {s.role ? <span className="rel-draft-role">{s.role}</span> : null}
              {s.text}
              {s.hud ? <span className={`rel-draft-hud ${s.hud_kind}`}>на экране: {s.hud}</span> : null}
            </p>
          ))}
        </div>
        {EXTRAS.some(([k]) => draft[k]) || draft.hashtags?.length ? (
          <dl className="rel-draft-extra">
            {EXTRAS.filter(([k]) => draft[k]).map(([k, label]) => (
              <div key={k}>
                <dt>{label}</dt>
                <dd>{String(draft[k])}</dd>
              </div>
            ))}
            {draft.hashtags?.length ? (
              <div>
                <dt>Хэштеги</dt>
                <dd>{draft.hashtags.join(" ")}</dd>
              </div>
            ) : null}
          </dl>
        ) : null}
      </section>
      <section className="panel series-panel rel-draft">
        <div className="rel-draft-h">
          <h2>Биты</h2>
          <span className="muted">{plural(draft.beats.length, "бит", "бита", "битов")}, время по длине текста</span>
        </div>
        <ol className="rel-beats">
          {draft.beats.map((b) => (
            <li key={b.i}>
              <span className="rel-beat-n" aria-hidden>{b.i + 1}</span>
              <div className="rel-beat-b">
                <span className="rel-beat-t">{b.text}</span>
                <div className="rel-beat-m">
                  {b.sec ? <span className="rel-beat-f">{secText(b.sec)}</span> : null}
                  <BeatLine label="Где" value={b.place} />
                  <BeatLine label="Кто" value={b.who.join(", ")} />
                  <BeatLine label="На экране" value={b.action} />
                  <BeatLine label="Деталь" value={b.accent} />
                  <BeatLine label="Камера" value={CAMERA_LABEL[b.camera] ?? b.camera} />
                  <BeatLine label="Звук" value={b.sound} />
                </div>
              </div>
            </li>
          ))}
        </ol>
      </section>
      <ConfirmDialog
        open={asking}
        title="Удалить сценарий?"
        body="Текст и биты этого черновика пропадут. Клипов у него нет, денег он не тратил."
        busy={remove.isPending}
        onCancel={() => setAsking(false)}
        onConfirm={() => remove.mutate()}
      />
    </>
  );
}
