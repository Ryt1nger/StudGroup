import type { ReactNode } from "react";
import {
  CONNECT_URL, GROUPS_COUNT, GROUP_PRICE, appPoints, faq, features, footerLinks, nav, plans, reviews, steps, universities,
} from "./content";
import logoDark from "./assets/brand/logo-lockup.dark.png";
import logoLight from "./assets/brand/logo-lockup.light.png";

const images = import.meta.glob<string>("./assets/*.webp", { eager: true, import: "default" });
const img = (name: string) => images[`./assets/${name}.webp`];

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
  return (
    <header className="header">
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
  return (
    <section className="hero" id="top">
      <div className="container hero-grid">
        <div className="hero-copy">
          <span className="badge"><TelegramIcon /> Всё в Telegram</span>
          <h1>Учёба <span className="grad">под<br />контролем</span><br />в вашей группе</h1>
          <p className="lead">StudGroup анализирует сообщения вашей учебной группы и автоматически собирает задания, дедлайны, изменения в расписании и важные объявления.</p>
          <ConnectButton arrow className="btn-lg">Подключить группу</ConnectButton>
          <p className="hero-note">Работает прямо в Telegram • 1 группа — {GROUP_PRICE} ₽/месяц</p>
          <div className="proof">
            <div className="avatars" aria-hidden="true">
              {reviews.map((r) => (<img key={r.avatar} src={img(r.avatar)} alt="" width="36" height="36" />))}
            </div>
            <p>Уже помогают студентам<br />в {GROUPS_COUNT} учебных группах</p>
          </div>
        </div>
        <div className="hero-art">
          <img src={img("phones-dark")} alt="Экраны приложения StudGroup: «Сегодня» и «Задания»" width="900" height="1125" fetchPriority="high" />
        </div>
      </div>
    </section>
  );
}

function Features() {
  return (
    <section className="features" id="features" aria-label="Возможности">
      <div className="container feature-grid">
        {features.map((f) => (
          <article key={f.title} className="feature">
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
        <div>
          <h2>Весь учебный процесс в одном приложении</h2>
          <p className="sub">StudGroup работает прямо в Telegram. Удобный WebApp, который показывает только нужное — без лишнего.</p>
          <ul className="points">
            {appPoints.map((p) => (<li key={p}><Check />{p}</li>))}
          </ul>
        </div>
        <img src={img("phones-light")} alt="Экраны приложения: «Задания», «Сегодня», «Предметы»" width="1000" height="750" loading="lazy" />
      </div>
    </section>
  );
}

function Pricing() {
  return (
    <section className="section" id="pricing">
      <div className="container">
        <h2>Простые тарифы</h2>
        <p className="sub">Выберите подходящий вариант для вашей учебной группы</p>
        <div className="plans">
          {plans.map((p) => (
            <article key={p.name} className={`plan${p.featured ? " featured" : ""}`}>
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
        <h2>Как это работает</h2>
        <p className="sub">Подключите группу за несколько минут</p>
        <ol className="steps">
          {steps.map((s, i) => (
            <li key={s.title} className="step">
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
        <h2>Подходит для всех вузов</h2>
        <p className="sub">Уже используется в учебных группах</p>
        <div className="unis">
          {universities.map((u) => (
            <div key={u.name} className="uni">
              <img src={img(u.logo)} alt="" width="44" height="44" loading="lazy" />
              <div><strong>{u.name}</strong><span>{u.city}</span></div>
            </div>
          ))}
          <div className="uni stat">
            <div><b>{GROUPS_COUNT}</b><strong>учебных групп</strong><span>уже с нами</span></div>
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
        <h2>Что говорят студенты</h2>
        <div className="reviews">
          {reviews.map((r) => (
            <figure key={r.name} className="review">
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
        <div className="faq-head"><h2>Частые вопросы</h2><a href="#faq">Все вопросы →</a></div>
        <div className="faq">
          {faq.map((f) => (
            <details key={f.q}>
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
      <div className="container cta-row">
        <img src={img("cap")} alt="" width="120" height="120" loading="lazy" />
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
