# -*- coding: utf-8 -*-
"""ТТ-Детектор — бэкенд (§6 ТЗ).

Документы пользователя не сохраняются: обработка в памяти, базы нет,
содержимое не логируется.
"""
import io
import json
import sys
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))

import formal      # noqa: E402
import model       # noqa: E402
import pipeline    # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
CACHE = ROOT / 'runs' / 'example.json'

# Публичный пример — синтетический документ: разбор виден всем, кто откроет
# ссылку, и класть туда чужой рабочий концепт нельзя. Приватная эталонная пара
# (concept_v2.md + reference_edits.json) в демонстрации не участвует и служит
# только измерению полноты в scripts/gate.py и scripts/score.py.
EXAMPLE_DOC = DATA / 'demo_v2.md'
EXAMPLE_EDITS = DATA / 'demo_edits.json'

MAX_CHARS = 40_000          # §3: лимит входа
MAX_UPLOAD_BYTES = 10 << 20

app = FastAPI(title='ТТ-Детектор', version='1.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],     # прототип; персональных данных не обрабатываем
    allow_methods=['*'],
    allow_headers=['*'],
)


class TextIn(BaseModel):
    text: str = Field(default='')


def _check_length(text: str) -> str:
    text = (text or '').strip()
    if not text:
        raise HTTPException(400, 'Пустой текст')
    if len(text) > MAX_CHARS:
        raise HTTPException(
            413, f'Текст длиннее {MAX_CHARS} знаков ({len(text)}). '
                 'Сократите фрагмент или разберите его по частям.')
    return text


# --- эталонная пара -------------------------------------------------------

def example_available() -> bool:
    return EXAMPLE_DOC.exists() and EXAMPLE_EDITS.exists()


def load_edits() -> list[dict]:
    return json.loads(EXAMPLE_EDITS.read_text(encoding='utf-8'))['edits']


def _norm(text: str) -> str:
    import re
    return re.sub(r'\s+', ' ', (text or '').lower().replace('ё', 'е')).strip()


def match_block(defects: list[dict], formal_hits: list[dict]) -> dict:
    """Блок совпадения (§7): сколько правок эталонной версии система нашла сама.

    Засчитываются и находки формального модуля — для пользователя это один
    и тот же результат работы системы.
    """
    haystack = _norm(' \n '.join(
        [f"{d.get('quote', '')} {d.get('consequence', '')}" for d in defects] +
        [f"{h.get('context', '')} {h.get('title', '')}" for h in formal_hits]))

    found, missed = [], []
    for edit in load_edits():
        hits = [kw for kw in edit.get('keywords', []) if _norm(kw) in haystack]
        record = {'id': edit['id'], 'description': edit['description']}
        if hits:
            record['matched'] = hits[:3]
            found.append(record)
        else:
            missed.append(record)
    return {'found': found, 'missed': missed,
            'hits': len(found), 'total': len(found) + len(missed)}


# --- эндпоинты ------------------------------------------------------------

@app.get('/api/health')
def health() -> dict:
    try:
        provider = model.resolve_provider()
        ready = True
    except Exception:
        provider, ready = None, False
    return {'ready': ready, 'provider': provider,
            'example': example_available(), 'limit': MAX_CHARS}


@app.post('/api/formal')
def api_formal(payload: TextIn) -> dict:
    """Формальные проверки. Синхронно, миллисекунды, без модели."""
    text = _check_length(payload.text)
    return {'formal': formal.check(text)}


@app.post('/api/analyze')
def api_analyze(payload: TextIn) -> dict:
    """Разбор модели по схеме из §4.1."""
    text = _check_length(payload.text)
    try:
        return pipeline.analyze_document(text)
    except Exception as exc:
        raise HTTPException(502, f'Модель недоступна: {type(exc).__name__}')


@app.post('/api/example')
def api_example() -> dict:
    """Разбор эталонного документа и блок совпадения.

    Разбор занимает около минуты, поэтому результат готовится заранее и
    отдаётся из кеша: посетитель демонстрации ждать не должен.
    """
    if not example_available():
        raise HTTPException(
            404, f'Режим примера отключён: нет файлов {EXAMPLE_DOC.name} '
                 f'и {EXAMPLE_EDITS.name} в data/.')

    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding='utf-8'))

    text = EXAMPLE_DOC.read_text(encoding='utf-8')
    result = pipeline.analyze_document(text)
    formal_hits = formal.check(text)
    payload = {**result, 'formal': formal_hits, 'source': text,
               'match': match_block(result['defects'], formal_hits)}
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                     encoding='utf-8')
    return payload


@app.post('/api/upload')
async def api_upload(file: UploadFile = File(...)) -> dict:
    """Извлечение текста из .docx / .txt / .md. Файл не сохраняется."""
    name = (file.filename or '').lower()
    if not name.endswith(('.docx', '.txt', '.md')):
        raise HTTPException(400, 'Поддерживаются .docx, .txt и .md')

    blob = await file.read()
    if len(blob) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, 'Файл больше 10 МБ')

    if name.endswith('.docx'):
        try:
            from docx import Document
            doc = Document(io.BytesIO(blob))
            text = '\n\n'.join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception:
            raise HTTPException(400, 'Не удалось прочитать .docx')
    else:
        text = blob.decode('utf-8', errors='replace')

    text = text.strip()
    if not text:
        raise HTTPException(400, 'В файле нет текста')
    return {'text': text, 'chars': len(text), 'over_limit': len(text) > MAX_CHARS}


# Статика монтируется последней, чтобы не перекрывать /api/*.
FRONTEND = ROOT / 'frontend'
if FRONTEND.exists():
    app.mount('/', StaticFiles(directory=str(FRONTEND), html=True), name='frontend')


if __name__ == '__main__':
    import os
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))
