import { PageHead } from "@/components/ui";

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="help-step">
      <div className="help-step-n">{n}</div>
      <div>
        <strong>{title}</strong>
        <div className="muted" style={{ fontSize: 13.5, marginTop: 4, lineHeight: 1.5 }}>{children}</div>
      </div>
    </div>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="panel help-section">
      <h2 className="help-h2">{title}</h2>
      {children}
    </section>
  );
}

const TOC = [
  ["how", "Как это работает"],
  ["quickstart", "Быстрый старт (вручную)"],
  ["auto", "Авто-конвейер"],
  ["accounts", "Аккаунты и публикация"],
  ["settings", "Настройки и шаринг"],
  ["features", "Полезные фичи"],
  ["statuses", "Статусы"],
  ["faq", "Если что-то не так"],
];

export function HelpPage() {
  return (
    <>
      <PageHead title="Помощь" sub="Как пользоваться сервисом — от загрузки видео до публикации" />

      <nav className="help-toc">
        {TOC.map(([id, label]) => (
          <a key={id} href={`#${id}`}>{label}</a>
        ))}
      </nav>

      <div className="help-wrap">
        <Section id="how" title="Как это работает">
          <p className="muted" style={{ marginTop: 0 }}>
            Сервис превращает длинное видео в готовые вертикальные клипы 9:16 с субтитрами и публикует их
            в YouTube, TikTok и Instagram. Путь: <b>Проект → Анализ (AI) → Кандидаты → Рендер → Публикация</b>.
            Всё это можно сделать вручную по шагам или автоматически на вкладке «Авто».
          </p>
        </Section>

        <Section id="quickstart" title="Быстрый старт (вручную)">
          <div className="help-steps">
            <Step n={1} title="Создать проект">
              <b>Проекты → Добавить</b>: вставьте ссылку (mp4/YouTube/Twitch/Smotvibe) или загрузите файл.
            </Step>
            <Step n={2} title="Вкладка «Исходник»">
              При необходимости обрежьте чёрные полосы (кнопка «🔍 Найти полосы») и «Сохранить кадр».
              Затем выберите <b>пресет анализа</b> (под контент), провайдер <b>gemini</b> и нажмите
              «Анализировать». Галка «Транскрипт (Whisper)» повышает точность нарезки.
            </Step>
            <Step n={3} title="Вкладка «Кандидаты»">
              AI-нарезка → готовые клипы. Слева большое превью с рамкой 9:16 и зонами баннера/субтитров,
              снизу таймлайн, справа панель «Рендер». Настройте лук, субтитры (движок + стиль + положение),
              баннер, музыку, зеркало. Кнопка <b>«🎯 Авто-фокус»</b> наводит кадр на лица/движение.
              Отметьте нужные чипы и нажмите <b>«▶ Рендерить выбранные»</b>.
            </Step>
            <Step n={4} title="Вкладка «Клипы»">
              Готовые клипы. У каждого — «Опубликовать» и «Удалить».
            </Step>
            <Step n={5} title="Публикация">
              «Опубликовать» → выберите аккаунт(ы) и приватность → отправьте. Статус — на странице «Задачи».
            </Step>
          </div>
        </Section>

        <Section id="auto" title="Авто-конвейер (вкладка «Авто»)">
          <p className="muted" style={{ marginTop: 0 }}>
            Всё вышеперечисленное одной кнопкой: вставьте ссылку, выберите пресет анализа, оформление
            (лук, субтитры, баннер, музыка, зеркало) и аккаунты для публикации. Поле «Интервал, ч»:
            <b> 0</b> = опубликовать сразу, иначе клипы выходят по расписанию с заданным шагом. Нажмите
            «▶ Запустить» — сервис скачает, проанализирует, отрендерит и опубликует сам. Ход — на «Задачи» и «Авто».
          </p>
        </Section>

        <Section id="accounts" title="Аккаунты и публикация">
          <p className="muted" style={{ marginTop: 0 }}>
            <b>Аккаунты → Добавить</b>: платформа (youtube / tiktok / instagram), метка, cookies залогиненного
            аккаунта и (по желанию) прокси.
          </p>
          <ul className="help-ul">
            <li>Минимум cookies: YouTube — <code>SID, HSID, SSID, APISID, SAPISID</code>; TikTok и Instagram — <code>sessionid</code>.</li>
            <li>Статус <b>«готов»</b> = обязательные cookies распознаны.</li>
            <li>YouTube-сессия <b>самообновляется</b> после каждой загрузки — cookies живут долго.</li>
            <li><b>Прокси — на каждый аккаунт свой</b> (приоритет над глобальным). Желательно тот же регион/IP, что при снятии cookies.</li>
            <li>Cookies — это доступ к аккаунту: не пересылайте их в мессенджеры, вставляйте только в поле сервиса.</li>
          </ul>
        </Section>

        <Section id="settings" title="Настройки и шаринг">
          <p className="muted" style={{ marginTop: 0 }}>
            Сервис уже идёт с заготовками. В <b>Настройках</b> можно добавить: луки рендера, баннеры, музыку,
            стили субтитров, промпты для анализа, значения по умолчанию.
          </p>
          <ul className="help-ul">
            <li><b>Экспорт/импорт</b> (вкладка в Настройках): скачать бандл с пресетами/субтитрами/промптами
              (и опц. аккаунтами) и передать коллеге — он загрузит и получит те же заготовки.</li>
          </ul>
        </Section>

        <Section id="features" title="Полезные фичи">
          <ul className="help-ul">
            <li><b>🪞 Зеркало (лево↔право)</b> — отражает видео по горизонтали (репост отличается от оригинала).</li>
            <li><b>Умный кадр / фокус</b> — рамка 9:16 следит за лицом/движением; можно поправить вручную или детектором.</li>
            <li><b>Транскрипт (Whisper)</b> в анализе — точнее границы нарезки и цитаты; кэшируется на исходнике.</li>
            <li><b>Кроп полос</b> — обрезка чёрных рамок у исходника, применяется ко всем клипам проекта.</li>
          </ul>
        </Section>

        <Section id="statuses" title="Статусы">
          <div className="help-table-wrap">
            <table className="help-table">
              <tbody>
                <tr><td><code>queued</code></td><td>в очереди, ждёт обработки</td></tr>
                <tr><td><code>running / rendering / analyzing</code></td><td>выполняется</td></tr>
                <tr><td><code>succeeded / done</code></td><td>успешно</td></tr>
                <tr><td><code>failed</code></td><td>ошибка (текст — в задаче)</td></tr>
                <tr><td><code>needs_reauth</code></td><td>cookies аккаунта больше не принимаются — обновите их</td></tr>
              </tbody>
            </table>
          </div>
        </Section>

        <Section id="faq" title="Если что-то не так">
          <ul className="help-ul">
            <li><b>Анализ «high demand»</b> — перегрузка Gemini на стороне Google, не сбой сервиса. Перезапустите анализ.</li>
            <li><b>Анализ долго думает</b> — первый проход распознавания речи (Whisper); результат кэшируется.</li>
            <li><b>Публикация → needs_reauth</b> — обновите cookies аккаунта (Аккаунты).</li>
            <li><b>TikTok/Instagram просит captcha</b> — войдите в браузере, пройдите проверку, снимите свежие cookies.</li>
            <li><b>Кадр «не туда»</b> — поправьте фокус на «Кандидатах» (🎯 Авто-фокус или вручную) и перерендерьте.</li>
            <li><b>Видео с чёрными полосами</b> — обрежьте их на «Исходнике» и сохраните кадр.</li>
          </ul>
        </Section>
      </div>
    </>
  );
}
