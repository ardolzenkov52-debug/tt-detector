# -*- coding: utf-8 -*-
"""Извлечение текста из .docx владельца проекта в data/*.md.

Ничего не дописывает и не переформулирует: только текст абзацев и таблиц
в порядке следования в документе.
"""
import sys
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


def iter_blocks(parent):
    """Абзацы и таблицы в исходном порядке."""
    body = parent.element.body
    for child in body.iterchildren():
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            yield Paragraph(child, parent)
        elif tag == 'tbl':
            yield Table(child, parent)


def para_to_md(p: Paragraph) -> str:
    text = p.text.strip()
    if not text:
        return ''
    style = (p.style.name or '').lower()
    if style.startswith('heading'):
        try:
            level = int(style.split()[-1])
        except ValueError:
            level = 2
        return '#' * min(level, 6) + ' ' + text
    if style.startswith('list') or style.startswith('спис'):
        return '- ' + text
    return text


def table_to_md(t: Table) -> str:
    rows = []
    for row in t.rows:
        cells = [' '.join(c.text.split()) for c in row.cells]
        rows.append('| ' + ' | '.join(cells) + ' |')
    if not rows:
        return ''
    head = rows[0]
    sep = '| ' + ' | '.join(['---'] * len(t.columns)) + ' |'
    return '\n'.join([head, sep] + rows[1:])


def convert(src: Path, dst: Path) -> None:
    doc = Document(str(src))
    out = []
    for block in iter_blocks(doc):
        if isinstance(block, Paragraph):
            line = para_to_md(block)
        else:
            line = table_to_md(block)
        if line:
            out.append(line)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text('\n\n'.join(out) + '\n', encoding='utf-8')
    print(f'{src.name} -> {dst} ({len(dst.read_text(encoding="utf-8"))} знаков)')


if __name__ == '__main__':
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
