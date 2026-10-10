import { useEffect, useState } from "react";
import { chat, CONNECT_URL } from "./content";
import { reducedMotion } from "./hooks";
import { Mark, TgIcon } from "./ui";
import calendar from "./assets/calendar.dark.png";
import orbs from "./assets/orbs.dark.png";

const TICK = 1500;
const HOLD = 4; // тиков паузы после последнего сообщения

const hue = (s: string) => {
  let h = 0;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
};

function useChatLoop() {
  const total = chat.length;
  const [shown, setShown] = useState(reducedMotion() ? total + 2 : 0);
  useEffect(() => {
    if (reducedMotion()) return;
    const id = setInterval(() => setShown((n) => (n >= total + HOLD ? 0 : n + 1)), TICK);
    return () => clearInterval(id);
  }, [total]);
  return shown;
}

export function Hero() {
  const shown = useChatLoop();
  // сообщение «размечается» через тик после появления
  const marked = (i: number) => i <= shown - 2;
  const visible = chat.map((m, i) => ({ m, i })).filter(({ i }) => i < shown);
  const window6 = visible.slice(-5);
  const cards = chat.map((m, i) => ({ m, i })).filter(({ m, i }) => m.card && marked(i));
  const read = Math.min(shown, chat.length);

  return (
    <section className="hero night" id="top">
      <div className="wrap hero-grid">
        <div className="hero-copy">
          <h1 className="display">
            В чате 400 сообщений. <Mark when>Домашка — одно.</Mark>
          </h1>
          <p className="lead">
            StudGroup читает чат вашей группы в Telegram и вытаскивает из него только важное: задания, сроки, перенос пар и объявления.
            Остальное остаётся шумом.
          </p>
          <div className="hero-cta">
            <a className="btn btn-marker" href={CONNECT_URL} target="_blank" rel="noopener noreferrer">
              <TgIcon /> Подключить группу
            </a>
            <a className="btn btn-ghost" href="#demo">Проверить на сообщениях</a>
          </div>
        </div>

        <div className="hero-stage" aria-label="Пример: как StudGroup разбирает чат группы">
          <img className="hero-ill ill-cal" src={calendar} alt="" width={120} />
          <img className="hero-ill ill-orbs" src={orbs} alt="" width={110} />
          <div className="tg">
            <div className="tg-head">
              <span className="tg-ava" aria-hidden="true">21</span>
              <div>
                <b>Группа 21-Б</b>
                <small>пример чата</small>
              </div>
            </div>
            <ol className="tg-body" aria-live="off">
              {window6.map(({ m, i }) => (
                <li key={i} className={`msg k-${m.k} ${marked(i) ? "marked" : ""}`}>
                  <span className="who" style={{ color: `hsl(${hue(m.who)} 85% 72%)` }}>{m.who}</span>
                  <span className="txt mk-inline">{m.text}</span>
                  {marked(i) && m.k === "q" && <span className="stamp-q">вопрос, пропускаю</span>}
                  {marked(i) && m.k === "a" && m.card && <span className="stamp-a">{m.card.tag}</span>}
                </li>
              ))}
            </ol>
          </div>

          <div className="tray" aria-label="Карточки, которые собрал StudGroup">
            <div className="tray-head">
              <b>Сегодня в приложении</b>
              <span>{read} прочитано → {cards.length} {cards.length === 1 ? "карточка" : cards.length >= 2 && cards.length <= 4 ? "карточки" : "карточек"}</span>
            </div>
            <ul>
              {cards.length === 0 && <li className="tray-empty">Пока пусто — ждём важное сообщение</li>}
              {cards.map(({ m, i }) => (
                <li key={i} className="tcard">
                  <em>{m.card!.tag}</em>
                  <strong>{m.card!.title}</strong>
                  <small>{m.card!.when}</small>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}
