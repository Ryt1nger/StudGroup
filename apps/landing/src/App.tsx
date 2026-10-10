import { useEffect, useState } from "react";
import { CONNECT_URL, nav } from "./content";
import { Hero } from "./Hero";
import { Sweep } from "./Sweep";
import { Demo } from "./Demo";
import { Day } from "./Day";
import { Rules, Plans, Faq, Cta, Footer, logoDark } from "./Rest";

function Header() {
  const [solid, setSolid] = useState(false);
  useEffect(() => {
    const f = () => setSolid(window.scrollY > 24);
    f();
    window.addEventListener("scroll", f, { passive: true });
    return () => window.removeEventListener("scroll", f);
  }, []);
  return (
    <header className={`top ${solid ? "solid" : ""}`}>
      <div className="wrap top-in">
        <a href="#top" aria-label="StudGroup, наверх"><img src={logoDark} alt="StudGroup" height={30} /></a>
        <nav aria-label="Разделы">
          {nav.map((n) => <a key={n.href} href={n.href}>{n.label}</a>)}
        </nav>
        <a className="btn btn-marker btn-sm" href={CONNECT_URL} target="_blank" rel="noopener noreferrer">Подключить</a>
      </div>
    </header>
  );
}

export function App() {
  return (
    <>
      <a className="skip" href="#demo">К содержанию</a>
      <Header />
      <main>
        <Hero />
        <Sweep />
        <Demo />
        <Day />
        <Rules />
        <Plans />
        <Faq />
        <Cta />
      </main>
      <Footer />
    </>
  );
}
