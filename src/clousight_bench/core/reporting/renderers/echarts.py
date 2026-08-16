"""EChartsRenderer: a single self-contained HTML report. Inlines the vendored
ECharts UMD, embeds the ReportBundle as ``window.__BUNDLE__``, and renders each
panel with an ECharts instance chosen by ``chart.kind`` (bar / grouped_bar /
stacked_bar / quadrant / timeseries) plus HTML tables for scalar and capability
panels. No external resource — the document opens offline and is archivable."""

from __future__ import annotations

import importlib.resources as resources
import json

from clousight_bench.core.reporting.bundle import ReportBundle
from clousight_bench.core.reporting.renderers.base import ReportRenderer


def _echarts_js() -> str:
    return (
        resources.files("clousight_bench.core.reporting.assets")
        .joinpath("echarts.min.js")
        .read_text(encoding="utf-8")
    )


# The inline app: builds the DOM from window.__BUNDLE__ and picks an ECharts
# option per chart.kind. Kept dependency-free (vanilla DOM + ECharts global).
_APP_JS = r"""
(function(){
  var B = window.__BUNDLE__;
  var TAB_ORDER = ['Performance','Reliability','Observability','Cost','Capability'];
  var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  function el(tag, cls, txt){var e=document.createElement(tag); if(cls)e.className=cls;
    if(txt!=null)e.textContent=txt; return e;}
  function optBar(chart, stacked){
    var cats = chart.x_label.split(' / ');
    var series = chart.series.map(function(s){
      return {name:s.name, type:'bar', stack: stacked?'all':undefined, data:s.points};});
    return {tooltip:{trigger:'axis'}, legend:{top:0}, grid:{top:36,left:52,right:16,bottom:28},
            xAxis:{type:'category', data:cats, axisLabel:{interval:0,rotate:cats.length>3?20:0}},
            yAxis:{type:'value', name:chart.y_label}, series:series};
  }
  function optQuadrant(chart){
    var pts = chart.series.map(function(p){return {name:p.name, value:[p.x, p.y]};});
    return {tooltip:{formatter:function(o){return o.data.name+'<br>'+chart.x_label+': '+
              o.data.value[0]+'<br>'+chart.y_label+': '+o.data.value[1];}},
      grid:{top:24,left:64,right:24,bottom:44},
      xAxis:{type:'value', name:chart.x_label, scale:true},
      yAxis:{type:'value', name:chart.y_label, scale:true},
      series:[{type:'scatter', symbolSize:16,
        label:{show:true, formatter:function(o){return o.data.name;}, position:'top', fontSize:10},
        data:pts,
        markLine:{silent:true, symbol:'none', lineStyle:{type:'dashed'}, data:[
          {xAxis:chart.x_split},{yAxis:chart.y_split}]}}]};
  }
  function optTimeseries(byname){
    var names = Object.keys(byname);
    var maxv=0, minv=Infinity;
    names.forEach(function(n){byname[n].forEach(function(p){
      maxv=Math.max(maxv,p.value); if(p.value>0)minv=Math.min(minv,p.value);});});
    var logy = maxv>0 && minv>0 && minv!==Infinity && (maxv/minv) > 100;
    var series = names.map(function(n){return {name:n, type:'line', smooth:true, symbolSize:6,
      data: byname[n].map(function(p){return [p.t, p.value];})};});
    return {tooltip:{trigger:'axis'}, legend:{top:0}, grid:{top:36,left:60,right:24,bottom:52},
      dataZoom:[{type:'inside'},{type:'slider'}],
      xAxis:{type:'value', name:'step'},
      yAxis:{type: logy?'log':'value', name: logy?'value (log)':'value'}, series:series};
  }
  function mkChart(host, option){
    var c = echarts.init(host, dark?'dark':null, {renderer:'canvas'});
    c.setOption(option); window.addEventListener('resize', function(){c.resize();});
  }
  function tableFor(panel){
    var t = el('table','tbl'); var seen={};
    panel.cells.forEach(function(c){c.metrics.forEach(function(m){seen[m.name]=1;});});
    var keys = Object.keys(seen);
    if(!keys.length){ return el('div','muted','—'); }
    var hr = el('tr'); hr.appendChild(el('th','','platform'));
    keys.forEach(function(k){hr.appendChild(el('th','',k));}); t.appendChild(hr);
    panel.cells.forEach(function(c){var r=el('tr');
      r.appendChild(el('td','', c.platform||c.execution||'—'));
      var byn={}; c.metrics.forEach(function(m){byn[m.name]=m.value_num!=null?m.value_num:m.value_str;});
      keys.forEach(function(k){r.appendChild(el('td','num', byn[k]!=null?String(byn[k]):'·'));});
      t.appendChild(r);}); return t;
  }
  function capMatrix(dom){
    var m=dom.capability_matrix||{}; var plats=dom.platforms||[]; var t=el('table','tbl');
    var hr=el('tr'); hr.appendChild(el('th','','capability'));
    plats.forEach(function(p){hr.appendChild(el('th','',p));}); t.appendChild(hr);
    Object.keys(m).forEach(function(cap){var r=el('tr'); r.appendChild(el('td','',cap));
      plats.forEach(function(p){r.appendChild(el('td','', (m[cap]||{})[p]||'·'));}); t.appendChild(r);});
    return t;
  }
  function chartCard(root, title, option){
    var card=el('section','card'); card.appendChild(el('h4','',title));
    var host=el('div','chart'); card.appendChild(host); root.appendChild(card);
    mkChart(host, option);
  }
  var root = document.getElementById('app');
  B.domains.forEach(function(dom){
    root.appendChild(el('h2','', dom.domain+' · '+dom.mode+' ('+(dom.platforms||[]).join(', ')+')'));
    (dom.red_flags||[]).forEach(function(f){root.appendChild(el('div','flag',f));});
    var byTab={}; (dom.panels||[]).forEach(function(p){
      (byTab[p.tab||'Other']=byTab[p.tab||'Other']||[]).push(p);});
    var tabs = TAB_ORDER.filter(function(t){return byTab[t];})
      .concat(Object.keys(byTab).filter(function(t){return TAB_ORDER.indexOf(t)<0;}));
    tabs.forEach(function(tab){
      root.appendChild(el('h3','tabhdr',tab));
      byTab[tab].forEach(function(p){
        var ch=p.chart;
        if(ch && ch.kind==='timeseries'){
          chartCard(root, p.title, optTimeseries((dom.series||{})[p.task_ids[0]]||{})); return;}
        if(ch && ch.kind==='quadrant'){ chartCard(root, p.title, optQuadrant(ch)); return; }
        if(ch && (ch.kind==='bar'||ch.kind==='grouped_bar'||ch.kind==='stacked_bar')){
          chartCard(root, p.title, optBar(ch, ch.kind==='stacked_bar')); return;}
        var card=el('section','card'); card.appendChild(el('h4','',p.title));
        card.appendChild(tableFor(p)); root.appendChild(card);
      });
    });
    root.appendChild(el('h3','tabhdr','Capability matrix'));
    var cap=el('section','card'); cap.appendChild(capMatrix(dom)); root.appendChild(cap);
  });
})();
"""


_CSS = (
    "body{font-family:system-ui,'Noto Sans SC',sans-serif;margin:0;background:#f7f9fc;color:#0f172a}"
    "@media(prefers-color-scheme:dark){body{background:#0b1220;color:#e2e8f0}"
    ".card{background:#111a2e !important;border-color:#1e293b !important}"
    ".tbl th,.tbl td{border-color:#1e293b !important}}"
    ".wrap{max-width:1180px;margin:0 auto;padding:1.5rem}"
    ".card{background:#fff;border:1px solid #e2e8f0;border-radius:.6rem;padding:1rem;margin:.8rem 0}"
    ".chart{width:100%;height:360px}"
    ".tbl{width:100%;border-collapse:collapse;font-size:.85rem}"
    ".tbl th,.tbl td{border-bottom:1px solid #e2e8f0;padding:.35rem .5rem;text-align:left}"
    ".num{font-variant-numeric:tabular-nums}.muted{color:#94a3b8}"
    ".flag{color:#b45309;margin:.3rem 0}.tabhdr{margin-top:1.4rem}"
    "h4{margin:.2rem 0 .6rem}"
    ".topbar{background:linear-gradient(100deg,#1e4fd6,#2f6df6);color:#fff;"
    "padding:.8rem 1.5rem;font-weight:600;position:sticky;top:0;z-index:9}"
)


class EchartsRenderer(ReportRenderer):
    name = "echarts"
    output_suffix = ".html"

    def render(self, bundle: ReportBundle) -> str:
        data = json.dumps(bundle.to_dict(), ensure_ascii=False)
        return (
            "<!DOCTYPE html><html lang=zh><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Clousight Bench 指北测评</title><style>{_CSS}</style></head><body>"
            "<div class=topbar>Clousight Bench · 报告</div>"
            "<div class=wrap id=app></div>"
            f"<script>{_echarts_js()}</script>"
            f"<script>window.__BUNDLE__={data};</script>"
            f"<script>{_APP_JS}</script>"
            "</body></html>"
        )
