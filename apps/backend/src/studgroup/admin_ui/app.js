const $=id=>document.getElementById(id);
const fmt=n=>n==null?'Не измеряется':new Intl.NumberFormat('ru-RU').format(n);
const usd=n=>n==null?'Нет данных':'$'+Number(n).toFixed(4);
const date=s=>s?new Date(s).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):'Нет даты';
const names={groups:'Подключённые группы',month_expenses:'Расходы за текущий месяц',reserves:'Незакрытые резервы AI',members:'Участники',messages:'Сообщения в чатах',text:'Анализ текста',images:'Анализ фото',legacy:'Старый AI',errors:'Ошибки',incidents:'Инциденты',expenses:'Расходы на AI',homework:'Домашние задания',events:'События',telegram:'Исходящие Telegram'};
const states={completed:'Готово',ready:'Готово',sent:'Отправлено',failed:'Ошибка',error:'Ошибка',queued:'В очереди',pending:'Ожидает',stale:'Устарело',superseded:'Заменено'};
let session=null,groupsReady=false,modalKind=null,offset=0,seq=0,modalSeq=0,previousFocus=null;
function node(tag,cls,value){const n=document.createElement(tag);if(cls)n.className=cls;if(value!==undefined)n.textContent=String(value);return n;}
function add(parent,tag,cls,value){const n=node(tag,cls,value);parent.append(n);return n;}
function clear(id){$(id).replaceChildren();}
async function get(url){const r=await fetch(url,{headers:{Authorization:'Bearer '+session},cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error?.message||'Ошибка '+r.status);return d;}
function filter(){const p=new URLSearchParams({days:$('days').value});if($('group').value)p.set('group_id',$('group').value);return p;}
function card(label,value,note,kind,icon,cls=''){const b=node('button','metric '+cls);b.type='button';b.dataset.detail=kind;add(b,'span','icon',icon);add(b,'span','label',label);add(b,'span','number',value);add(b,'span','description',note+' · Нажмите для подробностей');$('spotlight').append(b);}
function small(host,label,value,note,kind){const b=node('button','mini-card'+(kind?'':' off'));b.type='button';if(kind)b.dataset.detail=kind;else b.dataset.info=note;add(b,'span','title',label);add(b,'span','big',value);add(b,'span','hint',note);host.append(b);}
function row(host,label,value,kind){const b=node('button','click-row');b.type='button';b.dataset.detail=kind;add(b,'span','',label);add(b,'strong','right',value);host.append(b);}
function notice(host,value){add(host,'div','notice',value);}
function graph(id,arrays,colors,kind,currency=false){
 const host=$(id);host.replaceChildren();const box=node('div','chart');const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 600 200');svg.setAttribute('preserveAspectRatio','none');box.append(svg);
 const draw=(type,a,text)=>{const e=document.createElementNS('http://www.w3.org/2000/svg',type);for(const [k,v] of Object.entries(a))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;svg.append(e);return e;};
 const max=Math.max(1,...arrays.flatMap(s=>s.map(x=>Number(x.value)||0)))*1.12;const count=arrays[0]?.length||0;
 for(let i=0;i<5;i++){draw('line',{x1:42,y1:15+i*39,x2:595,y2:15+i*39,class:'chart-grid'});draw('text',{x:1,y:18+i*39,class:'chart-label'},currency?'$'+(max*(4-i)/4).toFixed(2):fmt(Math.round(max*(4-i)/4)));}
 arrays.forEach((data,j)=>{const pts=data.map((p,i)=>[42+(count<2?0:i/(count-1)*550),172-(Number(p.value)||0)/max*154]);if(pts.length>1)draw('polyline',{points:pts.map(p=>p.join(',')).join(' '),fill:'none',stroke:colors[j],'stroke-width':3});pts.forEach((p,i)=>{draw('circle',{cx:p[0],cy:p[1],r:3,fill:colors[j]});});});
 if(count){const s=arrays[0];for(const i of [...new Set([0,Math.floor((count-1)/2),count-1])])draw('text',{x:42+(count<2?0:i/(count-1)*550),y:194,class:'chart-label','text-anchor':i===0?'start':i===count-1?'end':'middle'},s[i].date.slice(5));}
 host.append(box);box.addEventListener('click',()=>openDetail(kind));
}
function stateList(host,title,states,kind){
 add(host,'p','finance-label',title);
 const entries=Object.entries(states||{});if(!entries.length){add(host,'p','empty','Записей нет');return;}
 for(const [key,value] of entries)row(host,statesName(key),fmt(value),kind);
}
function statesName(key){return states[key]||key;}
function render(data,roundTrip){
 const s=data.summary, f=data.finance;
 if(!groupsReady){for(const g of data.groups){const o=node('option','',g.name);o.value=g.id;$('group').append(o);}groupsReady=true;}
 clear('spotlight');
 card('Участники',fmt(s.members),'Активные членства','members','♧');
 card('Сообщения',fmt(s.messages),'Из чатов за период','messages','↗');
 card('AI · текст',fmt(s.text_runs),'Прогоны анализа текста','text','✧');
 card('AI · фото',fmt(s.image_runs),'Проходы обработки изображений','images','▧');
 card('Ошибки AI',fmt(s.errors),'Ошибки разных очередей','errors','!','danger');
 card('Открытые инциденты',fmt(s.incidents_open),'Во всей системе','incidents','⚑',s.incidents_open?'danger':'success');
 card('Списано на AI',usd(s.spent_usd),'Бюджетный журнал','expenses','$');
 card('Создано ДЗ',fmt(s.homework),'За выбранный период','homework','▤');
 graph('chart-messages',[data.series.messages],['#4da8ff'],'messages');
 clear('audience-metrics');
 small($('audience-metrics'),'Авторов сообщений',fmt(s.chat_authors),'Уникальные отправители в чатах','messages');
 small($('audience-metrics'),'Подключённые группы',fmt(s.groups),'Активные пилотные группы','groups');
 small($('audience-metrics'),'Заходы в приложение','Нет учёта','История открытий WebApp раньше не собиралась',null);
 small($('audience-metrics'),'События',fmt(s.events),'Созданные события за период','events');
 graph('chart-ai',[data.series.text,data.series.images],['#a47aff','#39d9bc'],'text');
 clear('ai-status');stateList($('ai-status'),'Анализ текста',data.states.text,'text');stateList($('ai-status'),'Анализ изображений',data.states.images,'images');stateList($('ai-status'),'Старая очередь',data.states.legacy,'legacy');
 graph('chart-spend',[data.series.spend],['#5caaff'],'expenses',true);
 clear('finance-breakdown');for(const kind of f.ledger_by_kind)row($('finance-breakdown'),'AI · '+kind.kind,usd(kind.usd),'expenses');
 clear('finance-status');const fs=$('finance-status');const mini=node('div','mini-grid');fs.append(mini);
 small(mini,'AI за текущий месяц',usd(f.month_charged_usd),'Списано в календарном месяце','month_expenses');
 small(mini,'Резервы AI',usd(f.unsettled_reserved_usd),'Незакрытые суммы','reserves');
 small(mini,'Деньги компании','Нет подключения','Бухгалтерия и банки пока не подключены',null);
 small(mini,'Балансы провайдеров','Нет подключения','Провайдеры не передают балансы в эту админку',null);
 notice(fs,f.coverage);add(fs,'div','footnote',f.accounting_note);
 clear('speed');
 small($('speed'),'Обработка текста',s.text_latency_ms==null?'Нет данных':(s.text_latency_ms/1000).toFixed(2)+' с','По журналу прогонов AI','text');
 small($('speed'),'Обработка фото',s.image_latency_ms==null?'Нет данных':(s.image_latency_ms/1000).toFixed(2)+' с','По журналу AI-вызовов','images');
 clear('server-status');const server=$('server-status');const m=node('div','mini-grid');server.append(m);
 small(m,'Соединение с API',fmt(roundTrip)+' мс','Текущий запрос вместе с сетью',null);
 small(m,'Сбор метрик в БД',fmt(data.diagnostics.query_duration_ms)+' мс','Измерено сервером',null);
 small(m,'Исторический uptime','Не измеряется','Нет подключённых измерений доступности',null);
 small(m,'CPU / RAM','Не подключено','Требуется серверная интеграция Render',null);
 notice(server,s.incidents_open?'Есть открытые инциденты: '+fmt(s.incidents_open):'Сейчас открытых инцидентов нет. Это не равно 100% uptime.');
 clear('queues');for(const [label,key,kind] of [['AI · текст','text','text'],['AI · фото','images','images'],['Старый AI','legacy','legacy'],['Telegram','telegram','telegram']])row($('queues'),label,fmt(Object.values(data.states[key]||{}).reduce((a,b)=>a+b,0)),kind);
 $('updated').textContent='Срез: '+date(data.as_of)+' МСК · Данные из рабочей базы · AI-вызовы для просмотра не выполняются.';
 $('dashboard').hidden=false;
}
async function load(){
 if(!session)return;const id=++seq;$('status').textContent='Получаем реальные данные…';$('status').classList.remove('error');
 try{const start=performance.now();const d=await get('/v1/admin/analytics?'+filter());if(id!==seq)return;render(d,Math.round(performance.now()-start));$('status').textContent='Обновлено: '+date(d.as_of)+' МСК';showRecent();}catch(e){if(id!==seq)return;$('status').classList.add('error');$('status').textContent='Ошибка обновления: '+e.message;}
}
function explain(message){previousFocus=document.activeElement;modalKind=null;modalSeq++;$('modal-title').textContent='Показатель пока недоступен';$('modal-subtitle').textContent='Причина отсутствия данных';$('modal-items').replaceChildren();notice($('modal-items'),message+'. Вместо вымышленных цифр показываем отсутствие измерений.');$('modal-status').textContent='';$('modal-more').hidden=true;$('modal').hidden=false;$('modal-close').focus();}
async function openDetail(kind){if(!names[kind])return;previousFocus=document.activeElement;modalKind=kind;offset=0;modalSeq++;$('modal-title').textContent=names[kind];$('modal-subtitle').textContent=(kind==='month_expenses'?'Текущий календарный месяц':kind==='reserves'?'За всё время':$('days').selectedOptions[0].textContent)+' · '+$('group').selectedOptions[0].textContent;$('modal-items').replaceChildren();$('modal').hidden=false;$('modal-close').focus();await more(false);}
async function more(append){
 if(!modalKind)return;const id=++modalSeq,kind=modalKind;const p=filter();p.set('offset',offset);p.set('limit',40);$('modal-more').hidden=true;$('modal-status').textContent='Загрузка записей…';
 try{const d=await get('/v1/admin/details/'+kind+'?'+p);if(id!==modalSeq)return;if(!append)$('modal-items').replaceChildren();
 for(const x of d.items){const article=node('article','detail');const top=node('div','detail-top');add(top,'strong','',x.title);add(top,'span','pill',statesName(x.status));article.append(top);const fields=node('div','detail-fields');for(const [label,value] of [['ID',x.id],['Дата',date(x.at)],...Object.entries(x.fields||{})]){const line=node('div');add(line,'small','',label);add(line,'span','',String(value));fields.append(line);}article.append(fields);$('modal-items').append(article);}
 if(!d.items.length&&!append)add($('modal-items'),'div','empty','За период записей нет.');offset+=d.items.length;$('modal-more').hidden=!d.has_more;$('modal-status').textContent='Показано записей: '+fmt(offset);
 }catch(e){if(id===modalSeq)$('modal-status').textContent='Ошибка: '+e.message;}
}
async function showRecent(){const host=$('recent-errors');host.replaceChildren();try{const p=filter();p.set('limit',5);const d=await get('/v1/admin/details/errors?'+p);if(!d.items.length){add(host,'div','empty','За период ошибок не найдено');return;}for(const x of d.items)row(host,x.title+' · '+date(x.at),statesName(x.status),'errors');}catch(e){notice(host,'Не удалось получить ошибки: '+e.message);}}
function close(){modalKind=null;modalSeq++;$('modal').hidden=true;previousFocus?.focus?.();}
document.addEventListener('click',e=>{const d=e.target.closest('[data-detail]');if(d){openDetail(d.dataset.detail);return;}const i=e.target.closest('[data-info]');if(i)explain(i.dataset.info);});
$('modal-close').addEventListener('click',close);$('modal-backdrop').addEventListener('click',close);$('modal-more').addEventListener('click',()=>more(true));
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('modal').hidden)close();});
$('group').addEventListener('change',load);$('days').addEventListener('change',load);$('refresh').addEventListener('click',load);
async function start(){const tg=window.Telegram?.WebApp;if(!tg?.initData){$('status').textContent='Откройте админку из личного чата с ботом StudGroup.';return;}tg.ready();tg.expand();try{const r=await fetch('/v1/session/bootstrap',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init_data:tg.initData}),cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error?.message||'Вход не удался');session=d.access_token;await load();}catch(e){$('status').classList.add('error');$('status').textContent=e.message;}}
start();setInterval(()=>{if(session&&!document.hidden&&$('modal').hidden)load();},60000);
