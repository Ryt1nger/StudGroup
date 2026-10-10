import { useEffect, useRef, useState } from "react";
import type { CSSProperties, PointerEvent, ReactNode } from "react";
import {
  CONNECT_URL, GROUPS_COUNT, GROUP_PRICE, appPoints, faq, features, footerLinks, nav, plans, reviews, steps, universities,
} from "./content";
import logoDark from "./assets/brand/logo-lockup.dark.png";
import logoLight from "./assets/brand/logo-lockup.light.png";

const images = import.meta.glob<string>("./assets/*.webp", { eager: true, import: "default" });
const img = (name: string) => images[`./assets/${name}.webp`];

/** Scroll-reveal: атрибут + задержка каскада. Срабатывает через IntersectionObserver (см. useReveal). */
const rv = (i = 0) => ({ "data-reveal": "", style: { "--i": i } as CSSProperties });

function useReveal() {
  useEffect(() => {
    const els = Array.from(document.querySelectorAll<HTMLElement>("[data-reveal]"));
    if (!("IntersectionObserver" in window)) { els.forEach((e) => e.classList.add("in")); return; }
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }, { threshold: 0.15, rootMargin: "0px 0px -40px 0px" });
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, []);
}

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Число, которое «набегает» при появлении в зоне видимости. Без анимации при reduced motion. */
function CountUp({ value }: { value: string }) {
  const target = parseInt(value, 10);
  const suffix = value.replace(/^\d+/, "");
  const [n, setN] = useState(target);
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || Number.isNaN(target) || reducedMotion() || !("IntersectionObserver" in window)) return;
    let raf = 0;
    const io = new IntersectionObserver(([e]) => {
      if (!e.isIntersecting) return;
      io.disconnect();
      const t0 = performance.now(), dur = 1400;
      const tick = (t: number) => {
        const k = Math.min(1, (t - t0) / dur);
        setN(Math.round(target * (1 - Math.pow(1 - k, 3))));
        if (k < 1) raf = requestAnimationFrame(tick);
      };
      setN(0); raf = requestAnimationFrame(tick);
    }, { threshold: 0.6 });
    io.observe(el);
    return () => { io.disconnect(); cancelAnimationFrame(raf); };
  }, [target]);
  return <b ref={ref} aria-label={value}>{n}{suffix}</b>;
}

function TelegramIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
      <path fill="currentColor" d="M21.9 4.3 18.7 19.5c-.2 1-.9 1.3-1.7.8l-4.8-3.6-2.3 2.2c-.3.3-.5.5-1 .5l.3-4.9 8.9-8c.4-.3-.1-.5-.6-.2L6.5 13 1.8 11.5c-1-.3-1-1 .2-1.5L20.4 2.9c.9-.3 1.6.2 1.5 1.4Z" />
    </svg>
  );
}

function ConnectButton({ children = "Подключить группу", arrow = false, className = "" }: { children?: ReactNode; arrow?: boolean; className?: string }) {
  return (
    <a className={`btn btn-primary ${className}`} href={CONNECT_URL}>
      <TelegramIcon />
      <span>{children}</span>
      {arrow && <span aria-hidden="true">→</span>}
    </a>
  );
}

function Check() {
  return (
    <svg className="check" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="12" fill="currentColor" />
      <path d="m7 12.5 3.2 3.2L17 8.8" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Header() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 24);
    on(); window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  return (
    <header className={`header${scrolled ? " scrolled" : ""}`}>
      <div className="container header-row">
        <a href="#top" aria-label="StudGroup — на главную">
          <img className="logo" src={logoDark} alt="StudGroup" width="160" height="36" />
        </a>
        <nav className="nav" aria-label="Основная навигация">
          {nav.map((l) => (<a key={l.href} href={l.href}>{l.label}</a>))}
        </nav>
        <ConnectButton className="btn-sm">Подключить группу</ConnectButton>
      </div>
    </header>
  );
}

function Hero() {
  // Параллакс от указателя: только для мыши, CSS сам отключает его при reduced motion.
  const onMove = (e: PointerEvent<HTMLElement>) => {
    if (e.pointerType !== "mouse") return;
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--px", ((e.clientX - r.left) / r.width - 0.5).toFixed(3));
    e.currentTarget.style.setProperty("--py", ((e.clientY - r.top) / r.height - 0.5).toFixed(3));
  };
  const onLeave = (e: PointerEvent<HTMLElement>) => {
    e.currentTarget.style.setProperty("--px", "0");
    e.currentTarget.style.setProperty("--py", "0");
  };
  const d = (n: number) => ({ "--d": n }) as CSSProperties;
  return (
    <section className="hero" id="top" onPointerMove={onMove} onPointerLeave={onLeave}>
      <div className="container hero-grid">
        <div className="hero-copy">
          <span className="badge in-up" style={{ "--s": 0 } as CSSProperties}><TelegramIcon /> Всё в Telegram</span>
          <h1 className="in-up" style={{ "--s": 1 } as CSSProperties}>Учёба <span className="grad">под<br />контролем</span><br />в вашей группе</h1>
          <p className="lead in-up" style={{ "--s": 2 } as CSSProperties}>StudGroup анализирует сообщения вашей учебной группы и автоматически собирает задания, дедлайны, изменения в расписании и важные объявления.</p>
          <div className="in-up" style={{ "--s": 3 } as CSSProperties}>
            <ConnectButton arrow className="btn-lg">Подключить группу</ConnectButton>
            <p className="hero-note">Работает прямо в Telegram • 1 группа — {GROUP_PRICE} ₽/месяц</p>
          </div>
          <div className="proof in-up" style={{ "--s": 4 } as CSSProperties}>
            <div className="avatars" aria-hidden="true">
              {reviews.map((r) => (<img key={r.avatar} src={img(r.avatar)} alt="" width="36" height="36" />))}
            </div>
            <p>Уже помогают студентам<br />в {GROUPS_COUNT} учебных группах</p>
          </div>
        </div>
        <div className="hero-art">
          <span className="layer art-main" style={d(10)}>
            <img src={img("phones-dark")} alt="Экраны приложения StudGroup: «Сегодня» и «Задания»" width="900" height="1125" fetchPriority="high" />
          </span>
          <span className="layer art-float f-calendar" style={d(34)} aria-hidden="true"><img src={img("float-calendar")} alt="" width="277" height="267" /></span>
          <span className="layer art-float f-megaphone" style={d(22)} aria-hidden="true"><img src={img("float-megaphone")} alt="" width="295" height="257" /></span>
          <span className="layer art-float f-materials" style={d(26)} aria-hidden="true"><img src={img("float-materials")} alt="" width="403" height="234" /></span>
        </div>
      </div>
    </section>
  );
}

function Features() {
  return (
    <section className="features" id="features" aria-label="Возможности">
      <div className="container feature-grid">
        {features.map((f, i) => (
          <article key={f.title} className="feature" {...rv(i)}>
            <img src={img(f.icon)} alt="" width="84" height="84" loading="lazy" />
            <h3>{f.title}</h3>
            <p>{f.text}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function AppShowcase() {
  return (
    <section className="showcase">
      <div className="container showcase-grid">
        <div {...rv()}>
          <h2>Весь учебный процесс в одном приложении</h2>
          <p className="sub">StudGroup работает прямо в Telegram. Удобный WebApp, который показывает только нужное — без лишнего.</p>
          <ul className="points">
            {appPoints.map((p) => (<li key={p}><Check />{p}</li>))}
          </ul>
        </div>
        <img {...rv(1)} src={img("phones-light")} alt="Экраны приложения: «Задания», «Сегодня», «Предметы»" width="1000" height="750" loading="lazy" />
      </div>
    </section>
  );
}

function Pricing() {
  return (
    <section className="section" id="pricing">
      <div className="container">
        <h2 {...rv()}>Простые тарифы</h2>
        <p className="sub" {...rv(1)}>Выберите подходящий вариант для вашей учебной группы</p>
        <div className="plans">
          {plans.map((p, i) => (
            <article key={p.name} className={`plan${p.featured ? " featured" : ""}`} {...rv(i)}>
              {p.featured && <span className="ribbon">Рекомендуем</span>}
              <div className="plan-head">
                <img src={img(p.icon)} alt="" width="48" height="48" loading="lazy" />
                <div><h3>{p.name}</h3><span>{p.note}</span></div>
              </div>
              <p className="price"><strong>{p.price}</strong>{p.period && <span> {p.period}</span>}</p>
              <p className="plan-caption">{p.caption}</p>
              <ul>{p.items.map((i) => (<li key={i}><Check />{i}</li>))}</ul>
              {p.featured
                ? <ConnectButton className="btn-block">{p.cta}</ConnectButton>
                : <a className="btn btn-ghost btn-block" href={CONNECT_URL}>{p.cta}</a>}
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function HowItWorks() {
  return (
    <section className="section tight" id="how">
      <div className="container">
        <h2 {...rv()}>Как это работает</h2>
        <p className="sub" {...rv(1)}>Подключите группу за несколько минут</p>
        <ol className="steps">
          {steps.map((s, i) => (
            <li key={s.title} className="step" {...rv(i)}>
              <span className="step-n" aria-hidden="true">{i + 1}</span>
              <img src={img(s.icon)} alt="" width="64" height="56" loading="lazy" />
              <h3>{s.title}</h3>
              <p>{s.text}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function Universities() {
  return (
    <section className="section tight">
      <div className="container">
        <h2 {...rv()}>Подходит для всех вузов</h2>
        <p className="sub" {...rv(1)}>Уже используется в учебных группах</p>
        <div className="unis">
          {universities.map((u, i) => (
            <div key={u.name} className="uni" {...rv(i)}>
              <img src={img(u.logo)} alt="" width="44" height="44" loading="lazy" />
              <div><strong>{u.name}</strong><span>{u.city}</span></div>
            </div>
          ))}
          <div className="uni stat" {...rv(4)}>
            <div><CountUp value={GROUPS_COUNT} /><strong>учебных групп</strong><span>уже с нами</span></div>
            <img src={img("cap")} alt="" width="84" height="84" loading="lazy" />
          </div>
        </div>
      </div>
    </section>
  );
}

function Reviews() {
  return (
    <section className="section tight" id="reviews">
      <div className="container">
        <h2 {...rv()}>Что говорят студенты</h2>
        <div className="reviews">
          {reviews.map((r, i) => (
            <figure key={r.name} className="review" {...rv(i)}>
              <blockquote>«{r.text}»</blockquote>
              <figcaption>
                <img src={img(r.avatar)} alt="" width="44" height="44" loading="lazy" />
                <span><strong>{r.name}</strong><small>{r.meta}</small></span>
              </figcaption>
            </figure>
          ))}
        </div>
      </div>
    </section>
  );
}

function Faq() {
  return (
    <section className="section tight" id="faq">
      <div className="container">
        <div className="faq-head" {...rv()}><h2>Частые вопросы</h2><a href="#faq">Все вопросы →</a></div>
        <div className="faq">
          {faq.map((f, i) => (
            <details key={f.q} {...rv(i % 2)}>
              <summary>{f.q}</summary>
              <p>{f.a}</p>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}

function CtaBand() {
  return (
    <section className="cta-band">
      <div className="container cta-row" {...rv()}>
        <img className="cta-cap" src={img("cap")} alt="" width="120" height="120" loading="lazy" />
        <div>
          <h2>Подключите свою группу уже сегодня</h2>
          <p>Начните пользоваться StudGroup и сделайте учёбу проще</p>
        </div>
        <ConnectButton className="btn-lg">Подключить группу</ConnectButton>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="footer">
      <div className="container footer-row">
        <img className="logo" src={logoLight} alt="StudGroup" width="140" height="32" />
        <nav aria-label="Нижняя навигация">{footerLinks.map((l) => (<a key={l.label} href={l.href}>{l.label}</a>))}</nav>
        <small>© {new Date().getFullYear()} StudGroup. Учёба под контролем.</small>
      </div>
    </footer>
  );
}

export function App() {
  useReveal();
  return (
    <>
      <Header />
      <main>
        <Hero />
        <Features />
        <AppShowcase />
        <Pricing />
        <HowItWorks />
        <Universities />
        <Reviews />
        <Faq />
        <CtaBand />
      </main>
      <Footer />
    </>
  );
}
