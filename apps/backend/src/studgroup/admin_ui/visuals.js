/* StudGroup operator charts: measured values only, responsive SVG geometry. */
(function(){
"use strict";
const SVG="http://www.w3.org/2000/svg";
const COLORS=["#d5e0ef","#91a9cf","#b8aecb","#d494a1","#8ca6bf","#9b9db8"];
const nf=new Intl.NumberFormat("ru-RU");
const clean=v=>Number.isFinite(Number(v))?Number(v):0;
const format=v=>Math.abs(v)>=1000?nf.format(Math.round(v)):Math.abs(v)>=10?nf.format(Math.round(v)):nf.format(Number(v.toFixed(2)));
const dollars=v=>"$"+(Math.abs(v)<0.01?Number(v).toFixed(4):Number(v).toFixed(2));
const dateLabel=s=>/^\d{4}-\d{2}-\d{2}$/.test(s)?new Date(s+"T12:00:00Z").toLocaleDateString("ru-RU",{day:"numeric",month:"short",timeZone:"UTC"}):String(s);
function element(name,attributes={},parent,text){
 const el=document.createElementNS(SVG,name);
 for(const [key,value] of Object.entries(attributes))el.setAttribute(key,String(value));
 if(text!==undefined)el.textContent=String(text);
 if(parent)parent.append(el);
 return el;
}
function empty(id,message="За выбранный период данных нет"){
 const host=document.getElementById(id);host.replaceChildren();
 const el=document.createElement("div");el.className="chart-empty";
 el.textContent=message;host.append(el);
}
function hostCanvas(id,label,height=248){
 const host=document.getElementById(id);host.replaceChildren();
 const figure=document.createElement("div");figure.className="chart-figure";
 const canvas=document.createElement("div");canvas.className="chart-canvas";
 const width=Math.max(340,Math.round(host.getBoundingClientRect().width)||660);
 const svg=element("svg",{viewBox:"0 0 "+width+" "+height,preserveAspectRatio:"xMidYMid meet",role:"img","aria-label":label},canvas);
 figure.append(canvas);host.append(figure);
 return {host,figure,canvas,svg,width,height};
}
function tooltip(shape,txt){element("title",{},shape,txt);}
function niceMaximum(value){
 if(value<=0)return 1;
 const raw=value*1.12;
 const p=Math.pow(10,Math.floor(Math.log10(raw)));
 const unit=raw/p;
 return (unit<=1?1:unit<=2?2:unit<=2.5?2.5:unit<=5?5:10)*p;
}
function grid(svg,width,height,max,opts={}){
 const margin={left:58,right:15,top:14,bottom:36};
 const innerW=width-margin.left-margin.right,innerH=height-margin.top-margin.bottom;
 for(let n=0;n<=4;n++){
   const y=margin.top+innerH*n/4;
   element("line",{x1:margin.left,y1:y,x2:width-margin.right,y2:y,class:"grid-line"},svg);
   const tick=max*(4-n)/4;
   element("text",{x:margin.left-13,y:y+4,"text-anchor":"end",class:"tick"},svg,opts.currency?dollars(tick):format(tick));
 }
 return {...margin,innerW,innerH,baseline:margin.top+innerH};
}
function axisDates(svg,labels,atX,y,all=false){
 if(!labels.length)return;
 const indices=all&&labels.length<=8?labels.map((_,i)=>i):
   [...new Set([0,Math.floor((labels.length-1)/4),Math.floor((labels.length-1)/2),Math.floor((labels.length-1)*3/4),labels.length-1])];
 indices.forEach((i,k)=>element("text",{
   x:atX(i),y,"text-anchor":i===0?"start":i===labels.length-1?"end":"middle",class:"tick date-tick"
 },svg,dateLabel(labels[i])));
}
function legend(figure,series){
 if(series.length<=1)return;
 const wrap=document.createElement("div");wrap.className="plot-legend";
 series.forEach((item,i)=>{
   const node=document.createElement(item.kind?"button":"span");
   if(item.kind){node.type="button";node.dataset.detail=item.kind;node.title="Открыть: "+item.label;}
   const dot=document.createElement("i");dot.style.background=item.color||COLORS[i%COLORS.length];
   node.append(dot,document.createTextNode(item.label));wrap.append(node);
 });
 figure.append(wrap);
}
function plot(id,series,opts={}){
 const rows=series[0]?.values||[];if(!rows.length)return empty(id);
 const valid=x=>x!==null&&x!==undefined&&Number.isFinite(Number(x));
 const numbers=series.flatMap(s=>s.values.filter(valid).map(Number));
 if(!numbers.length)return empty(id,"Замеры скорости пока отсутствуют");
 const {figure,svg,width,height}=hostCanvas(id,opts.title||"Динамика");
 const maximum=niceMaximum(Math.max(...numbers));
 const geo=grid(svg,width,height,maximum,{currency:opts.currency});
 const labels=opts.labels||rows.map((_,i)=>String(i+1));
 const x=i=>geo.left+(rows.length<2?.5:i/(rows.length-1))*geo.innerW;
 const y=v=>geo.baseline-clean(v)/maximum*geo.innerH;
 axisDates(svg,labels,x,height-7);
 series.forEach((item,index)=>{
   const color=item.color||COLORS[index%COLORS.length];
   const chunks=[];let chunk=[];
   item.values.forEach((value,i)=>{
     if(valid(value))chunk.push({x:x(i),y:y(value),value,index:i});
     else if(chunk.length){chunks.push(chunk);chunk=[];}
   });if(chunk.length)chunks.push(chunk);
   chunks.forEach(points=>{
     if(opts.area&&points.length>1){
       const fill="M"+points.map(p=>p.x+","+p.y).join("L")+"L"+points.at(-1).x+","+geo.baseline+"L"+points[0].x+","+geo.baseline+"Z";
       element("path",{d:fill,fill:color,opacity:".12"},svg);
     }
     if(points.length>1)element("polyline",{
       points:points.map(p=>p.x+","+p.y).join(" "),fill:"none",stroke:color,
       "stroke-width":"2.8","stroke-linecap":"round","stroke-linejoin":"round"
     },svg);
     points.forEach(point=>{
       const circle=element("circle",{cx:point.x,cy:point.y,r:3.7,fill:color,stroke:"#141e32","stroke-width":"2",class:"chart-point",...(item.kind?{"data-detail":item.kind}:{})},svg);
       tooltip(circle,dateLabel(labels[point.index])+" · "+item.label+": "+(opts.currency?dollars(point.value):format(point.value)));
     });
   });
 });
 legend(figure,series);
}
function bars(id,series,opts={}){
 const rows=series[0]?.values||[];if(!rows.length)return empty(id);
 const {figure,svg,width,height}=hostCanvas(id,opts.title||"Объёмы обработки");
 const maxValues=rows.map((_,i)=>series.reduce((s,item)=>s+Math.max(0,clean(item.values[i])),0));
 const maximum=niceMaximum(Math.max(...maxValues));
 const geo=grid(svg,width,height,maximum,{currency:opts.currency}),slot=geo.innerW/rows.length;
 const barWidth=Math.min(36,Math.max(3,slot*.68));
 const labels=opts.labels||rows.map((_,i)=>String(i+1));
 rows.forEach((_,i)=>{
   let accumulated=0;
   series.forEach((item,k)=>{
     const count=Math.max(0,clean(item.values[i]));if(count<=0)return;
     const h=count/maximum*geo.innerH,rect=element("rect",{
       x:geo.left+i*slot+(slot-barWidth)/2,
       y:geo.baseline-accumulated-h,
       width:barWidth,height:h,
       fill:item.color||COLORS[k%COLORS.length],
       rx:Math.min(2.6,h/2),
       class:"chart-bar",...(item.kind?{"data-detail":item.kind}:{})
     },svg);
     tooltip(rect,dateLabel(labels[i])+" · "+item.label+": "+(opts.currency?dollars(count):format(count)));
     accumulated+=h;
   });
 });
 axisDates(svg,labels,i=>geo.left+(i+.5)*slot,height-7);
 legend(figure,series);
}
function donut(id,items,opts={}){
 const currency=opts.centerLabel==="USD";
 const records=items.filter(item=>clean(item.value)>0);
 const total=records.reduce((sum,item)=>sum+clean(item.value),0);
 if(!total)return empty(id,"За этот период анализ не выполнялся");
 const host=document.getElementById(id);host.replaceChildren();
 const layout=document.createElement("div");layout.className="donut-layout";
 const chart=document.createElement("div");chart.className="donut-visual";
 const svg=element("svg",{viewBox:"0 0 240 240",preserveAspectRatio:"xMidYMid meet",role:"img","aria-label":opts.title||"Распределение результатов"},chart);
 const center=120,radius=89,stroke=23,circ=2*Math.PI*radius;
 element("circle",{cx:center,cy:center,r:radius,fill:"none",stroke:"#25324b","stroke-width":stroke},svg);
 let offset=0;
 records.forEach((item,i)=>{
   const amount=clean(item.value),share=amount/total;
   const arc=element("circle",{cx:center,cy:center,r:radius,fill:"none",
     stroke:item.color||COLORS[i%COLORS.length],"stroke-width":stroke,
     "stroke-dasharray":share*circ+" "+circ,"stroke-dashoffset":-offset*circ,
     transform:"rotate(-90 120 120)",class:"donut-arc",
     ...(item.kind?{"data-detail":item.kind}:{})
   },svg);
   tooltip(arc,item.label+": "+(currency?dollars(amount):nf.format(amount))+" ("+Math.round(share*100)+"%)");
   offset+=share;
 });
 element("text",{x:120,y:119,class:"donut-number","text-anchor":"middle"},svg,currency?dollars(total):nf.format(total));
 element("text",{x:120,y:144,class:"donut-label","text-anchor":"middle"},svg,opts.centerLabel||"всего");
 const legendNode=document.createElement("div");legendNode.className="donut-legend-list";
 records.forEach((item,i)=>{
   const line=document.createElement("div");line.className="donut-legend-row";
   if(item.kind){line.dataset.detail=item.kind;line.tabIndex=0;}
   const dot=document.createElement("span");dot.className="donut-legend-dot";dot.style.background=item.color||COLORS[i%COLORS.length];
   const name=document.createElement("span");name.className="donut-name";name.textContent=item.label;
   const count=document.createElement("strong");count.className="donut-count";count.textContent=currency?dollars(clean(item.value)):nf.format(item.value);
   const percent=document.createElement("span");percent.className="donut-percent";percent.textContent=Math.round(item.value/total*100)+"%";
   line.append(dot,name,count,percent);legendNode.append(line);
 });
 layout.append(chart,legendNode);host.append(layout);
}
function hourBars(id,items,timeZone="Europe/Moscow"){
 if(!items?.length)return empty(id);
 const {svg,width,height}=hostCanvas(id,"Сообщения по часам, время Москвы");
 const max=niceMaximum(Math.max(...items.map(item=>clean(item.value))));
 const geo=grid(svg,width,height,max);
 const slot=geo.innerW/24,barWidth=Math.min(27,slot*.7);
 items.forEach((entry,i)=>{
   const height=clean(entry.value)/max*geo.innerH;
   if(height>0){
     const rectangle=element("rect",{x:geo.left+i*slot+(slot-barWidth)/2,y:geo.baseline-height,
       width:barWidth,height,rx:2.5,fill:i>=8&&i<21?"#43d9df":"#517195",class:"chart-bar"},svg);
     tooltip(rectangle,String(i).padStart(2,"0")+":00–"+String((i+1)%24).padStart(2,"0")+":00 МСК · "+nf.format(entry.value)+" сообщений");
   }
   if(i%4===0)element("text",{x:geo.left+(i+.5)*slot,y:geo.baseline+26,"text-anchor":"middle",class:"tick"},svg,String(i).padStart(2,"0")+":00");
 });
}
const knownErrors={
 invalid_shadow_context:"Контекст сообщения не подошёл для анализа",
 context_schema_invalid:"AI вернул данные в неправильном формате",
 extraction_release_budget_exhausted:"Достигнут лимит расходов на AI",
 context_insufficient_balance:"Не хватило бюджета для анализа",
 context_evidence_outside_envelope:"Данные не прошли проверку источника"
};
function humanError(code){return knownErrors[code]||"Другая техническая ошибка";}
function reasons(id,items){
 const host=document.getElementById(id);host.replaceChildren();
 if(!items?.length)return empty(id,"Причин ошибок за период не обнаружено");
 const maximum=Math.max(...items.map(item=>clean(item.count)),1);
 items.slice(0,7).forEach((item,i)=>{
   const button=document.createElement("button");button.className="reason-row";button.type="button";button.dataset.detail="errors";
   const top=document.createElement("div");top.className="reason-top";
   const name=document.createElement("strong");name.textContent=item.source+": "+humanError(item.code);
   const count=document.createElement("span");count.textContent=nf.format(item.count);
   top.append(name,count);const track=document.createElement("div");track.className="reason-track";
   const indicator=document.createElement("div");indicator.style.width=(clean(item.count)/maximum*100)+"%";
   indicator.style.background=i===0?"#ec708b":"#8c8bff";track.append(indicator);
   button.append(top,track);button.title="Код: "+item.code;host.append(button);
 });
}
window.StudCharts={plot,bars,donut,hourBars,reasons,empty,humanError,colors:COLORS};
})();