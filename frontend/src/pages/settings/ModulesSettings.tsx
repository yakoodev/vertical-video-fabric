import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { modulesApi, modulesKey, type ModuleCost, type ModulesChange, type ServiceModule } from "@/api/modules";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorState, Loading } from "@/components/ui";
import { useToast } from "@/components/Toast";

const COST: Record<ModuleCost, { label: string; color: string; soft: string }> = {
  cpu: { label: "CPU", color: "var(--info)", soft: "var(--info-soft)" },
  ram: { label: "RAM", color: "var(--warn)", soft: "var(--warn-soft)" },
  gpu: { label: "GPU", color: "var(--warn)", soft: "var(--warn-soft)" },
  network: { label: "сеть", color: "var(--accent-2)", soft: "var(--accent-soft)" },
  paid: { label: "платно", color: "var(--danger)", soft: "var(--danger-soft)" },
};

function CostBadge({ cost, note }: { cost: ModuleCost; note: string }) {
  const c = COST[cost];
  return (
    <span className="badge" title={note} style={{ background: c.soft, color: c.color, borderColor: "transparent" }}>
      {c.label}
    </span>
  );
}

type Pending = { changes: Record<string, boolean>; plan: ModulesChange };

/**
 * «Настройки → Модули» (app/modules.py): включить и выключить части сервиса. Выключенный модуль
 * не держит фоновых работников и моделей в памяти, прячет свои инструменты у агентов и кнопки здесь.
 * Зависимости — каскадом, с подтверждением; модули из env (MODULES_DISABLED/ENABLED) заблокированы.
 */
export function ModulesSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  // Ресурсы живые: раз в 5 с, пока страница открыта.
  const q = useQuery({ queryKey: modulesKey, queryFn: modulesApi.get, refetchInterval: 5000 });
  const [pending, setPending] = useState<Pending | null>(null);
  const byId = useMemo(() => new Map((q.data?.modules ?? []).map((m) => [m.id, m])), [q.data]);
  const name = (id: string) => byId.get(id)?.name ?? id;

  const apply = useMutation({
    mutationFn: ({ changes, confirm }: { changes: Record<string, boolean>; confirm: boolean }) =>
      modulesApi.set(changes, confirm),
    onSuccess: (view) => {
      setPending(null);
      qc.setQueryData(modulesKey, view);
      // Пункты меню и панели других страниц смотрят в тот же кэш; остальное — перечитать.
      void qc.invalidateQueries();
      const changed = Object.entries(view.changed);
      if (changed.length)
        toast.success(changed.map(([id, on]) => `${name(id)} — ${on ? "включён" : "выключен"}`).join(", "));
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не получилось"),
  });
  // Сначала — что изменится (dry_run): каскад зависимостей спрашиваем у владельца.
  const preview = useMutation({
    mutationFn: (changes: Record<string, boolean>) => modulesApi.dryRun(changes).then((plan) => ({ changes, plan })),
    onSuccess: ({ changes, plan }) => {
      if (plan.also_off.length || plan.also_on.length) setPending({ changes, plan });
      else apply.mutate({ changes, confirm: true });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не получилось"),
  });

  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const { groups, modules, resources, env } = q.data;
  const offCount = modules.filter((m) => !m.enabled).length;

  const toggle = (m: ServiceModule, next: boolean) => preview.mutate({ [m.id]: next });
  const busy = apply.isPending || preview.isPending;

  return (
    <div style={{ display: "grid", gap: 16, maxWidth: 920 }}>
      <div className="panel" style={{ display: "grid", gap: 10 }}>
        <strong>Модули сервиса</strong>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Выключенный модуль не запускает своих фоновых работников и не грузит моделей, его инструменты пропадают у
          агентов (MCP, чат, ИИ-монтажёр), адреса отвечают «модуль выключен», а кнопки и пункты меню скрываются.
          Сохранённые данные не удаляются: включите модуль — всё на месте, без перезапуска. Для установки без
          интерфейса — переменные <span className="mono">{env.disabled}</span> и <span className="mono">{env.enabled}</span>{" "}
          (id через запятую).
        </p>
        <ResourcesView resources={resources} offCount={offCount} name={name} />
      </div>

      {groups.map((g) => {
        const items = modules.filter((m) => m.group === g.id);
        if (!items.length) return null;
        return (
          <div key={g.id} className="panel" style={{ display: "grid", gap: 2, padding: 0, overflow: "hidden" }}>
            <div style={{ padding: "12px 16px 6px", fontWeight: 600 }}>{g.name}</div>
            {items.map((m) => (
              <ModuleRow key={m.id} m={m} name={name} busy={busy} onToggle={(v) => toggle(m, v)} />
            ))}
          </div>
        );
      })}

      <ConfirmDialog
        open={!!pending}
        title="Изменятся и другие модули"
        danger={!!pending && Object.values(pending.changes).some((v) => !v)}
        confirmLabel="Применить"
        busy={apply.isPending}
        body={
          pending ? (
            <div style={{ display: "grid", gap: 6 }}>
              {pending.plan.also_off.length ? (
                <div>
                  Выключатся и зависящие: <b>{pending.plan.also_off.map(name).join(", ")}</b>.
                </div>
              ) : null}
              {pending.plan.also_on.length ? (
                <div>
                  Включатся и нужные для работы: <b>{pending.plan.also_on.map(name).join(", ")}</b>.
                </div>
              ) : null}
            </div>
          ) : null
        }
        onConfirm={() => pending && apply.mutate({ changes: pending.changes, confirm: true })}
        onCancel={() => setPending(null)}
      />
    </div>
  );
}

function ModuleRow({
  m,
  name,
  busy,
  onToggle,
}: {
  m: ServiceModule;
  name: (id: string) => string;
  busy: boolean;
  onToggle: (next: boolean) => void;
}) {
  const locked = m.locked === "env";
  const blocked = m.flag && !m.enabled && m.blocked_by.length > 0;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto",
        gap: 12,
        padding: "10px 16px",
        borderTop: "1px solid var(--hairline)",
        opacity: m.enabled ? 1 : 0.72,
      }}
    >
      <div style={{ display: "grid", gap: 4, minWidth: 0 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontWeight: 500 }}>{m.name}</span>
          <span className="mono muted" style={{ fontSize: 11 }}>
            {m.id}
          </span>
          {m.cost.map((c) => (
            <CostBadge key={c} cost={c} note={m.cost_note} />
          ))}
          {locked ? (
            <span className="badge" title="Задано переменной окружения — меняется только там">
              env
            </span>
          ) : null}
        </div>
        <div className="muted" style={{ fontSize: 13 }}>
          {m.description}
        </div>
        {m.cost_note ? (
          <div className="muted" style={{ fontSize: 12 }}>
            Нагрузка: {m.cost_note}
          </div>
        ) : null}
        {m.depends.length ? (
          <div style={{ fontSize: 12, color: blocked ? "var(--warn)" : "var(--text-faint)" }}>
            {blocked ? "Не работает: выключено " : "Нужны: "}
            {(blocked ? m.blocked_by : m.depends).map(name).join(", ")}
          </div>
        ) : null}
        {m.dependents.length ? (
          <div style={{ fontSize: 12, color: "var(--text-faint)" }}>
            Выключение выключит и: {m.dependents.map(name).join(", ")}
          </div>
        ) : null}
      </div>
      <label className="switch" title={locked ? "Задано в env — меняется только там" : m.enabled ? "Выключить" : "Включить"}>
        <input
          type="checkbox"
          checked={m.enabled}
          disabled={busy || locked}
          onChange={(e) => onToggle(e.target.checked)}
          aria-label={`${m.name}: ${m.enabled ? "включён" : "выключен"}`}
        />
        <span className="switch-track" />
      </label>
    </div>
  );
}

function ResourcesView({
  resources,
  offCount,
  name,
}: {
  resources: { rss_mb: number | null; threads: number; workers: { name: string; module: string; module_enabled: boolean; running: boolean }[]; heavy: { running?: { kind?: string; label?: string }[]; waiting?: number } | null; whisper_loaded: string[] };
  offCount: number;
  name: (id: string) => string;
}) {
  const heavyRunning = resources.heavy?.running ?? [];
  const cell = { display: "grid", gap: 2, padding: "8px 12px", background: "var(--surface-2)", borderRadius: "var(--r-sm)" } as const;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}>
        <div style={cell}>
          <span className="muted" style={{ fontSize: 11 }}>Память процесса</span>
          <span className="mono">{resources.rss_mb == null ? "—" : `${Math.round(resources.rss_mb)} МБ`}</span>
        </div>
        <div style={cell}>
          <span className="muted" style={{ fontSize: 11 }}>Потоков</span>
          <span className="mono">{resources.threads}</span>
        </div>
        <div style={cell}>
          <span className="muted" style={{ fontSize: 11 }}>Тяжёлые задачи</span>
          <span className="mono">
            {heavyRunning.length ? heavyRunning.map((h) => h.kind).join(", ") : "нет"}
            {resources.heavy?.waiting ? ` · ждут ${resources.heavy.waiting}` : ""}
          </span>
        </div>
        <div style={cell}>
          <span className="muted" style={{ fontSize: 11 }}>Whisper в памяти</span>
          <span className="mono">{resources.whisper_loaded.length ? resources.whisper_loaded.join(", ") : "нет"}</span>
        </div>
        <div style={cell}>
          <span className="muted" style={{ fontSize: 11 }}>Выключено модулей</span>
          <span className="mono">{offCount}</span>
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", fontSize: 12 }}>
        {resources.workers.map((w) => (
          <span
            key={w.name}
            className={`badge ${w.running ? "running" : ""}`}
            title={`${name(w.module)}${w.module_enabled ? "" : " — модуль выключен"}`}
          >
            <span className="dot" />
            {w.name}: {w.running ? "работает" : w.module_enabled ? "не запущен" : "выключен"}
          </span>
        ))}
      </div>
    </div>
  );
}
