/**
 * Ушли со страницы: браузер не сразу закрывает загрузки убранных <video> и <img>, а соединений на хост
 * у него 6, и новая страница ждёт в очереди. Один наблюдатель на всё приложение обрывает загрузку
 * наших файлов, как только элемент убран из документа. Элемент, который React лишь переставил, не трогаем.
 */
function release(el: Element) {
  if (el.isConnected) return;
  if (el instanceof HTMLVideoElement) {
    if (!(el.getAttribute("src") ?? "").startsWith("/media/")) return;
    el.pause();
    el.removeAttribute("src");
    el.load();
  } else if (el instanceof HTMLImageElement && !el.complete) {
    if ((el.getAttribute("src") ?? "").startsWith("/media/")) el.removeAttribute("src");
  }
}

export function watchMediaRelease() {
  new MutationObserver((records) => {
    for (const record of records) {
      record.removedNodes.forEach((node) => {
        if (!(node instanceof Element)) return;
        if (node.matches("video, img")) release(node);
        node.querySelectorAll("video, img").forEach(release);
      });
    }
  }).observe(document.body, { childList: true, subtree: true });
}
