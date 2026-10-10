import { useEffect, useRef, useState } from "react";
import { dayScenes } from "./content";

type Tab = (typeof dayScenes)[number]["tab"];

const rows: Record<Tab, { head: string; items: { t: string; s: string; flag?: string }[] }> = {
  today: {
    head: "Сегодня, вторник",
    items: [
      { t: "Физика", s: "09:00 · лекция · ауд. 118" },
      { t: "Матан", s: "14:30 · ауд. 214", flag: "перенос" },
      { t: "Физика, задачи 4–9", s: "сдать в четверг", flag: "новое" },
      { t: "История", s: "контрольная в пятницу" },
    ],
  },
  tasks: {
    head: "Задания",
    items: [
      { t: "Физика, задачи 4–9", s: "к четвергу", flag: "новое" },
      { t: "Химия, лаба №3", s: "к четвергу" },
      { t: "История, темы 1–3", s: "контрольная · пятница" },
    ],
  },
  schedule: {
    head: "Расписание · среда",
    items: [
      { t: "Алгоритмы", s: "09:00 · ауд. 305" },
      { t: "Матан", s: "было 11:10 → стало 14:30 · ауд. 214", flag: "перенос" },
      { t: "Английский", s: "16:00 · ауд. 402" },
    ],
  },
};

function Phone({ tab }: { tab: Tab }) {
  const d = rows[tab];
  return (
    <div className="phone" role="img" aria-label={`Пример экрана приложения: ${d.head}`}>
      <div className="phone-bar"><i /></div>
      <div key={tab} className="phone-screen">
        <b>{d.head}</b>
        <ul>
          {d.items.map((r) => (
            <li key={r.t} className={r.flag ? "has-flag" : ""}>
              <div>
                <strong>{r.t}</strong>
                <small>{r.s}</small>
              </div>
              {r.flag && <em>{r.flag}</em>}
            </li>
          ))}
        </ul>
      </div>
      <nav className="phone-tabs" aria-hidden="true">
        {(["today", "tasks", "schedule"] as Tab[]).map((k) => (
          <span key={k} className={k === tab ? "on" : ""}>{k === "today" ? "Сегодня" : k === "tasks" ? "Задания" : "Расписание"}</span>
        ))}
        <span>Предметы</span>
      </nav>
    </div>
  );
}

export function Day() {
  const [active, setActive] = useState(0);
  const refs = useRef<(HTMLLIElement | null)[]>([]);

  useEffect(() => {
    if (!("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver(
      (es) => es.forEach((e) => e.isIntersecting && setActive(Number((e.target as HTMLElement).dataset.i))),
      { rootMargin: "-45% 0px -45% 0px" },
    );
    refs.current.forEach((el) => el && io.observe(el));
    return () => io.disconnect();
  }, []);

  return (
    <section className="day paper-2" id="day">
      <div className="wrap">
        <header className="sec-head">
          <h2 className="h2">Один день группы — и ни одного вопроса «а что задали?»</h2>
        </header>
        <div className="day-grid">
          <ol className="day-steps">
            {dayScenes.map((s, i) => (
              <li key={s.time} data-i={i} ref={(el) => { refs.current[i] = el; }} className={i === active ? "is-on" : ""}>
                <time>{s.time}</time>
                <p className="chatline">{s.chat}</p>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
                <div className="phone-inline"><Phone tab={s.tab} /></div>
              </li>
            ))}
          </ol>
          <div className="day-phone"><Phone tab={dayScenes[active].tab} /></div>
        </div>
      </div>
    </section>
  );
}
