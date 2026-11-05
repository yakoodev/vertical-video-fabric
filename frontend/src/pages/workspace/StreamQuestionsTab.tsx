import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ApiError } from "@/api/client";
import {
  streamQuestionsApi as Q,
  streamQuestionsKey,
  type MemeMoment,
  type MemeOption,
  type StreamQuestion,
  type StreamQuestionsState,
} from "@/api/streamQuestions";
import { useToast } from "@/components/Toast";
import { EmptyState, ErrorState, Loading } from "@/components/ui";
import "@/styles/stream-questions.css";

const KIND_LABEL: Record<StreamQuestion["kind"], string> = {
  who: "Кто это",
  context: "Что происходит",
  joke: "Шутка",
  keep: "Брать момент?",
  other: "Вопрос",
};

function timecode(sec: number): string {
  const total = Math.max(0, Math.floor(sec));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0");
  return `${h ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

/** Кадр момента; кусочек видео кодируется и грузится только по нажатию. */
function MomentPreview({ clip, thumb, start, end }: { clip: string; thumb: string; start: number; end: number }) {
  const [playing, setPlaying] = useState(false);
  const [broken, setBroken] = useState(false);
  const label = `${timecode(start)}${end > start ? `–${timecode(end)}` : ""}`;
  if (broken) return <div className="sq-media sq-media-gone">Записи нет. Момент на {label} стрима</div>;
  return (
    <div className="sq-media">
      {playing ? (
        <video src={clip} controls autoPlay playsInline poster={thumb} onError={() => setBroken(true)} />
      ) : (
        <button type="button" className="sq-play" onClick={() => setPlaying(true)} aria-label={`Смотреть момент ${label}`}>
          <img src={thumb} alt="" loading="lazy" onError={() => setBroken(true)} />
          <span className="sq-play-icon" aria-hidden="true" />
        </button>
      )}
      <span className="sq-time mono">{label}</span>
    </div>
  );
}

export function QuestionCard({
  item,
  onSave,
}: {
  item: StreamQuestion;
  onSave: (body: { option: number | null; text: string; skip?: boolean }) => Promise<unknown>;
}) {
  const [text, setText] = useState(item.answer_text);
  const [saved, setSaved] = useState(false);
  const timer = useRef<number | null>(null);
  const latest = useRef(text);
  latest.current = text;

  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);

  const save = (body: { option: number | null; text: string; skip?: boolean }) => {
    onSave(body).then(() => {
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1600);
    }, () => undefined);
  };
  const flush = () => {
    if (timer.current) {
      window.clearTimeout(timer.current);
      timer.current = null;
      save({ option: item.answer_option, text: latest.current });
    }
  };
  const typed = (value: string) => {
    setText(value);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      save({ option: item.answer_option, text: value });
    }, 700);
  };

  return (
    <article className={`sq-card sq-${item.status}`}>
      <MomentPreview clip={Q.questionClip(item.id)} thumb={Q.questionThumb(item.id)} start={item.t_start} end={item.t_end} />
      <div className="sq-body">
        <div className="sq-meta">
          <span className="sq-kind">{KIND_LABEL[item.kind]}</span>
          <span className="sq-scope">{item.scope === "channel" ? "на весь проект" : "только этот стрим"}</span>
          {item.about ? <span className="sq-scope">{item.about}</span> : null}
          {item.status === "skipped" ? <span className="sq-scope">пропущено</span> : null}
          {saved ? <span className="sq-saved" role="status">Сохранено</span> : null}
        </div>
        <h3>{item.question}</h3>
        <div className="sq-options" role="group" aria-label="Варианты ответа">
          {item.options.map((option, index) => {
            const active = item.answer_option === index;
            return (
              <button
                key={option}
                type="button"
                className={`sq-option${active ? " active" : ""}`}
                aria-pressed={active}
                onClick={() => save({ option: active ? null : index, text })}
              >
                {option}
              </button>
            );
          })}
        </div>
        <div className="sq-own">
          <input
            value={text}
            onChange={(event) => typed(event.target.value)}
            onBlur={flush}
            maxLength={1000}
            placeholder="Свой ответ или пояснение"
            aria-label="Свой ответ"
          />
          {item.status === "skipped" ? (
            <button type="button" className="btn ghost sm" onClick={() => save({ option: null, text: "" })}>
              Вернуть
            </button>
          ) : (
            <button
              type="button"
              className="btn ghost sm"
              onClick={() => {
                if (timer.current) window.clearTimeout(timer.current);
                timer.current = null;
                setText("");
                save({ option: null, text: "", skip: true });
              }}
            >
              Пропустить
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

function OptionPicture({ option }: { option: MemeOption }) {
  if (option.mime.startsWith("video/")) {
    return <video src={Q.optionPreview(option.id)} muted loop playsInline autoPlay />;
  }
  return <img src={Q.optionPreview(option.id)} alt={option.title} loading="lazy" />;
}

export function MemeCard({
  moment,
  onChoose,
}: {
  moment: MemeMoment;
  onChoose: (body: { option_id?: number | null; none?: boolean }) => void;
}) {
  return (
    <article className={`sq-card sq-${moment.status === "open" ? "open" : "answered"}`}>
      <MomentPreview clip={Q.momentClip(moment.id)} thumb={Q.momentThumb(moment.id)} start={moment.t_start} end={moment.t_end} />
      <div className="sq-body">
        <h3>{moment.why}</h3>
        <div className="sq-memes" role="group" aria-label="Варианты мема">
          {moment.options.map((option) => {
            const active = moment.chosen_option_id === option.id;
            return (
              <button
                key={option.id}
                type="button"
                className={`sq-meme${active ? " active" : ""}`}
                aria-pressed={active}
                onClick={() => onChoose({ option_id: active ? null : option.id })}
              >
                <OptionPicture option={option} />
                <span className="sq-meme-title">{option.title}</span>
                {option.note ? <span className="sq-meme-note">{option.note}</span> : null}
                <span className="sq-meme-origin">
                  {option.source}
                  {option.license ? `: ${option.license}` : ""}
                </span>
              </button>
            );
          })}
        </div>
        <button
          type="button"
          className={`sq-option sq-none${moment.status === "none" ? " active" : ""}`}
          aria-pressed={moment.status === "none"}
          onClick={() => onChoose(moment.status === "none" ? { option_id: null } : { none: true })}
        >
          Без мема
        </button>
      </div>
    </article>
  );
}

function recount(state: StreamQuestionsState): StreamQuestionsState {
  const count = <T extends { status: string }>(rows: T[], status: string) => rows.filter((r) => r.status === status).length;
  return {
    ...state,
    progress: {
      questions: {
        total: state.questions.length,
        answered: count(state.questions, "answered"),
        skipped: count(state.questions, "skipped"),
        open: count(state.questions, "open"),
      },
      memes: {
        total: state.moments.length,
        chosen: count(state.moments, "chosen"),
        none: count(state.moments, "none"),
        open: count(state.moments, "open"),
      },
    },
  };
}

export function StreamQuestionsTab({ sourceId }: { sourceId: string }) {
  const toast = useToast();
  const client = useQueryClient();
  const key = useMemo(() => streamQuestionsKey(sourceId), [sourceId]);
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "memes" ? "memes" : "questions";
  const [onlyOpen, setOnlyOpen] = useState(false);
  const query = useQuery({ queryKey: key, queryFn: () => Q.state(sourceId) });

  const problem = (error: unknown, fallback: string) =>
    toast.error(error instanceof ApiError ? error.message : fallback);

  const answer = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { option: number | null; text: string; skip?: boolean } }) =>
      Q.answer(id, body),
    onSuccess: (fresh) => {
      client.setQueryData<StreamQuestionsState>(key, (old) =>
        old ? recount({ ...old, questions: old.questions.map((q) => (q.id === fresh.id ? fresh : q)) }) : old);
      if (fresh.scope === "channel" && fresh.status === "answered" && fresh.brief && !fresh.brief.written) {
        toast.push(fresh.brief.reason ?? "В бриф канала ответ не записан", "info");
      }
    },
    onError: (error) => problem(error, "Ответ не сохранился"),
  });
  const choose = useMutation({
    mutationFn: ({ id, body }: { id: number; body: { option_id?: number | null; none?: boolean } }) => Q.choose(id, body),
    onSuccess: (fresh) => {
      client.setQueryData<StreamQuestionsState>(key, (old) =>
        old ? recount({ ...old, moments: old.moments.map((m) => (m.id === fresh.id ? fresh : m)) }) : old);
    },
    onError: (error) => problem(error, "Выбор не сохранился"),
  });

  if (query.isLoading) return <Loading />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const state = query.data!;
  const { questions: qp, memes: mp } = state.progress;
  const doneQuestions = qp.answered + qp.skipped;
  const doneMemes = mp.chosen + mp.none;
  const done = tab === "questions" ? doneQuestions : doneMemes;
  const total = tab === "questions" ? qp.total : mp.total;
  const questions = onlyOpen ? state.questions.filter((q) => q.status === "open") : state.questions;
  const moments = onlyOpen ? state.moments.filter((m) => m.status === "open") : state.moments;

  return (
    <div className="sq-page">
      <div className="sq-head">
        <div className="sq-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "questions"}
            className={tab === "questions" ? "active" : ""}
            onClick={() => setParams({}, { replace: true })}
          >
            Вопросы <span className="mono">{doneQuestions}/{qp.total}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "memes"}
            className={tab === "memes" ? "active" : ""}
            onClick={() => setParams({ tab: "memes" }, { replace: true })}
          >
            Мемы <span className="mono">{doneMemes}/{mp.total}</span>
          </button>
        </div>
        <label className="sq-filter">
          <input type="checkbox" checked={onlyOpen} onChange={(event) => setOnlyOpen(event.target.checked)} />
          Только без ответа
        </label>
        {total ? (
          <div className="sq-bar" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
            <span style={{ width: `${Math.round((done / total) * 100)}%` }} />
          </div>
        ) : null}
        {tab === "questions" && !state.project_id && state.questions.some((q) => q.scope === "channel") ? (
          <p className="sq-note">Видео не в проекте: ответы про канал останутся только у этого стрима.</p>
        ) : null}
      </div>

      {tab === "questions" ? (
        questions.length ? (
          <div className="sq-list">
            {questions.map((item) => (
              <QuestionCard key={item.id} item={item} onSave={(body) => answer.mutateAsync({ id: item.id, body })} />
            ))}
          </div>
        ) : (
          <EmptyState
            icon="💬"
            title={qp.total ? "На всё ответили" : "Вопросов пока нет"}
            hint={qp.total ? "Монтажёр прочитает ответы перед отбором моментов." : "Их задаёт монтажёр-агент, когда посмотрит стрим."}
          />
        )
      ) : moments.length ? (
        <div className="sq-list">
          {moments.map((moment) => (
            <MemeCard key={moment.id} moment={moment} onChoose={(body) => choose.mutate({ id: moment.id, body })} />
          ))}
        </div>
      ) : (
        <EmptyState
          icon="🖼"
          title={mp.total ? "Всё выбрано" : "Мемов пока нет"}
          hint={mp.total ? "Выбранные мемы уже в библиотеке файлов." : "Варианты предлагает монтажёр-агент к конкретным моментам."}
        />
      )}
    </div>
  );
}
