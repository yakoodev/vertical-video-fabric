import { useEffect, useRef, useState } from "react";
import { clipPlansApi } from "@/api/clipPlans";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";

/**
 * «Файл клипа» — весь клип одним JSON-документом (vvf.clip/1).
 *
 * Тот же файл, с которым работает внешний агент через tools/vvf.py (см.
 * docs/AGENTS.md): куски с таймкодами, трек кадрирования, настройки рендера,
 * переходы, заметки. Здесь его можно поправить руками, скачать, отдать агенту и
 * загрузить обратно. Сервер проверяет файл ЦЕЛИКОМ и применяет только если всё
 * в порядке — наполовину применённого клипа не бывает.
 */
export function ClipFileDialog({
  clipPlanId,
  onClose,
  onApplied,
}: {
  clipPlanId: number;
  onClose: () => void;
  onApplied: (changes: string[]) => void;
}) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const [verdict, setVerdict] = useState<"" | "ok" | "bad">("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let alive = true;
    clipPlansApi
      .getSpec(clipPlanId)
      .then((spec) => alive && setText(JSON.stringify(spec, null, 2)))
      .catch((e) => toast.error(e instanceof ApiError ? e.message : "Не удалось загрузить файл клипа"))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [clipPlanId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const parse = (): unknown | null => {
    try {
      return JSON.parse(text);
    } catch (e) {
      setVerdict("bad");
      setProblems([`это не JSON: ${(e as Error).message}`]);
      return null;
    }
  };

  const check = async () => {
    const spec = parse();
    if (spec === null) return;
    setBusy(true);
    try {
      const res = await clipPlansApi.validateSpec(clipPlanId, spec);
      setProblems(res.problems);
      setVerdict(res.ok ? "ok" : "bad");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Не удалось проверить");
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    const spec = parse();
    if (spec === null) return;
    setBusy(true);
    try {
      const res = await clipPlansApi.putSpec(clipPlanId, spec);
      setText(JSON.stringify(res.spec, null, 2));
      setProblems([]);
      setVerdict("ok");
      onApplied(res.changes);
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : undefined;
      const list =
        detail && typeof detail === "object" && "problems" in (detail as object)
          ? ((detail as { problems: string[] }).problems ?? [])
          : [e instanceof ApiError ? e.message : "Не удалось применить"];
      setProblems(list);
      setVerdict("bad");
    } finally {
      setBusy(false);
    }
  };

  const download = () => {
    const blob = new Blob([text + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `clip-${clipPlanId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const upload = (file: File) => {
    file.text().then((body) => {
      setText(body);
      setVerdict("");
      setProblems([]);
    });
  };

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onClose}>
      <div
        className="modal clipfile"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Файл клипа"
      >
        <div className="clipfile-head">
          <div>
            <h3>Файл клипа</h3>
            <p className="muted">
              Весь клип одним файлом: куски, кадрирование, настройки рендера, переходы. Тот же файл правит
              внешний агент — инструкция в <code>docs/AGENTS.md</code>. Применяется целиком или никак.
            </p>
          </div>
          <button className="btn ghost" onClick={onClose} disabled={busy} aria-label="Закрыть">
            ✕
          </button>
        </div>

        <textarea
          className="clipfile-text mono"
          value={loading ? "Загружаю…" : text}
          onChange={(e) => {
            setText(e.target.value);
            setVerdict("");
          }}
          spellCheck={false}
          disabled={loading}
          aria-label="JSON файла клипа"
        />

        {verdict === "ok" && !problems.length ? <div className="clipfile-ok">Файл в порядке</div> : null}
        {problems.length ? (
          <ul className="clipfile-problems">
            {problems.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        ) : null}

        <div className="clipfile-actions">
          <button className="btn" onClick={() => fileRef.current?.click()} disabled={busy || loading}>
            Загрузить…
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) upload(f);
              e.target.value = "";
            }}
          />
          <button className="btn" onClick={download} disabled={busy || loading}>
            Скачать
          </button>
          <span className="clipfile-spacer" />
          <button className="btn" onClick={() => void check()} disabled={busy || loading}>
            Проверить
          </button>
          <button className="btn primary" onClick={() => void apply()} disabled={busy || loading}>
            {busy ? "…" : "Применить"}
          </button>
        </div>
      </div>
    </div>
  );
}
