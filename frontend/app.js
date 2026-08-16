/* ТТ-Детектор — интерфейс (§3 ТЗ).
   React без сборки: htm даёт JSX-подобный синтаксис прямо в браузере.
   Роутера нет, state-менеджера нет — одна страница. */

const { useState, useRef, useCallback } = React;
const html = htm.bind(React.createElement);

const LIMIT = 40000;

const DEFECT_TYPES = {
  1: 'Нет критерия проверки',
  2: 'Требование подменено решением',
  3: 'Ссылка на несуществующий носитель',
  4: 'Незакрытый плейсхолдер',
  5: 'Отложенное значение',
  6: 'Открытый список',
  7: 'Нет адресата и срока',
  8: 'Конфликт',
};

/* Подстановки вида [значение] подсвечиваются: пользователь должен видеть,
   что система не выдумала число, а оставила дыру. */
function withGaps(text) {
  const parts = String(text || '').split(/(\[[^\]]+\])/g);
  return parts.map((p, i) =>
    p.startsWith('[') && p.endsWith(']')
      ? html`<span class="gap" key=${i}>${p}</span>`
      : p);
}

function CopyButton({ text, label = 'Скопировать всё' }) {
  const [done, setDone] = useState(false);
  return html`<button class="copy" onClick=${() => {
    navigator.clipboard.writeText(text).then(() => {
      setDone(true);
      setTimeout(() => setDone(false), 1600);
    });
  }}>${done ? 'скопировано' : label}</button>`;
}

function Stages({ formalDone, modelDone, running }) {
  const state = (done) => done ? 'done' : (running ? 'active' : 'wait');
  return html`
    <div class="stages">
      <div class=${'stage ' + state(formalDone)}>
        <span class="mark">${formalDone ? '✓' : '·'}</span>
        <span>Формальные проверки${formalDone ? '' : ' — идут'}</span>
      </div>
      <div class=${'stage ' + state(modelDone)}>
        <span class="mark">${modelDone ? '✓' : '·'}</span>
        <span>Анализ модели${modelDone ? '' : ' — идёт, обычно около минуты'}</span>
      </div>
    </div>`;
}

function MatchBlock({ match }) {
  return html`
    <section>
      <h2>Совпадение с доведённой версией документа</h2>
      <div class="score">
        <span class="big">${match.hits}</span>
        <span class="of">из ${match.total}</span>
      </div>
      <p class="score-note">
        Столько правок, внесённых автором вручную при доработке документа,
        система нашла самостоятельно — не видя доведённой версии.
      </p>
      <div class="cols">
        <div class="found">
          <h3>Найдено</h3>
          <ul>${match.found.map((e, i) => html`<li key=${i}>${e.description}</li>`)}</ul>
        </div>
        <div class="missed">
          <h3>Пропущено</h3>
          <ul>${match.missed.map((e, i) => html`<li key=${i}>${e.description}</li>`)}</ul>
        </div>
      </div>
    </section>`;
}

function FormalBlock({ items }) {
  if (!items || !items.length) return null;
  return html`
    <section>
      <h2>Формальные проверки</h2>
      <div class="formal">
        <div>Найдено без модели, по правилам: ${items.length}</div>
        <ul>
          ${items.map((h, i) => html`
            <li key=${i}>
              <span class="m">${h.match}</span> — ${h.title}
              ${h.note ? html`<span class="hint"> (${h.note})</span>` : null}
            </li>`)}
        </ul>
      </div>
    </section>`;
}

function Defects({ defects }) {
  if (!defects || !defects.length) return null;
  const groups = {};
  defects.forEach((d) => {
    const t = d.type || 0;
    (groups[t] = groups[t] || []).push(d);
  });
  const order = Object.keys(groups).sort((a, b) => a - b);
  return html`
    <section>
      <h2>Дефекты — ${defects.length}</h2>
      ${order.map((t) => html`
        <div class="type-group" key=${t}>
          <div class="type-name">${DEFECT_TYPES[t] || 'Прочее'}</div>
          ${groups[t].map((d, i) => html`
            <div class="card" key=${i}>
              <blockquote>${d.quote}</blockquote>
              <div class="why">${d.consequence}</div>
            </div>`)}
        </div>`)}
    </section>`;
}

function Questions({ questions }) {
  if (!questions || !questions.length) return null;
  const plain = questions.map((q, i) => `${i + 1}. ${q}`).join('\n');
  return html`
    <section class="questions">
      <h2>Вопросы исполнителя — ${questions.length}<${CopyButton} text=${plain} /></h2>
      <ol>${questions.map((q, i) => html`<li key=${i}>${q}</li>`)}</ol>
    </section>`;
}

function Fixes({ fixes, assumptions }) {
  if ((!fixes || !fixes.length) && (!assumptions || !assumptions.length)) return null;
  return html`
    <section>
      <h2>Исправления</h2>
      ${(fixes || []).map((f, i) => html`
        <div class="fix" key=${i}>
          <div class="side before"><span class="label">было</span>${f.before}</div>
          <div class="side after"><span class="label">стало</span>${withGaps(f.after)}</div>
        </div>`)}
      ${assumptions && assumptions.length ? html`
        <div style=${{ marginTop: 24 }}>
          <div class="type-name">Допущения, которые должен подтвердить заказчик</div>
          <ul>${assumptions.map((a, i) => html`<li key=${i}>${withGaps(a)}</li>`)}</ul>
        </div>` : null}
    </section>`;
}

function App() {
  const [text, setText] = useState('');
  const [formal, setFormal] = useState(null);
  const [result, setResult] = useState(null);
  const [match, setMatch] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [over, setOver] = useState(false);
  const fileRef = useRef(null);

  const tooLong = text.length > LIMIT;

  const reset = () => { setFormal(null); setResult(null); setMatch(null); setError(null); };

  const post = async (url, body) => {
    const res = await fetch(url, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Ошибка ${res.status}`);
    }
    return res.json();
  };

  /* Формальные проверки возвращаются немедленно и сразу отрисовываются;
     модельный анализ докатывается позже и дорисовывается ниже. */
  const analyze = useCallback(async (value) => {
    reset();
    setRunning(true);
    try {
      post('/api/formal', { text: value })
        .then((f) => setFormal(f.formal))
        .catch(() => {});
      const got = await post('/api/analyze', { text: value });
      setResult(got);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }, []);

  const runExample = useCallback(async () => {
    reset();
    setRunning(true);
    try {
      const got = await post('/api/example');
      setText(got.source || '');
      setFormal(got.formal);
      setMatch(got.match);
      setResult(got);
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  }, []);

  const upload = useCallback(async (file) => {
    reset();
    const form = new FormData();
    form.append('file', file);
    try {
      const res = await fetch('/api/upload', { method: 'POST', body: form });
      const got = await res.json();
      if (!res.ok) throw new Error(got.detail || 'Не удалось прочитать файл');
      setText(got.text);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  return html`
    <div class="wrap">
      <header>
        <h1>ТТ-Детектор</h1>
        <p>Показывает, из-за каких формулировок в ваших технических требованиях
           исполнитель сделает не то.</p>
      </header>

      <div class="entry">
        <button class="primary" onClick=${runExample} disabled=${running}>
          ${running && !formal ? 'Разбираю…' : 'Попробовать на примере'}
        </button>

        <div class="or">или свой черновик</div>

        <div class=${'dropzone' + (over ? ' over' : '')}
             onDragOver=${(e) => { e.preventDefault(); setOver(true); }}
             onDragLeave=${() => setOver(false)}
             onDrop=${(e) => {
               e.preventDefault(); setOver(false);
               if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
             }}>
          <textarea value=${text} onInput=${(e) => setText(e.target.value)}
                    placeholder="Вставьте текст ТТ или перетащите сюда файл .docx, .txt, .md" />
        </div>

        <div class="row">
          <button class="secondary" disabled=${running || !text.trim() || tooLong}
                  onClick=${() => analyze(text)}>Разобрать</button>
          <button class="secondary" onClick=${() => fileRef.current.click()}>Выбрать файл</button>
          <input type="file" accept=".docx,.txt,.md" ref=${fileRef} style=${{ display: 'none' }}
                 onChange=${(e) => e.target.files[0] && upload(e.target.files[0])} />
          <span class=${'counter' + (tooLong ? ' over' : '')}>
            ${text.length.toLocaleString('ru')} / ${LIMIT.toLocaleString('ru')}
          </span>
        </div>

        ${tooLong ? html`<div class="warn">
          Текст длиннее ${LIMIT.toLocaleString('ru')} знаков — разбор не запустится.
          Сократите фрагмент или разберите документ по частям.
        </div>` : null}
      </div>

      ${running || formal || result
        ? html`<${Stages} formalDone=${!!formal} modelDone=${!!result} running=${running} />`
        : null}

      ${error ? html`<div class="error">${error}</div>` : null}
      ${match ? html`<${MatchBlock} match=${match} />` : null}
      ${formal ? html`<${FormalBlock} items=${formal} />` : null}
      ${result ? html`<${Defects} defects=${result.defects} />` : null}
      ${result ? html`<${Questions} questions=${result.questions} />` : null}
      ${result ? html`<${Fixes} fixes=${result.fixes} assumptions=${result.assumptions} />` : null}
    </div>`;
}

ReactDOM.createRoot(document.getElementById('root')).render(html`<${App} />`);
