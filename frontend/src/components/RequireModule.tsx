import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { EmptyState, Loading } from "@/components/ui";
import { useModules } from "@/hooks/useModules";

/** Панель или кнопка модуля: выключен — не рендерится вовсе (и не шлёт запросов). */
export function ModuleOnly({ id, children }: { id: string; children: ReactNode }) {
  const { on } = useModules();
  return on(id) ? <>{children}</> : null;
}

/** Страница модуля: модуль выключен в «Настройки → Модули» — вместо неё подсказка, где включить. */
export function RequireModule({ id, children }: { id: string; children: ReactNode }) {
  const { on, ready, data, isLoading } = useModules();
  if (isLoading) return <Loading />; // одна короткая проверка, чтобы страница не дёргала выключенный модуль
  if (ready(id) || on(id)) return <>{children}</>;
  const name = data?.modules.find((m) => m.id === id)?.name ?? id;
  return (
    <EmptyState
      icon="⏸"
      title={`Модуль «${name}» выключен`}
      hint="Включить можно в настройках — данные модуля сохранены."
      actions={
        <Link className="btn" to="/settings/modules">
          Настройки → Модули
        </Link>
      }
    />
  );
}
