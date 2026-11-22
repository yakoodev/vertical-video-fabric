"""Стенд «выдумки распознавания»: Whisper на тишине, шуме, музыке и хвостах тишины после речи.

По ролику «Скрытый слой» (docs/knowledge/youtube/skrytyj-sloj.md): 14 записей без речи (отрицательный контроль),
наши TTS-фразы с 30 с почти тишины в конце, фразы под музыкой и куски дубляжа (положительный контроль).

    python -m tools.asr_bench.run build                       # записи стенда в D:/vvf-offload/asr_bench
    python -m tools.asr_bench.run run --profile provider_ru   # один профиль места вызова → results/<профиль>.json
    python -m tools.asr_bench.run report                      # таблица по всем результатам

Генерация детерминированная (seed), метрики — tools/asr_bench/metrics.py, профили мест вызова — profiles.py.
"""
