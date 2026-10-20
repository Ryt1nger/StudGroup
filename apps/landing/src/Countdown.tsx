import { useEffect, useState } from "react";
import { CONNECT_URL, RELEASE_AT } from "./content";
import calendar from "./assets/calendar.dark.png";
import { TgIcon } from "./ui";

const plural = (n: number, f: [string, string, string]) => {
  const m = n % 100, d = n % 10;
  return m > 10 && m < 20 ? f[2] : d === 1 ? f[0] : d >= 2 && d <= 4 ? f[1] : f[2];
};
const pad = (n: number) => String(n).padStart(2, "0");

function left(now: number) {
  const ms = Math.max(0, RELEASE_AT - now);
  const s = Math.floor(ms / 1000);
  return { done: ms === 0, d: Math.floor(s / 86400), h: Math.floor(s / 3600) % 24, m: Math.floor(s / 60) % 60, s: s % 60 };
}

function Cell({ value, label }: { value: string; label: string }) {
  return (
    <div className="cd-cell">
      <span key={value} className="cd-num">{value}</span>
      <small>{label}</small>
    </div>
  );
}

export function Countdown() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const t = left(now);
  const label = t.done
    ? "StudGroup запущен"
    : `До релиза осталось ${t.d} ${plural(t.d, ["день", "дня", "дней"])}, ${t.h} ${plural(t.h, ["час", "часа", "часов"])}`;

  return (
    <section className="release" id="release" aria-label="Обратный отсчёт до релиза">
      <img className="rel-ill" src={calendar} alt="" width={200} />
      <div className="wrap rel-in">
        <h2 className="h2 rel-h">{t.done ? "StudGroup запущен" : "Релиз 1 ноября"}</h2>
        <p className="lead">{t.done ? "Подключайте группу — бот уже ждёт в Telegram." : "До запуска осталось:"}</p>
        {!t.done && (
          <div className="cd" role="timer" aria-label={label}>
            <Cell value={String(t.d)} label={plural(t.d, ["день", "дня", "дней"])} />
            <Cell value={pad(t.h)} label={plural(t.h, ["час", "часа", "часов"])} />
            <Cell value={pad(t.m)} label={plural(t.m, ["минута", "минуты", "минут"])} />
            <Cell value={pad(t.s)} label={plural(t.s, ["секунда", "секунды", "секунд"])} />
          </div>
        )}
        <a className="btn btn-white" href={CONNECT_URL} target="_blank" rel="noopener noreferrer">
          <TgIcon /> {t.done ? "Подключить группу" : "Подключить группу заранее"}
        </a>
      </div>
    </section>
  );
}
