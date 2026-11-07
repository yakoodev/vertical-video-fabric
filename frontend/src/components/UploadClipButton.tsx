import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { clipGroupKeys } from "@/api/clipGroups";
import { clipsApi } from "@/api/clips";
import { ApiError } from "@/api/client";
import { qk } from "@/api/keys";
import { navKeys } from "@/api/nav";
import { useToast } from "@/components/Toast";

/** Готовый ролик файлом: он не перекодируется, сразу открывается его экран клипа. С ``projectId`` кладётся в этот проект. */
export function UploadClipButton({ projectId, className = "btn primary", label = "📤 Загрузить клип" }: { projectId?: number; className?: string; label?: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const upload = useMutation({
    mutationFn: (file: File) => clipsApi.uploadEdited(file, { projectId }),
    onSuccess: (made) => {
      toast.success("Клип загружен");
      qc.invalidateQueries({ queryKey: qk.clips() });
      qc.invalidateQueries({ queryKey: clipGroupKeys.all });
      qc.invalidateQueries({ queryKey: navKeys.all });
      navigate(made.to);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось загрузить клип"),
  });
  return (
    <label className={className} style={{ cursor: upload.isPending ? "wait" : "pointer" }} title="Готовый файл: сервис его не меняет">
      {upload.isPending ? "Загрузка…" : label}
      <input
        type="file"
        accept="video/*"
        hidden
        disabled={upload.isPending}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) upload.mutate(f);
          e.target.value = "";
        }}
      />
    </label>
  );
}
