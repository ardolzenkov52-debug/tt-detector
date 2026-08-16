# -*- coding: utf-8 -*-
"""Вызов модели и устойчивый разбор ответа.

Промпт хранится в prompt.md, не в коде. Провайдер и ключи — из окружения.

ТЗ §6 фиксирует Anthropic SDK; бесплатный контур потребовал сменного движка,
поэтому вызов разведён за одну функцию analyze(). Добавление провайдера —
это одна функция _call_<имя> и строка в _PROVIDERS.

Выбор провайдера: переменная LLM_PROVIDER, иначе по наличию ключа.
"""
import json
import os
import random
import re
import time
from pathlib import Path

PROMPT_PATH = Path(__file__).with_name('prompt.md')
ENV_PATH = Path(__file__).resolve().parents[1] / '.env'

DEFAULT_MODELS = {
    'gigachat': 'GigaChat-2-Max',
    'gemini': 'gemini-2.5-pro',
    'anthropic': 'claude-opus-5',
}
MAX_TOKENS = 32000
GIGACHAT_MAX_TOKENS = 8192

# Схема выдачи из §4.1 — ею же принуждаем провайдеров, умеющих structured output.
RESPONSE_SCHEMA = {
    'type': 'object',
    'properties': {
        'defects': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'type': {'type': 'integer'},
                    'quote': {'type': 'string'},
                    'consequence': {'type': 'string'},
                },
                'required': ['type', 'quote', 'consequence'],
            },
        },
        'questions': {'type': 'array', 'items': {'type': 'string'}},
        'fixes': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'before': {'type': 'string'},
                    'after': {'type': 'string'},
                },
                'required': ['before', 'after'],
            },
        },
        'assumptions': {'type': 'array', 'items': {'type': 'string'}},
    },
    'required': ['defects', 'questions', 'fixes', 'assumptions'],
}


def _load_env() -> None:
    """Подхватывает переменные из .env в корне проекта, если их нет в окружении."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip().strip('"\'')
        if value:
            os.environ.setdefault(key.strip(), value)


_load_env()


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding='utf-8')


def resolve_provider() -> str:
    """Явный выбор через LLM_PROVIDER, иначе по наличию ключа."""
    explicit = (os.environ.get('LLM_PROVIDER') or '').strip().lower()
    if explicit:
        if explicit not in DEFAULT_MODELS:
            raise ValueError(f'Неизвестный LLM_PROVIDER: {explicit}. '
                             f'Доступны: {", ".join(DEFAULT_MODELS)}')
        return explicit
    if os.environ.get('GIGACHAT_CREDENTIALS'):
        return 'gigachat'
    if os.environ.get('GEMINI_API_KEY'):
        return 'gemini'
    if os.environ.get('ANTHROPIC_API_KEY'):
        return 'anthropic'
    raise RuntimeError(
        'Не найден ключ модели. Положите в .env одну из строк:\n'
        '  GIGACHAT_CREDENTIALS=...  (авторизационные данные, developers.sber.ru)\n'
        '  GEMINI_API_KEY=...        (бесплатно, https://aistudio.google.com/apikey)\n'
        '  ANTHROPIC_API_KEY=...     (платно, по ТЗ §6)')


def resolve_model(provider: str) -> str:
    env_name = {
        'gigachat': 'GIGACHAT_MODEL',
        'gemini': 'GEMINI_MODEL',
        'anthropic': 'ANTHROPIC_MODEL',
    }[provider]
    return os.environ.get(env_name) or DEFAULT_MODELS[provider]


# --------------------------------------------------------------------------
# Разбор ответа
# --------------------------------------------------------------------------

def _strip_fences(raw: str) -> str:
    text = raw.strip()
    fence = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.S)
    if fence:
        return fence.group(1).strip()
    # модель могла добавить преамбулу — берём самый внешний JSON-объект
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end > start:
        return text[start:end + 1]
    return text


def parse_response(raw: str) -> dict:
    data = json.loads(_strip_fences(raw))
    if not isinstance(data, dict):
        raise ValueError('Ожидался JSON-объект')
    for key in ('defects', 'questions', 'fixes', 'assumptions'):
        data.setdefault(key, [])
    return data


# --------------------------------------------------------------------------
# Провайдеры: каждая функция возвращает сырой текст ответа
# --------------------------------------------------------------------------

def _call_gigachat(system: str, text: str, model: str) -> str:
    from gigachat import GigaChat
    from gigachat.models import Chat, Messages, MessagesRole

    # Сертификаты НУЦ Минцифры обычно отсутствуют в системном хранилище.
    # Штатный путь — указать GIGACHAT_CA_BUNDLE с цепочкой; иначе проверка
    # отключается (так описано в документации ГигаЧата).
    ca_bundle = os.environ.get('GIGACHAT_CA_BUNDLE') or None
    kwargs = {
        'credentials': os.environ['GIGACHAT_CREDENTIALS'],
        'scope': os.environ.get('GIGACHAT_SCOPE', 'GIGACHAT_API_PERS'),
        'model': model,
        'timeout': 600,
    }
    if ca_bundle:
        kwargs['ca_bundle_file'] = ca_bundle
    else:
        kwargs['verify_ssl_certs'] = False

    messages = [
        Messages(role=MessagesRole.SYSTEM, content=system),
        Messages(role=MessagesRole.USER, content=text),
    ]
    max_tokens = int(os.environ.get('GIGACHAT_MAX_TOKENS', GIGACHAT_MAX_TOKENS))

    # response_format не используем осознанно: json_object ГигаЧат не знает
    # (400 Unknown type), а json_schema на этом промпте возвращает битый JSON.
    # Формат держится текстом промпта плюс устойчивым парсингом.
    with GigaChat(**kwargs) as giga:
        resp = giga.chat(Chat(model=model, messages=messages,
                              max_tokens=max_tokens))
    return resp.choices[0].message.content or ''


def _call_gemini(system: str, text: str, model: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    resp = client.models.generate_content(
        model=model,
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type='application/json',
            max_output_tokens=MAX_TOKENS,
        ),
    )
    return resp.text or ''


def _call_anthropic(system: str, text: str, model: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    with client.messages.stream(
        model=model,
        max_tokens=MAX_TOKENS,
        output_config={'effort': 'high'},
        system=system,
        messages=[{'role': 'user', 'content': text}],
    ) as stream:
        resp = stream.get_final_message()
    if resp.stop_reason == 'refusal':
        raise ValueError('Модель отклонила запрос (stop_reason=refusal)')
    return ''.join(b.text for b in resp.content if b.type == 'text')


_PROVIDERS = {
    'gigachat': _call_gigachat,
    'gemini': _call_gemini,
    'anthropic': _call_anthropic,
}


def analyze(text: str, *, provider: str | None = None,
            extra_system: str = '') -> dict:
    """Один разбор фрагмента. При ошибке парсинга — один повтор, затем исключение.

    extra_system — приписка к системному промпту для проходов конвейера
    (например, сужение до конфликтов). Сам prompt.md не меняется.
    """
    provider = provider or resolve_provider()
    model = resolve_model(provider)
    call = _PROVIDERS[provider]
    system = load_prompt() + extra_system

    last_error = None
    for _ in range(2):
        raw = _call_with_retry(call, system, text, model)
        try:
            return parse_response(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
    raise ValueError(f'Модель вернула неразбираемый ответ: {last_error}')


# Бесплатные тарифы ограничивают число одновременных запросов; отказ по лимиту
# — состояние временное, а не ошибка задания.
TRANSIENT = ('ratelimit', 'timeout', 'toomanyrequests', 'serviceunavailable',
             'connect', 'readtimeout', 'overloaded')
RETRIES = int(os.environ.get('LLM_RETRIES', 5))


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return any(marker in name for marker in TRANSIENT) or '429' in str(exc)


def _call_with_retry(call, system: str, text: str, model_name: str) -> str:
    delay = 2.0
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            return call(system, text, model_name)
        except Exception as exc:
            if not _is_transient(exc):
                raise
            last = exc
            if attempt == RETRIES - 1:
                break
            time.sleep(delay + random.uniform(0, 1))
            delay = min(delay * 2, 30)
    raise last if last else RuntimeError('вызов модели не удался')
