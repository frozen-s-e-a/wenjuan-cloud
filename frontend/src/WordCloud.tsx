import { useEffect, useRef, useState } from 'react';
import cloud from 'd3-cloud';
import type { Word } from './api';
interface Placed { text: string; size: number; x: number; y: number; rotate: number; count: number }
export default function WordCloud({ words, onSelect }: { words: Word[]; onSelect: (word: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [placed, setPlaced] = useState<Placed[]>([]);
  const [working, setWorking] = useState(false);
  const signature = JSON.stringify(words);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(entries => setWidth(Math.max(240, Math.floor(entries[0].contentRect.width))));
    observer.observe(ref.current); return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!words.length) { setPlaced([]); setWorking(false); return; }
    let seed = 42;
    const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
    const max = words[0]?.count || 1;
    setWorking(true);
    const layout = cloud<any>().size([width, 410]).words(words.slice(0, 80).map(w => ({ text: w.term, count: w.count })))
      .padding(5).rotate(() => 0).font('sans-serif').fontSize(w => Math.min(width / Math.max(3, [...w.text].length), 17 + 43 * Math.sqrt(w.count / max)))
      .random(random).timeInterval(16).on('end', result => { setPlaced(result as Placed[]); setWorking(false); });
    layout.start();
    return () => { layout.stop(); };
  }, [signature, width]);
  const colors = ['#95e5eb', '#b6a8ff', '#86aaf0', '#d9d4f6', '#70c6d2'];
  return <div className="cloud-area" ref={ref} aria-busy={working}>
    {!words.length ? <div className="empty-content"><span className="empty-orbit">✧</span><h3>等待想法汇成星海</h3><p>收到有效回答后，词语会出现在这里。</p></div> :
      <svg width="100%" height="410" viewBox={`0 0 ${width} 410`} aria-label="回答词云，点击词语查看对应原文">
        <g transform={`translate(${width / 2},205)`}>{placed.map((w, i) => <text key={w.text} textAnchor="middle" fontFamily="sans-serif" fontSize={w.size} fill={colors[i % colors.length]} transform={`translate(${w.x},${w.y})`} role="button" tabIndex={0} className="cloud-word" aria-label={`${w.text}，${w.count} 条回答提及`} onClick={() => onSelect(w.text)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(w.text); } }}><title>{w.text} · {w.count} 条回答提及</title>{w.text}</text>)}</g>
      </svg>}
    {working && <span className="cloud-working" role="status">正在排列词语…</span>}
  </div>;
}
