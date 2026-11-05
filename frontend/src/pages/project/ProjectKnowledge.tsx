import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { projectsApi, type KnowledgeGroup, type KnowledgeRow, type KnowledgeView } from "@/api/projects";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Icon } from "@/components/Icon";
import { useToast } from "@/components/Toast";
import { ErrorState, Loading, plural } from "@/components/ui";
import { projectPath } from "@/lib/paths";

/**
 * «Что агент знает о проекте» (app/project_knowledge.py): короткая карточка рядом со списком видео и
 * полный список на вкладке «Лор и правила», где строку можно поправить и убрать. Других проектов тут нет.
 */

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

const GROUP_HINT: Record<string, string> = {
  videos: "Это агент видит, только когда работает с этим видео.",
  memes: "Выбор меняется на вкладке «Вопросы».",
};

function dateShort(value?: string): string {
  if (!value) return "";
  const d = new Date(value.includes("T") ? value : `${value.replace(" ", "T")}Z`);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}

/** В брифе у строки есть своя подпись: имя человека, шутка, сюжет, тема. Её тоже можно поправить. */
const titleEditable = (row: KnowledgeRow) => /^brief:(people|arcs|jokes|taboo):/.test(row.ref);

function removeBody(row: KnowledgeRow) {
  if (row.ref.startsWith("question:")) return "Агент больше не получит этот ответ. Вопрос останется пропущенным, ответить заново можно на вкладке «Вопросы».";
  if (row.ref.startsWith("meme:")) return "Агент больше не получит этот выбор, у момента будет «без мема». Выбрать заново можно на вкладке «Вопросы».";
  if (row.ref.startsWith("rule:")) return "Агент больше не будет следовать этому правилу. Оно останется в истории на вкладке «Правила».";
  if (row.origin.startsWith("ваш ответ")) return "Агент больше не получит этот ответ. Вопрос останется пропущенным.";
  return "Агент больше не получит это ни в чатах, ни при нарезке.";
}

function RowOrigin({ row }: { row: KnowledgeRow }) {
  const date = dateShort(row.at);
  const text = [row.origin, date].filter(Boolean).join(", ");
  return (
    <div className="kitem-o" title={row.quote ? `Ваши слова: ${row.quote}` : undefined}>
      {row.href ? <Link to={row.href}>{text}</Link> : text}
      {row.note ? <span> · {row.note}</span> : null}
    </div>
  );
}

function RowText({ row }: { row: KnowledgeRow }) {
  return (
    <div className="kitem-x">
      {row.title ? <b>{row.title}{row.text ? ": " : ""}</b> : null}
      {row.text}
    </div>
  );
}

function Row({ row, projectId, onChanged }: { row: KnowledgeRow; projectId: number; onChanged: (v: KnowledgeView) => void }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(row.text);
  const [title, setTitle] = useState(row.title ?? "");
  const [removing, setRemoving] = useState(false);
  const save = useMutation({
    mutationFn: () => projectsApi.updateKnowledge(projectId, row.ref, text, titleEditable(row) ? title : undefined),
    onSuccess: (view) => {
      setEditing(false);
      onChanged(view);
    },
    onError: (e) => toast.error(errText(e, "Не сохранилось")),
  });
  const remove = useMutation({
    mutationFn: () => projectsApi.deleteKnowledge(projectId, row.ref),
    onSuccess: (view) => {
      setRemoving(false);
      onChanged(view);
      toast.success("Убрано, агент этого больше не получит");
    },
    onError: (e) => toast.error(errText(e, "Не убралось")),
  });
  return (
    <div className="kitem">
      {editing ? (
        <div className="kitem-edit">
          {titleEditable(row) ? <input className="input" aria-label="Подпись" value={title} onChange={(e) => setTitle(e.target.value)} /> : null}
          {row.ref.startsWith("question:") ? <div className="muted kitem-o">{row.title}</div> : null}
          <textarea
            className="input"
            rows={Math.min(6, Math.max(2, Math.ceil(text.length / 70)))}
            autoFocus
            aria-label="Текст"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setEditing(false);
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && text.trim()) save.mutate();
            }}
          />
        </div>
      ) : (
        <div className="kitem-main">
          <RowText row={row} />
          <RowOrigin row={row} />
        </div>
      )}
      <div className="kitem-a">
        {editing ? (
          <>
            <button className="btn primary sm" disabled={!text.trim() || save.isPending} onClick={() => save.mutate()}>
              Сохранить
            </button>
            <button className="btn ghost sm" onClick={() => setEditing(false)}>
              Отмена
            </button>
          </>
        ) : (
          <>
            {row.editable ? (
              <button
                className="btn sm"
                onClick={() => {
                  setText(row.text);
                  setTitle(row.title ?? "");
                  setEditing(true);
                }}
              >
                Поправить
              </button>
            ) : null}
            {row.removable ? (
              <button className="btn sm" onClick={() => setRemoving(true)}>
                Убрать
              </button>
            ) : null}
          </>
        )}
      </div>
      <ConfirmDialog
        open={removing}
        title="Убрать это из того, что агент знает?"
        body={removeBody(row)}
        confirmLabel="Убрать"
        busy={remove.isPending}
        onCancel={() => setRemoving(false)}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}

function Waiting({ view, projectId, short }: { view: KnowledgeView; projectId: number; short?: boolean }) {
  const { questions, memes, items } = view.waiting;
  if (!questions && !memes) return null;
  const parts = [questions ? plural(questions, "вопрос", "вопроса", "вопросов") : "", memes ? plural(memes, "мем", "мема", "мемов") : ""].filter(Boolean);
  return (
    <div className="kwait">
      <div className="kwait-h">
        <span>Ждут вашего ответа: {parts.join(" и ")}</span>
        <Link className="btn sm" to={projectPath(projectId, "questions")}>
          Ответить
        </Link>
      </div>
      {short ? null : (
        <ul>
          {items.map((q) => (
            <li key={q.id}>{q.text}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

const EMPTY_TEXT =
  "Пока ничего. Это копится из ваших ответов на вопросы и из чатов: напишите агенту «запомни: …» в чате проекта, видео или клипа.";

/** Карточка рядом со списком видео: две первые группы и ссылка на весь список. */
export function KnowledgeCard({ projectId }: { projectId: number }) {
  const query = useQuery({ queryKey: qk.channelKnowledge(projectId), queryFn: () => projectsApi.knowledge(projectId) });
  const view = query.data;
  return (
    <section className="panel know know-card" aria-label="Что агент знает о проекте">
      <div className="know-h">
        <h2>Что агент знает о проекте</h2>
        {view?.total ? <span className="muted">{view.total}</span> : null}
      </div>
      {query.isLoading ? <Loading /> : null}
      {query.isError ? <p className="muted">Не открылось: {errText(query.error, "ошибка")}</p> : null}
      {view && !view.total ? <p className="muted know-empty">{EMPTY_TEXT}</p> : null}
      {view?.groups.slice(0, 2).map((g) => (
        <div key={g.key} className="kg">
          <h3>{g.title}</h3>
          {g.items.slice(0, 2).map((row) => (
            <div key={row.ref} className="kitem kitem-ro">
              <div className="kitem-main">
                <RowText row={row} />
                <RowOrigin row={row} />
              </div>
            </div>
          ))}
        </div>
      ))}
      {view ? <Waiting view={view} projectId={projectId} short /> : null}
      <Link className="know-more" to={projectPath(projectId, "know")}>
        {view?.total ? "Посмотреть всё и поправить" : "Открыть и добавить факт"} <Icon name="arrow" size={15} />
      </Link>
    </section>
  );
}

function GroupBlock({ group, projectId, onChanged }: { group: KnowledgeGroup; projectId: number; onChanged: (v: KnowledgeView) => void }) {
  return (
    <div className="kg">
      <h3>{group.title}</h3>
      {GROUP_HINT[group.key] ? <p className="muted kg-hint">{GROUP_HINT[group.key]}</p> : null}
      {group.items.map((row) => (
        <Row key={`${row.ref}|${row.text}`} row={row} projectId={projectId} onChanged={onChanged} />
      ))}
    </div>
  );
}

/** Полный список: всё, что агент получает в этом проекте, откуда оно взялось, поправить, убрать, добавить. */
export function KnowledgeTab({ projectId }: { projectId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const query = useQuery({ queryKey: qk.channelKnowledge(projectId), queryFn: () => projectsApi.knowledge(projectId) });
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const onChanged = (view: KnowledgeView) => {
    qc.setQueryData(qk.channelKnowledge(projectId), view);
    // Строка могла лежать в брифе, правилах или ответах: их экраны перечитают своё.
    qc.invalidateQueries({ queryKey: qk.channelBrief(projectId) });
    qc.invalidateQueries({ queryKey: ["channel-projects", String(projectId), "rules"] });
    qc.invalidateQueries({ queryKey: ["stream-questions"] });
  };
  const add = useMutation({
    mutationFn: () => projectsApi.addFact(projectId, draft),
    onSuccess: (out) => {
      onChanged(out.view);
      setDraft("");
      setAdding(false);
      if (!out.created) toast.success("Такой факт уже есть");
    },
    onError: (e) => toast.error(errText(e, "Не добавилось")),
  });
  if (query.isLoading) return <Loading />;
  if (query.isError || !query.data) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  const view = query.data;
  return (
    <section className="panel know">
      <div className="know-h">
        <h2>Что агент знает о проекте</h2>
        <button className="btn sm" onClick={() => setAdding(true)}>
          <Icon name="plus" size={15} /> Добавить факт
        </button>
      </div>
      <p className="muted know-lead">Агент получает это в чатах проекта, его видео и клипов и при нарезке. В другие проекты не попадает.</p>
      {adding ? (
        <div className="kadd">
          <textarea
            className="input"
            rows={2}
            autoFocus
            aria-label="Новый факт"
            placeholder="Например: Глеб — модератор чата, на стримах копает обсидиан"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setAdding(false);
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && draft.trim()) add.mutate();
            }}
          />
          <div className="kitem-a">
            <button className="btn primary sm" disabled={!draft.trim() || add.isPending} onClick={() => add.mutate()}>
              Сохранить
            </button>
            <button className="btn ghost sm" onClick={() => setAdding(false)}>
              Отмена
            </button>
          </div>
        </div>
      ) : null}
      <Waiting view={view} projectId={projectId} />
      {!view.total && !adding ? <p className="muted know-empty">{EMPTY_TEXT}</p> : null}
      {view.groups.map((g) => (
        <GroupBlock key={g.key} group={g} projectId={projectId} onChanged={onChanged} />
      ))}
    </section>
  );
}
