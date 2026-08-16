# -*- coding: utf-8 -*-
"""Модуль формальных проверок (§5 ТЗ).

Детерминированные проверки на регулярных выражениях. Никакой модели.
Работает за миллисекунды, результат отдаётся раньше модельного.

Выход: список объектов {rule, match, position}.
"""
import re

# Латинские и кириллические омоглифы для X/Х в шаблонах-заглушках.
X = '[XxХх]'

# Строка-разделитель markdown-таблицы: артефакт конвертации .docx, не дефект.
TABLE_SEPARATOR = re.compile(r'^\s*\|?[\s:|-]*-{3,}[\s:|-]*\|?\s*$')

# (код правила, человекочитаемое название, регулярное выражение)
_RAW_RULES: list[tuple[str, str, str]] = [
    ('placeholder.dashes', 'Незакрытый плейсхолдер: прочерки',
     r'_{3,}|(?<![-\w])-{3,}(?![-\w])'),
    ('placeholder.questions', 'Незакрытый плейсхолдер: вопросительные знаки',
     r'\?{2,}'),
    ('placeholder.date', 'Незакрытый плейсхолдер: шаблон даты',
     rf'{X}{{2}}\.{X}{{2}}\.{X}{{2,4}}'),
    ('placeholder.number', 'Незакрытый плейсхолдер: номер вида № ХХХ',
     rf'№\s*{X}{{2,}}'),
    ('placeholder.percent', 'Незакрытый плейсхолдер: буква вместо числа в процентах',
     rf'\b{X}\s?%'),
    ('placeholder.letter_value', 'Незакрытый плейсхолдер: буква вместо числа',
     rf'(?:за|через|не менее|не более|в течение)\s+{X}\b|'
     rf'\b[NНn]\s*(?:часов|часа|суток|дней|минут|секунд|штук|шт)\b'),

    ('open_list.including', 'Открытый список: «включая, но не ограничиваясь»',
     r'включая,?\s*но\s*не\s*ограничива\w*'),
    ('open_list.etc', 'Открытый список: «и т.п.», «и др.», «и прочее»',
     r'и\s*т\.?\s*п\.?|и\s*т\.?\s*д\.?|и\s*др\.?(?!\w)|и\s*проч\w*|'
     r'и\s*(?:иные|другие|прочие)\b'),
    ('open_list.other_any', 'Открытый список: «а также иные / любым доступным»',
     r'а\s*также\s+(?:иные|любые|другие)\b|люб\w+\s+доступн\w+'),

    ('deferred.later', 'Отложенное значение: «уточняется» / «будет определено»',
     r'уточня\w+|будет\s+определ\w+|определя\w+\s+на\s+этапе|'
     r'подлежит\s+уточнени\w+'),
    ('deferred.per_tz', 'Отложенное значение: «согласно ТЗ» / «не менее заданного»',
     r'согласно\s+(?:ТЗ|техническому\s+заданию)|'
     r'не\s+менее\s+заданного|в\s+соответствии\s+с\s+ТЗ'),
    ('deferred.agreement', 'Отложенное значение: «по согласованию сторон»',
     r'по\s+согласовани\w+\s+сторон|по\s+отдельному\s+соглашени\w+'),

    ('media.picture', 'Ссылка на носитель: рисунок, схема, чертёж',
     r'см\.?\s*(?:рис\w*|схем\w+|чертёж\w*|чертеж\w*)|'
     r'согласно\s+чертеж\w+|на\s+рисунке\s+\d'),
    ('media.appendix', 'Ссылка на носитель: приложение',
     r'см\.?\s*приложени\w+|в\s+приложении\s+[№\d]|'
     r'согласно\s+приложени\w+'),
    ('media.file', 'Ссылка на носитель: видео или изображение',
     r'\b(?:video|image|видеозапис\w*|видеоролик\w*)\b'),

    ('recommended', 'Рекомендация вместо требования',
     r'рекомендуем\w+|целесообразно|желательно'),

    ('no_criterion.adverb', 'Оценка без критерия проверки',
     r'\b(?:быстро|стабильно|надёжно|надежно|своевременно|удобно|оптимально|'
     r'качественно|эффективно|бесперебойно|корректно)\b'),
    ('no_criterion.adjective', 'Свойство без единиц измерения',
     r'\b(?:высок\w+\s+(?:точност\w+|надёжност\w+|надежност\w+|производительност\w+)|'
     r'минимальн\w+\s+задержк\w+|достаточн\w+\s+(?:объём\w*|объем\w*|быстродейств\w+)|'
     r'приемлем\w+\s+\w+|сопоставим\w+\s+\w+)\b'),
]

# Регистр не учитывается: «Рекомендуемый» ловится наравне с «рекомендуемый».
RULES: list[tuple[str, str, re.Pattern]] = [
    (code, title, re.compile(src, re.I)) for code, title, src in _RAW_RULES
]

MEDIA_NOTE = 'наличие приложения проверить невозможно'


def line_of(text: str, position: int) -> int:
    """Номер строки (с единицы) для позиции в тексте."""
    return text.count('\n', 0, position) + 1


def line_text(text: str, position: int) -> str:
    """Строка целиком, в которую попала позиция."""
    start = text.rfind('\n', 0, position) + 1
    end = text.find('\n', position)
    return text[start:end if end != -1 else len(text)]


def context(text: str, start: int, end: int, width: int = 60) -> str:
    """Фрагмент вокруг совпадения — чтобы пользователь видел, где это в документе."""
    left = max(0, start - width)
    right = min(len(text), end + width)
    return ' '.join(text[left:right].split())


def check(text: str) -> list[dict]:
    """Прогоняет все правила по тексту. Возвращает список находок."""
    found = []
    for code, title, pattern in RULES:
        for m in pattern.finditer(text):
            if code == 'placeholder.dashes' and \
                    TABLE_SEPARATOR.match(line_text(text, m.start())):
                continue
            item = {
                'rule': code,
                'title': title,
                'match': m.group(0).strip(),
                'position': m.start(),
                'line': line_of(text, m.start()),
                'context': context(text, m.start(), m.end()),
            }
            if code.startswith('media.'):
                item['note'] = MEDIA_NOTE
            found.append(item)
    found.sort(key=lambda x: x['position'])
    return found


if __name__ == '__main__':
    import json
    import sys
    from pathlib import Path

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parents[1] / 'data' / 'concept_v2.md'
    body = src.read_text(encoding='utf-8')
    hits = check(body)
    print(f'{src.name}: {len(hits)} находок\n')
    for h in hits:
        print(f"  стр.{h['line']:>4}  [{h['rule']}]  «{h['match']}»")
        print(f"          …{h['context']}…")
    print()
    print(json.dumps(hits[:2], ensure_ascii=False, indent=2))
