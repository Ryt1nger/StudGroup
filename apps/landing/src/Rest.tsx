import { useRef, useState } from "react";
import { faq, footerLinks, ladder, plans, rules, CONNECT_URL, BOT_USERNAME, botLink } from "./content";
import { reducedMotion } from "./hooks";
import { Mark, TgIcon } from "./ui";
import logoDark from "./assets/brand/logo-lockup.dark.png";
import logoLight from "./assets/brand/logo-lockup.light.png";

export { logoDark, logoLight };

export function Rules() {
  return (
    <section className="rules night" id="rules">
      <div className="wrap rules-grid">
        <div>
          <h2 className="h2">Бот не выдумывает. <Mark>Он ищет доказательства.</Mark></h2>
          <ul className="rule-list">
            {rules.map((r) => (
              <li key={r.title}>
                <h3>{r.title}</h3>
                <p>{r.text}</p>
              </li>
            ))}
          </ul>
        </div>
        <figure className="ladder">
          <figcaption>Если сообщения противоречат друг другу, побеждает тот, кто выше</figcaption>
          <ol>
            {ladder.map((l, i) => (
              <li key={l.name} style={{ ["--i" as string]: i }}>
                <b>{l.name}</b>
                <span>{l.text}</span>
              </li>
            ))}
          </ol>
        </figure>
      </div>
    </section>
  );
}

function IdCard({ p }: { p: (typeof plans)[number] }) {
  const ref = useRef<HTMLElement>(null);
  const move = (e: React.PointerEvent) => {
    const el = ref.current;
    if (!el || e.pointerType !== "mouse" || reducedMotion()) return;
    const r = el.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5;
    const y = (e.clientY - r.top) / r.height - 0.5;
    el.style.setProperty("--rx", `${(-y * 8).toFixed(2)}deg`);
    el.style.setProperty("--ry", `${(x * 10).toFixed(2)}deg`);
  };
  const leave = () => {
    ref.current?.style.setProperty("--rx", "0deg");
    ref.current?.style.setProperty("--ry", "0deg");
  };
  return (
    <article ref={ref} className={`idc ${p.featured ? "feat" : ""}`} onPointerMove={move} onPointerLeave={leave}>
      <span className="hole" aria-hidden="true" />
      <p className="idc-note">{p.note}</p>
      <h3>{p.name}</h3>
      <p className="idc-price"><strong>{p.price}</strong> <span>{p.period}</span></p>
      <ul>
        {p.items.map((it) => <li key={it}>{it}</li>)}
      </ul>
      <a className={`btn ${p.featured ? "btn-marker" : "btn-line"}`} href={p.href} target="_blank" rel="noopener noreferrer">{p.cta}</a>
      <span className="barcode" aria-hidden="true" />
    </article>
  );
}

export function Plans() {
  return (
    <section className="plans paper" id="plans">
      <div className="wrap">
        <header className="sec-head">
          <h2 className="h2">Студенческий — бесплатно. Группа — вскладчину.</h2>
          <p className="lead dim">Один платёж на всю группу, оплата через СБП.</p>
        </header>
        <div className="idc-row">
          {plans.map((p) => <IdCard key={p.name} p={p} />)}
        </div>
      </div>
    </section>
  );
}

function QA({ q, a }: { q: string; a: string }) {
  const [state, setState] = useState<"closed" | "typing" | "open">("closed");
  const timer = useRef<number>(0);
  const toggle = () => {
    window.clearTimeout(timer.current);
    if (state !== "closed") return setState("closed");
    if (reducedMotion()) return setState("open");
    setState("typing");
    timer.current = window.setTimeout(() => setState("open"), 650);
  };
  return (
    <li className="qa">
      <button className="qa-q" aria-expanded={state !== "closed"} onClick={toggle}>{q}</button>
      {state === "typing" && <div className="qa-a typing" aria-hidden="true"><i /><i /><i /></div>}
      {state === "open" && <div className="qa-a">{a}</div>}
    </li>
  );
}

export function Faq() {
  return (
    <section className="faq paper-2" id="faq">
      <div className="wrap faq-wrap">
        <header className="sec-head">
          <h2 className="h2">Спросите бота</h2>
          <p className="lead dim">Нажмите на вопрос — получите ответ. Остальное бот расскажет в Telegram.</p>
        </header>
        <ul className="qa-list">{faq.map((f) => <QA key={f.q} {...f} />)}</ul>
        <a className="btn btn-line" href={botLink("faq")} target="_blank" rel="noopener noreferrer"><TgIcon /> Все ответы в боте</a>
      </div>
    </section>
  );
}

export function Cta() {
  return (
    <section className="cta night">
      <div className="wrap">
        <h2 className="cta-h">Хватит <Mark>листать чат.</Mark></h2>
        <p className="lead">Подключите группу за пару минут — дальше бот работает сам.</p>
        <a className="btn btn-marker btn-xl" href={CONNECT_URL} target="_blank" rel="noopener noreferrer"><TgIcon /> Подключить группу</a>
      </div>
    </section>
  );
}

export function Footer() {
  return (
    <footer className="foot night">
      <div className="wrap foot-in">
        <img src={logoDark} alt="StudGroup" height={28} />
        <nav aria-label="Ссылки">
          {footerLinks.map((l) => <a key={l.label} href={l.href} target="_blank" rel="noopener noreferrer">{l.label}</a>)}
        </nav>
        <a className="foot-bot" href={`https://t.me/${BOT_USERNAME}`} target="_blank" rel="noopener noreferrer">@{BOT_USERNAME}</a>
      </div>
    </footer>
  );
}
