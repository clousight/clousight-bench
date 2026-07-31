"""Compact self-authored inline chart interaction (no third-party library).
Hover tooltip on any [data-value] element; legend items toggle their series."""
from __future__ import annotations

CHART_JS = """
(function(){
  var tip=document.createElement('div');tip.className='chart-tip';
  tip.style.cssText='position:fixed;pointer-events:none;background:#111;color:#fff;'+
  'padding:.2rem .5rem;border-radius:.3rem;font-size:.75rem;opacity:0;transition:opacity .1s;z-index:9';
  document.body.appendChild(tip);
  document.querySelectorAll('svg [data-value]').forEach(function(el){
    el.addEventListener('mousemove',function(e){
      tip.textContent=(el.getAttribute('data-series')||'')+' \\u00b7 '+
        (el.getAttribute('data-label')||'')+': '+el.getAttribute('data-value');
      tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';tip.style.opacity=1;
    });
    el.addEventListener('mouseleave',function(){tip.style.opacity=0;});
  });
  document.querySelectorAll('.legend-item').forEach(function(li){
    li.style.cursor='pointer';
    li.addEventListener('click',function(){
      var s=li.getAttribute('data-series');li.classList.toggle('off');
      document.querySelectorAll('[data-series="'+s+'"]').forEach(function(el){
        if(!el.classList.contains('legend-item'))
          el.style.display=el.style.display==='none'?'':'none';
      });
    });
  });
})();
"""
