# -*- coding: utf-8 -*-
"""Готовит кеш разбора эталонного документа для режима примера.

Разбор занимает около минуты — посетитель демонстрации столько ждать не должен,
поэтому результат считается заранее и кладётся в runs/example.json.

Запуск:  python scripts/build_example.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

import app       # noqa: E402
import formal    # noqa: E402
import pipeline  # noqa: E402


def main() -> int:
    src = app.EXAMPLE_DOC
    if not src.exists():
        print(f'Нет файла {src} — режим примера отключён.')
        return 2

    text = src.read_text(encoding='utf-8')
    out = ROOT / 'runs' / 'example.json'

    # Правила формальных проверок меняются чаще, чем нужен новый прогон модели:
    # с этим флагом разбор берётся из кеша, пересчитывается только он и метрика.
    if '--refresh-formal' in sys.argv and out.exists():
        cached = json.loads(out.read_text(encoding='utf-8'))
        result = {k: cached.get(k, []) for k in
                  ('defects', 'questions', 'fixes', 'assumptions')}
        elapsed = 0.0
        print(f'Разбор взят из кеша, пересчитываю формальные проверки...')
    else:
        print(f'Разбираю {src.name}, {len(text)} знаков...')
        started = time.monotonic()
        result = pipeline.analyze_document(
            text, progress=lambda m: print('   ', m, flush=True))
        elapsed = time.monotonic() - started

    formal_hits = formal.check(text)
    payload = {**result, 'formal': formal_hits, 'source': text,
               'match': app.match_block(result['defects'], formal_hits)}

    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding='utf-8')

    m = payload['match']
    print(f'\nготово за {elapsed:.0f} с -> {out}')
    print(f'  дефектов {len(result["defects"])}, вопросов {len(result["questions"])}, '
          f'исправлений {len(result["fixes"])}, допущений {len(result["assumptions"])}')
    print(f'  формальных находок {len(formal_hits)}')
    print(f'  блок совпадения: {m["hits"]} из {m["total"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
