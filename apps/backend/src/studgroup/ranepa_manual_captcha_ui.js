"use strict";
(async () => {
  const app = window.Telegram?.WebApp;
  const status = document.getElementById("status");
  const puzzle = document.getElementById("puzzle");
  const button = document.getElementById("submit");
  const id = location.pathname.split("/").filter(Boolean).pop();
  if (!app?.initData || !/^[a-f0-9-]{36}$/.test(id)) {
    status.textContent = "Откройте кнопку «Пройти CAPTCHA» непосредственно из личного чата Telegram.";
    return;
  }
  app.ready();
  app.expand();
  const endpoint = "/v1/ranepa-captcha/" + id;
  const call = async (action, extra = {}) => {
    const response = await fetch(endpoint + "/" + action, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      cache: "no-store",
      body: JSON.stringify({init_data: app.initData, ...extra}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data?.error?.message || "Проверка недоступна");
    return data;
  };
  let state;
  try {
    state = await call("state");
  } catch (error) {
    status.textContent = error.message;
    return;
  }
  if (state.status !== "pending" || !state.screenshot || !state.left || !state.right) {
    status.textContent = "Эта CAPTCHA уже закрыта или срок её решения истёк.";
    return;
  }
  document.getElementById("challenge").src = state.screenshot;
  document.getElementById("left").src = state.left;
  document.getElementById("right").src = state.right;
  puzzle.hidden = false;
  status.textContent = "Сессия активна. Подберите два поворота и отправьте ответ.";
  for (const side of ["left","right"]) {
    const slider = document.getElementById("angle-" + side);
    const image = document.getElementById(side);
    const display = document.getElementById("value-" + side);
    const update = () => {
      const angle = Number(slider.value);
      image.style.transform = "rotate(" + angle + "deg)";
      display.textContent = angle + "°";
    };
    slider.addEventListener("input", update);
    update();
  }
  button.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "Передаём решение в живую сессию…";
    try {
      const result = await call("answer", {
        angle_left: Number(document.getElementById("angle-left").value),
        angle_right: Number(document.getElementById("angle-right").value),
      });
      status.textContent = result.message;
      puzzle.hidden = true;
    } catch (error) {
      button.disabled = false;
      status.textContent = error.message;
    }
  });
})();
