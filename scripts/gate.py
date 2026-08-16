# -*- coding: utf-8 -*-
"""Шаг 1 ТЗ — гейт стабильности.

Прогоняет data/concept_v2.md через промпт N раз (по умолчанию 3), сравнивает
наборы дефектов между прогонами и отдельно проверяет, ловится ли во всех
прогонах конфликт по SCADA.

Запуск:  python scripts/gate.py [runs]
Результат прогонов кладётся в runs/gate_run_*.json для последующего разбора.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

import model  # noqa: E402
import pipeline  # noqa: E402

DOC = ROOT / 'data' / 'concept_v2.md'
OUT_DIR = ROOT / 'runs'
SIMILARITY = 0.5


def norm(text: str) -> str:
    return re.sub(r'[^\w\s%]', ' ', (text or '').lower()).strip()


def tokens(text: str) -> set:
    return {t for t in norm(text).split() if len(t) > 3}


def same_defect(a: dict, b: dict) -> bool:
    """Дефекты считаем одним, если цитаты сильно пересекаются."""
    qa, qb = norm(a.get('quote', '')), norm(b.get('quote', ''))
    if not qa or not qb:
        return False
    if qa in qb or qb in qa:
        return True
    ta, tb = tokens(a.get('quote', '')), tokens(b.get('quote', ''))
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= SIMILARITY


def has_scada_conflict(defects: list) -> dict | None:
    for d in defects:
        blob = f"{d.get('quote', '')} {d.get('consequence', '')}".lower()
        if 'scada' in blob:
            return d
    return None


def cluster(runs: list) -> list:
    """Группируем дефекты всех прогонов в кластеры «один и тот же дефект»."""
    clusters = []
    for run_idx, defects in enumerate(runs):
        for d in defects:
            for c in clusters:
                if any(same_defect(d, m['defect']) for m in c['members']):
                    c['members'].append({'run': run_idx, 'defect': d})
                    c['runs'].add(run_idx)
                    break
            else:
                clusters.append({'members': [{'run': run_idx, 'defect': d}],
                                 'runs': {run_idx}})
    return clusters


def main() -> int:
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    if not DOC.exists():
        print(f'Нет файла {DOC} — гейт не запускается.')
        return 2

    text = DOC.read_text(encoding='utf-8')
    OUT_DIR.mkdir(exist_ok=True)

    provider = model.resolve_provider()
    print(f'Провайдер: {provider} / {model.resolve_model(provider)}')
    print(f'Документ:  {DOC.name}, {len(text)} знаков\n')

    runs = []
    for i in range(n_runs):
        print(f'Прогон {i + 1}/{n_runs}...', flush=True)
        result = pipeline.analyze_document(
            text, progress=lambda m: print(f'    {m}', flush=True))
        (OUT_DIR / f'gate_run_{i + 1}.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        runs.append(result.get('defects', []))

    print('\n=== Объёмы ===')
    for i, defects in enumerate(runs, 1):
        types = sorted({d.get('type') for d in defects})
        print(f'  прогон {i}: дефектов {len(defects)}, типы {types}')

    print('\n=== SCADA-конфликт ===')
    scada_hits = 0
    for i, defects in enumerate(runs, 1):
        hit = has_scada_conflict(defects)
        if hit:
            scada_hits += 1
            print(f'  прогон {i}: НАЙДЕН (тип {hit.get("type")}) — '
                  f'{hit.get("quote", "")[:110]}')
        else:
            print(f'  прогон {i}: НЕ НАЙДЕН')
    print(f'  итого: {scada_hits}/{n_runs}')

    clusters = cluster(runs)
    stable = [c for c in clusters if len(c['runs']) == n_runs]
    unstable = [c for c in clusters if len(c['runs']) < n_runs]

    print(f'\n=== Стабильность ===')
    print(f'  уникальных дефектов всего: {len(clusters)}')
    print(f'  во всех прогонах:          {len(stable)}')
    print(f'  плавающих:                 {len(unstable)}')
    ratio = len(stable) / len(clusters) if clusters else 0
    print(f'  доля стабильного ядра:     {ratio:.0%}')

    print('\n--- Стабильное ядро ---')
    for c in stable:
        d = c['members'][0]['defect']
        print(f'  [тип {d.get("type")}] {d.get("quote", "")[:100]}')

    print('\n--- Плавающие ---')
    for c in sorted(unstable, key=lambda c: -len(c['runs'])):
        d = c['members'][0]['defect']
        seen = ','.join(str(r + 1) for r in sorted(c['runs']))
        print(f'  [{len(c["runs"])}/{n_runs}, прогоны {seen}] '
              f'[тип {d.get("type")}] {d.get("quote", "")[:90]}')

    # Критерий из §8: конфликт по SCADA должен ловиться во всех прогонах.
    # Стабильность сырого списка дефектов порогом не меряется — она падает от
    # роста числа находок, а не от нестабильности сути. Устойчивость по
    # существу считает scripts/score.py по эталонным правкам.
    ok = scada_hits == n_runs
    print(f'\nГЕЙТ: {"ПРОЙДЕН" if ok else "НЕ ПРОЙДЕН"}'
          f'  (конфликт по SCADA {scada_hits}/{n_runs})')
    print('Полнота разбора — scripts/score.py по файлам из runs/.')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
