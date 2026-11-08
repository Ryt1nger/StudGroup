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
  text:"Обработки текстовых сообщений",images:"Анализ фотографий",legacy:"Старая очередь",
  errors:"Ошибки обработки",incidents:"Системные предупреждения",expenses:"Списания AI",
  month_expenses:"Расходы на AI в этом месяце",reserves:"Незакрытые резервы AI",
  homework:"Домашние задания",events:"События",telegram:"Отправка Telegram"
};
const state={
  completed:"Выполнено",ready:"Готово",sent:"Отправлено",failed:"Ошибка",error:"Ошибка",
  queued:"В очереди",pending:"Ожидает",running:"Обрабатывается",processing:"Обрабатывается",
  stale:"Результат устарел",superseded:"Заменено новой версией",settled:"Списано",
  rejected:"Отклонено",canceled:"Отменено",cancelled:"Отменено",published:"Опубликовано",
  legacy:"Старый режим",draft:"Черновик",unknown:"Неизвестно",active:"Активно"
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
  pilot_authorized:"Доступ к пилоту",schedule_version:"Версия расписания"
};
let token=null,loadedGroups=false,activeRequest=0,detailRequest=0,detailKind=null,detailOffset=0,previousFocus=null,lastData=null,lastVisuals=null,outcome="text";
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
  line.setAttribute("fill","none");line.setAttribute("stroke",color||"#087e83");
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
  if(seriesValues?.length>1){const graph=dom("span","kpi-spark");spark(graph,seriesValues,extra==="danger"?"#c45e63":"#087e83");middle.append(graph);}
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
  const block=dom("button","server-cell");block.type="button";block.dataset.info=note;
  text(block,"span","",title);text(block,"strong","",value);text(block,"small","",note);
  host.append(block);
}
function addInsight(host,value,critical=false){text(host,"span","snapshot-pill"+(critical?" critical":""),value);}
function explainError(code){return C.humanError(code||"");}
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
function renderActivity(d,v){
  const days=v.daily.map(x=>x.date);
  C.plot("chart-activity",[{label:"Сообщения",values:series(v,"messages"),color:"#087e83"}],{labels:days,title:"Сообщения по дням",area:true});chartClickable("chart-activity","messages");
  C.hourBars("chart-hours",v.by_hour_utc);chartClickable("chart-hours","messages");
  const host=$("usage-strip");host.replaceChildren();
  info(host,"Написали хотя бы раз",number(d.summary.chat_authors),"Разные отправители в Telegram-чатах","messages");
  info(host,"Участники",number(d.summary.members),"Активные участники подключённых групп","members");
  info(host,"Создано событий",number(d.summary.events),"Записи событий за период","events");
  info(host,"Заходы в WebApp","Нет измерений","До подключения счётчика входов приложение их не записывало",null);
  $("activity-caption").textContent="За "+$("days").selectedOptions[0].textContent.toLowerCase()+" · последнее сообщение: "+datetime(d.summary.last_message_at)+".";
}
function renderOutcomes(d){
  const statuses=d.states[outcome]||{},kind=outcome;
  const colors={completed:"#087e83",ready:"#087e83",sent:"#087e83",failed:"#bd5854",error:"#bd5854",stale:"#d2a15f",superseded:"#8196aa",queued:"#8494ab",pending:"#8494ab",running:"#5362af",processing:"#5362af"};
  const entries=Object.entries(statuses).sort((a,b)=>b[1]-a[1]);
  const items=entries.map(([key,value])=>({label:russianState(key),value,color:colors[key]||"#9dabb7"}));
  C.donut("chart-outcomes",items,{title:"Распределение результатов обработки",centerLabel:"обработок"});chartClickable("chart-outcomes",kind);
  $("outcome-description").textContent=kind==="images"?"«Устарел» означает, что результат был заменён после изменения исходных данных. Это не сбой.":"Процент готовых результатов не равен точности AI. Проверку качества нужно считать отдельно.";
  for(const tab of $("outcome-tabs").querySelectorAll("button"))tab.classList.toggle("active",tab.dataset.outcome===outcome);
}
function renderAi(d,v){
  const dates=v.daily.map(x=>x.date);
  C.bars("chart-workload",[{label:"Текст",values:series(v,"text"),color:"#087e83"},{label:"Фото",values:series(v,"images"),color:"#5060bf"}],{labels:dates,title:"Количество прогонов по дням"});chartClickable("chart-workload","text");
  renderOutcomes(d);
  C.plot("chart-latency",[{label:"Текст",values:v.daily.map(x=>x.text_latency_ms==null?null:x.text_latency_ms/1000),color:"#087e83"},{label:"Фото",values:v.daily.map(x=>x.photo_latency_ms==null?null:x.photo_latency_ms/1000),color:"#d39051"}],{labels:dates,title:"Скорость обработки AI"});chartClickable("chart-latency","text");
  C.bars("chart-output",[{label:"ДЗ",values:series(v,"homework"),color:"#087e83"},{label:"События",values:series(v,"events"),color:"#5060bf"},{label:"Уведомления",values:series(v,"notifications"),color:"#d39051"}],{labels:dates,title:"Созданные записи"});chartClickable("chart-output","homework");
}
function renderFinance(d,v){
  clear("finance-summary");financeTile($("finance-summary"),"Списано за месяц",money(d.finance.month_charged_usd),"Календарный месяц, UTC","month_expenses",true);
  financeTile($("finance-summary"),"Зарезервировано",money(d.finance.unsettled_reserved_usd),"Незакрытые резервы, за всё время","reserves");
  financeTile($("finance-summary"),"Все оплаты компании","Не подключено","Нет данных о счетах и платёжных сервисах",null);
  C.bars("chart-finance",[{label:"Списано",values:v.finance_month.series.map(x=>Number(x.value)||0),color:"#087e83"}],{labels:v.finance_month.series.map(x=>x.date),currency:true,title:"Дневные расходы на AI"});chartClickable("chart-finance","month_expenses");
  const kindNames={cplus:"Анализ текста",image:"Обработка фото",cabinet_image:"Фото из учебной системы",text:"Текстовые запросы"};
  const kinds=v.finance_month.by_kind.map((x,i)=>({label:kindNames[x.kind]||"Другая обработка",value:Number(x.usd),color:C.colors[i%C.colors.length]}));
  C.donut("chart-finance-kind",kinds,{title:"Распределение расходов AI",centerLabel:"USD"});chartClickable("chart-finance-kind","month_expenses");
  const host=$("unavailable-finance");host.replaceChildren();for(const [title,note] of [["Баланс AI-провайдеров","Нет подключения к биллингу провайдеров"],["Баланс компании и будущие счета","Не подключены банковские и платёжные источники"]]){
    const b=dom("button","integration");b.type="button";b.dataset.info=note;text(b,"small","","НЕТ ПОДКЛЮЧЕНИЯ");text(b,"b","",title);text(b,"span","",note);host.append(b);
  }
}
function renderServer(d,v,roundTrip){
  const server=v.server,host=$("server-overview");host.replaceChildren();
  metric(host,"Обычный ответ API, p50",server.p50_ms==null?"Нет замеров":number(server.p50_ms)+" мс","Медиана за 15 минут");
  metric(host,"Медленные запросы, p95",server.p95_ms==null?"Нет замеров":number(server.p95_ms)+" мс","95% измеренных запросов не медленнее");
  metric(host,"Ошибки сервера",number(server.errors_5xx),"Ответы с кодом 5xx, 15 минут");
  metric(host,"Запросов к API",number(server.sample_count),"Измерены после запуска текущего процесса");
  C.bars("chart-requests",[{label:"Обычные ответы",values:server.requests.map(x=>Math.max(0,x.count-x.errors_5xx)),color:"#087e83"},{label:"Серверные ошибки",values:server.requests.map(x=>x.errors_5xx),color:"#bf5a55"}],{labels:server.requests.map(x=>x.minute.slice(11,16)),title:"Запросы API поминутно"});
  chartClickable("chart-requests","incidents");
  $("server-caption").textContent=server.note+" Последний запрос панели занял "+number(roundTrip)+" мс с учётом сети.";
  clear("queue-detail");const list=$("queue-detail");
  for(const [title,key,detail] of [["Текстовый анализ","text","text"],["Анализ фотографий","images","images"],["Старая обработка","legacy","legacy"],["Исходящие Telegram","telegram","telegram"]]){
    const counts=d.states[key]||{};const b=dom("button","queue-row");b.type="button";b.dataset.detail=detail;text(b,"strong","",title);text(b,"span","",number(Object.values(counts).reduce((a,n)=>a+n,0))+" записей ↗");list.append(b);
  }
}
function renderErrors(d,v){
  const dates=v.daily.map(x=>x.date);
  C.bars("chart-errors",[{label:"Текст",values:series(v,"text_errors"),color:"#bf5a55"},{label:"Фото",values:series(v,"image_errors"),color:"#d39051"},{label:"Старая очередь",values:series(v,"legacy_errors"),color:"#9eaab9"}],{labels:dates,title:"Ошибки обработки по дням"});chartClickable("chart-errors","errors");
  C.reasons("chart-reasons",v.error_reasons);
  loadRecent();
}
function render(d,v,elapsed){
  lastData=d;lastVisuals=v;
  if(!loadedGroups){for(const g of d.groups){const o=dom("option","",g.name);o.value=g.id;$("group").append(o);}loadedGroups=true;}
  renderSnapshot(d.summary);renderKpi(d.summary,v);renderActivity(d,v);renderAi(d,v);renderFinance(d,v);renderServer(d,v,elapsed);renderErrors(d,v);
  $("snapshot-time").textContent=datetime(d.as_of);
  $("updated").textContent="Источник: действующая база StudGroup. Срез "+datetime(d.as_of)+". Внутренние данные AI не раскрываются. Все суммы в USD. Показатели без источника отмечены отдельно.";
  $("dashboard").hidden=false;
  $("rail-state").textContent=d.summary.incidents_open?"Есть предупреждения":"Данные получены";
  $("rail-updated").textContent="Обновлено "+datetime(d.as_of);
  $("rail-dot").classList.toggle("error",Boolean(d.summary.incidents_open));
}
async function load(){
  if(!token)return;const id=++activeRequest;const status=$("status");status.classList.remove("error");status.textContent="Обновляем показатели…";
  try{const begin=performance.now();const query=criteria().toString();const [d,v]=await Promise.all([api("/v1/admin/analytics?"+query),api("/v1/admin/visuals?"+query)]);
    if(id!==activeRequest)return;render(d,v,Math.round(performance.now()-begin));status.textContent="Данные обновлены · "+datetime(d.as_of)+" · период: "+$("days").selectedOptions[0].textContent.toLowerCase();
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
  for(const x of items){const article=dom("article","detail");const top=dom("div","detail-top");text(top,"strong","",x.title);const status=text(top,"span","pill"+(["failed","error"].includes(x.status)?" bad":""),russianState(x.status));article.append(top);
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
    for(const item of d.items){const b=dom("button","incident-row");b.type="button";b.dataset.detail="incidents";const col=dom("span","incident-info");text(col,"strong","incident-title",item.title);text(col,"small","incident-time",datetime(item.at)+" · повторений: "+number(item.fields?.occurrences||0));b.append(col);text(b,"span","incident-badge","Открыть ↗");host.append(b);}
  }catch(error){text(host,"p","chart-empty","Не удалось загрузить предупреждения: "+error.message);}
}
function closeModal(){detailKind=null;detailRequest++;$("modal").hidden=true;document.body.style.overflow="";previousFocus?.focus?.();}
document.addEventListener("click",e=>{const d=e.target.closest("[data-detail]");if(d){openDetail(d.dataset.detail);return;}const i=e.target.closest("[data-info]");if(i)explain(i.dataset.info);});
$("outcome-tabs").addEventListener("click",e=>{const tab=e.target.closest("[data-outcome]");if(tab){outcome=tab.dataset.outcome;if(lastData)renderOutcomes(lastData);}});
$("modal-close").addEventListener("click",closeModal);$("modal-backdrop").addEventListener("click",closeModal);$("modal-more").addEventListener("click",()=>more(true));
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!$("modal").hidden)closeModal();if(e.key==="Tab"&&!$("modal").hidden){const focus=[...$("modal").querySelectorAll("button:not([hidden]),summary")];if(!focus.length)return;if(e.shiftKey&&document.activeElement===focus[0]){e.preventDefault();focus.at(-1).focus();}else if(!e.shiftKey&&document.activeElement===focus.at(-1)){e.preventDefault();focus[0].focus();}}});
for(const id of ["group","days"])$(id).addEventListener("change",load);
$("refresh").addEventListener("click",load);
document.querySelectorAll(".nav-link").forEach(a=>a.addEventListener("click",()=>{document.querySelectorAll(".nav-link").forEach(b=>b.classList.remove("selected"));a.classList.add("selected");}));
async function start(){
  const tg=window.Telegram?.WebApp;if(!tg?.initData){$("status").classList.add("error");$("status").textContent="Для входа откройте админку через личный чат с ботом StudGroup.";return;}tg.ready();tg.expand();
  try{const r=await fetch("/v1/session/bootstrap",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({init_data:tg.initData}),cache:"no-store"});const result=await r.json();if(!r.ok)throw Error(result.error?.message||"Не удалось авторизоваться");token=result.access_token;await load();}
  catch(error){$("status").classList.add("error");$("status").textContent="Ошибка входа: "+error.message;}
}
start();setInterval(()=>{if(token&&!document.hidden&&$("modal").hidden)load();},60000);
