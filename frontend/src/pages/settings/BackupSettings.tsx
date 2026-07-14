import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { backupApi, type ImportCounts } from "@/api/backup";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";

export function BackupSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [withAccounts, setWithAccounts] = useState(false);
  const [busy, setBusy] = useState(false);

  const download = async () => {
    setBusy(true);
    try {
      const bundle = await backupApi.exportBundle(withAccounts);
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = withAccounts ? "vvf-bundle-with-accounts.json" : "vvf-bundle.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success("Бандл скачан");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Не удалось экспортировать");
    } finally {
      setBusy(false);
    }
  };

  const importer = useMutation({
    mutationFn: (bundle: unknown) => backupApi.importBundle(bundle),
    onSuccess: (c: ImportCounts) => {
      toast.success(
        `Импортировано: пресеты ${c.ffmpeg_presets}, субтитры ${c.subtitle_profiles}, ` +
          `промпты ${c.prompt_presets}, аккаунты ${c.accounts} (пропущено ${c.skipped})`,
      );
      qc.invalidateQueries({ queryKey: qk.ffmpegPresets });
      qc.invalidateQueries({ queryKey: qk.subtitleProfiles });
      qc.invalidateQueries({ queryKey: qk.promptPresets });
      qc.invalidateQueries({ queryKey: qk.accounts });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось импортировать файл"),
  });

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      const text = await file.text();
      importer.mutate(JSON.parse(text));
    } catch {
      toast.error("Файл не читается как JSON-бандл");
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div style={{ display: "grid", gap: 16, maxWidth: 640 }}>
      <div className="panel" style={{ display: "grid", gap: 12 }}>
        <strong>Экспорт — поделиться с коллегами</strong>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Скачивает бандл (JSON) с пресетами рендера, стилями субтитров и промптами. Импортируйте его
          на другой машине, чтобы получить те же заготовки.
        </p>
        <label className="check">
          <input type="checkbox" checked={withAccounts} onChange={(e) => setWithAccounts(e.target.checked)} />
          <span>Включить аккаунты (с cookies)</span>
        </label>
        {withAccounts ? (
          <div style={{ color: "var(--danger)", fontSize: 12.5 }}>
            ⚠️ Бандл будет содержать живые cookies аккаунтов — это доступ к аккаунтам. Передавайте только
            доверенным людям и по защищённому каналу.
          </div>
        ) : null}
        <div>
          <button className="btn primary" disabled={busy} onClick={download}>
            {busy ? "Готовлю…" : "⬇ Скачать бандл"}
          </button>
        </div>
      </div>

      <div className="panel" style={{ display: "grid", gap: 12 }}>
        <strong>Импорт</strong>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Загрузите ранее скачанный бандл. Существующие элементы (по названию) не перезаписываются —
          добавляются только новые.
        </p>
        <div>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            style={{ display: "none" }}
            onChange={(e) => onFile(e.target.files?.[0])}
          />
          <button className="btn" disabled={importer.isPending} onClick={() => fileRef.current?.click()}>
            {importer.isPending ? "Импортирую…" : "⬆ Загрузить бандл"}
          </button>
        </div>
      </div>
    </div>
  );
}
