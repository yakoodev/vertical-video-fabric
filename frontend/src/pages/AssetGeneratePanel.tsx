import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { montageAssetsApi } from "@/api/assets";
import { qk } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";

/**
 * ✨ ИИ рисует недостающую картинку. Рисуется ОРИГИНАЛЬНОЕ изображение (без
 * реальных людей, известных персонажей и логотипов) — такое можно публиковать,
 * в отличие от найденного в интернете чужого мема.
 */
export function AssetGeneratePanel() {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [when, setWhen] = useState("");

  const generate = useMutation({
    mutationFn: () => montageAssetsApi.generate(prompt.trim(), "", when.trim()),
    onSuccess: (asset) => {
      qc.invalidateQueries({ queryKey: qk.montageAssets });
      setPrompt("");
      setWhen("");
      toast.success(`Нарисовано: «${asset.label}» — уже в библиотеке`);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось нарисовать"),
  });

  return (
    <div className="asearch">
      <button className="btn sm" onClick={() => setOpen((v) => !v)}>
        ✨ Нарисовать картинку {open ? "▾" : "▸"}
      </button>
      {open ? (
        <div className="asearch-body">
          <div className="asearch-head">
            <input
              className="input"
              placeholder="Что нарисовать: «золотая монета со смайликом», «красная табличка СТОП»…"
              value={prompt}
              maxLength={400}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && prompt.trim().length > 2 && generate.mutate()}
            />
            <button className="btn primary" disabled={generate.isPending || prompt.trim().length < 3} onClick={() => generate.mutate()}>
              {generate.isPending ? "Рисую…" : "Нарисовать"}
            </button>
          </div>
          <input
            className="input"
            placeholder="Когда уместно вставлять (необязательно): «когда говорят про деньги»"
            value={when}
            maxLength={300}
            onChange={(e) => setWhen(e.target.value)}
          />
          <p className="muted asearch-note">
            Рисуется оригинальная картинка — без реальных людей, известных персонажей и логотипов, поэтому её безопасно
            публиковать. Файл сразу попадает в библиотеку и переиспользуется в других клипах.
          </p>
        </div>
      ) : null}
    </div>
  );
}
