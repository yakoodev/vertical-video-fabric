import { useEffect, useMemo, useState } from "react";
import { type UseQueryResult } from "@tanstack/react-query";
import { type SourceDetail, type ClipPlan } from "@/api/types";

/**
 * Что показывает список черновиков и что отмечено «в рендер». Живёт над экранами: когда избранных нет,
 * те же фильтры ведут рельс редактора, а при возврате из редактора список остаётся таким же.
 */
export function useDraftsFilters({
  query, plans,
}: {
  query: UseQueryResult<SourceDetail>;
  plans: ClipPlan[];
}) {
  const [chosen, setChosen] = useState<Set<number> | null>(null);
  const [hideDuplicates, setHideDuplicates] = useState(false);
  const [candidateSearch, setCandidateSearch] = useState("");
  const [hiddenAnalyses, setHiddenAnalyses] = useState<Set<number>>(new Set());
  const [onlyFavorites, setOnlyFavorites] = useState(false);
  const [showHidden, setShowHidden] = useState(false);

  // Default-select every plan for batch render once they load.
  useEffect(() => {
    if (chosen === null && plans.length) setChosen(new Set(plans.map((p) => p.id)));
  }, [plans.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const selected = chosen ?? new Set<number>();

  // Candidates accumulate across analyses — group them so it's clear which run
  // produced what (newest run first).
  const planGroups = useMemo(() => {
    const q = candidateSearch.trim().toLowerCase();
    const by = new Map<number, typeof plans>();
    for (const p of plans) {
      if (hideDuplicates && p.duplicate_of != null) continue;
      if (!showHidden && p.hidden) continue;
      if (onlyFavorites && !p.favorite) continue;
      if (q && !(p.title ?? "").toLowerCase().includes(q)) continue;
      const key = p.analysis_id ?? 0;
      by.set(key, [...(by.get(key) ?? []), p]);
    }
    // Best candidates first inside each analysis.
    for (const [, group] of by) group.sort((a, b) => (b.quality ?? 0) - (a.quality ?? 0));
    return [...by.entries()]
      .filter(([analysisId]) => !hiddenAnalyses.has(analysisId))
      .sort((a, b) => b[0] - a[0]);
  }, [plans, hideDuplicates, candidateSearch, hiddenAnalyses, onlyFavorites, showHidden]);
  const shownCount = planGroups.reduce((n, [, g]) => n + g.length, 0);
  const favCount = plans.filter((p) => p.favorite).length;
  const hiddenCount = plans.filter((p) => p.hidden).length;
  // All analyses present (for the show/hide filter chips), independent of search/hide.
  const analysisChips = useMemo(() => {
    const by = new Map<number, number>();
    for (const p of plans) by.set(p.analysis_id ?? 0, (by.get(p.analysis_id ?? 0) ?? 0) + 1);
    return [...by.entries()].sort((a, b) => b[0] - a[0]);
  }, [plans]);
  const analysisMeta = (analysisId: number) => {
    if (!analysisId) return "Добавлены вручную";
    const a = query.data?.analyses?.find((x) => x.id === analysisId);
    return a ? `Анализ #${a.id} · ${a.provider}${a.model ? ` · ${a.model}` : ""}` : `Анализ #${analysisId}`;
  };

  return {
    selected, planGroups, shownCount, favCount, hiddenCount, analysisChips, analysisMeta, setChosen, hideDuplicates,
    setHideDuplicates, candidateSearch, setCandidateSearch, hiddenAnalyses, setHiddenAnalyses, onlyFavorites,
    setOnlyFavorites, showHidden, setShowHidden,
  };
}
