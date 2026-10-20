import { useEffect, useRef } from "react";
import { CONNECT_URL } from "./content";
import { reducedMotion } from "./hooks";
import { Mark, TgIcon } from "./ui";
import calendar from "./assets/calendar.dark.png";
import appToday from "./assets/app-today.webp";
import appTasks from "./assets/app-tasks.webp";
import appSchedule from "./assets/app-schedule.webp";

// Стена чата: шум тускнеет, учебное (a) горит фирменным фиолетовым.
const rows: { t: string; a?: boolean }[][] = [
  [{ t: "кто на кофе" }, { t: "К четвергу задачи 4–9", a: true }, { t: "😂😂" }, { t: "скинь мем про сессию" }, { t: "я проспал" }, { t: "Матан → 14:30, ауд. 214", a: true }, { t: "ку-ку" }, { t: "а во сколько?" }],
  [{ t: "норм" }, { t: "ржу" }, { t: "В пятницу контрольная по истории", a: true }, { t: "кто сдал?" }, { t: "+" }, { t: "ой всё" }, { t: "Физру отменили", a: true }, { t: "стикер" }],
  [{ t: "а завтра точно пара?" }, { t: "Староста: сбор в 9:00", a: true }, { t: "ребят, привет" }, { t: "👍" }, { t: "скиньте конспект" }, { t: "Химия: лаба №3 до чт", a: true }, { t: "хм" }, { t: "го в 5" }],
  [{ t: "ща" }, { t: "Зачёт по английскому — в среду", a: true }, { t: "лол" }, { t: "кто в столовку" }, { t: "ааааа" }, { t: "Лекция в ауд. 214", a: true }, { t: "ну ок" }, { t: "я не понял" }],
  [{ t: "точно?" }, { t: "Деканат: справки до пт", a: true }, { t: "стикер" }, { t: "ржу" }, { t: "ребят, а ссылка на зум?" }, { t: "Новая методичка", a: true }, { t: "ну ок" }, { t: "норм" }],
];

export function Hero() {
  const stage = useRef<HTMLDivElement>(null);

  // лёгкий параллакс веера телефонов за курсором
  useEffect(() => {
    const el = stage.current;
    if (!el || reducedMotion()) return;
    const move = (e: PointerEvent) => {
      if (e.pointerType !== "mouse") return;
      el.style.setProperty("--mx", ((e.clientX / window.innerWidth - 0.5) * 2).toFixed(3));
      el.style.setProperty("--my", ((e.clientY / window.innerHeight - 0.5) * 2).toFixed(3));
    };
    window.addEventListener("pointermove", move, { passive: true });
    return () => window.removeEventListener("pointermove", move);
  }, []);

  return (
    <section className="hero night" id="top">
      <div className="wall" aria-hidden="true">
        {rows.map((r, i) => (
          <div key={i} className={`wall-row ${i % 2 ? "rev" : ""}`} style={{ ["--d" as string]: `${70 + i * 9}s` }}>
            {[0, 1].map((k) => (
              <div className="wall-track" key={k}>
                {r.map((m, j) => <span key={j} className={`wb ${m.a ? "wb-a" : ""}`}>{m.t}</span>)}
              </div>
            ))}
          </div>
        ))}
      </div>

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

        <div className="fan" ref={stage} aria-label="Экраны приложения StudGroup">
          <img className="fan-glass" src={calendar} alt="" width={150} />
          <div className="fan-ph f-l"><img src={appSchedule} alt="Расписание в приложении StudGroup" width={390} height={844} /></div>
          <div className="fan-ph f-r"><img src={appTasks} alt="Задания в приложении StudGroup" width={390} height={844} /></div>
          <div className="fan-ph f-c"><img src={appToday} alt="Экран «Сегодня» в приложении StudGroup" width={390} height={844} /></div>
          <div className="push p1"><b>Новое задание</b><span>Физика, задачи 4–9 — к четвергу</span></div>
          <div className="push p2"><b>Перенос пары</b><span>Матан в среду → 14:30, ауд. 214</span></div>
        </div>
      </div>
    </section>
  );
}
