import { useEffect, useRef, useState } from "react";
import { dayScenes } from "./content";
import appToday from "./assets/app-today.webp";
import appTasks from "./assets/app-tasks.webp";
import appSchedule from "./assets/app-schedule.webp";

type Tab = (typeof dayScenes)[number]["tab"];

const shots: Record<Tab, { src: string; alt: string }> = {
  today: { src: appToday, alt: "Экран «Сегодня» в приложении StudGroup: следующая пара и дедлайны дня" },
  tasks: { src: appTasks, alt: "Экран «Задания» в приложении StudGroup: ближайшие дедлайны и фильтры" },
  schedule: { src: appSchedule, alt: "Экран «Расписание» в приложении StudGroup: пары по дням недели" },
};

function Phone({ tab }: { tab: Tab }) {
  const d = shots[tab];
  return (
    <div className="phone">
      <img key={tab} src={d.src} alt={d.alt} width={390} height={844} />
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
