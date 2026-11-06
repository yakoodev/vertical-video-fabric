import { useEffect, useRef } from "react";
import { historyKey } from "@/pages/workspace/editor/historyKey";

/**
 * Клавиши редактора и списка: один постоянный слушатель на окне, а действия ставит в эти ссылки
 * последний рендер (buildDraftsEditorSession), поэтому они всегда видят свежее состояние.
 */
export function useEditorShortcuts() {
  const keyHandlerRef = useRef<((e: KeyboardEvent) => void) | null>(null);
  // Ctrl+Z: снимок состояния делает сервер перед каждой правкой, здесь только
  // просим шаг назад и перечитываем клип в панель. Ctrl+Shift+Z и Ctrl+Y — шаг вперёд.
  const undoRef = useRef<() => void>(() => undefined);
  const redoRef = useRef<() => void>(() => undefined);
  // Ctrl+D — копия выделенного слоя (ставится обработчиком последнего рендера).
  const duplicateRef = useRef<() => void>(() => undefined);
  const copyLayerRef = useRef<() => boolean>(() => false);
  const pasteLayerRef = useRef<() => boolean>(() => false);
  const nudgeLayerRef = useRef<(key: string, big: boolean) => boolean>(() => false);
  // Keyboard: F ★ / X hide / E edit the moment under the pointer; in the editor
  // F stars the open clip and Esc goes back to the moments (see «?» in the shell).
  // One stable listener (a hook must not sit after the early returns below); it
  // calls whatever handler the latest render left in keyHandlerRef.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const history = historyKey(e);
      if (history) {
        if (t && (t.isContentEditable || ["INPUT", "TEXTAREA"].includes(t.tagName))) return;
        e.preventDefault();
        (history === "redo" ? redoRef : undoRef).current();
        return;
      }
      // Ctrl+C / Ctrl+V — слой в буфер и обратно (в том числе в другой клип).
      if ((e.ctrlKey || e.metaKey) && !e.altKey && ["c", "v", "с", "м"].includes(e.key.toLowerCase())) {
        if (t && (t.isContentEditable || ["INPUT", "TEXTAREA"].includes(t.tagName))) return;
        if (window.getSelection()?.toString()) return;
        const copy = ["c", "с"].includes(e.key.toLowerCase());
        if ((copy ? copyLayerRef : pasteLayerRef).current()) e.preventDefault();
        return;
      }
      // Alt+стрелки — подвинуть выделенный слой (Shift — крупнее).
      if (e.altKey && !e.ctrlKey && e.key.startsWith("Arrow")) {
        if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
        if (nudgeLayerRef.current(e.key, e.shiftKey)) e.preventDefault();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "d") {
        if (t && (t.isContentEditable || ["INPUT", "TEXTAREA"].includes(t.tagName))) return;
        e.preventDefault();
        duplicateRef.current();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))) return;
      if (document.querySelector(".modal-backdrop, .drawer-backdrop")) return;
      keyHandlerRef.current?.(e);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return {
    keyHandlerRef, undoRef, redoRef, duplicateRef, copyLayerRef, pasteLayerRef, nudgeLayerRef,
  };
}
