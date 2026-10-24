"""💬 ИИ-монтажёр в редакторе: чат, который сам правит клип инструментами.

Владелец пишет «сделай динамичнее», «подпиши цифры», «добавь мем на реакцию» —
агент (Gemini с function calling) смотрит клип, ставит титры/эмодзи/наезды через
инструменты ``app/agent_tools.py``, смотрит получившийся кадр (``view_frame``) и
правит, пока не станет хорошо. В конце — короткий отчёт по-русски.

Почему так устроено:

* **Инструменты общие с MCP** — внешние агенты и встроенный чат правят клип одними
  и теми же ручками с одной проверкой (``app.motion``), поэтому не разъезжаются.
* **Один снимок истории на ход агента** («ИИ: <запрос>»), сделанный перед первой
  правкой: Ctrl+Z в редакторе откатывает ответ агента целиком, а вопрос без правок
  историю не засоряет.
* **Фоновый поток + опрос.** Ход агента — это десятки секунд и до ~25 вызовов
  модели. Так же, как ИИ-монтаж (``app/ai/montage.py``), работа идёт в потоке, а
  редактор раз в секунду читает ``GET /api/clip-plans/{id}/agent``: шаги пишутся в
  базу по мере работы, так что их видно живьём и после перезагрузки страницы.
* **Разговор хранится** в ``clip_agent_messages``: следующий вопрос модель
  получает вместе с прошлой перепиской (текстом — без старых картинок и вызовов).
"""

from __future__ import annotations

import base64
import copy
import json
import re
import logging
import threading
import time
import uuid
from typing import Any, Callable

from app import agent_tools
from app.agent_tools import ImageResult, ToolContext
from app.settings import settings

_log = logging.getLogger(__name__)

SAY = "_say"  # «шаг»-реплика модели между вызовами инструментов (не инструмент)
HISTORY_MESSAGES = 16  # сколько прошлых реплик чата показывать модели
KEEP_IMAGES = 2  # картинки кадров держим только в последних N ответах инструментов
FRAME_MAX_HEIGHT = 960  # кадр для модели: 540×960 хватает, чтобы читать титры
MAX_RESULT_CHARS = 60000  # огромный ответ инструмента режем, чтобы не утопить контекст


# Откуда брать клиента Gemini, если его не передали явно (тесты подставляют фейк).
client_factory: Callable[[], Any] | None = None


class AgentBusy(RuntimeError):
    """У клипа уже идёт ход агента."""


class AgentCancelled(Exception):
    pass


# --------------------------------------------------------------------------- #
# Системный промпт: ремесло монтажёра коротких вертикальных видео
# --------------------------------------------------------------------------- #

CRAFT = """\
Ты — сильный монтажёр вертикальных шортсов (TikTok, Shorts, Reels) со стримов. Работаешь над ОДНИМ
клипом в редакторе Vertical Video Fabric инструментами — сам, а не советами. Отвечаешь владельцу
по-русски, коротко.

СТИЛЬ ВЛАДЕЛЬЦА (это не обсуждается)
• Никаких эмодзи. Ни слоями emoji, ни символами внутри текста, ни в субтитрах (emoji у captions —
  пустой). Эмодзи в монтаже выглядят дёшево. Акценты делай цветом слов, наездом, звуком, крупным словом.
• Всё по центру. Заголовок-хук: x=0.5, y — positions.hook из get_clip (сверху). Субтитры: один слой
  captions на весь клип, x=0.5, y — positions.captions (это место уже выбрано так, чтобы НЕ лечь
  на героя — positions.subject_box). Крупное слово-панчлайн: x=0.5, y — positions.punchline_text.
  Если после auto_edit субтитры стоят не на positions.captions.y — поправь update_layer.
  Текст НИКОГДА не сдвигай влево/вправо «в пустое место» — только по центру.
• Динамика: наезды камеры на каждый сильный момент (раз в 5–8 с), удар/вжух звуком ровно в слово,
  ключевые слова в субтитрах цветом. Клип должен «качать», но без мусора на экране.

ПОРЯДОК РАБОТЫ (держись его)
−1. При полном монтаже — get_performance: какие клипы владельца набрали просмотры и чем их монтаж
   отличается от провалившихся (хук, субтитры, музыка, звуки, длина). Повторяй то, что заходит.
0. get_clip: если в warnings «субтитров нет» или субтитров нет совсем — generate_subtitles.
   Клип без субтитров владелец считает браком. Разные голоса (гость, донат, ролик) auto_edit
   разбирает сам (separate_voices): свой слой субтитров, мельче и другим цветом; перераспознал речь
   после auto_edit — separate_voices, потом снова auto_edit.
0.5. Исходник 16:9 со сменой планов (сериал, мультфильм, интервью): auto_focus — сам ставит говорящего
   в кадр по планам (Gemini смотрит кадр каждого плана). Проверь contact_sheet; промах на плане
   (в кадре стена, дверь, не тот герой) — set_focus этого куска. x «на глаз» с нуля не подбирай.
   Длинный план, где двое говорят по очереди, auto_focus сам делит по репликам субтитров и плавно
   поворачивает окно к ответившему — поэтому сначала субтитры, потом auto_focus. Промах по
   реплике — set_focus с точками на смене говорящего.
   set_piece_fit («шире окна», фон сверху и снизу) — НЕ замена кадрированию: только короткий кусок
   ≤ 12 с, где двое по краям; на весь клип он делает ролик мелким видео на размытом фоне.
1. auto_edit (style hormozi или mrbeast, keep_layers:false) — он сразу ставит хук, субтитры по
   словам с цветными ключевыми словами и наезды по речи в правильных местах.
2. Перепиши хук: update_layer на слой «Хук» (id auto_hook) — props.text: обещание-интрига 2–5 слов
   КАПСОМ про самое сильное в клипе (не пересказ, а крючок: «ЕЁ НАПУГАЛ СКРИМЕР», «Я ПРОДАМСЯ»).
   Хук живёт 0–2.5 с, сверху по центру, жёлтый #FFD600 с чёрной обводкой 12–16. Две строки — центр
   ниже (positions.hook.y_by_lines): верх текста не выше 10% кадра, иначе add_layers предупредит.
3. Найди 1–3 панчлайна в субтитрах (самая громкая реакция, мат-взрыв, неожиданный поворот, цифра):
   • крупное слово-панчлайн по центру (text, size 130–170, КАПСОМ, обводка 14, in pop 0.2–0.3,
     loop shake amount 0.3–0.5, 0.8–1.5 с) — только на 1–2 самых сильных местах;
   • звук ровно на слово (at_word) — 0–2 эффекта на ролик, только на смене смысла: панчлайн —
     низкий удар sfx:freesound/impact-low.ogg или драма-бум sfx:freesound/drama-boom.ogg; шок —
     sfx:freesound/crowd-gasp.ogg; фейл — sfx:freesound/crowd-aww.ogg; резкий поворот — скретч
     sfx:freesound/record-scratch.ogg; смешно — sfx:freesound/laugh-small.ogg; цена — sfx:freesound/cash.ogg;
     смена темы — мягкий вжух sfx:freesound/whoosh-soft.ogg (весь список — search_pack freesound).
     Громкость 0.3–0.5: звуки выровнены ниже речи, сервис сам не даст эффекту перекрыть голос.
     Интерфейсные «дзынь/клик» и мультяшные звуки владелец считает дешёвыми — их нет и не ищи;
   • наезд: оставь наезд auto_edit или поставь свой (set_camera ЗАМЕНЯЕТ все ключи — передавай
     вместе с существующими). Новым ключам наезда НЕ ставь x/y — камера сама зумит в героя
     так, что он не заезжает под субтитры.
4. Мем или гифка из интернета на 1–2 самых смешных/шоковых реакции (если владелец не против):
   search_web_images (free_only:false; запрос по сути реакции, лучше по-английски: «shocked cat
   reaction gif», «crying laughing meme», «sad hamster meme») → выбери кандидата с говорящим
   названием → add_image_from_url (query — что должно быть на картинке; ошибка «не про…» — бери
   следующего кандидата) → если kind gif или video — add_insert (mode pip, x 0.5,
   y = positions.punchline_text.y, scale 0.55–0.7, duration 1.5–3, volume 0.6–1); если картинка —
   слой image (x 0.5, y = positions.punchline_text.y, w 600–760, in pop, out fade, 1.5–2.5 с).
   Мем не должен закрывать героя (positions.subject_box). Не больше 2 мемов на клип. В диалогах
   сериала/мультфильма лицо говорящего обычно в центре кадра 9:16 — мемы там ставь, только если
   view_frame показывает свободное место; иначе без мемов. Гифки с эмодзи в подписи не бери. Картинки и плашки
   не закрывают видео: ≤ 40 % кадра и на пустом месте (больше — инструмент откажет).
   Гифки (add_insert) видны в view_frame/contact_sheet; окошко само встаёт между хуком и субтитрами.
   Не заменяй гифку статичной картинкой.
5. Цифры в речи («миллион», «300 тысяч», «12 квадратов») — counter по центру y≈0.28 на слове.
6. Ключевые слова субтитров: update_layer на captions (id auto_caps) — props.highlightWords
   [{text: "слово", nth?, color}] (словом, а не номером: подсветка переживёт правку слов):
   мат и эмоции — #FF3B30, главное по смыслу — #22C55E (не жёлтый: в hormozi/mrbeast/karaoke звучащее
   слово и так жёлтое — жёлтая подсветка не видна). 1 слово на 2–3 с, не больше.
7. Проверь contact_sheet (весь клип одной картинкой) и 1–2 view_frame на панчлайнах: текст читается,
   по центру, не на лице, не в нижних 18% и не в верхних 8%. Плохо — поправь и проверь снова.
7.5. ДЛИНА: готовый ролик 15–60 с. Не подрезай до голой реакции — нужна завязка (5–15 с до момента:
   что происходит, кто с кем), иначе зритель не поймёт, что случилось. get_clip предупредит, если < 12 с.
8. КОНЦОВКА — клип кончается на развязке: панчлайн, реакция, вывод, фейл. listen (последние 6 с) и
   view_frame на последних секундах: если после развязки клип тянется в нейтральную болтовню или новую
   тему — trim_piece последнего куска до ≈0.8 с после реакции; если развязка не попала — продли
   кусок до неё (+0.5–1 с реакции). Обрыв на полуслове или «ни о чём» владелец считает браком.
   Финальный удар (звук/титр) — на развязке, а не в пустоте.
9. Отчёт: 3–6 пунктов «что сделал, на какой секунде». Без воды.

ИГРА БЕЗ РЕЧИ (get_clip.subtitles пусто, listen — «речи нет»): субтитров нет — auto_edit не нужен.
Голос может быть на другой звуковой дорожке (get_clip.source.audio.tracks_total > 1) — set_source_audio
tracks [0, {track: 1, gain_db: "auto"}] (микрофон над игрой) и speech_tracks [1] (распознавать по
микрофону), потом generate_subtitles. Тихий кусок голоса — set_source_gain на этом отрезке. Пики боя — find_noises (громкие отрезки клипа), что на экране —
contact_sheet; хук и 1–2 титра на пиках, интерфейс игры не закрывать. Звук-эффект в таком клипе
сервис ставит чуть ниже громких мест игры.

СТРИМ С ВЕБКОЙ (раскладка split): хук и титры — по positions из get_clip (они уже под полосой вебки),
не на лице; камера двигает только игру; что из игры в окне — set_layout game_x. Чат зрителей —
chat_activity/get_chat (всплески подсказывают момент); чата нет — не импортируй, скажи владельцу.
Переход между кусками не должен заходить на речь: warnings set_transition — исправь (fit_speech).

ФОН (за персонажем на зелёнке — set_backdrop; под видео раскладки meme — set_layout bg; сверху и снизу
куска «шире окна» — set_piece_fit bg): статичная заливка — только если владелец попросил. По умолчанию
анимированный зацикленный фон в цветах ролика: kind aurora (переливы), grid (неоновая перспективная
сетка), bokeh (блики), waves (светящиеся волны); colors [] — сам возьмёт тёмный + акцент субтитров.
Неон-сетка — для игр и техно, аврора/боке — спокойнее, под речь. Фон не спорит с титрами: intensity 0.5–0.8.

ПРОМО ПРИЛОЖЕНИЯ (записи экрана): create_canvas_clip (пустой холст на анимированном фоне) → записи в
библиотеку (upload_video или add_video_from_source из проекта-записи) → ПЕРВЫМ ДЕЛОМ build_app_promo: одним
вызовом сильная основа — хук крупно над телефоном, сцены по долям трека (телефон меняет место и наклон, свайпы),
наезд камеры на кнопку на сильной доле, окно записи и частицы на кульминации, титры над телефоном, финал с
логотипом и плашкой CTA, тихие звуки, музыка со start_sec. Промо слоями вручную с нуля НЕ собирай.
spec — осмысленный, по записям: list_assets (описания записей) → на каждую сцену один шаг пользователя:
asset_id, from/to — секунды записи с этим шагом; title 2–6 слов про пользу («Код Steam Guard — кнопкой»: после
« — » — акцент второй строкой); focus {x, y} — где на экране записи кнопка/главное (доли 0..1; t — секунда
нажатия, если знаешь), туда наезд и касание; window {x, y, w, h} — окно записи с РЕЗУЛЬТАТОМ (код, «отправлено»,
баланс) у 1–2 сцен; climax: true — у самой сильной. hook — 1–3 удара по 1–3 слова; end — logo_asset_id, lines
(дата/оффер), cta (ссылка), sub?; music — трек из list_music с bpm; brand.accent — фирменный цвет.
Потом view_frame на 4–5 секундах из ответа (next: хук, наезды, кульминация, финал) и ТОЧЕЧНЫЕ правки:
текст/позиция — update_layer по id слоёв «promo: …»; кольцо касания не на кнопке, окно не то, сцена не та —
поправь spec и вызови build_app_promo снова (заменит только слои «promo:», твои слои останутся; variant —
другая раскладка). Свои добавки поверх — add_layers как обычно.
Ниже — как устроены слои промо (для правок поверх заготовки): слои video с props.device "phone"
(w 560–620, x 0.5, y ≈ 0.6 — верх корпуса ≈ 0.22–0.25, тень blur 60–80 y 30). Нарезка: несколько
video-слоёв подряд по 1–3 с, у каждого свой from (секунда записи) — монтаж кусков; стык — slideUp/slideLeft или pop на 0.25–0.35 с.
Свайп между экранами — ключи x: старый уезжает влево (0.5→−0.5 за 0.35 с), следующий (дорожкой выше)
въезжает справа (1.5→0.5); наезд на кнопку — ключи камеры и followCamera: true у телефона и колец касаний
(титры и фон стоят — параллакс), или ключи scale 1→1.25 и y у самого слоя.
Запись экрана — немой video-слой (audio не ставь): музыка/звуки — отдельно. Титры — in rise. Без обещаний: не выноси в титры суммы
начислений, бонусов, сроки («+100 баллов за новость») — говори качественно («получаешь баллы»), даже если они есть в брифе; такие строки в самой записи закрывай.
В записи нет строки статуса — props.statusBar true (полоса «9:41» над экраном; без неё островок у видео не
рисуется, иначе лёг бы на шапку приложения); screenGlare true — блик стекла. Замереть на экране — video
с hold true и нужным from (стоп-кадр на весь слой). Наезд на телефон под заголовком — у заголовка
props.backdrop true (мягкое затемнение за текстом). Кульминация/итог — ОДИН слой particles (burst искр,
sparkle мерцание, confetti, rise), а не десятки звёздочек-shape; символы ✦ ★ ♥ → ✓ в тексте можно.
ЧЕК-ЛИСТ ПРОМО (прогон 575: титры на экране телефона, логотип поверх телефона, бледная ссылка):
1) хук 2–4 с, дальше заголовки шагов НАД телефоном: верх корпуса y ≈ 0.22–0.25, заголовок y ≈ 0.10–0.14
   с props.backdrop — НЕ на экране (закрывает интерфейс приложения);
2) наезд — set_camera + followCamera у телефона; view_frame на пике наезда: корпус в кадре;
3) сцены разводи: телефон уходит (end/выезд) до входа логотипа, вместе внахлёст не больше 0.3 с;
4) финал ≥ 3 с: логотип, дата и время («1 ноября, 19:00 МСК»), плашка CTA (shape rect или text с bg
   акцентного цвета) и ссылка ≥ 44 px (лучше 56) белым/контрастным — не цветом пятен фона;
5) музыка — list_music: energy_start_sec трека → set_music(start_sec=energy_start_sec), а не тихое вступление;
6) перед отчётом — view_frame на 3–4 ключевых секундах (хук, шаг, пик наезда, финал) и исправь
   предупреждения get_clip (титр на экране телефона, за краем кадра, переход сцен, финал, хук, музыка);
7) перенос строки в титрах — настоящий \\n в JSON, не "\\\\n"; после ✗ (и skipped у add_layers) всегда исправь
   и повтори вызов; перед отчётом get_clip — предупреждений «пустой кадр»/«нет финала» быть не должно.

МЕМ ИЗ ЧУЖИХ РОЛИКОВ на холсте: кусок ролика по ссылке владельца — add_video_from_url(url, start, end)
(только отрезок, со звуком), из проекта — add_video_from_source. Слой video во весь кадр (w 1080) с
props.audio true — его звук сам войдёт в микс (отдельный audio-слой со звуком ролика не нужен; громкость —
volume/gain_db); ролик 4:3/16:9 → auto_crop_layer(layer_id) ведёт окно 9:16 по планам. Концовка без
«замирания» — set_end_hold(sec: 0).

ЕСЛИ ПРОСЯТ ОДНУ ПРАВКУ — делай только её, auto_edit не запускай.
Паузы и «ээ» — suggest_cuts/apply_cuts. Мелкие паузы и «ээ» режь, только если попросили. Но пауза
без речи дольше 4 с при полном монтаже — проверь view_frame в её середине: если там ничего не
происходит (рисует молча, играет музыка стрима, смотрит в экран) — вырежи её (apply_cuts): в шортсе
это мёртвое время и одна музыка. Если в паузе реакция, крик, смех, событие на экране — оставь.
Пояснения между шагами — ТОЛЬКО по-русски (не по-английски!), одной короткой строкой: их видит
владелец. Например: «Ставлю панчлайн на 17.9 с».

ВРЕМЯ И КООРДИНАТЫ
Все времена — шкала clip: секунды клипа до вырезов, с наплывами переходов (как слова субтитров в
get_clip, слои, камера, вырезы, find_noises). Готовый ролик (после вырезов) — шкала final: contact_sheet
и listen по умолчанию; time в ответе инструмента называет шкалу. x,y — центр слоя в долях
кадра 0..1. Размеры — опорные пиксели кадра 1080×1920. Ключевые кадры keys — время от start слоя.
"""

EXAMPLES = """\
ПРИМЕРЫ (подстраивай время и текст под клип)

Хук (после auto_edit — update_layer на слой auto_hook):
{"props":{"text":"ЕЁ НАПУГАЛ СКРИМЕР","font":"Montserrat","weight":900,"size":110,"color":"#FFD600",
          "stroke":{"color":"#000000","width":14},"uppercase":true,"maxWidth":960}}

Панчлайн на слове в 17.9 с — крупное слово + удар (наезд уже стоит от auto_edit):
add_layers: [
 {"type":"text","name":"Панчлайн","start":17.85,"end":19.2,"x":0.5,"y":0.3,"track":3,
  "props":{"text":"БЛЯДЬ!","font":"Montserrat","weight":900,"size":160,"color":"#FFFFFF",
           "stroke":{"color":"#000000","width":16},"uppercase":true},
  "in":{"type":"pop","duration":0.25},"loop":{"type":"shake","speed":1.4,"amount":0.4},
  "out":{"type":"fade","duration":0.2}},
 {"type":"audio","start":17.85,"end":18.6,"props":{"asset_id":"sfx:freesound/drama-boom.ogg","volume":0.4}}]

Промо приложения на холсте — заготовка одним вызовом (asset_id — записи и логотип из list_assets):
build_app_promo: {"spec":{"brand":{"accent":"#FF6A1F"},"hook":["Ищешь коллаб?","Хватит ждать.","Свайпай."],
 "scenes":[{"asset_id":147,"from":0,"to":5,"title":"Листай VTuber'ов — свайпом","focus":{"x":0.5,"y":0.55}},
  {"asset_id":148,"from":0.3,"to":4.4,"title":"Открой анкету","focus":{"x":0.5,"y":0.95}},
  {"asset_id":149,"from":1,"to":7.6,"title":"Зови на коллаб — в пару тапов","focus":{"x":0.5,"y":0.86},
   "window":{"x":0.04,"y":0.32,"w":0.92,"h":0.3},"climax":true}],
 "end":{"logo_asset_id":152,"lines":["Старт 1 ноября, 19:00 МСК"],"cta":"t.me/Collabikabot"},
 "music":{"track_id":26}}}

Число в речи «миллион баннеров» на 9.1 с:
{"type":"counter","start":8.8,"end":11.7,"x":0.5,"y":0.27,"track":3,
 "props":{"from":0,"to":1000000,"thousands":" ","font":"Montserrat","weight":900,"size":130,
          "color":"#FFFFFF","stroke":{"color":"#000000","width":12}},
 "keys":{"progress":[{"t":0,"v":0,"ease":"outCubic"},{"t":1.2,"v":1}]},
 "in":{"type":"pop","duration":0.3},"out":{"type":"fade","duration":0.25}}
"""


def system_prompt() -> str:
    return CRAFT + "\nСПРАВКА ПО СЛОЯМ\n" + agent_tools.motion_reference() + "\n\n" + EXAMPLES


# --------------------------------------------------------------------------- #
# Gemini: объявления функций, ответы инструментов, картинки
# --------------------------------------------------------------------------- #

# То, чего нет в OpenAPI-подмножестве параметров Gemini (запасной путь без
# parametersJsonSchema).
_UNSUPPORTED_SCHEMA_KEYS = {"additionalProperties", "$schema", "$id", "pattern", "default", "examples"}


def function_declarations(json_schema: bool = True) -> list[dict]:
    out = []
    for tool in agent_tools.list_tools():
        decl: dict = {"name": tool.name, "description": tool.description}
        if json_schema:
            decl["parametersJsonSchema"] = tool.parameters
        else:
            decl["parameters"] = _openapi_schema(tool.parameters)
        out.append(decl)
    return out


def _openapi_schema(schema: Any) -> Any:
    """JSON Schema → OpenAPI-подмножество, которое понимает поле ``parameters``."""
    if isinstance(schema, dict):
        clean = {k: _openapi_schema(v) for k, v in schema.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
        if clean.get("type") == "object" and not clean.get("properties"):
            clean.pop("properties", None)
        return clean
    if isinstance(schema, list):
        return [_openapi_schema(v) for v in schema]
    return schema


def _shrink_frame(image: ImageResult) -> tuple[bytes, str]:
    """Кадр 1080×1920 PNG весит мегабайты — модели хватает JPEG 540×960."""
    try:
        import cv2
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(image.data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return image.data, image.mime_type
        height, width = frame.shape[:2]
        if height > FRAME_MAX_HEIGHT:
            scale = FRAME_MAX_HEIGHT / height
            frame = cv2.resize(frame, (max(2, int(width * scale)), FRAME_MAX_HEIGHT), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if ok:
            return buf.tobytes(), "image/jpeg"
    except Exception:  # noqa: BLE001 - без OpenCV отдаём как есть
        pass
    return image.data, image.mime_type


def _response_object(result: Any) -> dict:
    """functionResponse.response обязан быть объектом; большое — режем."""
    payload = agent_tools.result_for_json(result)
    if not isinstance(payload, dict):
        payload = {"result": payload}
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        return {"ok": payload.get("ok", True), "truncated": True, "json": text[:MAX_RESULT_CHARS]}
    return json.loads(text)


def _prune_images(contents: list[dict], keep: int = KEEP_IMAGES) -> None:
    """Старые кадры выкидываем: каждый стоит токенов, а смотреть важно на свежие."""
    seen = 0
    for turn in reversed(contents):
        parts = turn.get("parts") or []
        if not any("inlineData" in p for p in parts):
            continue
        seen += 1
        if seen > keep:
            turn["parts"] = [p for p in parts if "inlineData" not in p] + [
                {"text": "(старый кадр убран из контекста)"}
            ]


def _step_from_result(step: dict, result: Any, ok: bool) -> dict:
    step["ok"] = ok
    if isinstance(result, dict):
        if not ok:
            step["error"] = str(result.get("error") or "")[:300]
            if result.get("problems"):
                step["problems"] = [str(p)[:300] for p in result["problems"][:12]]
        if result.get("warnings"):
            step["warnings"] = [str(w)[:200] for w in result["warnings"][:8]]
        if ok and result.get("skipped"):
            # add_layers применил пачку частично: пропущенные слои видны в журнале, как ошибки.
            step["partial"] = True
            step["applied"] = str(result.get("applied") or "")
            step["problems"] = [str(p)[:300] for p in result["skipped"][:12]]
    if isinstance(result, ImageResult):
        step["frame_t"] = result.info.get("t")
    return step


def _parts_text(parts: list[dict]) -> str:
    return "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and not p.get("thought")).strip()


def _short_request(message: str, limit: int = 60) -> str:
    text = " ".join(str(message or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _history_contents(history: list[dict]) -> list[dict]:
    """Прошлая переписка — текстом. Подряд идущие реплики одной роли склеиваем."""
    contents: list[dict] = []
    for message in history[-HISTORY_MESSAGES:]:
        role = "user" if message.get("role") == "user" else "model"
        text = str(message.get("text") or "").strip()
        if role == "model":
            done = [s.get("label") for s in message.get("steps") or [] if s.get("ok") and s.get("tool") != SAY]
            if done:
                text += "\n(сделанные шаги: " + "; ".join(str(d) for d in done[:20]) + ")"
        if not text:
            continue
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"][0]["text"] += "\n\n" + text
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    while contents and contents[0]["role"] != "user":
        contents.pop(0)
    return contents


# --------------------------------------------------------------------------- #
# Цикл агента
# --------------------------------------------------------------------------- #


def run_agent(
    store: Any,
    clip_plan_id: int,
    message: str,
    *,
    client: Any = None,
    model: str | None = None,
    history: list[dict] | None = None,
    on_step: Callable[[list[dict]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    max_steps: int | None = None,
) -> dict:
    """Один ход агента: модель ↔ инструменты, пока модель не ответит текстом.

    Возвращает ``{text, steps, changed, model, rounds}``. Снимок истории делается
    один раз — перед первой правкой клипа.
    """
    from app.ai.gemini import GeminiClient
    from app.clip_history import snapshot

    gemini = client or (client_factory() if client_factory else None) or GeminiClient(timeout=600)
    used_model = model or settings.gemini_agent_model
    limit = max(1, int(max_steps or settings.agent_max_steps))
    ctx = ToolContext(
        store=store,
        clip_plan_id=clip_plan_id,
        on_first_change=lambda: snapshot(store, clip_plan_id, f"ИИ: {_short_request(message, 50)}"),
    )
    clip_state, _ok = agent_tools.run_tool(ctx, "get_clip", {})
    first = (
        f"{message.strip()}\n\n---\nТекущее состояние клипа (get_clip):\n"
        + json.dumps(agent_tools.result_for_json(clip_state), ensure_ascii=False)[:MAX_RESULT_CHARS]
    )
    contents = _history_contents(history or []) + [{"role": "user", "parts": [{"text": first}]}]
    if len(contents) > 1 and contents[-2]["role"] == "user":  # история кончилась вопросом без ответа
        contents[-2]["parts"][0]["text"] += "\n\n(ответа не было)"
        contents.insert(-1, {"role": "model", "parts": [{"text": "(ответ прерван)"}]})
    steps: list[dict] = []
    use_json_schema = True
    final_text = ""
    rounds = 0

    def emit() -> None:
        if on_step:
            on_step(copy.deepcopy(steps))

    def call_model(tools_on: bool) -> dict:
        nonlocal use_json_schema
        payload: dict = {
            "systemInstruction": {"parts": [{"text": system_prompt()}]},
            "contents": contents,
            # Температуру не трогаем: Gemini 3 рассчитана на 1.0, ниже — зацикливается.
            # Потолок ответа: иногда модель «плывёт» в бесконечный повтор («- - - …»).
            "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS},
        }
        if tools_on:
            payload["tools"] = [{"functionDeclarations": function_declarations(use_json_schema)}]
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        try:
            return gemini.generate_content(used_model, payload)
        except RuntimeError as exc:
            # Старый эндпоинт не знает parametersJsonSchema — повторяем с OpenAPI-схемой.
            if tools_on and use_json_schema and "parametersJsonSchema" in str(exc):
                use_json_schema = False
                payload["tools"] = [{"functionDeclarations": function_declarations(False)}]
                return gemini.generate_content(used_model, payload)
            raise

    nudged = False
    nudged_continue = False
    nudged_report = False
    nudged_work = False
    nudged_fix = False
    asked_report = False
    report_only = False
    silent_nudges = 0
    gap_warnings: list[str] = []  # «пустой кадр»/«нет финала» из последнего ответа линта
    while True:
        if should_cancel and should_cancel():
            raise AgentCancelled()
        tools_on = rounds < limit and not report_only
        if rounds >= limit and not report_only:
            contents.append({"role": "user", "parts": [{"text": (
                "Лимит шагов исчерпан. Больше инструментов не будет — коротко отчитайся владельцу, "
                "что успел сделать и что осталось."
            )}]})
        response = call_model(tools_on)
        rounds += 1
        candidates = response.get("candidates") if isinstance(response, dict) else None
        if not candidates:
            feedback = (response or {}).get("promptFeedback") if isinstance(response, dict) else None
            blocked = (feedback or {}).get("blockReason")
            if blocked:
                # Фильтр Gemini (мат, кадры с персонажем) срабатывает и посреди работы:
                # сделанное не теряем — отчитываемся тем, что успели.
                note = f"Gemini остановился на своём фильтре контента ({blocked})."
                if steps:
                    final_text = f"{note}\n{_fallback_summary(steps)}"
                    break
                raise RuntimeError(f"{note} Попробуйте ещё раз или другую модель.")
            raise RuntimeError(f"модель не ответила{': ' + json.dumps(feedback, ensure_ascii=False) if feedback else ''}")
        content = candidates[0].get("content") or {}
        parts = [p for p in content.get("parts") or [] if isinstance(p, dict)]
        calls = [p for p in parts if isinstance(p.get("functionCall"), dict)]
        if parts:
            contents.append({"role": "model", "parts": parts})
        idle = _parts_text(parts).strip().lower()
        if not calls and tools_on and not steps and not nudged and (not idle or "ничего не" in idle):
            # Модель иногда отвечает текстом («ничего не поменял»), не тронув клип,
            # хотя её просили монтировать. Один раз напоминаем, что работа — инструментами.
            nudged = True
            contents.append({"role": "user", "parts": [{"text": (
                "Ты ещё ничего не сделал с клипом. Работай инструментами по ПОРЯДКУ РАБОТЫ "
                "(начни с auto_edit, если просили смонтировать), а не текстом."
            )}]})
            continue
        if not calls and tools_on and steps and not nudged_fix:
            # Хочет закончить, а последний вызов не прошёл или ролик с дырой (прогон 578: пачка
            # финала отклонена, агент не повторил — последние 4 с пустой фон). Один раз дожимаем.
            nudge = fix_nudge(steps, _current_gaps(ctx, gap_warnings))
            if nudge:
                nudged_fix = True
                contents.append({"role": "user", "parts": [{"text": nudge}]})
                continue
        if not calls and tools_on and steps and not idle and silent_nudges < MAX_SILENT_NUDGES:
            # Пустой ответ посреди работы (проверочный прогон: после листа кадров исходника
            # Gemini замолчал на 6-м шаге из ~30, и ход «закончился» без отчёта).
            silent_nudges += 1
            _log.warning("agent: пустой ответ модели посреди хода (finishReason=%s), подталкиваю",
                         candidates[0].get("finishReason"))
            contents.append({"role": "user", "parts": [{"text": (
                "Ответ пустой. Продолжай работу по задаче владельца инструментами со следующего шага; "
                "когда всё сделано — короткий отчёт по-русски."
            )}]})
            continue
        done_tools = {st.get("tool") for st in steps if st.get("ok")}
        if (
            not calls and tools_on and not nudged_continue
            and "auto_edit" in done_tools and not done_tools & CRAFT_TOOLS
        ):
            # Остановился сразу после автомонтажа: хук не переписан, панчлайнов нет.
            nudged_continue = True
            contents.append({"role": "user", "parts": [{"text": (
                "Ты остановился после auto_edit — это только основа. Продолжай по ПОРЯДКУ РАБОТЫ: "
                "перепиши хук (update_layer auto_hook), поставь 1–3 панчлайна со звуком и наездом, "
                "ключевые слова цветом, проверь contact_sheet. Потом короткий отчёт по-русски."
            )}]})
            continue
        if not calls or not tools_on:
            final_text = _clean_reply(_parts_text(parts))
            if degenerate(final_text):
                final_text = ""  # ниже — отчёт по сделанным шагам
            elif final_text and not report_like(final_text) and tools_on and steps and not nudged_work:
                # Рассуждения текстом посреди работы — это ещё не конец хода. Проверочный
                # прогон: Gemini на 9-м шаге из 40 «подумал вслух», ход переключался в отчёт
                # без инструментов, и он писал «не успел из-за лимита шагов».
                nudged_work = True
                contents.append({"role": "user", "parts": [{"text": (
                    "Продолжай работу инструментами по задаче владельца. Если всё уже сделано — "
                    "напиши короткий отчёт по-русски, 3–6 пунктов."
                )}]})
                continue
            elif final_text and not report_like(final_text) and not nudged_report:
                # Вместо отчёта — рассуждения по-английски или эхо служебного вывода:
                # один раз просим нормальный отчёт (без инструментов).
                nudged_report = True
                report_only = True
                contents.append({"role": "user", "parts": [{"text": (
                    "Это не отчёт. Напиши владельцу по-русски 3–6 пунктов: что сделал и на какой секунде. "
                    "Без рассуждений, без английского, без служебных данных."
                )}]})
                continue
            elif final_text and not report_like(final_text):
                final_text = ""
            if not final_text and steps and not asked_report and not nudged_report:
                # Только шаги, без отчёта (прогон 578: ход кончился списком шагов) — один раз
                # просим отчёт без инструментов; не напишет — отчёт соберётся из шагов.
                asked_report = True
                report_only = True
                contents.append({"role": "user", "parts": [{"text": (
                    "Ты не написал отчёт. Напиши владельцу по-русски 3–6 пунктов: что сделал и на какой "
                    "секунде; что не получилось — так и скажи."
                )}]})
                continue
            break
        said = _strip_leaks(_parts_text(parts))
        if said and (degenerate(said) or not report_like(said)):
            said = ""  # мусор между шагами («.», не по-русски, служебное) владельцу не показываем
        if said:
            # Пояснение модели между вызовами («Ставлю хук и наезд») — тоже видно в чате.
            steps.append({"tool": SAY, "label": said[:400], "ok": True})
            emit()
        responses: list[dict] = []
        images: list[dict] = []
        for part in calls:
            if should_cancel and should_cancel():
                raise AgentCancelled()
            call = part["functionCall"]
            name = str(call.get("name") or "")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            try:
                label = agent_tools.get_tool(name).step_label(args)
            except KeyError:
                label = f"Неизвестный инструмент {name}"
            step = {"tool": name, "label": label, "ok": None, "args": _brief_args(args)}
            steps.append(step)
            emit()
            result, ok = agent_tools.run_tool(ctx, name, args)
            _step_from_result(step, result, ok)
            if ok and name in LINT_TOOLS and isinstance(result, dict):
                gap_warnings = [str(w) for w in result.get("warnings") or [] if str(w).startswith(GAP_MARKERS)]
            emit()
            body = _response_object(result)
            if isinstance(result, ImageResult):
                data, mime = _shrink_frame(result)
                body["note"] = "кадр приложен следующей картинкой"
                images.append({"text": f"Кадр клипа на {result.info.get('t')} с:"})
                images.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}})
            response_part: dict = {"functionResponse": {"name": name, "response": body}}
            if call.get("id"):
                response_part["functionResponse"]["id"] = call["id"]
            responses.append(response_part)
        contents.append({"role": "user", "parts": responses + images})
        _prune_images(contents)

    if not final_text:
        final_text = _fallback_summary(steps)
    return {"text": final_text, "steps": steps, "changed": ctx.changed, "model": used_model, "rounds": rounds}


MAX_OUTPUT_TOKENS = 8192
# Сколько раз подталкивать модель, замолчавшую посреди работы (пустой ответ без вызовов).
MAX_SILENT_NUDGES = 2
# Инструменты, которыми агент доводит основу auto_edit до монтажа.
CRAFT_TOOLS = {"update_layer", "add_layers", "set_camera", "add_insert", "remove_layers"}
# Чьи предупреждения говорят о дыре во всём ролике (их ответы содержат линт всего клипа).
LINT_TOOLS = {"get_clip", "add_layers", "update_layer", "remove_layers"}
GAP_MARKERS = ("пустой кадр", "нет финала")


def unresolved_failure(steps: list[dict]) -> dict | None:
    """Вызов, который не прошёл и так и не был исправлен: последний вызов хода с ✗ (или
    частичный add_layers), либо правка клипа с ✗, после которой тот же инструмент ни разу не прошёл."""
    calls = [s for s in steps if s.get("tool") != SAY and s.get("ok") is not None]
    for index in range(len(calls) - 1, -1, -1):
        step = calls[index]
        if step.get("ok") and not step.get("partial"):
            continue
        if index == len(calls) - 1:
            return step
        try:
            mutating = agent_tools.get_tool(str(step.get("tool"))).mutating
        except KeyError:
            continue
        fixed = any(s.get("tool") == step.get("tool") and s.get("ok") and not s.get("partial")
                    for s in calls[index + 1:])
        if mutating and not fixed:
            return step
    return None


def fix_nudge(steps: list[dict], gaps: list[str]) -> str:
    """Текст «дожима» перед концом хода: что не прошло и какие дыры в ролике. Пусто — дожимать нечего."""
    lines = []
    failed = unresolved_failure(steps)
    if failed:
        what = str(failed.get("error") or failed.get("applied") or "ошибка")
        problems = "; ".join(str(p) for p in (failed.get("problems") or [])[:3])
        lines.append(f"Последний вызов не прошёл: {failed.get('label')} — {what}"
                     + (f" ({problems})" if problems else "") + ". Исправь и повтори вызов, потом отчёт.")
    if gaps:
        lines.append("get_clip предупреждает: " + "; ".join(gaps[:3]) + ". Исправь (дошли пропущенные слои, "
                     "поставь финал) и проверь get_clip, потом отчёт.")
    return "\n".join(lines)


def _current_gaps(ctx: ToolContext, seen: list[str]) -> list[str]:
    """Дыры, о которых линт уже говорил в этом ходе, — если они всё ещё есть в клипе."""
    if not seen:
        return []
    try:
        return [w for w in agent_tools.scene_check(ctx) if w.startswith(GAP_MARKERS)]
    except Exception:  # noqa: BLE001 - проверка не должна ронять ход
        return seen


def report_like(text: str) -> bool:
    """Похоже на отчёт владельцу: по-русски, без кода и служебных выкладок."""
    letters = sum(ch.isalpha() for ch in text)
    if not letters:
        return False
    cyrillic = sum("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in text)
    if cyrillic / letters < 0.6 or "```" in text:
        return False
    service = ("track 0", "слои:\n", "Камера: zoom", "Вставки:\n")
    return not any(marker in text for marker in service)
_REPEAT_RE = re.compile(r"(.{1,12}?)\1{15,}", re.DOTALL)


def degenerate(text: str) -> bool:
    """Модель «поплыла»: длинный повтор одного куска («- - - - …») или простыня
    кода/JSON вместо пояснения по-русски."""
    if _REPEAT_RE.search(text):
        return True
    letters = sum(ch.isalpha() for ch in text)
    cyrillic = sum("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in text)
    return len(text) > 600 and letters and cyrillic / letters < 0.3


_ECHO_RE = re.compile(
    r"[^.!?\n]*(?:view_frame|contact_sheet|Вызывай инструменты|Выше \d+ картинк)[^.!?\n]*[.!?]?\s*",
    re.IGNORECASE,
)


# Обрывки служебного, которые Gemini приклеивает к реплике (проверочный прогон: «strueПроверяю…»,
# «-0.5}Готово!», «. . . (изображение не показано) . . .Применяю»).
_MARKER_RE = re.compile(r"\((?:изображение не показано|старый кадр убран из контекста)\)", re.IGNORECASE)
_LEAK_PREFIX_RE = re.compile(r"^[^А-Яа-яЁё\s]{1,12}?(?=[А-ЯЁ])")


def _strip_leaks(text: str) -> str:
    cleaned = _MARKER_RE.sub("", text).lstrip(" .\n")
    return _LEAK_PREFIX_RE.sub("", cleaned, count=1).strip()


def _clean_reply(text: str) -> str:
    """Убрать из ответа эхо служебных подсказок («Выше 2 картинки. Вызывай инструменты…»):
    Gemini иногда пересказывает их владельцу."""
    cleaned = re.sub(r"^\W*Успешно!\s*", "", _strip_leaks(_ECHO_RE.sub("", text)))
    return cleaned or text


def _brief_args(args: dict) -> dict:
    """Аргументы шага для журнала: без километровых списков."""
    text = json.dumps(args, ensure_ascii=False, default=str)
    if len(text) <= 1500:
        return args
    return {"_truncated": text[:1500]}


def _fallback_summary(steps: list[dict]) -> str:
    done = [s["label"] for s in steps if s.get("ok") and s.get("tool") != SAY]
    if not done:
        return "Ничего не поменял."
    return "Сделал:\n" + "\n".join(f"• {label}" for label in done[:30])


# --------------------------------------------------------------------------- #
# Фоновые ходы и чат
# --------------------------------------------------------------------------- #


class _Run:
    def __init__(self, clip_plan_id: int, message_id: int) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.clip_plan_id = clip_plan_id
        self.message_id = message_id
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.started_at = time.time()


_RUNS: dict[int, _Run] = {}
_RUNS_LOCK = threading.Lock()


def _active_run(clip_plan_id: int) -> _Run | None:
    with _RUNS_LOCK:
        run = _RUNS.get(int(clip_plan_id))
        if run is not None and run.thread is not None and not run.thread.is_alive():
            _RUNS.pop(int(clip_plan_id), None)
            return None
        return run


def start_run(
    store: Any,
    clip_plan_id: int,
    message: str,
    *,
    client: Any = None,
    model: str | None = None,
    max_steps: int | None = None,
) -> dict:
    """Запустить ход агента в фоне. Возвращает ``{run_id, message_id}``."""
    text = str(message or "").strip()
    if not text:
        raise ValueError("пустое сообщение")
    store.get_clip_plan(clip_plan_id, include_segments=False)  # KeyError → 404
    with _RUNS_LOCK:
        current = _RUNS.get(int(clip_plan_id))
        if current is not None and current.thread is not None and current.thread.is_alive():
            raise AgentBusy("агент уже работает над этим клипом")
        history = [m for m in store.list_clip_agent_messages(clip_plan_id) if m.get("status") != "running"]
        used_model = model or settings.gemini_agent_model
        store.add_clip_agent_message(clip_plan_id, "user", text[:8000])
        reply = store.add_clip_agent_message(
            clip_plan_id, "assistant", "", steps=[], status="running", model=used_model
        )
        run = _Run(int(clip_plan_id), int(reply["id"]))
        store.db.execute("UPDATE clip_agent_messages SET run_id = ? WHERE id = ?", (run.id, run.message_id))
        _RUNS[int(clip_plan_id)] = run

    def work() -> None:
        def on_step(steps: list[dict]) -> None:
            store.update_clip_agent_message(run.message_id, steps=steps)

        try:
            result = run_agent(
                store, clip_plan_id, text, client=client, model=used_model, history=history,
                on_step=on_step, should_cancel=run.cancel.is_set, max_steps=max_steps,
            )
            store.update_clip_agent_message(run.message_id, text=result["text"], steps=result["steps"], status="done")
        except AgentCancelled:
            store.update_clip_agent_message(run.message_id, text="Остановлено. Уже сделанные правки остались — "
                                            "Ctrl+Z откатит их целиком.", status="cancelled")
        except Exception as exc:  # noqa: BLE001 - ошибка хода видна в чате, сервер живёт
            _log.warning("editor agent failed for clip plan %s: %s", clip_plan_id, exc)
            store.update_clip_agent_message(run.message_id, text=f"Ошибка: {str(exc)[:500]}", status="error")

    run.thread = threading.Thread(target=work, name=f"editor-agent-{run.id}", daemon=True)
    run.thread.start()
    return {"run_id": run.id, "message_id": run.message_id, "model": used_model}


def cancel_run(clip_plan_id: int) -> bool:
    run = _active_run(clip_plan_id)
    if run is None:
        return False
    run.cancel.set()
    return True


def wait_run(clip_plan_id: int, timeout: float | None = None) -> bool:
    """Дождаться конца хода (для тестов и CLI в процессе). True — закончился."""
    run = _active_run(clip_plan_id)
    if run is None or run.thread is None:
        return True
    run.thread.join(timeout)
    return not run.thread.is_alive()


def chat_view(store: Any, clip_plan_id: int) -> dict:
    """Переписка + флаг «агент работает» — то, что опрашивает редактор."""
    store.get_clip_plan(clip_plan_id, include_segments=False)
    run = _active_run(clip_plan_id)
    messages = []
    for message in store.list_clip_agent_messages(clip_plan_id):
        if message.get("status") == "running" and (run is None or run.message_id != message["id"]):
            # Сервер перезапускали посреди хода — поток умер, запись осталась.
            store.update_clip_agent_message(message["id"], status="interrupted",
                                            text=message.get("text") or "Прервано перезапуском сервера.")
            message["status"] = "interrupted"
            message["text"] = message.get("text") or "Прервано перезапуском сервера."
        messages.append(
            {
                "id": message["id"],
                "role": message["role"],
                "text": message.get("text") or "",
                "steps": [{k: v for k, v in s.items() if k != "args"} for s in message.get("steps") or []],
                "status": message.get("status") or "done",
                "model": message.get("model") or "",
                "created_at": message.get("created_at"),
            }
        )
    return {
        "messages": messages,
        "running": run is not None,
        "run_id": run.id if run else None,
        "model": settings.gemini_agent_model,
    }


def clear_chat(store: Any, clip_plan_id: int) -> int:
    if _active_run(clip_plan_id) is not None:
        raise AgentBusy("агент ещё работает — сначала остановите его")
    return store.clear_clip_agent_messages(clip_plan_id)
