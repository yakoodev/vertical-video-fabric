// Разделы инспектора: график, плашка, плашка канала, карточка соцсети.
import { NAMEPLATE_SIZE, SOCIAL_MAX_LINES, nameplateAccent, type ChartDatum } from "@/motion";
import { Color, Num, Section, Toggle, type LayerOf, type PatchProps } from "./fields";

export function ChartSection({ layer, patchProps }: { layer: LayerOf<"chart">; patchProps: PatchProps }) {
  return (
    <Section title="График">
      <div className="li-row">
        <select className="input" value={layer.props.kind} onChange={(e) => patchProps({ kind: e.target.value })}>
          {[
            ["bar", "Столбцы"],
            ["hbar", "Полосы"],
            ["line", "Линия"],
            ["pie", "Пирог"],
            ["donut", "Кольцо"],
          ].map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
        <Toggle label="Значения" checked={layer.props.showValues ?? true} onChange={(showValues) => patchProps({ showValues })} />
      </div>
      <label>
        <span className="li-cap">Заголовок</span>
        <input className="input" value={layer.props.title ?? ""} onChange={(e) => patchProps({ title: e.target.value })} />
      </label>
      <div className="li-data">
        {(layer.props.data ?? []).map((d: ChartDatum, i: number) => (
          <div className="li-row" key={i}>
            <input
              className="input li-grow"
              value={d.label}
              onChange={(e) => patchProps({ data: layer.props.data.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })}
            />
            <input
              className="input li-val"
              type="number"
              value={d.value}
              onChange={(e) =>
                patchProps({ data: layer.props.data.map((x, j) => (j === i ? { ...x, value: Number(e.target.value) || 0 } : x)) })
              }
            />
            <button
              type="button"
              className="btn ghost sm"
              onClick={() => patchProps({ data: layer.props.data.filter((_, j) => j !== i) })}
              aria-label="Убрать строку"
            >
              ×
            </button>
          </div>
        ))}
        {(layer.props.data?.length ?? 0) < 12 ? (
          <button
            type="button"
            className="btn ghost sm"
            onClick={() => patchProps({ data: [...(layer.props.data ?? []), { label: "Новое", value: 10 }] })}
          >
            ＋ строка
          </button>
        ) : null}
      </div>
    </Section>
  );
}

export function CalloutSection({ layer, refWidth, patchProps }: { layer: LayerOf<"callout">; refWidth: number; patchProps: PatchProps }) {
  return (
    <Section title="Плашка">
      <label>
        <span className="li-cap">Заголовок</span>
        <input className="input" value={layer.props.title} onChange={(e) => patchProps({ title: e.target.value })} />
      </label>
      <label>
        <span className="li-cap">Подпись</span>
        <input className="input" value={layer.props.subtitle ?? ""} onChange={(e) => patchProps({ subtitle: e.target.value })} />
      </label>
      <div className="li-row">
        <select className="input" value={layer.props.style ?? "bar"} onChange={(e) => patchProps({ style: e.target.value })}>
          <option value="bar">Полоса</option>
          <option value="box">Коробка</option>
          <option value="pill">Таблетка</option>
          <option value="neon">Неон</option>
        </select>
        <Color label="Акцент" value={layer.props.accent ?? "#7c5cff"} onChange={(accent) => patchProps({ accent })} />
        <label>
          <span className="li-cap">Значок</span>
          <input className="input li-emoji" value={layer.props.icon ?? ""} onChange={(e) => patchProps({ icon: e.target.value })} />
        </label>
      </div>
      <div className="li-row">
        <Num label="Ширина" value={layer.props.w ?? 860} min={200} max={refWidth} step={10} onChange={(w) => patchProps({ w })} />
        <Num label="Скос°" value={layer.props.skew ?? 0} min={-30} max={30} onChange={(skew) => patchProps({ skew })} />
      </div>
    </Section>
  );
}

export function NameplateSection({ layer, patchProps }: { layer: LayerOf<"nameplate">; patchProps: PatchProps }) {
  return (
    <Section title="Плашка канала">
      <label>
        <span className="li-cap">Ник</span>
        <input className="input" value={layer.props.handle ?? ""} maxLength={40} onChange={(e) => patchProps({ handle: e.target.value })} />
      </label>
      <div className="li-row">
        <select className="input" value={layer.props.platform ?? "twitch"} onChange={(e) => patchProps({ platform: e.target.value })}>
          <option value="twitch">Twitch</option>
          <option value="youtube">YouTube</option>
          <option value="tiktok">TikTok</option>
          <option value="telegram">Telegram</option>
          <option value="kick">Kick</option>
          <option value="none">Без значка</option>
        </select>
        <select className="input" value={layer.props.style ?? "glass"} onChange={(e) => patchProps({ style: e.target.value })}>
          <option value="glass">Стекло</option>
          <option value="solid">Заливка</option>
        </select>
        <Color label="Акцент" value={nameplateAccent(layer.props)} onChange={(accent) => patchProps({ accent })} />
      </div>
      <div className="li-row">
        <Num label="Высота" value={layer.props.size ?? NAMEPLATE_SIZE} min={32} max={200} onChange={(size) => patchProps({ size })} />
        <select className="input" value={layer.props.align ?? "center"} onChange={(e) => patchProps({ align: e.target.value })}>
          <option value="left">У левого края рамки</option>
          <option value="center">По центру</option>
          <option value="right">У правого края рамки</option>
        </select>
      </div>
      <div className="li-row">
        <select
          className="input"
          title="Своя анимация плашки: вход от значка, редкий блик и тик значка"
          value={layer.props.anim ?? "subtle"}
          onChange={(e) => patchProps({ anim: e.target.value })}
        >
          <option value="none">Без анимации</option>
          <option value="subtle">Анимация: мягко</option>
          <option value="lively">Анимация: живо</option>
        </select>
        <select
          className="input"
          title="Свернуться в значок в последние 0.6 с слоя"
          value={layer.props.outro ?? "none"}
          onChange={(e) => patchProps({ outro: e.target.value })}
        >
          <option value="none">Без свёртки</option>
          <option value="collapse">Свернуться в конце</option>
        </select>
      </div>
      <Toggle label="Точка LIVE" checked={Boolean(layer.props.live)} onChange={(live) => patchProps({ live })} />
    </Section>
  );
}

/** Пустое поле числа → поля нет вовсе (счётчик не показан); null схема бэкенда приняла бы за 0. */
const countOrNull = (v: string) => (v.trim() === "" ? undefined : Math.max(0, Math.round(Number(v) || 0)));

export function SocialSection({ layer, refWidth, patchProps }: { layer: LayerOf<"social">; refWidth: number; patchProps: PatchProps }) {
  const p = layer.props;
  const post = p.style === "post";
  return (
    <Section title={post ? "Пост" : "Комментарий"}>
      <div className="li-row">
        <select className="input" value={p.style ?? "comment"} onChange={(e) => patchProps({ style: e.target.value })}>
          <option value="comment">Комментарий</option>
          <option value="post">Пост ленты</option>
        </select>
        <select className="input" value={p.theme ?? "dark"} onChange={(e) => patchProps({ theme: e.target.value })}>
          <option value="dark">Тёмная</option>
          <option value="light">Светлая</option>
        </select>
      </div>
      <label>
        <span className="li-cap">{post ? "Имя" : "Ник"}</span>
        <input className="input" value={p.name ?? ""} maxLength={40} onChange={(e) => patchProps({ name: e.target.value })} />
      </label>
      {post ? (
        <label>
          <span className="li-cap">@ник</span>
          <input className="input" value={p.handle ?? ""} maxLength={40} onChange={(e) => patchProps({ handle: e.target.value })} />
        </label>
      ) : null}
      <label>
        <span className="li-cap">Текст</span>
        <textarea className="input" rows={3} value={p.text ?? ""} maxLength={500} onChange={(e) => patchProps({ text: e.target.value })} />
      </label>
      <div className="li-row">
        <label>
          <span className="li-cap">Когда</span>
          <input className="input" value={p.time ?? ""} maxLength={24} onChange={(e) => patchProps({ time: e.target.value })} />
        </label>
        <label>
          <span className="li-cap">В аватаре</span>
          <input className="input li-emoji" value={p.avatar ?? ""} maxLength={8} onChange={(e) => patchProps({ avatar: e.target.value })} />
        </label>
        <Color label="Цвет аватара" value={p.accent ?? "#8b5cf6"} onChange={(accent) => patchProps({ accent })} />
      </div>
      <div className="li-row">
        <label className="li-grow" title="id картинки из «Файлов для монтажа»">
          <span className="li-cap">Фото аватара (id)</span>
          <input className="input" value={p.avatarImage ?? ""} onChange={(e) => patchProps({ avatarImage: e.target.value.trim() || null })} />
        </label>
        {post ? (
          <label className="li-grow" title="id картинки из «Файлов для монтажа»; рамка 5:3">
            <span className="li-cap">Картинка поста (id)</span>
            <input className="input" value={p.image ?? ""} onChange={(e) => patchProps({ image: e.target.value.trim() || null })} />
          </label>
        ) : null}
      </div>
      <div className="li-row">
        <Num label="Лайков" value={p.likes ?? 0} min={0} max={1e10} onChange={(likes) => patchProps({ likes: Math.round(likes) })} />
        <label className="li-num" title="Пусто — число стоит; иначе считает вверх по ходу слоя">
          <span>Досчитать до</span>
          <input className="input" value={p.likesTo ?? ""} onChange={(e) => patchProps({ likesTo: countOrNull(e.target.value) })} />
        </label>
        <Toggle label="Лайк стоит" checked={Boolean(p.liked)} onChange={(liked) => patchProps({ liked })} />
      </div>
      {post ? (
        <div className="li-row">
          <label className="li-num" title="Пусто — не показывать">
            <span>Ответов</span>
            <input className="input" value={p.replies ?? ""} onChange={(e) => patchProps({ replies: countOrNull(e.target.value) })} />
          </label>
          <label className="li-num" title="Пусто — не показывать">
            <span>Репостов</span>
            <input className="input" value={p.reposts ?? ""} onChange={(e) => patchProps({ reposts: countOrNull(e.target.value) })} />
          </label>
          <Toggle label="Галочка" checked={Boolean(p.verified)} onChange={(verified) => patchProps({ verified })} />
        </div>
      ) : null}
      <div className="li-row">
        <Num label="Ширина" value={p.w ?? 900} min={400} max={refWidth} step={10} onChange={(w) => patchProps({ w })} />
        <Num label="Строк не больше" value={p.maxLines ?? SOCIAL_MAX_LINES} min={1} max={20} onChange={(maxLines) => patchProps({ maxLines: Math.round(maxLines) })} />
      </div>
    </Section>
  );
}
