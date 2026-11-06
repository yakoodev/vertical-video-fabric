import { useEffect, useRef, useState } from "react";
import { useModules } from "@/hooks/useModules";
import { readPref } from "@/pages/workspace/InspectorParts";
import { type Selection } from "@/pages/workspace/ClipTimeline";
import { INSP_TABS, LIB_TABS, type InspTab, type LibTab } from "@/pages/workspace/editor/editorHelpers";

/**
 * Панели редактора: вкладка библиотеки, вкладка инспектора и выделение на таймлайне (оно само открывает нужную вкладку).
 */
export function useEditorPanels() {
  const { on: moduleOn } = useModules();
  const libTabs = LIB_TABS.filter((t) => !t.modules || t.modules.some(moduleOn));
  const [libTab, setLibTab] = useState<LibTab>(() => {
    try {
      const saved = localStorage.getItem("vvf.ed.libTab");
      return LIB_TABS.some((t) => t.key === saved) ? (saved as LibTab) : "moments";
    } catch {
      return "moments";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("vvf.ed.libTab", libTab);
    } catch {
      /* приватное окно — просто не запоминаем */
    }
  }, [libTab]);
  // Что выбрано на таймлайне: кусок, строка субтитров или вставка.
  const [tlSelected, setTlSelected] = useState<Selection>(null);
  // Вкладка правой панели — запоминается; выбор на таймлайне сам открывает нужную.
  const [inspTab, setInspTab] = useState<InspTab>(() => {
    const saved = readPref("vvf.ed.inspTab");
    return INSP_TABS.includes(saved as InspTab) ? (saved as InspTab) : "clip";
  });
  const inspScrollRef = useRef<HTMLDivElement>(null);
  const tlSelKey = tlSelected ? `${tlSelected.kind}:${"id" in tlSelected ? tlSelected.id : tlSelected.index}` : "";
  useEffect(() => {
    if (!tlSelected) return;
    const to: Record<NonNullable<Selection>["kind"], InspTab> = {
      layer: "layers",
      cam: "layers",
      insert: "layers",
      piece: "piece",
      mute: "audio",
      sub: "subs",
    };
    setInspTab(to[tlSelected.kind]);
    if (inspScrollRef.current) inspScrollRef.current.scrollTop = 0;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tlSelKey]);

  return {
    setTlSelected, libTabs, inspScrollRef, libTab, setLibTab, tlSelected, inspTab, setInspTab,
  };
}
