import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apKeys, autopilotApi, type TelegramStatus } from "@/api/autopilot";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";

/**
 * Свой Telegram-бот сервиса для автопилота (app/notify_tg.py): карточки одобрения с кнопками,
 * ответы владельца, сводки, темы проектов. Токен хранится зашифрованным; без него всё — в UI.
 */
export function TelegramSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({ queryKey: apKeys.telegram, queryFn: autopilotApi.telegram });
  const [token, setToken] = useState("");
  const [form, setForm] = useState({ chat_id: "", thread_id: "", public_url: "", allowed_user_ids: "", use_proxy: false, enabled: true });
  useEffect(() => {
    const s = status.data;
    if (s)
      setForm({
        chat_id: s.chat_id,
        thread_id: s.thread_id == null ? "" : String(s.thread_id),
        public_url: s.public_url,
        allowed_user_ids: s.allowed_user_ids.join(", "),
        use_proxy: s.use_proxy,
        enabled: s.configured ? s.enabled || !s.env_disabled : true,
      });
  }, [status.data]);
  const onErr = (e: unknown) => toast.error(e instanceof ApiError ? e.message : "Не получилось");
  const done = (s: TelegramStatus) => qc.setQueryData(apKeys.telegram, s);
  const save = useMutation({
    mutationFn: () => autopilotApi.saveTelegram({ ...form, token: token.trim() || undefined }),
    onSuccess: (s) => {
      done(s);
      setToken("");
      toast.success("Бот сохранён");
    },
    onError: onErr,
  });
  const clear = useMutation({ mutationFn: autopilotApi.clearTelegram, onSuccess: done, onError: onErr });
  const test = useMutation({
    mutationFn: autopilotApi.testTelegram,
    onSuccess: (r) => (r.ok ? toast.success(`Бот @${r.bot} на связи — проверь чат`) : toast.error(r.error || "Не вышло")),
    onError: onErr,
  });
  const s = status.data;

  return (
    <div style={{ display: "grid", gap: 16, maxWidth: 680 }}>
      <div className="panel" style={{ display: "grid", gap: 12 }}>
        <strong>Telegram-бот автопилота</strong>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Карточки «на одобрение» с превью и кнопками Публиковать / Переделать / В мусор, ответы текстом, сводки недели и
          чаты проектов. Нужен <b>отдельный</b> бот сервиса (@BotFather → /newbot): чужой бот с тем же токеном будет мешать
          получать ответы. Не задан — всё работает только здесь, в «Одобрении».
        </p>
        <div style={{ fontSize: 13 }}>
          {s?.configured ? (
            <>
              Сейчас: <span className="mono">{s.token}</span> → чат <span className="mono">{s.chat_id}</span>
              {s.thread_id ? ` · тема ${s.thread_id}` : ""}
              {s.source === "env" ? <span className="muted"> · токен из .env</span> : null}
              {s.env_disabled ? <span style={{ color: "var(--warn)" }}> · выключен AUTOPILOT_TG_ENABLED=0</span> : null}
              {!s.enabled && !s.env_disabled ? <span className="muted"> · выключен</span> : null}
            </>
          ) : (
            <span className="muted">Не подключён.</span>
          )}
          {s?.last_error ? <div style={{ color: "var(--danger)" }}>Последняя ошибка: {s.last_error}</div> : null}
        </div>
        <input
          className="input mono"
          type="password"
          autoComplete="off"
          placeholder={s?.configured ? "новый токен (пусто — оставить текущий)" : "токен бота 123456789:AA…"}
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <div style={{ display: "grid", gap: 8, gridTemplateColumns: "1fr 1fr" }}>
          <label className="field" style={{ margin: 0 }}>
            <span>chat_id группы (-100…)</span>
            <input className="input mono" value={form.chat_id} onChange={(e) => setForm({ ...form, chat_id: e.target.value })} />
          </label>
          <label className="field" style={{ margin: 0 }}>
            <span>Тема для карточек (thread_id, необязательно)</span>
            <input className="input mono" value={form.thread_id} onChange={(e) => setForm({ ...form, thread_id: e.target.value })} />
          </label>
          <label className="field" style={{ margin: 0 }}>
            <span>Адрес сервиса для ссылок</span>
            <input className="input" placeholder="https://vvf.example.ru" value={form.public_url} onChange={(e) => setForm({ ...form, public_url: e.target.value })} />
          </label>
          <label className="field" style={{ margin: 0 }}>
            <span>Кому можно отвечать (user id через запятую)</span>
            <input className="input mono" placeholder="пусто — всем в чате" value={form.allowed_user_ids} onChange={(e) => setForm({ ...form, allowed_user_ids: e.target.value })} />
          </label>
        </div>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <label className="check">
            <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
            <span>Включён</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={form.use_proxy} onChange={(e) => setForm({ ...form, use_proxy: e.target.checked })} />
            <span>Через прокси сервиса (если Telegram режут)</span>
          </label>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn primary" disabled={save.isPending || (!token.trim() && !s?.configured && !form.chat_id)} onClick={() => save.mutate()}>
            {save.isPending ? "Сохраняю…" : "Сохранить"}
          </button>
          <button className="btn" disabled={test.isPending || !s?.configured} onClick={() => test.mutate()}>
            {test.isPending ? "Проверяю…" : "Проверить"}
          </button>
          {s?.source === "service" ? (
            <button className="btn ghost" disabled={clear.isPending} onClick={() => clear.mutate()}>
              Убрать бота
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
