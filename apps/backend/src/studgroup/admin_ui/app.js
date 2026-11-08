/* StudGroup owner UI. Source of every figure: protected backend API. */
"use strict";
const $=id=>document.getElementById(id),C=window.StudCharts;
const nf=new Intl.NumberFormat("ru-RU");
const number=n=>n==null?"Нет данных":nf.format(n);
const money=n=>n==null?"Нет данных":"$"+Number(n).toFixed(4);
const datetime=x=>x?new Date(x).toLocaleString("ru-RU",{day:"numeric",month:"short",hour:"2-digit",minute:"2-digit",timeZone:"Europe/Moscow"})+" МСК":"Дата не записана";
const date=x=>x?new Date(x).toLocaleDateString("ru-RU",{day:"numeric",month:"short",timeZone:"UTC"}):"";
const label={
  groups:"Подключённые группы",members:"Участники групп",messages:"Сообщения в чатах",
  bot_updates:"Обновления Telegram",bot_panels:"Текущие панели бота",bot_sessions:"Действующие сессии приложения",
  text:"Обработки текстовых сообщений",images:"Анализ фотографий",legacy:"Старая очередь",
  errors:"Ошибки обработки",incidents:"Системные предупреждения",expenses:"Списания AI",
  month_expenses:"Расходы на AI в этом месяце",reserves:"Незакрытые резервы AI",
  homework:"Домашние задания",events:"События",notifications:"Уведомления",telegram:"Отправка Telegram"
};
const state={
  completed:"Выполнено",ready:"Готово",sent:"Отправлено",failed:"Ошибка",error:"Ошибка",
  queued:"В очереди",pending:"Ожидает",running:"Обрабатывается",processing:"Обрабатывается",
  stale:"Результат устарел",superseded:"Заменено новой версией",settled:"Списано",
  rejected:"Отклонено",canceled:"Отменено",cancelled:"Отменено",published:"Опубликовано",
  legacy:"Старый режим",draft:"Черновик",unknown:"Неизвестно",active:"Активно",
  открыто:"Требует внимания",восстановлено:"Восстановлено"
};
const fields={
  id:"Номер записи",group_id:"Идентификатор группы",attempts:"Сколько попыток",
  engine:"Версия анализа",latency_ms:"Длительность, миллисекунды",
  charged_usd:"Списанная сумма, USD",reserved_usd:"Резерв, USD",
  settled_at:"Дата списания",tokens_in:"Входные токены",tokens_out:"Выходные токены",
  pass_number:"Номер прохода",model:"Модель",reused:"Результат переиспользован",
  error_code:"Технический код ошибки",revision:"Редакция",imported:"Импортировано",
  role:"Роль",published_at:"Дата публикации",verification:"Статус проверки",
  deadline:"Срок выполнения",detail_code:"Код причины",occurrences:"Количество повторений",
  last_seen_at:"Последний сигнал",recovered_at:"Восстановлено",
  pilot_authorized:"Доступ к пилоту",schedule_version:"Версия расписания",
  expires_at:"Сессия действует до",mode:"Режим панели",generation:"Версия панели",
};
let token=null,loadedGroups=false,activeRequest=0,detailRequest=0,detailKind=null,detailOffset=0,previousFocus=null,lastData=null,lastVisuals=null,lastBot=null,outcome="text",currentPage="overview";
function dom(tag,cls,value){const e=document.createElement(tag);if(cls)e.className=cls;if(value!==undefined)e.textContent=String(value);return e;}
function text(host,tag,cls,value){const e=dom(tag,cls,value);host.append(e);return e;}
function clear(id){$(id).replaceChildren();}
function hint(v){return v===undefined||v===null?"Нет данных":String(v);}
function russianState(v){return state[v]||"Другой статус: "+String(v);}
function criteria(){const p=new URLSearchParams({days:$("days").value});if($("group").value)p.set("group_id",$("group").value);return p;}
async function api(url){const r=await fetch(url,{cache:"no-store",headers:{Authorization:"Bearer "+token}});let result;try{result=await r.json();}catch{throw Error("Сервер ответил в непонятном формате");}if(!r.ok)throw Error(result.error?.message||"Сервер ответил с ошибкой "+r.status);return result;}
function spark(host,values,color){
  if(!Array.isArray(values)||values.length<2)return;
  const ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");
  svg.setAttribute("viewBox","0 0 110 34");svg.setAttribute("preserveAspectRatio","none");
  const max=Math.max(1,...values.map(Number)),points=values.map((v,i)=>(i/(values.length-1)*110)+","+(31-(Number(v)||0)/max*28));
  const line=document.createElementNS(ns,"polyline");line.setAttribute("points",points.join(" "));
  line.setAttribute("fill","none");line.setAttribute("stroke",color||"#43d9df");
  line.setAttribute("stroke-width","2.4");line.setAttribute("stroke-linejoin","round");
  svg.append(line);host.append(svg);
}
function comparisonNote(record,higherIsWorse=false){
  if(!record)return null;
  const current=Number(record.current)||0,previous=Number(record.previous)||0;
  if(!previous)return current?"В предыдущем периоде не было":"Без изменений";
  const delta=Math.round((current-previous)/previous*100);
  if(!delta)return "Как в предыдущем периоде";
  return (delta>0?"↑ ":"↓ ")+Math.abs(delta)+"% к прошлому периоду";
}
function buttonTile(host,title,value,note,detail,extra="",seriesValues=null,comparison=null){
  const b=dom("button","kpi-card "+extra);b.type="button";b.dataset.detail=detail;
  const header=dom("div","kpi-header");text(header,"span","kpi-heading",title);text(header,"span","kpi-index","↗");
  const middle=dom("div","kpi-middle");text(middle,"span","kpi-value",value);
  if(seriesValues?.length>1){const graph=dom("span","kpi-spark");spark(graph,seriesValues,extra==="danger"?"#c45e63":"#43d9df");middle.append(graph);}
  const footer=dom("div","kpi-footer");text(footer,"span","kpi-note",note);
  const change=comparisonNote(comparison);
  if(change){const cls=change.startsWith("↑")&&extra==="danger"?"negative":change.startsWith("↓")&&extra==="danger"?"":"neutral";text(footer,"span","kpi-change "+cls,change);}
  b.append(header,middle,footer);host.append(b);
}
function info(host,title,value,note,detail){
  const b=dom("button","strip-stat");b.type="button";
  if(detail)b.dataset.detail=detail;else b.dataset.info=note;
  text(b,"span","strip-label",title);text(b,"strong","strip-value",value);text(b,"span","strip-explain",note);
  host.append(b);
}
function financeTile(host,title,value,note,kind,primary=false){
  const btn=dom("button","finance-stat"+(primary?" primary":""));btn.type="button";
  if(kind)btn.dataset.detail=kind;else btn.dataset.info=note;
  text(btn,"span","finance-kicker",title);text(btn,"strong","finance-main",value);text(btn,"span","finance-explainer",note);
  host.append(btn);
}
function metric(host,title,value,note){
  const block=dom("button","server-cell");block.type="button";block.dataset.visual="server";
  text(block,"span","",title);text(block,"strong","",value);text(block,"small","",note);
  host.append(block);
}
function addInsight(host,value,critical=false){text(host,"span","snapshot-pill"+(critical?" critical":""),value);}
function explainError(code){return C.humanError(code||"");}
const incidentLabels={
  pipeline_error:"Ошибка в процессе обработки",
  processing_exhausted:"Обработка остановилась после повторов",
  ai_request_uncertain:"Неизвестен итог запроса AI",
  ai_hourly_safety_limit:"Сработал часовой лимит AI",
  invalid_source_reference:"Не найдено исходное сообщение",
  ai_processing_paused:"Работа AI приостановлена",
  ai_circuit_open:"Защита временно остановила AI",
  ai_budget_limit:"Исчерпан доступный бюджет AI",
  invalid_date_only:"Проблема с датой события"
};
function humanIncident(code){return incidentLabels[code]||"Другое системное предупреждение";}

function statusMean(status){
  if(status==="failed"||status==="error")return "Обработка завершилась ошибкой. Откройте технические данные, чтобы увидеть её код.";
  if(status==="stale")return "После изменения исходных данных этот результат стал неактуален. Это не обязательно сбой.";
  if(status==="ready"||status==="completed")return "Обработка успешно завершена, результат сохранён.";
  if(status==="superseded")return "Результат заменён более новой версией.";
  if(status==="pending"||status==="queued")return "Операция ждёт своей очереди.";
  if(status==="sent")return "Сообщение передано в Telegram.";
  return "Статус взят из рабочего журнала системы.";
}
function renderSnapshot(s){
  clear("snapshot-summary");const host=$("snapshot-summary");
  if(s.incidents_open)addInsight(host,number(s.incidents_open)+" открытых предупреждений",true);
  else addInsight(host,"Нет открытых системных предупреждений");
  if(s.errors)addInsight(host,number(s.errors)+" ошибок обработки за период",true);
  else addInsight(host,"Ошибки обработки не зафиксированы");
  addInsight(host,number(s.messages)+" сообщений получено");
  addInsight(host,number(s.text_runs+s.image_runs)+" прогонов AI");
}
function renderKpi(s,v){
  const host=$("kpi-grid");host.replaceChildren();
  const data=v.daily||[],seriesFor=key=>data.map(d=>Number(d[key])||0);
  buttonTile(host,"Сообщения в группах",number(s.messages),"За выбранный период","messages","",seriesFor("messages"),v.comparison?.messages);
  buttonTile(host,"Анализ текста",number(s.text_runs),"Запущенных обработок","text","",seriesFor("text"),v.comparison?.text);
  buttonTile(host,"Анализ фото",number(s.image_runs),"Проходов анализа, не фотографий","images","",seriesFor("images"),v.comparison?.images);
  buttonTile(host,"Ошибки обработки",number(s.errors),"В разных очередях","errors",s.errors?"danger":"",data.map(d=>Number(d.text_errors||0)+Number(d.image_errors||0)+Number(d.legacy_errors||0)),v.comparison?.errors);
  buttonTile(host,"Расходы на AI за месяц",money(s.month_spent_usd),"Из бюджета AI, не все расходы","month_expenses");
  buttonTile(host,"Открытые предупреждения",number(s.incidents_open),"По всей системе прямо сейчас","incidents",s.incidents_open?"danger":"");
}
function series(data,key){return data.daily.map(d=>Number(d[key])||0);}
function chartClickable(id,kind){const host=$(id);host.dataset.detail=kind;host.setAttribute("role","button");host.setAttribute("tabindex","0");host.title="Нажмите, чтобы открыть записи";host.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();openDetail(kind);}};}
function visualClickable(id,kind){
  const host=$(id);delete host.dataset.detail;host.dataset.visual=kind;
  host.setAttribute("role","button");host.setAttribute("tabindex","0");
  host.title="Нажмите, чтобы увидеть разбор графика";
  host.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();openVisual(kind);}};
}
function openVisual(kind){
  previousFocus=document.activeElement;detailKind=null;detailRequest++;
  const modal=$("modal");modal.hidden=false;document.body.style.overflow="hidden";
  clear("modal-items");$("modal-more").hidden=true;$("modal-status").textContent="";
  const host=$("modal-items");
  if(kind==="server"){
    $("modal-title").textContent="Нагрузка на сервер";
    $("modal-subtitle").textContent="Фактические запросы за последние 15 минут";
    const telemetry=lastVisuals?.server;
    if(!telemetry){text(host,"div","empty","Измерений пока нет.");}
    else{
      const note=dom("article","detail");text(note,"p","detail-explanation","Здесь показаны только результаты и скорость работы API. Мы не сохраняем адреса запросов, личные данные и токены. После перезапуска сервера отсчёт начинается заново.");host.append(note);
      for(const item of telemetry.requests||[]){
        const row=dom("article","detail");const header=dom("div","detail-top");
        text(header,"strong","",new Date(item.minute).toLocaleTimeString("ru-RU",{timeZone:"Europe/Moscow",hour:"2-digit",minute:"2-digit"})+" МСК");
        text(header,"span","pill",number(item.count)+" запросов");row.append(header);
        text(row,"p","detail-explanation","Ответов с ошибкой сервера: "+number(item.errors_5xx));
        host.append(row);
      }
    }
  }else{
    const isAi=kind==="ai";
    $("modal-title").textContent=isAi?"Что анализирует AI":"Что появилось у студентов";
    $("modal-subtitle").textContent=isAi?"Отдельные журналы текста и изображений":"Детализация созданных материалов за выбранный период";
    const note=dom("article","detail");
    text(note,"p","detail-explanation",isAi?"Один анализ фотографии может состоять из двух проходов. Результаты текста и фото не складываются в число уникальных сообщений.":"Публикации и уведомления — разные операции. Число записей не равно числу просмотров студентами.");host.append(note);
    const actions=isAi?[["Прогоны текста","text"],["Проходы обработки фото","images"]]:[["Домашние задания","homework"],["События","events"],["Уведомления","notifications"]];
    for(const [title,detail] of actions){
      const b=dom("button","incident-row");b.type="button";b.dataset.detail=detail;
      text(b,"span","incident-title",title);text(b,"span","incident-badge ok","Открыть журнал ↗");host.append(b);
    }
  }
  $("modal-close").focus();
}

function renderActivity(d,v,bot){
  const days=v.daily.map(x=>x.date);
  C.plot("chart-activity",[{label:"Сообщения",values:series(v,"messages"),color:"#43d9df"}],{labels:days,title:"Сообщения по дням",area:true});chartClickable("chart-activity","messages");
  C.hourBars("chart-hours",v.by_hour_moscow,"МСК");chartClickable("chart-hours","messages");
  const host=$("usage-strip");host.replaceChildren();
  info(host,"Пользователей в базе",number(bot.people.users_total),"Все зарегистрированные аккаунты",null);
  info(host,"Участников групп",number(bot.people.active_memberships),"Действующие членства","members");
  info(host,"Авторов сообщений",number(bot.people.unique_senders),"Писали в чатах за период","messages");
  info(host,"Действующих сессий",number(bot.people.active_sessions),"Активные сессии сейчас; не входы","bot_sessions");
  info(host,"Людей с активной сессией",number(bot.people.session_users),"Разные аккаунты в действующих сессиях","bot_sessions");
  info(host,"Создано событий",number(d.summary.events),"Записей за выбранный период","events");
  info(host,"История входов","Не собирается","Сейчас нет данных о прошлых запусках WebApp",null);
  info(host,"Подключённые группы",number(d.summary.groups),"Активные пилотные группы","groups");
  $("activity-caption").textContent="За "+$("days").selectedOptions[0].textContent.toLowerCase()+" · последнее сообщение: "+datetime(d.summary.last_message_at)+".";
}
function renderOutcomes(d){
  const statuses=d.states[outcome]||{},kind=outcome;
  const colors={completed:"#43d9df",ready:"#43d9df",sent:"#43d9df",failed:"#ec708b",error:"#ec708b",stale:"#f4b56d",superseded:"#83b7ff",queued:"#83b7ff",pending:"#83b7ff",running:"#8c8bff",processing:"#8c8bff"};
  const entries=Object.entries(statuses).sort((a,b)=>b[1]-a[1]);
  const items=entries.map(([key,value])=>({label:russianState(key),value,color:colors[key]||"#7187a8"}));
  C.donut("chart-outcomes",items,{title:"Распределение результатов обработки",centerLabel:"обработок"});chartClickable("chart-outcomes",kind);
  $("outcome-description").textContent=kind==="images"?"«Устарел» означает, что результат был заменён после изменения исходных данных. Это не сбой.":"Процент готовых результатов не равен точности AI. Проверку качества нужно считать отдельно.";
  for(const tab of $("outcome-tabs").querySelectorAll("button"))tab.classList.toggle("active",tab.dataset.outcome===outcome);
}
function renderAi(d,v){
  const dates=v.daily.map(x=>x.date);
  C.bars("chart-workload",[{label:"Текст",values:series(v,"text"),color:"#43d9df",kind:"text"},{label:"Фото",values:series(v,"images"),color:"#8c8bff",kind:"images"}],{labels:dates,title:"Количество прогонов по дням"});visualClickable("chart-workload","ai");
  renderOutcomes(d);
  C.plot("chart-latency",[{label:"Текст",values:v.daily.map(x=>x.text_latency_ms==null?null:x.text_latency_ms/1000),color:"#43d9df",kind:"text"},{label:"Фото",values:v.daily.map(x=>x.photo_latency_ms==null?null:x.photo_latency_ms/1000),color:"#f4b56d",kind:"images"}],{labels:dates,title:"Скорость обработки AI"});visualClickable("chart-latency","ai");
  C.bars("chart-output",[{label:"ДЗ",values:series(v,"homework"),color:"#43d9df",kind:"homework"},{label:"События",values:series(v,"events"),color:"#8c8bff",kind:"events"},{label:"Уведомления",values:series(v,"notifications"),color:"#f4b56d",kind:"notifications"}],{labels:dates,title:"Созданные записи"});visualClickable("chart-output","materials");
}
function renderFinance(d,v){
  clear("finance-summary");financeTile($("finance-summary"),"Списано за месяц",money(d.finance.month_charged_usd),"Календарный месяц, UTC","month_expenses",true);
  financeTile($("finance-summary"),"Зарезервировано",money(d.finance.unsettled_reserved_usd),"Незакрытые резервы, за всё время","reserves");
  financeTile($("finance-summary"),"Все оплаты компании","Не подключено","Нет данных о счетах и платёжных сервисах",null);
  C.bars("chart-finance",[{label:"Списано",values:v.finance_month.series.map(x=>Number(x.value)||0),color:"#43d9df"}],{labels:v.finance_month.series.map(x=>x.date),currency:true,title:"Дневные расходы на AI"});chartClickable("chart-finance","month_expenses");
  const kindNames={cplus:"Анализ текста",image:"Обработка фото",cabinet_image:"Фото из учебной системы",text:"Текстовые запросы"};
  const kinds=v.finance_month.by_kind.map((x,i)=>({label:kindNames[x.kind]||"Другая обработка",value:Number(x.usd),color:C.colors[i%C.colors.length]}));
  C.donut("chart-finance-kind",kinds,{title:"Распределение расходов AI",centerLabel:"USD"});chartClickable("chart-finance-kind","month_expenses");
  const host=$("unavailable-finance");host.replaceChildren();for(const [title,note] of [["Баланс AI-провайдеров","Нет подключения к биллингу провайдеров"],["Баланс компании и будущие счета","Не подключены банковские и платёжные источники"]]){
    const b=dom("button","integration");b.type="button";b.dataset.info=note;text(b,"small","","НЕТ ПОДКЛЮЧЕНИЯ");text(b,"b","",title);text(b,"span","",note);host.append(b);
  }
}
function renderServer(d,v,roundTrip){
  const server=v.server,host=$("server-overview");host.replaceChildren();
  metric(host,"Обычный ответ сервера",server.p50_ms==null?"Нет замеров":number(server.p50_ms)+" мс","Медиана за 15 минут");
  metric(host,"Медленные ответы сервера",server.p95_ms==null?"Нет замеров":number(server.p95_ms)+" мс","95% измеренных запросов не медленнее");
  metric(host,"Ошибки сервера",number(server.errors_5xx),"Ответы с кодом 5xx, 15 минут");
  metric(host,"Запросов к API",number(server.sample_count),"Измерены после запуска текущего процесса");
  C.bars("chart-requests",[{label:"Обычные ответы",values:server.requests.map(x=>Math.max(0,x.count-x.errors_5xx)),color:"#43d9df"},{label:"Серверные ошибки",values:server.requests.map(x=>x.errors_5xx),color:"#ec708b"}],{labels:server.requests.map(x=>x.minute.slice(11,16)),title:"Запросы API поминутно"});
  visualClickable("chart-requests","server");
  $("server-caption").textContent=server.note+" Последний запрос панели занял "+number(roundTrip)+" мс с учётом сети.";
  clear("queue-detail");const list=$("queue-detail");
  for(const [title,key,detail] of [["Текстовый анализ","text","text"],["Анализ фотографий","images","images"],["Старая обработка","legacy","legacy"],["Исходящие Telegram","telegram","telegram"]]){
    const counts=d.states[key]||{};const b=dom("button","queue-row");b.type="button";b.dataset.detail=detail;text(b,"strong","",title);text(b,"span","",number(Object.values(counts).reduce((a,n)=>a+n,0))+" записей ↗");list.append(b);
  }
}
function renderErrors(d,v){
  const dates=v.daily.map(x=>x.date);
  C.bars("chart-errors",[{label:"Текст",values:series(v,"text_errors"),color:"#ec708b",kind:"errors"},{label:"Фото",values:series(v,"image_errors"),color:"#f4b56d",kind:"errors"},{label:"Старая очередь",values:series(v,"legacy_errors"),color:"#83b7ff",kind:"errors"}],{labels:dates,title:"Ошибки обработки по дням"});chartClickable("chart-errors","errors");
  C.reasons("chart-reasons",v.error_reasons);
  loadRecent();
}
const PAGE_META={
 overview:{title:"Обзор",eyebrow:"STUDGROUP / СВОДКА",intro:"Самое важное о работе сервиса — без лишних технических терминов."},
 bot:{title:"Бот и Telegram",eyebrow:"STUDGROUP / TELEGRAM",intro:"Получение событий, отправка ответов и текущая работа бота."},
 audience:{title:"Пользователи",eyebrow:"STUDGROUP / ЛЮДИ",intro:"Кто пользуется приложением и насколько активны учебные группы."},
 ai:{title:"AI и обработка",eyebrow:"STUDGROUP / ОБРАБОТКА",intro:"Нагрузка, скорость, готовые результаты и ошибки искусственного интеллекта."},
 finance:{title:"Финансы",eyebrow:"STUDGROUP / РАСХОДЫ",intro:"Подтверждённые списания AI. Балансы и другие платежи показаны только при наличии данных."},
 server:{title:"Система",eyebrow:"STUDGROUP / ПРОИЗВОДИТЕЛЬНОСТЬ",intro:"Скорость ответов сервера, нагрузка и состояние очередей."},
 errors:{title:"Ошибки",eyebrow:"STUDGROUP / ИНЦИДЕНТЫ",intro:"Что пошло не так, когда произошло и какие причины записала система."}
};
function statRow(host,title,value,detail){
  const el=dom(detail?"button":"div","quick-stat");if(detail){el.type="button";el.dataset.detail=detail;}
  text(el,"span","",title);text(el,"strong","",value);
  host.append(el);
}
function botTile(host,title,value,note,detail,critical=false){
  const el=dom("button","bot-tile"+(critical?" critical":""));el.type="button";
  if(detail)el.dataset.detail=detail;else el.dataset.info=note;
  text(el,"span","bot-tile-name",title);
  text(el,"strong","bot-tile-value",value);
  text(el,"small","bot-tile-note",note);
  host.append(el);
}
function renderOverview(d,v,b){
  const dates=v.daily.map(p=>p.date);
  C.plot("chart-overview-messages",[
    {label:"Сообщения",values:series(v,"messages"),color:"#a1b7d7",kind:"messages"}
  ],{labels:dates,title:"Активность учебных чатов",area:true});
  chartClickable("chart-overview-messages","messages");
  C.bars("chart-overview-ai",[
    {label:"Текст",values:series(v,"text"),color:"#87a9ef",kind:"text"},
    {label:"Фото",values:series(v,"images"),color:"#b4a5ca",kind:"images"}
  ],{labels:dates,title:"Текст и фото по дням"});
  visualClickable("chart-overview-ai","ai");
  clear("overview-bot");
  statRow($("overview-bot"),"Обновления Telegram за период",number(b.bot.webhook_updates),"bot_updates");
  statRow($("overview-bot"),"Ответы успешно отправлены (всё время)",number(b.bot.sent),"telegram");
  statRow($("overview-bot"),"Неотправленные ошибки (всё время)",number(b.bot.failed),"telegram");
  clear("overview-spend");
  statRow($("overview-spend"),"AI за текущий месяц",money(d.finance.month_charged_usd),"month_expenses");
  statRow($("overview-spend"),"Незакрытые резервы",money(d.finance.unsettled_reserved_usd),"reserves");
  statRow($("overview-spend"),"Баланс компании","Не подключён",null);
}
function renderBot(d,v,b){
  clear("bot-stats");const host=$("bot-stats");
  botTile(host,"События Telegram",number(b.bot.webhook_updates),"За выбранный период · весь бот","bot_updates");
  botTile(host,"Сообщения в группах",number(b.messages.period_count),"За выбранный период · выбранная группа","messages");
  botTile(host,"Ответы отправлены",number(b.bot.sent),"За всё время, нет даты отправки","telegram");
  botTile(host,"Ошибки отправки",number(b.bot.failed),"За всё время · в очереди Telegram","telegram",b.bot.failed>0);
  botTile(host,"Ожидают отправки",number(b.bot.pending),"Текущее состояние очереди","telegram",b.bot.pending>0);
  botTile(host,"Панели бота",number(b.bot.panel_total),"Текущие панели, не открытия","bot_panels");
  C.plot("chart-bot-updates",[{label:"Обновлений Telegram",values:b.daily.map(p=>p.updates),color:"#a8bedb",kind:"bot_updates"}],{
    labels:b.daily.map(p=>p.date),title:"Входящие обновления от Telegram",area:true
  });
  chartClickable("chart-bot-updates","bot_updates");
  const order=["sent","pending","retry","queued","failed","dead","dead_letter","superseded","sending"];
  const labels={sent:"Отправлено",pending:"Ожидает",retry:"Повтор",queued:"В очереди",failed:"Ошибка",dead:"Не доставлено",dead_letter:"Не доставлено",superseded:"Заменено",sending:"Отправляется"};
  const colors={sent:"#dbe5f4",pending:"#83a9dc",retry:"#9baee3",queued:"#90aacd",failed:"#e19ea3",dead:"#df939a",dead_letter:"#df939a",superseded:"#75849b",sending:"#acbcd1"};
  const items=order.filter(k=>b.bot.outbox_states[k]).map(k=>({label:labels[k]||k,value:b.bot.outbox_states[k],color:colors[k],kind:"telegram"}));
  for(const [key,value] of Object.entries(b.bot.outbox_states))if(!order.includes(key)&&value>0)items.push({label:key,value,color:"#8193b2",kind:"telegram"});
  C.donut("chart-bot-delivery",items,{centerLabel:"записей",title:"Состояния исходящих Telegram"});
  chartClickable("chart-bot-delivery","telegram");
  clear("bot-signals");
  statRow($("bot-signals"),"Последнее обновление Telegram",datetime(b.bot.last_webhook_at),null);
  statRow($("bot-signals"),"Последнее сообщение в чатах",datetime(b.messages.last_at),null);
  statRow($("bot-signals"),"Новых сообщений за период",number(b.messages.new),"messages");
  statRow($("bot-signals"),"Импортировано за период",number(b.messages.imported),"messages");
  clear("bot-panels");
  for(const [key,value] of Object.entries(b.bot.panel_modes))statRow($("bot-panels"),"Режим панели: "+key,number(value),"bot_panels");
  if(!Object.keys(b.bot.panel_modes).length)statRow($("bot-panels"),"Активных панелей нет","0",null);
}
function renderPage(){
  if(!lastData||!lastVisuals||!lastBot)return;
  const d=lastData,v=lastVisuals,b=lastBot;
  if(currentPage==="overview")renderOverview(d,v,b);
  else if(currentPage==="bot")renderBot(d,v,b);
  else if(currentPage==="audience")renderActivity(d,v,b);
  else if(currentPage==="ai")renderAi(d,v);
  else if(currentPage==="finance")renderFinance(d,v);
  else if(currentPage==="server")renderServer(d,v,lastElapsedMs);
  else if(currentPage==="errors")renderErrors(d,v);
}
function selectPage(name,updateHistory=false){
  const next=PAGE_META[name]?name:"overview";
  if(updateHistory&&location.hash!=="#"+next)location.hash=next;
  currentPage=next;
  for(const panel of document.querySelectorAll("[data-screen]"))panel.hidden=panel.dataset.screen!==next;
  for(const nav of document.querySelectorAll("[data-nav]")){
    const on=nav.dataset.nav===next;
    nav.classList.toggle("selected",on);
    if(on)nav.setAttribute("aria-current","page");else nav.removeAttribute("aria-current");
  }
  const meta=PAGE_META[next];
  $("page-heading").textContent=meta.title;
  $("page-overline").textContent=meta.eyebrow;
  $("page-intro").textContent=meta.intro;
  $("breadcrumb-page").textContent=meta.title.toUpperCase();
  document.title=meta.title+" · StudGroup";
  if(lastData)renderPage();
}
function render(d,v,b,elapsed){
  lastData=d;lastVisuals=v;lastBot=b;lastElapsedMs=elapsed;
  if(!loadedGroups){
    for(const g of d.groups){const o=dom("option","",g.name);o.value=g.id;$("group").append(o);}
    loadedGroups=true;
  }
  renderSnapshot(d.summary);renderKpi(d.summary,v);
  $("snapshot-time").textContent=datetime(d.as_of);
  $("updated").textContent="Срез: "+datetime(d.as_of)+" · StudGroup · Подробные записи доступны по нажатию. Нет источника — нет числа.";
  $("dashboard").hidden=false;
  $("rail-state").textContent=d.summary.incidents_open?"Есть предупреждения":"Данные доступны";
  $("rail-updated").textContent="Обновлено "+datetime(d.as_of);
  $("rail-dot").classList.toggle("error",Boolean(d.summary.incidents_open));
  selectPage((location.hash||"").slice(1)||currentPage);
}
async function load(){
  if(!token)return;const id=++activeRequest;const status=$("status");status.classList.remove("error");status.textContent="Обновляем показатели…";
  try{const begin=performance.now();const query=criteria().toString();const [d,v,b]=await Promise.all([api("/v1/admin/analytics?"+query),api("/v1/admin/visuals?"+query),api("/v1/admin/bot-monitor?"+query)]);
    if(id!==activeRequest)return;render(d,v,b,Math.round(performance.now()-begin));status.textContent="Данные обновлены · "+datetime(d.as_of)+" · период: "+$("days").selectedOptions[0].textContent.toLowerCase();
  }catch(error){if(id!==activeRequest)return;status.classList.add("error");status.textContent="Не удалось загрузить статистику: "+error.message;}
}
function explain(message){
  previousFocus=document.activeElement;detailKind=null;detailRequest++;$("modal").hidden=false;
  $("modal-title").textContent="Показатель пока недоступен";$("modal-subtitle").textContent="Почему здесь нет числа";clear("modal-items");
  const card=dom("article","detail");text(card,"p","detail-explanation",message+". Эти данные система пока не собирает; подставлять нули или предполагаемые суммы было бы неправильно.");$("modal-items").append(card);
  $("modal-status").textContent="";$("modal-more").hidden=true;document.body.style.overflow="hidden";$("modal-close").focus();
}
async function openDetail(kind){
  if(!label[kind])return;previousFocus=document.activeElement;detailKind=kind;detailOffset=0;detailRequest++;
  $("modal-title").textContent=label[kind];$("modal-subtitle").textContent=
    (kind==="month_expenses"?"Текущий месяц":kind==="reserves"?"За всё время":$("days").selectedOptions[0].textContent)+" · "+$("group").selectedOptions[0].textContent;
  clear("modal-items");$("modal").hidden=false;document.body.style.overflow="hidden";$("modal-close").focus();await more(false);
}
function displayFields(host,fieldsObj,id){
  const details=dom("details");const summary=dom("summary","tech-toggle","Технические подробности");
  const grid=dom("div","tech-fields");
  for(const [key,value] of [["id",id],...Object.entries(fieldsObj||{})]){const r=dom("div");text(r,"small","",fields[key]||key);let result=value===true?"Да":value===false?"Нет":hint(value);if(key.endsWith("_at")||key==="deadline")result=datetime(value);if(key.endsWith("_usd"))result=money(value);text(r,"span","",result);grid.append(r);}
  details.append(summary,grid);host.append(details);
}
function renderDetailItems(items,append){
  if(!append)clear("modal-items");if(!items.length&&!append){text($("modal-items"),"div","chart-empty","За этот период подробных записей нет.");return;}
  for(const x of items){const article=dom("article","detail");const top=dom("div","detail-top");text(top,"strong","",detailKind==="incidents"?humanIncident(x.title):x.title);const status=text(top,"span","pill"+(["failed","error"].includes(x.status)?" bad":""),russianState(x.status));article.append(top);
    let meaning=statusMean(x.status);const code=x.fields?.error_code;if(code)meaning+=" Причина: "+explainError(code)+".";text(article,"p","detail-explanation",meaning);
    text(article,"div","detail-meta","Записано: "+datetime(x.at));displayFields(article,x.fields,x.id);$("modal-items").append(article);
  }
}
async function more(append){
  if(!detailKind||!token)return;const id=++detailRequest,kind=detailKind;const p=criteria();p.set("offset",String(detailOffset));p.set("limit","40");$("modal-status").textContent="Получаем записи…";$("modal-more").hidden=true;
  try{const d=await api("/v1/admin/details/"+encodeURIComponent(kind)+"?"+p.toString());if(id!==detailRequest||kind!==detailKind)return;renderDetailItems(d.items,append);detailOffset+=d.items.length;$("modal-more").hidden=!d.has_more;$("modal-status").textContent="Показано записей: "+number(detailOffset)+(d.has_more?" · есть ещё":"");
  }catch(error){if(id===detailRequest)$("modal-status").textContent="Ошибка чтения: "+error.message;}
}
async function loadRecent(){
  const host=$("recent-errors");host.replaceChildren();
  try{const p=criteria();p.set("limit","5");const d=await api("/v1/admin/details/incidents?"+p.toString());if(!d.items.length){text(host,"p","chart-empty","Открытых системных предупреждений нет.");return;}
    for(const item of d.items){const b=dom("button","incident-row");b.type="button";b.dataset.detail="incidents";const col=dom("span","incident-info");text(col,"strong","incident-title",humanIncident(item.title));text(col,"small","incident-time",datetime(item.at)+" · повторений: "+number(item.fields?.occurrences||0));b.append(col);text(b,"span","incident-badge","Открыть ↗");host.append(b);}
  }catch(error){text(host,"p","chart-empty","Не удалось загрузить предупреждения: "+error.message);}
}
function closeModal(){detailKind=null;detailRequest++;$("modal").hidden=true;document.body.style.overflow="";previousFocus?.focus?.();}
document.addEventListener("click",e=>{const d=e.target.closest("[data-detail]");if(d){openDetail(d.dataset.detail);return;}const v=e.target.closest("[data-visual]");if(v){openVisual(v.dataset.visual);return;}const i=e.target.closest("[data-info]");if(i)explain(i.dataset.info);});
$("outcome-tabs").addEventListener("click",e=>{const tab=e.target.closest("[data-outcome]");if(tab){outcome=tab.dataset.outcome;if(lastData)renderOutcomes(lastData);}});
$("modal-close").addEventListener("click",closeModal);$("modal-backdrop").addEventListener("click",closeModal);$("modal-more").addEventListener("click",()=>more(true));
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!$("modal").hidden)closeModal();if(e.key==="Tab"&&!$("modal").hidden){const focus=[...$("modal").querySelectorAll("button:not([hidden]),summary")];if(!focus.length)return;if(e.shiftKey&&document.activeElement===focus[0]){e.preventDefault();focus.at(-1).focus();}else if(!e.shiftKey&&document.activeElement===focus.at(-1)){e.preventDefault();focus[0].focus();}}});
for(const id of ["group","days"])$(id).addEventListener("change",load);
$("refresh").addEventListener("click",load);
window.addEventListener("hashchange",()=>selectPage(location.hash.slice(1)));
document.addEventListener("click",e=>{
  const jump=e.target.closest("[data-goto]");
  if(jump){selectPage(jump.dataset.goto,true);window.scrollTo({top:0,behavior:"smooth"});}
});
let lastMeasuredWidth=0,resizePending=false,lastElapsedMs=null;
window.addEventListener("resize",()=>{
  if(resizePending||!lastData||document.hidden)return;
  resizePending=true;
  requestAnimationFrame(()=>{
    resizePending=false;
    const width=document.documentElement.clientWidth;
    if(Math.abs(width-lastMeasuredWidth)<3)return;
    lastMeasuredWidth=width;
    renderPage();
  });
});
async function start(){
  const tg=window.Telegram?.WebApp;if(!tg?.initData){$("status").classList.add("error");$("status").textContent="Для входа откройте админку через личный чат с ботом StudGroup.";return;}tg.ready();tg.expand();
  try{const r=await fetch("/v1/session/bootstrap",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({init_data:tg.initData}),cache:"no-store"});const result=await r.json();if(!r.ok)throw Error(result.error?.message||"Не удалось авторизоваться");token=result.access_token;await load();}
  catch(error){$("status").classList.add("error");$("status").textContent="Ошибка входа: "+error.message;}
}
start();setInterval(()=>{if(token&&!document.hidden&&$("modal").hidden)load();},60000);
