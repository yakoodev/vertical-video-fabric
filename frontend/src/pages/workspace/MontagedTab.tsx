import { Link } from "react-router-dom";
import { VideoRendersGrid } from "@/pages/workspace/VideoRendersTab";

export function MontagedTab({ sourceId }: { sourceId: string }) {
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div className="panel" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <div>
          <strong>Свои смонтированные клипы</strong>
          <p className="muted" style={{ margin: "4px 0 0", fontSize: 13 }}>
            Готовые вертикальные видео из монтажки загружаются в общую библиотеку клипов — там их можно публиковать и
            перерендеривать с субтитрами/баннером.
          </p>
        </div>
        <Link to="/pubs?tab=ready" className="btn primary sm">
          Загрузить клип →
        </Link>
      </div>
      <VideoRendersGrid
        sourceId={sourceId}
        origin="montage"
        emptyIcon="🎞"
        emptyTitle="Монтажей нет"
        emptyHint="Монтаж из нескольких сегментов появится здесь"
      />
    </div>
  );
}
