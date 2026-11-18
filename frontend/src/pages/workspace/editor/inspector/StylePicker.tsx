import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { stylesApi, type StyleCard } from "@/api/styles";
import { type DraftsEditorSession } from "@/pages/workspace/editor/draftsEditorSession";

/** Стиль ролика (app/style_cards.py): карточка — образ, субтитры, переход, фон, эффект и входы одним нажатием. */
export function StylePicker({ session }: { session: DraftsEditorSession }) {
  const { activePlan, flushSettings, qc, sourceId, setSettingsNonce, toast } = session;
  const styles = useQuery({ queryKey: ["styles"], queryFn: stylesApi.list, staleTime: Infinity });
  const current = (activePlan?.render_settings as { style?: { id?: string } } | undefined)?.style?.id ?? "";
  const [pick, setPick] = useState("");
  const chosen = pick || current || styles.data?.[0]?.id || "";
  const card: StyleCard | undefined = styles.data?.find((s) => s.id === chosen);
  const apply = useMutation({
    mutationFn: async (id: string) => {
      await flushSettings();
      return stylesApi.apply(activePlan!.id, id);
    },
    onSuccess: async (r) => {
      const note = r.warnings.length ? ` · ${r.warnings[0]}` : "";
      toast.success(`Стиль «${r.style.short}»: ${r.changes.join(", ") || "без изменений"}${note} — Ctrl+Z откатит`);
      await qc.invalidateQueries({ queryKey: qk.source(sourceId) });
      setSettingsNonce((n) => n + 1);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Стиль не применился"),
  });
  if (!styles.data?.length) return null;
  return (
    <>
      <div className="ed-quick-row">
        <select
          className="input"
          style={{ flex: "1 1 120px", minWidth: 0 }}
          value={chosen}
          onChange={(e) => setPick(e.target.value)}
          title="Стиль ролика: одна карточка — образ, субтитры, переход, фон, эффект и входы текстов"
        >
          {styles.data.map((s) => (
            <option key={s.id} value={s.id}>
              {s.id === current ? `✓ ${s.short}` : s.short}
            </option>
          ))}
        </select>
        <button
          className="btn sm"
          disabled={!activePlan || !chosen || apply.isPending}
          onClick={() => chosen && apply.mutate(chosen)}
          title={card ? `${card.essence} Повтор не дублирует. Ctrl+Z откатит.` : undefined}
        >
          {apply.isPending ? "Применяю…" : "Применить стиль"}
        </button>
      </div>
      {card ? (
        <details className="ed-quick-note">
          <summary>{card.name}</summary>
          <p>{card.card.essence}</p>
          <p>
            <b>Не:</b> {card.card.not.join("; ")}
          </p>
          <p>
            <b>Приёмы:</b> {card.card.native.map((m) => `${m.id} — ${m.fits}`).join("; ")}
          </p>
          <p>
            <b>Грабли:</b> {card.card.pitfalls.join("; ")}
          </p>
        </details>
      ) : null}
    </>
  );
}
