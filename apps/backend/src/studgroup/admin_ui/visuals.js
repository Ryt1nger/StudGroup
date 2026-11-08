/* Dependency-free charts. Each graph displays live API data only. */
(function(){
  "use strict";
  const S="http://www.w3.org/2000/svg";
  const COLORS=["#087e83","#5060bf","#db8c45","#ad678f","#5a8c64","#d05e5e"];
  const nf=new Intl.NumberFormat("ru-RU");
  function el(tag,attrs={},parent=null,text){const node=document.createElementNS(S,tag);for(const [key,value] of Object.entries(attrs)){node.setAttribute(key,String(value));}if(text!==undefined)node.textContent=String(text);parent?.appendChild(node);return node;}
  function init(id,title){const host=document.getElementById(id);host.replaceChildren();const svg=el("svg",{viewBox:"0 0 700 250",preserveAspectRatio:"none",role:"img","aria-label":title});host.appendChild(svg);return svg;}
  function empty(id,label="Пока нет записей для графика"){const host=document.getElementById(id);host.replaceChildren();const div=document.createElement("div");div.className="chart-empty";div.textContent=label;host.appendChild(div);}
  const short=v=>{const n=Number(v)||0;return n>=1000?(n/1000).toFixed(1)+" тыс.":n>=10?nf.format(Math.round(n)):n.toFixed(n&&n<1?2:0)};
  function axes(svg,max,labels,opts={}){
    const x0=52,x1=687,y0=16,y1=210;
    for(let i=0;i<5;i++){const y=y1-(y1-y0)*i/4;el("line",{x1:x0,y1:y,x2:x1,y2:y,class:"grid-line"},svg);el("text",{x:8,y:y+4,class:"tick"},svg,(opts.currency?"$":"")+short(max*i/4));}
    if(labels.length){const picks=[0,Math.floor((labels.length-1)/2),labels.length-1];for(let q=0;q<picks.length;q++){const j=picks[q];const x=x0+(labels.length<=1?.5:j/(labels.length-1))*(x1-x0);el("text",{x,y:239,class:"tick","text-anchor":q===0?"start":q===2?"end":"middle"},svg,labels[j]?.slice(5)||"");}}
    return {x0,x1,y0,y1};
  }
  function tooltip(node,text){el("title",{},node,text);}
  function plot(id,series,opts={}){
    const rows=series[0]?.values||[];if(!rows.length){empty(id);return;}
    const title=opts.title||"График",valid=v=>v!==null&&v!==undefined&&Number.isFinite(Number(v));
    const values=series.flatMap(s=>s.values.filter(valid).map(Number));
    if(!values.length){empty(id,"Нет замеров скорости за выбранные дни");return;}
    const svg=init(id,title),max=Math.max(1,...values)*1.18;
    const labels=opts.labels||rows.map((_,i)=>String(i+1));
    const {x0,x1,y1}=axes(svg,max,labels,{currency:opts.currency}),w=x1-x0;
    const y=v=>y1-Number(v)/max*185;
    const x=i=>x0+(rows.length===1?.5:i/(rows.length-1))*w;
    series.forEach((item,k)=>{
      const color=item.color||COLORS[k%COLORS.length];
      const segments=[];let segment=[];
      item.values.forEach((v,i)=>{
        if(valid(v))segment.push([x(i),y(v),i]);
        else if(segment.length){segments.push(segment);segment=[];}
      });
      if(segment.length)segments.push(segment);
      if(opts.area&&segments.length===1&&segments[0].length>1){
        const pts=segments[0];const d="M"+pts.map(p=>p.slice(0,2).join(",")).join(" L")+" L"+pts.at(-1)[0]+","+y1+" L"+pts[0][0]+","+y1+" Z";
        el("path",{d,fill:color,opacity:.08},svg);
      }
      for(const pts of segments){
        if(pts.length>1)el("polyline",{points:pts.map(p=>p.slice(0,2).join(",")).join(" "),fill:"none",stroke:color,"stroke-width":3.2,"stroke-linejoin":"round","stroke-linecap":"round"},svg);
        for(const p of pts){
          const dot=el("circle",{cx:p[0],cy:p[1],r:3.4,fill:"#fff",stroke:color,"stroke-width":2.2,class:"chart-point"},svg);
          tooltip(dot,(labels[p[2]]||"")+" · "+item.label+": "+(opts.currency?"$":"")+short(item.values[p[2]]));
        }
      }
    });
    legend(id,series.map(s=>s.label),series.map((s,k)=>s.color||COLORS[k%COLORS.length]));
  }
  function bars(id,series,opts={}){
    const rows=series[0]?.values||[];if(!rows.length){empty(id);return;}
    const svg=init(id,opts.title||"Столбчатая диаграмма"),labels=opts.labels||rows.map((_,i)=>String(i+1));const width=635,x0=52,y1=210;
    const maxima=rows.map((_,i)=>series.reduce((a,s)=>a+(Number(s.values[i])||0),0));const max=Math.max(1,...maxima)*1.16;axes(svg,max,labels,{currency:opts.currency});const gap=width/rows.length,barW=Math.max(2,Math.min(35,gap*.64));
    rows.forEach((_,i)=>{let acc=0;series.forEach((s,k)=>{const v=Number(s.values[i])||0;const h=v/max*185;const r=el("rect",{x:x0+i*gap+(gap-barW)/2,y:y1-(acc+h),width:barW,height:Math.max(0,h),rx:Math.min(3,h/2),fill:s.color||COLORS[k%COLORS.length]},svg);tooltip(r,(labels[i]||"")+" · "+s.label+": "+(opts.currency?"$":"")+short(v));acc+=h;});});
    legend(id,series.map(s=>s.label),series.map((s,k)=>s.color||COLORS[k%COLORS.length]));
  }
  function donut(id,items,opts={}){
    const active=items.filter(d=>Number(d.value)>0);const total=active.reduce((a,d)=>a+Number(d.value),0);if(!total){empty(id,"Нет обработок за выбранный период");return;}
    const svg=init(id,opts.title||"Распределение");const cx=209,cy=125,r=76,stroke=23,circ=2*Math.PI*r;el("circle",{cx,cy,r,fill:"none",stroke:"#e8ecf0","stroke-width":stroke},svg);
    let off=0;active.forEach((d,i)=>{const portion=Number(d.value)/total;const arc=el("circle",{cx,cy,r,fill:"none",stroke:d.color||COLORS[i%COLORS.length],"stroke-width":stroke,"stroke-dasharray":(portion*circ)+" "+circ,"stroke-dashoffset":-(off*circ),"transform":"rotate(-90 "+cx+" "+cy+")"},svg);tooltip(arc,d.label+": "+nf.format(d.value)+" · "+(portion*100).toFixed(1)+"%");off+=portion;});
    el("text",{x:cx,y:119,"text-anchor":"middle",class:"donut-number"},svg,nf.format(total));el("text",{x:cx,y:141,"text-anchor":"middle",class:"donut-label"},svg,opts.centerLabel||"всего");
    const shown=active.slice(0,7);shown.forEach((d,i)=>{const y=46+i*28;el("circle",{cx:370,cy:y-4,r:5,fill:d.color||COLORS[i%COLORS.length]},svg);el("text",{x:384,y,class:"donut-legend"},svg,d.label.length>21?d.label.slice(0,20)+"…":d.label);el("text",{x:681,y,class:"donut-value","text-anchor":"end"},svg,short(d.value));});
  }
  function hourBars(id,items){
    if(!items.length){empty(id);return;}const svg=init(id,"Активность по московскому времени"),values=items.map(x=>Number(x.value)||0),max=Math.max(1,...values);axes(svg,max,[],{});const width=635;
    values.forEach((v,i)=>{const slot=width/24,bw=slot*.65,x=52+i*slot+(slot-bw)/2,h=v/max*185;const r=el("rect",{x,y:210-h,width:bw,height:h,rx:3,fill:i>=7&&i<=18?"#087e83":"#9bb7c0"},svg);tooltip(r,i+":00–"+(i+1)+":00 МСК · "+nf.format(v)+" сообщений");if(i%4===0)el("text",{x:x+bw/2,y:239,"text-anchor":"middle",class:"tick"},svg,String(i).padStart(2,"0")+":00");});
  }
  function reasons(id,items){
    const host=document.getElementById(id);host.replaceChildren();if(!items.length){const n=document.createElement("div");n.className="chart-empty";n.textContent="Причины ошибок не зафиксированы";host.append(n);return;}
    const max=Math.max(1,...items.map(x=>x.count));for(const item of items.slice(0,7)){const b=document.createElement("button");b.className="reason-row";b.type="button";b.dataset.detail="errors";const title=document.createElement("div");title.className="reason-top";const name=document.createElement("strong");name.textContent=item.source+": "+humanError(item.code);const count=document.createElement("span");count.textContent=nf.format(item.count);title.append(name,count);const track=document.createElement("div");track.className="reason-track";const progress=document.createElement("div");progress.style.width=(item.count/max*100)+"%";track.append(progress);b.append(title,track);b.title="Технический код: "+item.code;host.append(b);}
  }
  const knownErrors={
    invalid_shadow_context:"Контекст сообщения не подошёл для анализа",
    context_schema_invalid:"AI вернул данные в неправильном формате",
    extraction_release_budget_exhausted:"Лимит расходов на AI исчерпан",
    context_insufficient_balance:"Не хватило бюджета для анализа",
    context_evidence_outside_envelope:"Данные не прошли проверку источника"
  };
  function humanError(code){return knownErrors[code]||"Другая техническая ошибка";}
  function legend(id,labels,colors){if(labels.length<=1)return;const host=document.getElementById(id);const div=document.createElement("div");div.className="plot-legend";for(let i=0;i<labels.length;i++){const a=document.createElement("span");const dot=document.createElement("i");dot.style.background=colors[i];a.append(dot,document.createTextNode(labels[i]));div.append(a);}host.append(div);}
  window.StudCharts={plot,bars,donut,hourBars,reasons,empty,humanError,colors:COLORS};
})();
