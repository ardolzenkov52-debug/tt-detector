# -*- coding: utf-8 -*-
"""Шаг 2 ТЗ — подсчёт совпадения с эталонной разметкой (§7).

Правка считается найденной, если хотя бы одно из её keywords встречается
(без учёта регистра) в поле quote или consequence любого дефекта из выдачи
модели. Автоматический diff двух версий не используется: он даёт шум.

Запуск:  python scripts/score.py runs/gate_run_1.json
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITS = ROOT / 'data' / 'reference_edits.json'


def normalize(text: str) -> str:
    """ё → е, схлопывание пробелов, нижний регистр — чтобы разметка не зависела
    от типографики документа."""
    return re.sub(r'\s+', ' ', (text or '').lower().replace('ё', 'е')).strip()


def haystack(defects: list) -> str:
    """Всё, по чему разрешено искать ключевые слова: цитаты и последствия."""
    parts = []
    for d in defects:
        parts.append(d.get('quote', ''))
        parts.append(d.get('consequence', ''))
    return normalize(' \n '.join(parts))


def score(defects: list, edits: list) -> dict:
    hay = haystack(defects)
    found, missed = [], []
    for edit in edits:
        hits = [kw for kw in edit.get('keywords', []) if normalize(kw) in hay]
        record = {
            'id': edit['id'],
            'description': edit['description'],
            'defect_type': edit.get('defect_type'),
            'matched_keywords': hits,
        }
        (found if hits else missed).append(record)
    return {'found': found, 'missed': missed,
            'total': len(edits), 'hits': len(found)}


def report(result: dict, *, verbose: bool = True) -> None:
    print(f"\nНайдено {result['hits']} из {result['total']}\n")
    if verbose:
        print('--- Найдено ---')
        for e in result['found']:
            kw = ', '.join(f'«{k}»' for k in e['matched_keywords'][:3])
            print(f"  {e['id']}  {e['description']}")
            print(f"        по ключам: {kw}")
        print('\n--- Пропущено ---')
        for e in result['missed']:
            print(f"  {e['id']}  {e['description']}")

    ratio = result['hits'] / result['total'] if result['total'] else 0
    print(f"\nДоля: {ratio:.0%}. Ориентир ТЗ — около 10 из 13.")
    if result['hits'] < 8:
        print('Заметно ниже ориентира. По §7 сначала разбирается разметка '
              'reference_edits.json, промпт под цифру не подгоняется.')


def load_defects(path: Path) -> list:
    data = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(data, dict):
        return data.get('defects', [])
    raise ValueError(f'{path.name}: ожидался объект с полем defects')


def main() -> int:
    if not EDITS.exists():
        print(f'Нет файла {EDITS} — режим примера отключён.')
        return 2
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    edits = json.loads(EDITS.read_text(encoding='utf-8'))['edits']

    paths = [Path(p) for p in sys.argv[1:]]
    results = []
    for path in paths:
        if not path.exists():
            print(f'Нет файла {path}')
            return 2
        result = score(load_defects(path), edits)
        results.append((path, result))
        print(f'\n=== {path.name} ===')
        report(result, verbose=len(paths) == 1)

    if len(paths) > 1:
        print('\n=== Сводка по прогонам ===')
        for path, result in results:
            ids = ' '.join(e['id'] for e in result['found'])
            print(f"  {path.name}: {result['hits']}/{result['total']}  [{ids}]")
        stable = set.intersection(*({e['id'] for e in r['found']} for _, r in results))
        union = set().union(*({e['id'] for e in r['found']} for _, r in results))
        print(f'\n  во всех прогонах: {len(stable)}  {" ".join(sorted(stable))}')
        print(f'  хотя бы в одном:  {len(union)}  {" ".join(sorted(union))}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
