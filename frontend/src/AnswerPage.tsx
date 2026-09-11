import { useEffect, useRef, useState } from 'react';
import { api, write, errorText, type Question } from './api';
export default function AnswerPage() {
  const [question, setQuestion] = useState<Question | null>(null), [loading, setLoading] = useState(true);
  const [text, setText] = useState(''), [busy, setBusy] = useState(false), [done, setDone] = useState(false), [error, setError] = useState('');
  const pending = useRef({ id: '', text: '', question: 0 });
  async function load() {
    setLoading(true); setError('');
    try { const data = await api('/api/question/current'); setQuestion(data.question); setDone(data.submitted); }
    catch (e) { setError(errorText(e)); } finally { setLoading(false); }
  }
  useEffect(() => { load(); }, []);
  const length = [...text.trim()].length;
  async function submit() {
    if (!question || !length || length > 100 || busy) return;
    setBusy(true); setError('');
    try {
      if (!pending.current.id || pending.current.text !== text || pending.current.question !== question.id)
        pending.current = { id: crypto.randomUUID ? crypto.randomUUID() : '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, c => (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c) / 4).toString(16)), text, question: question.id };
      await api('/api/answers', write('POST', { question_id: question.id, text, request_id: pending.current.id })); setDone(true);
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <main className="answer-shell"><div className="space-grain" aria-hidden="true" /><header className="answer-brand"><span className="brand-symbol">✧</span><span>问卷云</span><span className="brand-sep" /><small>WENJUAN CLOUD</small></header>
    <section className={'question-stage ' + (done ? 'is-done' : '')}><div className="orbit orbit-one" aria-hidden="true" /><div className="orbit orbit-two" aria-hidden="true" /><div className="orbit orbit-three" aria-hidden="true" /><div className="question-content">
      {loading ? <p className="eyebrow" role="status">正在接收问题…</p> : done ? <div className="success" role="status"><span className="success-mark">✓</span><p className="eyebrow">SIGNAL RECEIVED</p><h1>你的想法，<br />已汇入这片星海。</h1><p>回答已收到，感谢参与。</p><button className="text-button" onClick={load}>查看是否有新的问题</button></div> : question ? <><p className="eyebrow"><span className="mini-line" />一个问题 · 无限可能<span className="mini-line" /></p><h1>{question.text}</h1><form onSubmit={e => { e.preventDefault(); submit(); }}><label className="sr-only" htmlFor="answer">你的回答</label><div className="answer-box"><textarea id="answer" className="answer-input" placeholder={question.hint} value={text} onChange={e => setText(e.target.value)} disabled={busy} rows={3} /><div className="input-bottom"><span>匿名回答</span><span className={length > 100 ? 'over-limit' : ''}>{length} / 100</span></div></div><button type="submit" className="submit-answer" disabled={busy || !length || length > 100}>{busy ? '正在传递…' : '提交回答'}<span aria-hidden="true">↗</span></button></form></> : <><p className="eyebrow">等待下一次相遇</p><h1>{error ? '暂时无法接收问题' : '当前暂未开放回答'}</h1><button className="secondary" onClick={load}>重新查看</button></>}
      {error && <div className="error-message" role="alert">{error}<button className="text-button" onClick={load}>刷新问题</button></div>}
    </div></section><footer className="answer-footer"><span>每一个想法，都有自己的光。</span><span className="footer-coordinate">A SPACE FOR YOUR THOUGHTS</span></footer></main>;
}
