// ИИ-видео: сборка клипа и журнал трат.
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { aivApi, aivKey, type Overview, type Script } from "@/api/aivideo";
import { useToast } from "@/components/Toast";
import { EpisodePicker, errText, rub, useEpisode, useInvalidate } from "./shared";
import { StageButton } from "./StageControls";

// --------------------------------------------------------------------------- //
// Сборка и журнал трат
// --------------------------------------------------------------------------- //

export function AssembleSub({ o }: { o: Overview }) {
  const ep = useEpisode(o);
  const toast = useToast();
  const invalidate = useInvalidate(o.project.id);
  const [music, setMusic] = useState("");
  const [result, setResult] = useState<{ clip_id: number; duration: number; editor_url: string; notes: string[] } | null>(null);
  const assemble = useMutation({
    mutationFn: () => aivApi.assemble(ep.id as number, music ? { music_track_id: Number(music) } : {}),
    onSuccess: (r) => {
      setResult(r);
      toast.success(`Клип #${r.clip_id}: ${r.duration} с`);
      invalidate();
    },
    onError: (e) => toast.error(errText(e, "Не собрано")),
  });
  const ledger = useQuery({ queryKey: [...aivKey(o.project.id), "ledger"], queryFn: () => aivApi.ledger(o.project.id) });
  const s: Script | undefined = ep.script;
  const missing = useMemo(
    () => (s?.shots ?? []).filter((x) => !x.keyframes?.length && !x.videos?.length).map((x) => x.idx),
    [s],
  );
  return (
    <div className="series-grid">
      <div className="series-col">
        <section className="panel series-panel">
          <div className="series-row">
            <EpisodePicker o={o} id={ep.id} setId={ep.setId} />
          </div>
          {s ? (
            <>
              <StageButton projectId={o.project.id} stage="voice" scriptId={s.id} label="Озвучка реплик: оценить" />
              {missing.length ? <span className="aiv-warn">Нет кадров у планов {missing.join(", ")} — «Раскадровка».</span> : null}
              <div className="series-row">
                <input className="input" placeholder="id музыки (необязательно)" value={music} onChange={(e) => setMusic(e.target.value)} />
                <button className="btn primary sm" disabled={!!missing.length || assemble.isPending} onClick={() => assemble.mutate()}>
                  🎬 Собрать клип
                </button>
              </div>
              {s.clip_plan_id ? (
                <span className="muted">
                  Последняя сборка: клип #{s.clip_plan_id}. Новая сборка станет его новой версией: субтитры и монтаж соберутся из серии заново, правки руками останутся в прошлой версии.
                </span>
              ) : null}
              {result ? (
                <div className="aiv-est">
                  <Link to={result.editor_url}>Открыть в редакторе →</Link>
                  {result.notes.map((n) => (
                    <span key={n} className="muted">
                      {n}
                    </span>
                  ))}
                </div>
              ) : null}
            </>
          ) : null}
        </section>
      </div>
      <div className="series-col">
        <section className="panel series-panel">
          <h3>Журнал трат</h3>
          {ledger.data?.ledger.length ? (
            <table className="aiv-ledger">
              <tbody>
                {ledger.data.ledger.map((l) => (
                  <tr key={l.id}>
                    <td className="muted">{l.created_at}</td>
                    <td>{l.label || l.stage}</td>
                    <td className="mono">{l.model}</td>
                    <td>{rub(l.amount_rub)}</td>
                    <td className="muted">{l.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">Трат не было.</p>
          )}
        </section>
      </div>
    </div>
  );
}
