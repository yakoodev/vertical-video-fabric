import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { qk } from "@/api/keys";
import { formatCount, formatSnapshotTime, statsApi } from "@/api/stats";
import { useModules } from "@/hooks/useModules";

/** Числа площадок (общий запрос со страницей «Публикации»); модуль статистики выключен — без запросов. */
export function usePublicationStats(enabled = true) {
  const statsOn = useModules().ready("platform_stats");
  return useQuery({
    queryKey: qk.publicationStats,
    queryFn: statsApi.publications,
    enabled: enabled && statsOn,
    refetchInterval: (q) => (q.state.data?.status.running ? 5000 : 60000),
  });
}

/**
 * Значок «Статистика» опубликованного клипа: 👁 просмотры (сумма по площадкам) и тревога
 * «0 просмотров через N ч» (app/zero_views.py) — ссылкой на карточку «внимание».
 */
export function StatsBadge({ clipId, className = "" }: { clipId: number; className?: string }) {
  const stats = usePublicationStats();
  const badge = stats.data?.clips?.[String(clipId)];
  if (!badge) return null;
  if (badge.alert) {
    return (
      <Link
        to={badge.alert.approval_id ? `/inbox?tab=approve&id=${badge.alert.approval_id}` : "/inbox?tab=approve"}
        className={`chip stats-badge stats-badge--alert ${className}`}
        title="Мало просмотров после публикации — причины и чек-лист в карточке"
      >
        ⚠ {formatCount(badge.alert.views)} за {badge.alert.hours} ч
      </Link>
    );
  }
  if (badge.views == null) return null;
  return (
    <span
      className={`chip stats-badge ${className}`}
      title={`Статистика: ${formatCount(badge.views)} просмотров, ${formatCount(badge.likes)} лайков${
        badge.captured_at ? ` на ${formatSnapshotTime(badge.captured_at)}` : ""
      }`}
    >
      👁 {formatCount(badge.views)}
    </span>
  );
}
