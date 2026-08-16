"""EChartsRenderer: a single self-contained, bilingual HTML report.

Inlines the vendored ECharts UMD, embeds the ReportBundle as ``window.__BUNDLE__``
and a localization payload as ``window.__I18N__``, and renders one dimension per
tab. Charts are grouped BY UNIT (never mixing ms + ratio + count on one axis);
every panel also shows a caliber table (localized name · value · unit · evidence)
and the task's one-line notes as its caption. Small discrete series get plain
labelled markers — no zoom. No external resource: opens offline, archivable."""

from __future__ import annotations

import importlib.resources as resources
import json

from clousight_bench.core.reporting.bundle import ReportBundle
from clousight_bench.core.reporting.renderers import i18n
from clousight_bench.core.reporting.renderers.base import ReportRenderer


def _echarts_js() -> str:
    return (
        resources.files("clousight_bench.core.reporting.assets")
        .joinpath("echarts.min.js")
        .read_text(encoding="utf-8")
    )


def _i18n_payload() -> dict:
    """The zh/en label maps the app resolves by current language."""
    metric = {
        key: {"zh": i18n.METRIC_LABELS.get(key, key), "en": key, "unit": i18n.METRIC_UNIT.get(key, "")}
        for key in set(i18n.METRIC_LABELS) | set(i18n.METRIC_UNIT)
    }
    unit = {cls: {"zh": zh, "en": en} for cls, (zh, en) in i18n.UNIT_LABELS.items()}
    return {"metric": metric, "ui": dict(i18n.UI_STRINGS), "unit": unit}


# The inline app. Vanilla DOM + the ECharts global. Rebuilds on language toggle.
_APP_JS = r"""
(function(){
  var B = window.__BUNDLE__, I = window.__I18N__;
  var LANG = 'zh';
  var TAB_ORDER = ['Performance','Reliability','Observability','Cost','Capability'];
  var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  var active = {};  // domain index -> active tab

  function el(t,c,x){var e=document.createElement(t); if(c)e.className=c; if(x!=null)e.textContent=x; return e;}
  function ml(k){var m=I.metric[k]; return m?(LANG==='zh'?m.zh:m.en):k;}
  function tl(en){return LANG==='zh'?(I.ui[en]||en):en;}
  function ul(cls){var u=I.unit[cls]; return u?(LANG==='zh'?u.zh:u.en):cls;}
  function unitOf(k){var m=I.metric[k]; return m?m.unit:'';}
  function fmt(v){
    if(v==null) return '·';
    if(typeof v!=='number') return String(v);
    if(v===0) return '0';
    if(Math.abs(v)<0.01) return v.toPrecision(3);
    if(Math.abs(v)>=1000) return v.toLocaleString(undefined,{maximumFractionDigits:2});
    return (Math.round(v*100)/100).toString();
  }

  function metricsOf(panel){ // {platform: {name: {num,str,unit,evidence,agg}}}
    var out={}; (panel.cells||[]).forEach(function(c){
      var who=c.platform||c.execution||'—'; out[who]=out[who]||{};
      (c.metrics||[]).forEach(function(m){out[who][m.name]=m;});
    }); return out;
  }
  function allNames(byWho){var s={}; Object.keys(byWho).forEach(function(w){
    Object.keys(byWho[w]).forEach(function(n){s[n]=1;});}); return Object.keys(s);}

  function mkChart(host, option){
    var c = echarts.init(host, dark?'dark':null, {renderer:'canvas'});
    c.setOption(option); window.addEventListener('resize', function(){c.resize();}); return c;
  }

  // One horizontal bar per unit-group (categories = metric names sharing a unit).
  function unitCharts(root, panel, byWho){
    var names = allNames(byWho), whos = Object.keys(byWho);
    var groups = {};
    names.forEach(function(n){
      var u=unitOf(n); if(u===''||u==='bool'||u==='text') return; // table-only
      var anyNum = whos.some(function(w){return byWho[w][n]&&byWho[w][n].value_num!=null;});
      if(anyNum){(groups[u]=groups[u]||[]).push(n);}
    });
    Object.keys(groups).forEach(function(u){
      var cats = groups[u];
      var scale = (u==='ratio')?100:1;
      var maxv=0,minv=Infinity;
      var series = whos.map(function(w){
        var data = cats.map(function(n){
          var m=byWho[w][n]; var v=(m&&m.value_num!=null)?m.value_num*scale:null;
          if(v!=null){maxv=Math.max(maxv,v); if(v>0)minv=Math.min(minv,v);} return v;});
        return {name:w, type:'bar', data:data,
                label:{show:true, position:'right', formatter:function(o){return fmt(o.value);}}};
      });
      var logx = (u==='ms') && maxv>0 && minv>0 && minv!==Infinity && (maxv/minv>50);
      var xAxis = (u==='ratio')
        ? {type:'value', max:100, name:'%'}
        : {type: logx?'log':'value', name: ul(u)+(logx?' · log':'')};
      var host=el('div','chart'); root.appendChild(host);
      mkChart(host, {tooltip:{trigger:'axis'}, legend: whos.length>1?{top:0}:undefined,
        grid:{top: whos.length>1?32:12, left:8, right:56, bottom:24, containLabel:true},
        xAxis:xAxis, yAxis:{type:'category', data:cats.map(ml)}, series:series});
    });
  }

  function caliberTable(panel, byWho){
    var names = allNames(byWho), whos = Object.keys(byWho);
    if(!names.length) return el('div','muted', tl('no data'));
    var t=el('table','tbl'), hr=el('tr');
    hr.appendChild(el('th','', LANG==='zh'?'指标':'metric'));
    hr.appendChild(el('th','', LANG==='zh'?'单位':'unit'));
    whos.forEach(function(w){hr.appendChild(el('th','', w));});
    hr.appendChild(el('th','', LANG==='zh'?'证据':'evidence'));
    t.appendChild(hr);
    names.forEach(function(n){
      var r=el('tr'); r.appendChild(el('td','',ml(n)));
      var u=unitOf(n); r.appendChild(el('td','muted', u?ul(u):'—'));
      var ev='';
      whos.forEach(function(w){var m=byWho[w][n];
        var v=m?(m.value_num!=null?fmt(m.value_num):m.value_str):null;
        r.appendChild(el('td','num', v!=null?String(v):'·')); if(m&&m.evidence)ev=m.evidence;});
      var td=el('td',''); if(ev){td.appendChild(el('span','badge ev'+ev, ev));} else {td.textContent='·';}
      r.appendChild(td); t.appendChild(r);
    });
    return t;
  }

  function optTimeseries(byname){
    var names=Object.keys(byname), maxv=0,minv=Infinity;
    names.forEach(function(n){byname[n].forEach(function(p){
      maxv=Math.max(maxv,p.value); if(p.value>0)minv=Math.min(minv,p.value);});});
    var logy = maxv>0&&minv>0&&minv!==Infinity&&(maxv/minv>100);
    var series=names.map(function(n){return {name:n, type:'line', smooth:true, symbolSize:7,
      label:{show:true, fontSize:9, formatter:function(o){return fmt(o.value[1]);}},
      data:byname[n].map(function(p){return [p.t,p.value];})};});
    return {tooltip:{trigger:'axis'}, legend: names.length>1?{top:0}:undefined,
      grid:{top: names.length>1?32:16, left:56, right:24, bottom:32},
      xAxis:{type:'value', name: LANG==='zh'?'第 n 次调用':'call #', minInterval:1},
      yAxis:{type: logy?'log':'value', name: logy?(LANG==='zh'?'值 (log)':'value (log)'):'value'},
      series:series};
  }
  function optQuadrant(ch){
    var pts=ch.series.map(function(p){return {name:p.name, value:[p.x,p.y]};});
    return {tooltip:{formatter:function(o){return o.data.name+'<br>'+ch.x_label+': '+fmt(o.data.value[0])+
              '<br>'+ch.y_label+': '+fmt(o.data.value[1]);}},
      grid:{top:24,left:64,right:24,bottom:44},
      xAxis:{type:'value',name:ch.x_label,scale:true}, yAxis:{type:'value',name:ch.y_label,scale:true},
      series:[{type:'scatter',symbolSize:16,label:{show:true,position:'top',fontSize:10,
        formatter:function(o){return o.data.name;}}, data:pts,
        markLine:{silent:true,symbol:'none',lineStyle:{type:'dashed'},
          data:[{xAxis:ch.x_split},{yAxis:ch.y_split}]}}]};
  }

  function card(root, title, note){
    var c=el('section','card'); c.appendChild(el('h4','',title));
    if(note) c.appendChild(el('p','cap',note)); root.appendChild(c); return c;
  }
  function noteFor(dom,panel){
    var ns=(panel.task_ids||[]).map(function(t){return (dom.notes||{})[t];}).filter(Boolean);
    return ns.length?ns.join('  ·  '):'';
  }

  function renderPanel(root, dom, p){
    var ch=p.chart, note=noteFor(dom,p);
    if(ch && ch.kind==='quadrant'){
      if((ch.series||[]).length<3){ // degenerate in single-platform / few points
        var c0=card(root, tl(p.title), (LANG==='zh'
          ?'点数不足(需多平台或多任务散布才有意义),暂不绘制象限。':'too few points for a quadrant (needs multi-platform spread).'));
        return;
      }
      var c=card(root, tl(p.title), note||(LANG==='zh'
        ?'X=冷启动代价,Y=暖态延迟;虚线为各点中位数分隔。':'X=cold-start cost, Y=warm latency; dashed = medians.'));
      var h=el('div','chart'); c.appendChild(h); mkChart(h, optQuadrant(ch)); return;
    }
    if(ch && ch.kind==='timeseries'){
      var s=(dom.series||{})[p.task_ids[0]]||{};
      var c=card(root, tl(p.title), note); var h=el('div','chart'); c.appendChild(h);
      mkChart(h, optTimeseries(s)); return;
    }
    // bar-family or table-only: group by unit + always a caliber table.
    var byWho=metricsOf(p);
    var c=card(root, tl(p.title), note);
    if(ch && (ch.kind==='bar'||ch.kind==='grouped_bar'||ch.kind==='stacked_bar')){
      unitCharts(c, p, byWho);
    }
    c.appendChild(caliberTable(p, byWho));
  }

  function capMatrix(dom){
    var m=dom.capability_matrix||{}, plats=dom.platforms||[]; var t=el('table','tbl'), hr=el('tr');
    hr.appendChild(el('th','', tl('capability')));
    plats.forEach(function(p){hr.appendChild(el('th','',p));}); t.appendChild(hr);
    Object.keys(m).forEach(function(cap){var r=el('tr'); r.appendChild(el('td','', tl(cap)));
      plats.forEach(function(p){r.appendChild(el('td','', (m[cap]||{})[p]||'·'));}); t.appendChild(r);});
    return t;
  }

  function render(){
    var app=document.getElementById('app'); app.innerHTML='';
    B.domains.forEach(function(dom, di){
      app.appendChild(el('h2','', tl(dom.domain)+' · '+(LANG==='zh'?(dom.mode==='single'?'单平台':'多平台'):dom.mode)
        +' ('+(dom.platforms||[]).join(', ')+')'));
      (dom.red_flags||[]).forEach(function(f){app.appendChild(el('div','flag',f));});
      var byTab={}; (dom.panels||[]).forEach(function(p){(byTab[p.tab||'Other']=byTab[p.tab||'Other']||[]).push(p);});
      var tabs=TAB_ORDER.filter(function(t){return byTab[t];})
        .concat(Object.keys(byTab).filter(function(t){return TAB_ORDER.indexOf(t)<0;}));
      tabs.push('__cap__');
      if(!active[di]||tabs.indexOf(active[di])<0) active[di]=tabs[0];
      var bar=el('div','tabs');
      tabs.forEach(function(tab){
        var label = tab==='__cap__'? tl('Capability matrix') : tl(tab);
        var btn=el('button','tab'+(tab===active[di]?' on':''), label);
        btn.onclick=function(){active[di]=tab; render();}; bar.appendChild(btn);
      });
      app.appendChild(bar);
      var body=el('div','tabbody'); app.appendChild(body);
      if(active[di]==='__cap__'){ var c=card(body, tl('Capability matrix'),''); c.appendChild(capMatrix(dom)); }
      else { byTab[active[di]].forEach(function(p){renderPanel(body, dom, p);}); }
    });
  }

  // header controls: language toggle + evidence legend
  var ctl=document.getElementById('ctl');
  var lb=el('button','langbtn', LANG==='zh'?'EN':'中');
  lb.onclick=function(){LANG=(LANG==='zh'?'en':'zh'); lb.textContent=(LANG==='zh'?'EN':'中');
    document.getElementById('legend').textContent=legendText(); render();};
  ctl.appendChild(lb);
  function legendText(){return LANG==='zh'
    ? '证据等级 A=文档读数 · B=环境观测 · C=受控测量'
    : 'evidence A=doc reading · B=env observation · C=controlled';}
  var lg=el('span','legend',legendText()); lg.id='legend'; ctl.appendChild(lg);
  render();
})();
"""


_CSS = (
    "body{font-family:system-ui,'Noto Sans SC',sans-serif;margin:0;background:#f7f9fc;color:#0f172a}"
    "@media(prefers-color-scheme:dark){body{background:#0b1220;color:#e2e8f0}"
    ".card{background:#111a2e !important;border-color:#1e293b !important}"
    ".tbl th,.tbl td{border-color:#1e293b !important}.tab{background:#111a2e !important;color:#93a4c4 !important}"
    ".tab.on{background:#2f6df6 !important;color:#fff !important}}"
    ".wrap{max-width:1180px;margin:0 auto;padding:1.2rem 1.5rem 4rem}"
    ".card{background:#fff;border:1px solid #e2e8f0;border-radius:.6rem;padding:1rem;margin:.7rem 0}"
    ".chart{width:100%;height:340px;margin:.3rem 0}"
    ".cap{color:#475569;font-size:.82rem;margin:.1rem 0 .6rem;line-height:1.45}"
    "@media(prefers-color-scheme:dark){.cap{color:#94a3b8}}"
    ".tbl{width:100%;border-collapse:collapse;font-size:.85rem}"
    ".tbl th,.tbl td{border-bottom:1px solid #e2e8f0;padding:.35rem .55rem;text-align:left}"
    ".num{font-variant-numeric:tabular-nums;text-align:right}.muted{color:#94a3b8}"
    ".flag{color:#b45309;margin:.3rem 0}h2{margin:1.4rem 0 .4rem;font-size:1.15rem}h4{margin:.1rem 0 .3rem}"
    ".tabs{display:flex;flex-wrap:wrap;gap:.35rem;margin:.4rem 0 .2rem}"
    ".tab{border:0;border-radius:.45rem;padding:.35rem .8rem;background:#e8eef8;color:#334155;"
    "cursor:pointer;font-size:.85rem}.tab.on{background:#2f6df6;color:#fff}"
    ".badge{display:inline-block;border-radius:.3rem;padding:.02rem .4rem;font-size:.72rem;color:#fff}"
    ".evA{background:#64748b}.evB{background:#0ea5e9}.evC{background:#16a34a}"
    ".topbar{background:linear-gradient(100deg,#1e4fd6,#2f6df6);color:#fff;padding:.7rem 1.5rem;"
    "display:flex;align-items:center;gap:1rem;position:sticky;top:0;z-index:9}"
    ".topbar .ttl{font-weight:600}.langbtn{border:1px solid #ffffff88;background:#ffffff22;color:#fff;"
    "border-radius:.4rem;padding:.15rem .6rem;cursor:pointer}.legend{font-size:.78rem;opacity:.9}"
)


class EchartsRenderer(ReportRenderer):
    name = "echarts"
    output_suffix = ".html"

    def render(self, bundle: ReportBundle) -> str:
        data = json.dumps(bundle.to_dict(), ensure_ascii=False)
        i18n_data = json.dumps(_i18n_payload(), ensure_ascii=False)
        return (
            "<!DOCTYPE html><html lang=zh><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Clousight Bench 指北测评</title><style>{_CSS}</style></head><body>"
            "<div class=topbar><span class=ttl>Clousight Bench · 指北测评</span>"
            "<span id=ctl style='margin-left:auto;display:flex;align-items:center;gap:.8rem'></span></div>"
            "<div class=wrap id=app></div>"
            f"<script>{_echarts_js()}</script>"
            f"<script>window.__BUNDLE__={data};window.__I18N__={i18n_data};</script>"
            f"<script>{_APP_JS}</script>"
            "</body></html>"
        )
