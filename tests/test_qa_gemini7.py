"""QA 7 Gemini-монтажёра (клип 899, плейбук vtuber-story).

1. Ответ на запрос со схемой бывает битым: одна запись вместо шести, запись без x1, число с бесконечными нулями
   до обрыва. Разбор считал это «аватара нет» и клал в кэш — теперь это обрыв ответа: повтор, потом сбой.
2. Отчёт агента начинался с эха служебной подписи к картинке («Описание: кадр приложен следующей картинкой…»).
"""

from __future__ import annotations

import json

from app.ai import avatar_box, editor_agent
from test_qa_gemini6 import CORNER, N, FakeAvatarGemini, _approx, _k, _parts


def test_partial_answer_is_a_failure_with_retry_not_no_avatar():
    one_of_six = json.dumps({"frames": [{"frame": 1, "avatar": True, "x0": 0.7, "y0": 0.4, "y1": 0.9}]})
    runaway = '{"frames": [{"frame": 1, "avatar": true, "x0": 0.71000000000000000000000000000'
    good = json.dumps({"frames": [{"frame": i + 1, "avatar": True, **CORNER} for i in range(N)]})
    fake = FakeAvatarGemini([one_of_six, runaway, good])
    boxes = avatar_box.ask_boxes(fake, _parts())
    assert len(fake.calls) == 3 and all(_approx(b, CORNER) for b in boxes)
    assert fake.calls[0]["generationConfig"]["maxOutputTokens"] == avatar_box.MAX_ANSWER_TOKENS
    # Строгий разбор: запись «аватар есть» без полной рамки — обрыв; явное avatar:false — ответ.
    missing_x1 = [{"frame": i + 1, "avatar": True, "x0": 0.7, "y0": 0.4, "y1": 0.9} for i in range(N)]
    assert avatar_box.parse_frames(json.dumps({"frames": missing_x1}), N, strict=True) == ([None] * N, False)
    mixed = [{"frame": 1, "avatar": False}] + [{"frame": i + 2, "avatar": True, **_k(CORNER)} for i in range(N - 1)]
    boxes, ok = avatar_box.parse_frames(json.dumps({"frames": mixed}), N, strict=True)
    assert ok and boxes[0] is None and _approx(boxes[1], CORNER)
    # Так было в QA 7: одна запись из шести, остальные кадры — пропуски, нестрогий разбор видел «аватара нет».
    assert avatar_box.parse_frames(one_of_six, N) == ([None] * N, True)
    assert avatar_box.parse_frames(one_of_six, N, strict=True)[1] is False


def test_report_without_service_caption_echo():
    leaked = ("я. Описание: кадр приложен следующей картинкой. \n"
              "Внимательно посмотри на неё, чтобы проверить монтаж. Без этого сервис не примет твой ответ) \n"
              "Готово. Отчёт по монтажу:\n\n1. Вырезал паузы.")
    assert editor_agent._strip_leaks(leaked) == "Готово. Отчёт по монтажу:\n\n1. Вырезал паузы."
    same_line = "Описание: кадр приложен следующей картинкой. (Эта картинка — contact_sheet. Внимательно посмотри на " \
                "неё. Без этого сервис не примет твой ответ) Готово, хук на месте."
    assert editor_agent._strip_leaks(same_line) == "Готово, хук на месте."
    normal = "Готово. Кадр приложен к отчёту ниже не был — смотри редактор."
    assert editor_agent._strip_leaks(normal) == normal
