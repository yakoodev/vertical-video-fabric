// Какой шаг истории просит сочетание клавиш. Модуль без импортов: node запускает его как есть (tests/test_frontend_editor_keys.py).

type KeyInfo = { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey: boolean };

/** Ctrl+Z — назад, Ctrl+Shift+Z и Ctrl+Y — вперёд, в том числе в русской раскладке. */
export function historyKey(e: KeyInfo): "undo" | "redo" | null {
  if (!(e.ctrlKey || e.metaKey) || e.altKey) return null;
  const key = e.key.toLowerCase();
  if (key === "z" || key === "я") return e.shiftKey ? "redo" : "undo";
  if ((key === "y" || key === "н") && !e.shiftKey) return "redo";
  return null;
}
