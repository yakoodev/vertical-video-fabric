import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { agentSettingsApi, agentSettingsKey } from "@/api/agentSettings";
import { projectPath } from "@/lib/paths";
import "@/styles/agent-settings.css";

/** Черновики и настройка «Останавливаться на списке клипов»: что значит ★ и где это поменять. */
export function DraftPickHint({ projectId }: { projectId: number | null }) {
  const query = useQuery({
    queryKey: agentSettingsKey(projectId ?? 0),
    queryFn: () => agentSettingsApi.get(projectId ?? 0),
    enabled: Boolean(projectId),
  });
  if (!projectId || !query.data?.settings.stop_at_clip_list) return null;
  return (
    <p className="mo-pick-hint muted" role="status">
      <span>Агент нашёл черновики и ждёт: монтирует только те, что ты отметил ★.</span>
      <Link to={projectPath(projectId, "autopilot")}>Настройка «Как работает агент»</Link>
    </p>
  );
}
