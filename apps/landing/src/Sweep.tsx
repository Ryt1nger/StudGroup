import { useEffect, useRef } from "react";
import { reducedMotion } from "./hooks";
import { CONNECT_URL } from "./content";

type Col = 0 | 1 | 2 | 3;
const cols = ["Домашка", "Расписание", "Контрольные", "Объявления"] as const;

// Учебные сообщения (уходят в колонки) и шум (растворяется).
const acad: { t: string; c: Col }[] = [
  { t: "Физика: задачи 4–9", c: 0 }, { t: "Химия: лаба №3", c: 0 }, { t: "Англ.: эссе к пн", c: 0 },
  { t: "Матан → 14:30", c: 1 }, { t: "Физру отменили", c: 1 }, { t: "Лекция в ауд. 214", c: 1 },
  { t: "История: КТ в пт", c: 2 }, { t: "Зачёт по английскому", c: 2 }, { t: "Контрольная по матану", c: 2 },
  { t: "Староста: сбор в 9:00", c: 3 }, { t: "Деканат: справки до пт", c: 3 }, { t: "Новая методичка", c: 3 },
];
const noise = ["кто на кофе", "😂😂", "скинь конспект", "я проспал", "ку-ку", "а во сколько?", "+", "норм", "кто сдал?", "скиньте мем", "ааааа", "ну ок", "👍", "кто в столовку", "ребят, привет", "хм", "лол", "я не понял", "точно?", "ща", "ржу", "ой всё", "го в 5", "стикер"];

// детерминированный «рандом», чтобы раскладка не прыгала между рендерами
const rnd = (n: number) => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
const clamp = (v: number) => Math.min(1, Math.max(0, v));
const ease = (v: number) => (v < 0.5 ? 4 * v * v * v : 1 - Math.pow(-2 * v + 2, 3) / 2);

const items = [
  ...noise.map((t, i) => ({ t, kind: "n" as const, i, c: 0 as Col, k: i })),
  ...acad.map((a, i) => ({ t: a.t, kind: "a" as const, i, c: a.c, k: i })),
];

export function Sweep() {
  const sec = useRef<HTMLElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const els = useRef<(HTMLDivElement | null)[]>([]);
  const head = useRef<HTMLHeadingElement>(null);
  const counter = useRef<HTMLSpanElement>(null);
  const colEls = useRef<(HTMLElement | null)[]>([]);
  const nat = useRef<number[]>([]);
  const foot = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const s = sec.current, st = stage.current;
    if (!s || !st) return;
    const still = reducedMotion();
    let raf = 0;

    const draw = () => {
      raf = 0;
      const r = s.getBoundingClientRect();
      const vh = window.innerHeight;
      const p = still ? 1 : clamp(-r.top / Math.max(1, r.height - vh));
      const W = st.clientWidth, H = st.clientHeight;
      const mobile = W < 640;
      const ncols = mobile ? 2 : 4;
      const bw = mobile ? Math.min(W * 0.44, 190) : Math.min(W * 0.21, 250);
      const gx = (W - bw * ncols) / (ncols + 1);
      let nIdx = 0;
      items.forEach((it, idx) => {
        const el = els.current[idx];
        if (!el) return;
        if (!nat.current[idx]) { el.style.width = "max-content"; nat.current[idx] = Math.min(el.offsetWidth, bw); }
        const nw = nat.current[idx];
        const sx = (rnd(idx * 3 + 1) * 0.9 + 0.02) * (W - bw);
        const sy = H * (0.2 + rnd(idx * 3 + 2) * 0.68);
        const sr = (rnd(idx * 3 + 3) - 0.5) * 24;
        if (it.kind === "n") {
          nIdx++;
          const f = ease(clamp((p - 0.3 - (idx % 6) * 0.015) / 0.3));
          el.style.width = `${nw}px`;
          el.style.transform = `translate(${sx}px, ${sy + f * 60}px) rotate(${sr * (1 - f)}deg) scale(${1 - f * 0.25})`;
          el.style.opacity = String(1 - f);
        } else {
          const hl = ease(clamp((p - 0.12) / 0.16));
          const col = it.c, row = it.i % 3;
          const ncolsUse = ncols;
          const cc = mobile ? col % 2 : col;
          const rowUse = mobile ? row + Math.floor(col / 2) * 3 : row;
          const rows = mobile ? 6 : 3;
          const rowH = mobile ? 52 : Math.min(92, (H * 0.6) / rows);
          const ex = gx + cc * (bw + gx);
          const ey = H * (mobile ? 0.3 : 0.42) + rowUse * (rowH + 8);
          const f = ease(clamp((p - 0.42 - it.k * 0.018) / 0.3));
          void ncolsUse;
          el.style.transform = `translate(${sx + (ex - sx) * f}px, ${sy + (ey - sy) * f}px) rotate(${sr * (1 - f)}deg)`;
          el.style.width = `${nw + (bw - nw) * f}px`;
          el.style.setProperty("--hl", hl.toFixed(3));
          el.style.setProperty("--card", f.toFixed(3));
          el.style.opacity = "1";
          el.style.color = f > 0.5 ? "#14163a" : "#fff";
          el.style.fontWeight = f > 0.5 ? "600" : "400";
        }
      });
      void nIdx;
      cols.forEach((_, c) => {
        const el = colEls.current[c];
        if (!el) return;
        const mobile2 = mobile;
        const cc = mobile2 ? c % 2 : c;
        const x = gx + cc * (bw + gx);
        const y = H * (mobile2 ? 0.3 : 0.42) - 34 + (mobile2 ? Math.floor(c / 2) * 3 * (52 + 8) : 0);
        el.style.transform = `translate(${x}px, ${y}px)`;
        el.style.width = `${bw}px`;
        el.style.opacity = String(ease(clamp((p - 0.7) / 0.2)));
      });
      const done = ease(clamp((p - 0.5) / 0.3));
      if (foot.current) { const o = ease(clamp((p - 0.85) / 0.12)); foot.current.style.opacity = String(o); foot.current.style.transform = `translateY(${(1 - o) * 20}px)`; foot.current.style.pointerEvents = o > 0.5 ? "auto" : "none"; }
      if (head.current) head.current.dataset.state = p > 0.5 ? "after" : "before";
      if (counter.current) counter.current.textContent = String(Math.round(items.length - (items.length - acad.length) * done));
    };
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(draw); };
    draw();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => { window.removeEventListener("scroll", onScroll); window.removeEventListener("resize", onScroll); cancelAnimationFrame(raf); };
  }, []);

  return (
    <section className="sweep night" ref={sec} aria-label="Неделя чата: из 36 сообщений остаются 12 карточек">
      <div className="sweep-stage" ref={stage}>
        <div className="sweep-top wrap">
          <h2 className="h2 sweep-h" ref={head} data-state="before">
            <span className="b">Обычная неделя в чате группы</span>
            <span className="a">А важного здесь вот сколько</span>
          </h2>
          <p className="sweep-count"><span ref={counter}>36</span><small>сообщений на экране</small></p>
        </div>
        {cols.map((c, i) => <b key={c} className="sweep-col" ref={(el) => { colEls.current[i] = el; }}>{c}</b>)}
        <div className="sweep-foot wrap" ref={foot}>
          <p>Всё это уже лежит в приложении — со сроками и ссылками на исходные сообщения.</p>
          <a className="btn btn-marker" href={CONNECT_URL} target="_blank" rel="noopener noreferrer">Подключить группу</a>
        </div>
        {items.map((it, idx) => (
          <div key={idx} ref={(el) => { els.current[idx] = el; }} className={`sw ${it.kind === "a" ? "sw-a" : "sw-n"}`}>
            <span>{it.t}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
