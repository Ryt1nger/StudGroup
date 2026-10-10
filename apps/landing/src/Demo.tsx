import { useState } from "react";
import { probes, type Verdict } from "./content";
import { Mark } from "./ui";

const stampText: Record<Verdict, string> = { card: "В карточки", ask: "Нужно уточнить", skip: "Пропущено", rev: "Правка" };

export function Demo() {
  const [id, setId] = useState<string | null>(null);
  const p = probes.find((x) => x.id === id) ?? null;

  return (
    <section className="demo paper" id="demo">
      <div className="wrap">
        <header className="sec-head">
          <h2 className="h2">Нажмите на сообщение — <Mark>увидите, что сделает бот</Mark></h2>
          <p className="lead dim">Не всё, что написано в чате, должно стать заданием. Вот как StudGroup решает, что с этим делать.</p>
        </header>

        <div className="probe-grid">
          <ul className="probe-list" role="listbox" aria-label="Сообщения из чата">
            {probes.map((x) => (
              <li key={x.id}>
                <button
                  className={`bubble ${id === x.id ? "is-on" : ""}`}
                  role="option"
                  aria-selected={id === x.id}
                  onClick={() => setId(x.id)}
                >
                  <span className="who">{x.from}</span>
                  <span>{x.text}</span>
                </button>
              </li>
            ))}
          </ul>

          <div className="sheet" aria-live="polite">
            {!p && (
              <p className="sheet-empty">
                <span className="arrow-left" aria-hidden="true" />
                Выберите сообщение слева
              </p>
            )}
            {p && (
              <div key={p.id} className={`sheet-in v-${p.verdict}`}>
                <span className="stamp">{stampText[p.verdict]}</span>
                <h3>{p.title}</h3>
                <dl>
                  {p.fields.map(([k, v]) => (
                    <div key={k}>
                      <dt>{k}</dt>
                      <dd>{v}</dd>
                    </div>
                  ))}
                </dl>
                <p className="why">{p.why}</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
