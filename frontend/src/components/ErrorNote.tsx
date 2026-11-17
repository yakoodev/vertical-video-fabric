import { Link } from "react-router-dom";
import type { ErrorView } from "@/api/types";

/**
 * Ошибка простыми словами: «что случилось — что сделать» (app/error_text.py), сырой текст площадки — под «подробнее».
 * Незнакомая ошибка (view нет) — сырой текст, как раньше. ``inLink`` — строка сама ссылка (Главная): без вложенной
 * ссылки и раскрывашки, сырой текст в подсказке.
 */
export function ErrorNote({
  raw,
  view,
  inLink,
  className = "err-note",
}: {
  raw: string;
  view?: ErrorView | null;
  inLink?: boolean;
  className?: string;
}) {
  if (!raw && !view) return null;
  if (!view) return <span className={`${className} err-note--raw`}>{raw}</span>;
  const action = view.action ? (
    <>
      {" — "}
      {view.to && !inLink ? (
        <Link to={view.to} className="err-note-act">
          {view.action}
        </Link>
      ) : (
        view.action
      )}
    </>
  ) : null;
  if (inLink) {
    return (
      <span className={className} title={raw}>
        {view.text}
        {action}
      </span>
    );
  }
  return (
    <span className={className}>
      <span className="err-note-t">
        {view.text}
        {action}
      </span>
      {raw && raw !== view.text ? (
        <details className="err-note-raw">
          <summary>подробнее</summary>
          <pre>{raw}</pre>
        </details>
      ) : null}
    </span>
  );
}
