# -*- coding: utf-8 -*-
"""Статический слепок готовой страницы — для работы над дизайном.

Зачем: в чат с моделью живое приложение не перенести. Внешние скрипты там
заблокированы, React не загрузится, к бэкенду страница не достучится — вы
увидите пустой экран. Слепок решает это: один файл, вся вёрстка с настоящим
разбором внутри, ни JavaScript, ни сервера. Дизайнерская работа сводится к
правке CSS, что и требуется.

Обратно переносится заменой frontend/styles.css. Если менялась и разметка —
соответствующие правки вносятся в frontend/app.js.

Запуск:  python scripts/build_snapshot.py
Результат: design/snapshot.html
"""
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'runs' / 'example.json'
STYLES = ROOT / 'frontend' / 'styles.css'
OUT = ROOT / 'design' / 'snapshot.html'

DEFECT_TYPES = {
    1: 'Нет критерия проверки',
    2: 'Требование подменено решением',
    3: 'Ссылка на несуществующий носитель',
    4: 'Незакрытый плейсхолдер',
    5: 'Отложенное значение',
    6: 'Открытый список',
    7: 'Нет адресата и срока',
    8: 'Конфликт',
}


def esc(text) -> str:
    return html.escape(str(text or ''))


def with_gaps(text) -> str:
    """Подстановки [значение] подсвечиваются — как в живом интерфейсе."""
    parts = re.split(r'(\[[^\]]+\])', str(text or ''))
    return ''.join(
        f'<span class="gap">{esc(p)}</span>' if p.startswith('[') and p.endswith(']')
        else esc(p)
        for p in parts)


def render(data: dict) -> str:
    chars = len(data.get('source') or '')
    counter = f'{chars:,}'.replace(',', ' ')
    out = ['<div class="wrap">']
    out.append(
        '<header><h1>ТТ-Детектор</h1>'
        '<p>Показывает, из-за каких формулировок в ваших технических требованиях '
        'исполнитель сделает не то.</p></header>')

    out.append(
        '<div class="entry">'
        '<button class="primary">Попробовать на примере</button>'
        '<div class="or">или свой черновик</div>'
        '<div class="dropzone"><textarea placeholder="Вставьте текст ТТ или '
        f'перетащите сюда файл .docx, .txt, .md">{esc(data.get("source"))}</textarea></div>'
        '<div class="row">'
        '<button class="secondary">Разобрать</button>'
        '<button class="secondary">Выбрать файл</button>'
        f'<span class="counter">{counter} / 40 000</span>'
        '</div></div>')

    out.append(
        '<div class="stages">'
        '<div class="stage done"><span class="mark">✓</span>'
        '<span>Формальные проверки</span></div>'
        '<div class="stage done"><span class="mark">✓</span>'
        '<span>Анализ модели</span></div></div>')

    m = data.get('match')
    if m:
        found = ''.join(f'<li>{esc(e["description"])}</li>' for e in m['found'])
        missed = ''.join(f'<li>{esc(e["description"])}</li>' for e in m['missed'])
        out.append(
            '<section><h2>Совпадение с доведённой версией документа</h2>'
            f'<div class="score"><span class="big">{m["hits"]}</span>'
            f'<span class="of">из {m["total"]}</span></div>'
            '<p class="score-note">Столько правок, внесённых автором вручную при '
            'доработке документа, система нашла самостоятельно — не видя '
            'доведённой версии.</p>'
            f'<div class="cols"><div class="found"><h3>Найдено</h3><ul>{found}</ul></div>'
            f'<div class="missed"><h3>Пропущено</h3><ul>{missed}</ul></div></div></section>')

    formal = data.get('formal') or []
    if formal:
        items = ''.join(
            f'<li><span class="m">{esc(h["match"])}</span> — {esc(h["title"])}'
            + (f'<span class="hint"> ({esc(h["note"])})</span>' if h.get('note') else '')
            + '</li>' for h in formal)
        out.append(
            '<section><h2>Формальные проверки</h2><div class="formal">'
            f'<div>Найдено без модели, по правилам: {len(formal)}</div>'
            f'<ul>{items}</ul></div></section>')

    defects = data.get('defects') or []
    if defects:
        groups: dict[int, list] = {}
        for d in defects:
            groups.setdefault(d.get('type') or 0, []).append(d)
        blocks = []
        for t in sorted(groups):
            cards = ''.join(
                f'<div class="card"><blockquote>{esc(d.get("quote"))}</blockquote>'
                f'<div class="why">{esc(d.get("consequence"))}</div></div>'
                for d in groups[t])
            blocks.append(
                f'<div class="type-group"><div class="type-name">'
                f'{esc(DEFECT_TYPES.get(t, "Прочее"))}</div>{cards}</div>')
        out.append(f'<section><h2>Дефекты — {len(defects)}</h2>'
                   + ''.join(blocks) + '</section>')

    questions = data.get('questions') or []
    if questions:
        items = ''.join(f'<li>{esc(q)}</li>' for q in questions)
        out.append(
            f'<section class="questions"><h2>Вопросы исполнителя — {len(questions)}'
            '<button class="copy">Скопировать всё</button></h2>'
            f'<ol>{items}</ol></section>')

    fixes = data.get('fixes') or []
    assumptions = data.get('assumptions') or []
    if fixes or assumptions:
        body = ''.join(
            '<div class="fix">'
            f'<div class="side before"><span class="label">было</span>{esc(f.get("before"))}</div>'
            f'<div class="side after"><span class="label">стало</span>{with_gaps(f.get("after"))}</div>'
            '</div>' for f in fixes)
        if assumptions:
            items = ''.join(f'<li>{with_gaps(a)}</li>' for a in assumptions)
            body += ('<div style="margin-top:24px"><div class="type-name">'
                     'Допущения, которые должен подтвердить заказчик</div>'
                     f'<ul>{items}</ul></div>')
        out.append(f'<section><h2>Исправления</h2>{body}</section>')

    out.append('</div>')
    return '\n'.join(out)


def main() -> int:
    if not CACHE.exists():
        print(f'Нет файла {CACHE}. Сначала: python scripts/build_example.py')
        return 2

    data = json.loads(CACHE.read_text(encoding='utf-8'))
    page = (
        '<!doctype html>\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>ТТ-Детектор</title>\n<style>\n'
        + STYLES.read_text(encoding='utf-8')
        + '\n</style>\n</head>\n<body>\n'
        + render(data)
        + '\n</body>\n</html>\n')

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page, encoding='utf-8')
    print(f'{OUT}  —  {len(page) // 1024} КБ, без скриптов и без бэкенда')
    print('Открывается двойным щелчком. Для работы над дизайном — этот файл.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
