# -*- coding: utf-8 -*-
"""Конвейер разбора документа.

Прямой вопрос «разбери документ целиком» модели среднего класса не по силам:
на тексте в 13 тысяч знаков она делает поверхностный проход и находит два-три
самых заметных дефекта. Замеры на GigaChat-2-Max:

    документ целиком                     2 дефекта, конфликт не найден
    по разделам                          5 дефектов, конфликт не найден
    по разделам + подобранные пары       см. гейт, конфликт найден

Отсюда три прохода:

    1. Формальные проверки  — регулярные выражения, без модели (formal.py).
    2. Проход по разделам   — документ режется на разделы верхнего уровня,
                              каждый разбирается отдельно.
    3. Проход по парам      — кандидаты на конфликт подбираются детерминированно
                              (термин, встречающийся в удалённых друг от друга
                              абзацах), модель лишь сравнивает пару.

Промпт из prompt.md во всех проходах один и тот же; третий проход добавляет к
нему сужающую приписку про тип 8.
"""
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import model

# --- приписки к системному промпту ---------------------------------------

CONFLICT_ONLY = (
    '\n\nСейчас ищи ТОЛЬКО дефекты типа 8 (конфликт): требования, противоречащие '
    'друг другу. Если противоречия нет — верни пустой список defects.'
)

# Тип 2 общим проходом не находится ни разу: на фоне остальных семи типов
# модель его не выделяет. Отдельным сужающим проходом — находится.
SUBSTITUTION_ONLY = (
    '\n\nСейчас ищи ТОЛЬКО дефекты типа 2 (требование подменено решением): места, '
    'где заказчик диктует способ реализации — конкретный алгоритм, формат файла, '
    'протокол, размещение вычислений — вместо того, чтобы задать требуемый '
    'результат. Помни оговорку: если решение продиктовано ограничением, названным '
    'в самом документе, это не дефект. Если таких мест нет — верни пустой '
    'список defects.'
    '\n\nЦитата обязана называть продиктованное решение. Описание архитектуры, '
    'перечисление компонентов и распределение функций между ними — не дефект: '
    'это состав системы, а не навязанный способ. Название компонента само по '
    'себе цитатой быть не может.'
)

# Тип 7, как и тип 2, в общем потоке не выделяется ни разу.
OWNERSHIP_ONLY = (
    '\n\nСейчас ищи ТОЛЬКО дефекты типа 7 (нет адресата и срока): требования, '
    'по которым непонятно, чья это зона ответственности или к какому моменту '
    'работа должна быть выполнена. Цитируй само требование. Описания работы '
    'самой системы, где исполнитель очевиден, дефектом не считай. Если таких '
    'мест нет — верни пустой список defects.'
)

# Цитата короче этого не удерживает дефект: по двум словам нельзя показать
# ни требования, ни последствия. Правило общее, не под конкретный документ.
MIN_QUOTE = 20

# --- параметры разбиения --------------------------------------------------

MIN_SECTION = 200        # раздел короче — приклеиваем к предыдущему
MIN_PARAGRAPH = 80       # блок короче — приклеиваем к предыдущему (пункты списков)
MIN_GAP = 3              # насколько далеко должны отстоять абзацы пары
MAX_PAIRS = 10           # потолок числа сравнений за разбор
TERM_MIN_SECTIONS = 2    # термин интересен, если встречается минимум в стольких
TERM_MAX_SECTIONS = 4    # и не более чем в стольких абзацах
STEM = 6                 # длина псевдоосновы слова
PER_ANCHOR = 2           # сколько партнёров подбирать каждому отрицанию

SECTION_HEAD = re.compile(r'^\s*(\d+)\.?\s+[А-ЯЁ]', re.M)
TERM = re.compile(r'\b[A-ZА-ЯЁ]{3,}(?:\s+[A-ZА-ЯЁ]{2,})?\b')
ACRONYM = re.compile(r'\b[A-ZА-ЯЁ]{3,}\b')
WORD = re.compile(r'\b[а-яё]{6,}\b')

# Абзац, сужающий область действия: «этого мы не делаем».
NEGATION = re.compile(
    r'не\s+предполага\w*|не\s+вход\w+|не\s+выполня\w+|не\s+хран\w+|'
    r'не\s+описыва\w+|не\s+предусм\w+|не\s+завис\w+|не\s+требу\w+|'
    r'вне\s+рамок|не\s+в\s+рамках', re.I)
# Абзац-предписание: «это должно быть».
REQUIREMENT = re.compile(
    r'должн\w+|обязан\w+|включающ\w+|включая|поддержк\w+|требуетс\w+|'
    r'необходим\w+|предусматрива\w+', re.I)
# Слова, общие почти для всякого абзаца этого документа: связи не образуют.
STOP_STEMS = {'систем', 'должна', 'данных', 'работы', 'уровне', 'состав'}


def split_sections(text: str) -> list[str]:
    """Режет документ по заголовкам верхнего уровня («2. Название»)."""
    bounds = [0] + [m.start() for m in SECTION_HEAD.finditer(text)] + [len(text)]
    sections: list[str] = []
    for i in range(len(bounds) - 1):
        part = text[bounds[i]:bounds[i + 1]].strip()
        if not part:
            continue
        if len(part) < MIN_SECTION and sections:
            sections[-1] += '\n\n' + part
        else:
            sections.append(part)
    return sections or [text]


def split_blocks(text: str) -> list[str]:
    """Абзацы, но пункты списков приклеены к своей вводной фразе: после
    конвертации из .docx каждый пункт — отдельный короткий абзац, и по
    отдельности он теряет смысл (так терялась половина конфликта E13)."""
    out: list[str] = []
    for raw in text.split('\n\n'):
        part = raw.strip()
        if not part:
            continue
        if len(part) < MIN_PARAGRAPH and out:
            out[-1] += '\n' + part
        else:
            out.append(part)
    return [b for b in out if len(b) > MIN_PARAGRAPH]


def _keys(par: str) -> set[str]:
    """Псевдоосновы значимых слов плюс аббревиатуры — грубая замена лемматизации."""
    found = {w[:STEM] for w in WORD.findall(par.lower())} - STOP_STEMS
    return found | set(ACRONYM.findall(par))


def _pairs_by_term(blocks: list[str]) -> list[tuple[int, int, str]]:
    """Способ первый: общий редкий термин в разнесённых абзацах.
    Так находится конфликт по SCADA."""
    where: dict[str, set[int]] = defaultdict(set)
    for i, para in enumerate(blocks):
        for term in set(TERM.findall(para)):
            where[term].add(i)

    scored: list[tuple[int, int, int, str]] = []
    for term, idxs in where.items():
        if not TERM_MIN_SECTIONS <= len(idxs) <= TERM_MAX_SECTIONS:
            continue
        ordered = sorted(idxs)
        for x in range(len(ordered)):
            for y in range(x + 1, len(ordered)):
                a, b = ordered[x], ordered[y]
                if b - a >= MIN_GAP:
                    scored.append((b - a, a, b, term))
    scored.sort(key=lambda x: -x[0])
    return [(a, b, term) for _, a, b, term in scored]


def _pairs_by_anchor(blocks: list[str]) -> list[tuple[int, int, str]]:
    """Способ второй: абзац сужает область («не предполагается»), партнёр —
    близкое по словам требование. Так находится конфликт по границам
    моделирования (E13), где общего термина у фрагментов нет."""
    keys = [_keys(b) for b in blocks]
    out: list[tuple[float, int, int, str]] = []
    for a, para in enumerate(blocks):
        if not NEGATION.search(para):
            continue
        scored = []
        for b, other in enumerate(blocks):
            if abs(b - a) < 2 or not REQUIREMENT.search(other):
                continue
            shared = keys[a] & keys[b]
            if not shared:
                continue
            weight = len(shared) / min(len(keys[a]), len(keys[b]))
            scored.append((weight, b, sorted(shared)[0]))
        scored.sort(reverse=True)
        out.extend((w, a, b, term) for w, b, term in scored[:PER_ANCHOR])
    out.sort(reverse=True)
    return [(a, b, term) for _, a, b, term in out]


def candidate_pairs(text: str) -> list[tuple[str, str, str]]:
    """Пары фрагментов на проверку противоречия.

    Два способа подбора дополняют друг друга: по общему термину и от отрицания.
    Каждый по отдельности теряет один из двух конфликтов эталона, вместе —
    находят оба.
    """
    blocks = split_blocks(text)
    if not blocks:
        return []

    merged: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()
    by_anchor = _pairs_by_anchor(blocks)
    by_term = _pairs_by_term(blocks)
    # чередуем, чтобы ни один способ не вытеснил другой из потолка
    for pair in [p for duo in zip(by_anchor, by_term) for p in duo] + \
                by_anchor[len(by_term):] + by_term[len(by_anchor):]:
        a, b, term = pair
        key = (min(a, b), max(a, b))
        if key in seen:
            continue
        seen.add(key)
        merged.append((a, b, term))
        if len(merged) >= MAX_PAIRS:
            break

    return [(term, blocks[a], blocks[b]) for a, b, term in merged]


# --- слияние результатов --------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r'[^\w\s%]', ' ', (text or '').lower().replace('ё', 'е')).strip()


def _tokens(text: str) -> set:
    return {t for t in _norm(text).split() if len(t) > 3}


def same_quote(a: str, b: str, threshold: float = 0.6) -> bool:
    """Две цитаты об одном фрагменте: одна входит в другую либо сильно пересекаются."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= threshold


def quote_is_real(quote: str, text: str) -> bool:
    """Цитата действительно есть в документе.

    Модель иногда сочиняет требование, которого в тексте нет («использовать
    протокол Modbus RTU», «PostgreSQL не ниже 10.x»). Продукт обещает дословную
    цитату: рецензент ищет фразу у себя, не находит — и перестаёт верить всему
    разбору. Поэтому выдумки отсекаются, а не показываются.

    Допускается расхождение по пробелам, регистру, «ё» и обрезке многоточием;
    иначе требуется, чтобы почти все слова цитаты нашлись в документе.
    """
    cleaned = _norm(re.sub(r'\.{2,}|…', ' ', quote or ''))
    if not cleaned:
        return False
    source = _norm(text)
    if cleaned in source:
        return True
    words = [w for w in cleaned.split() if len(w) > 3]
    if not words:
        return False
    present = sum(1 for w in words if w in source)
    return present / len(words) >= 0.9


def is_structural(quote: str, text: str) -> bool:
    """Цитата взята из строки таблицы или из заголовка раздела.

    Требования в таких строках не формулируются — это оглавление и сводка,
    а карточка «дефекта» с ячейкой таблицы бьёт по доверию к разбору.
    """
    needle = _norm(quote)
    if not needle:
        return False
    lines = [ln for ln in text.splitlines() if needle in _norm(ln)]
    if not lines:
        return False
    return all(ln.lstrip().startswith('|') or SECTION_HEAD.match(ln)
               for ln in lines)


def merge(parts: list[dict], source: str = '') -> dict:
    """Склеивает выдачи проходов, соблюдая правило одного дефекта на фрагмент."""
    defects: list[dict] = []
    for part in parts:
        for d in part.get('defects', []):
            if len((d.get('quote') or '').strip()) < MIN_QUOTE:
                continue
            if source and not quote_is_real(d['quote'], source):
                continue
            if source and is_structural(d['quote'], source):
                continue
            if any(same_quote(d['quote'], kept['quote']) for kept in defects):
                continue
            defects.append(d)

    def dedup(key: str) -> list:
        out, seen = [], set()
        for part in parts:
            for item in part.get(key, []):
                marker = _norm(item if isinstance(item, str) else item.get('before', ''))
                if not marker or marker in seen:
                    continue
                seen.add(marker)
                out.append(item)
        return out

    return {
        'defects': defects,
        'questions': dedup('questions'),
        'fixes': dedup('fixes'),
        'assumptions': dedup('assumptions'),
    }


# --- собственно разбор ----------------------------------------------------

def plan(text: str) -> list[tuple[str, str, str]]:
    """Список заданий для модели: (метка, фрагмент, приписка к промпту).

    Составляется детерминированно, без обращений к модели, — поэтому его можно
    посчитать заранее и показать в интерфейсе как объём предстоящей работы.
    """
    tasks: list[tuple[str, str, str]] = []
    sections = split_sections(text)
    for i, section in enumerate(sections, 1):
        tasks.append((f'раздел {i}', section, ''))
    for i, section in enumerate(sections, 1):
        tasks.append((f'раздел {i}, подмена решением', section, SUBSTITUTION_ONLY))
    for i, section in enumerate(sections, 1):
        tasks.append((f'раздел {i}, адресат и срок', section, OWNERSHIP_ONLY))
    for term, first, second in candidate_pairs(text):
        content = f'Фрагмент А:\n{first}\n\nФрагмент Б:\n{second}'
        tasks.append((f'пара [{term}]', content, CONFLICT_ONLY))
    return tasks


def analyze_document(text: str, *, provider: str | None = None,
                     progress=None, workers: int | None = None) -> dict:
    """Полный разбор. Задания независимы, поэтому идут параллельно: последовательно
    это около пяти минут, что для демонстрации неприемлемо."""
    provider = provider or model.resolve_provider()
    say = progress or (lambda *_: None)
    # Бесплатный ГигаЧат отбивает шесть одновременных запросов; три проходят.
    workers = workers or int(os.environ.get('ANALYZE_WORKERS', 3))

    tasks = plan(text)
    say(f'заданий: {len(tasks)}, потоков: {workers}')

    def run(task):
        label, content, extra = task
        try:
            return label, model.analyze(content, provider=provider,
                                        extra_system=extra)
        except Exception as exc:
            return label, exc

    parts: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for label, got in pool.map(run, tasks):
            if isinstance(got, Exception):
                say(f'  {label}: пропущено, {type(got).__name__}')
                continue
            if got['defects']:
                say(f'  {label}: дефектов {len(got["defects"])}')
            parts.append(got)

    return merge(parts, source=text)
