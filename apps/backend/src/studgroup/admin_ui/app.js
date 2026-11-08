const $ = id => document.getElementById(id);
const number = value => new Intl.NumberFormat('ru-RU').format(value ?? 0);
const date = value => value ? new Intl.DateTimeFormat('ru-RU', {dateStyle:'short',timeStyle:'short',timeZone:'Europe/Moscow'}).format(new Date(value)) : 'нет данных';
let token;
let groupsLoaded = false;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function clear(id) { $(id).replaceChildren(); }
function row(parent, label, value) {
  const item = node('div','row'); item.append(node('span','muted',label),node('strong','',String(value))); parent.append(item);
}
function heading(parent, text) { parent.append(node('div','section-title',text)); }
function empty(parent, text='Нет данных') { parent.append(node('div','empty',text)); }
function card(label, value, hint) {
  const item=node('article','card'); item.append(node('div','label',label),node('div','value',value),node('div','hint',hint)); $('cards').append(item);
}
function render(data) {
  if (!groupsLoaded) {
    for (const group of data.groups) { const option=node('option','',group.name); option.value=group.id; $('group').append(option); }
    groupsLoaded=true;
  }
  const s=data.summary;
  clear('cards');
  card('Активные группы',number(s.active_groups),'Пилотный доступ');
  card('Участники',number(s.active_members),'Активные членства');
  card('Сообщения · 7 дней',number(s.messages_7d),'Последнее: '+date(s.latest_message_at));
  card('Активные инциденты',number(s.active_incidents),'Системные, для всех групп');
  card('Домашние задания',number(s.published_homework),'Опубликованные');
  card('Сроки',number(s.active_deadlines),number(s.needs_clarification)+' требуют уточнения');
  card('Канонические карточки',number(s.canonical_items),'Активные');
  card('События',number(s.published_events),'Опубликованные');
  clear('queues');
  const queueNames={messages:'Входящие сообщения',legacy_ai_jobs:'Задания старого AI',shadow:'Теневой анализ',images:'Анализ изображений',bot_outbox:'Исходящие Telegram (все группы)'};
  for (const [key,counts] of Object.entries(data.queues)) { heading($('queues'),queueNames[key]); const entries=Object.entries(counts); if (!entries.length) empty($('queues')); for (const [state,count] of entries) row($('queues'),state,number(count)); }
  clear('ai');
  row($('ai'),'Журнал бюджета · списано, USD',s.charged_usd.toFixed(4));
  row($('ai'),'Журнал бюджета · резерв, USD',s.unsettled_reserved_usd.toFixed(4));
  row($('ai'),'Старые AI-попытки · USD',s.legacy_attempts_charged_usd.toFixed(4));
  row($('ai'),'Вызовы анализа фото · USD',s.image_calls_charged_usd.toFixed(4));
  const flags=data.runtime;
  for (const [label,key] of [['Старый AI','legacy_ai_enabled'],['Новый AI','new_engine_enabled'],['Теневой AI','shadow_enabled'],['Анализ фото','image_enabled']]) row($('ai'),label,flags[key]?'включён':'выключен');
  for (const mode of data.analysis_modes) { const group=data.groups.find(g=>g.id===mode.group_id); row($('ai'),group?.name||mode.group_id,mode.mode+(mode.hold?' · hold: '+mode.hold:'')); }
  clear('schedule');
  if (!data.schedule_versions.length) empty($('schedule'),'Групп пока нет');
  for (const schedule of data.schedule_versions) { heading($('schedule'),schedule.group_name); row($('schedule'),'Версия расписания',number(schedule.version)); }
  clear('incidents');
  if (!data.incidents.length) empty($('incidents'),'Активных инцидентов нет');
  for (const incident of data.incidents) { heading($('incidents'),'#'+incident.id+' · '+incident.code); row($('incidents'),'Открыт',date(incident.opened_at)); row($('incidents'),'Последний сигнал',date(incident.last_seen_at)); row($('incidents'),'Повторения',number(incident.occurrences)); }
  $('updated').textContent='Срез: '+date(data.as_of)+' · Все времена указаны по Москве. Инциденты и Telegram-очередь общесистемные.';
  $('dashboard').hidden=false;
  $('status').textContent='Данные актуальны на '+date(data.as_of);
  $('status').classList.remove('error');
}
async function load() {
  try {
    $('status').textContent='Обновляем данные…';
    const url='/v1/admin/dashboard'+($('group').value?'?group_id='+encodeURIComponent($('group').value):'');
    const response=await fetch(url,{headers:{Authorization:'Bearer '+token},cache:'no-store'});
    const data=await response.json();
    if (!response.ok) throw new Error(data.error?.message||'Ошибка загрузки');
    render(data);
  } catch(error) { $('status').textContent=error.message; $('status').classList.add('error'); }
}
async function start() {
  const webApp=window.Telegram?.WebApp;
  if (!webApp?.initData) { $('status').textContent='Откройте страницу через кнопку «Дашборд» в личном чате с ботом.'; return; }
  webApp.ready(); webApp.expand();
  try {
    const response=await fetch('/v1/session/bootstrap',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:webApp.initData}),cache:'no-store'});
    const data=await response.json();
    if (!response.ok) throw new Error(data.error?.message||'Не удалось войти');
    token=data.access_token;
    await load();
  } catch(error) { $('status').textContent=error.message; $('status').classList.add('error'); }
}
$('refresh').addEventListener('click',load);
$('group').addEventListener('change',load);
start();
setInterval(()=>{if(token && !document.hidden) load();},60000);
