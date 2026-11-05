import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ApiError } from "@/api/client";
import { navKeys } from "@/api/nav";
import { PROJECT_KINDS, projectsApi, type ProjectKind } from "@/api/projects";
import { qk } from "@/api/keys";
import { useToast } from "@/components/Toast";
import { projectPath } from "@/lib/paths";

/** «Новый проект» из боковой панели: название и вид, дальше — страница проекта. */
export function NewProjectDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ProjectKind>("stream_channel");
  const create = useMutation({
    mutationFn: () => projectsApi.create({ name: name.trim(), kind }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: qk.channelProjects });
      qc.invalidateQueries({ queryKey: navKeys.all });
      onClose();
      navigate(projectPath(p.id));
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Не удалось создать проект"),
  });
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal create-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Новый проект">
        <div className="pub-head">
          <h3>Новый проект</h3>
          <button className="pub-x" title="Закрыть" onClick={onClose}>
            ×
          </button>
        </div>
        <label className="field">
          <span>Название</span>
          <input
            className="input"
            autoFocus
            placeholder="Например, канал или тема"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && name.trim() && create.mutate()}
          />
        </label>
        <label className="field" style={{ marginTop: 10 }}>
          <span>Вид</span>
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value as ProjectKind)}>
            {PROJECT_KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
        </label>
        <div style={{ marginTop: 14 }}>
          <button className="btn primary" disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>
            Создать проект
          </button>
        </div>
      </div>
    </div>
  );
}
