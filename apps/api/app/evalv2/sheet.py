"""Static, self-contained HTML review sheet (no server, no external resources)."""

from __future__ import annotations

import json
from typing import Any

from app.evalv2 import labels

_TEMPLATE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>평가셋 v2 검수</title>
<style>
body{font-family:system-ui,'Noto Sans KR',sans-serif;margin:0;background:#f6f8fb;color:#0f172a}
.wrap{max-width:1000px;margin:0 auto;padding:16px}
.bar{position:sticky;top:0;background:#fff;border-bottom:1px solid #e2e8f0;padding:8px 16px;z-index:5;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.q{background:#fff;border:1px solid #e2e8f0;border-radius:10px;margin:12px 0;padding:12px 14px}
.q h3{margin:0 0 6px;font-size:15px}
.meta{color:#64748b;font-size:12px}
.c{border-top:1px solid #eef2f7;padding:8px 0;display:grid;grid-template-columns:1fr auto;gap:8px}
.c .ev{font-size:12px;color:#334155;background:#f8fafc;padding:4px 6px;border-radius:6px;margin-top:4px}
.badge{display:inline-block;font-size:11px;padding:1px 6px;border-radius:999px;background:#e2e8f0;margin-right:4px}
.badge.ptr{background:#fef3c7}.badge.llm2{background:#d1fae5}.badge.llm1{background:#e0f2fe}.badge.warn{background:#fee2e2}
button.m{border:1px solid #cbd5e1;background:#fff;border-radius:8px;padding:4px 12px;margin-left:4px;cursor:pointer;font-weight:700}
button.m.on-O{background:#047857;color:#fff}button.m.on-\25B3{background:#b45309;color:#fff}button.m.on-X{background:#b91c1c;color:#fff}
</style></head><body>
<div class="bar"><b>평가셋 v2 검수</b>
<label>검수자 <input id="reviewer" size="10"></label>
<label>검수일 <input id="reviewed_on" size="10" placeholder="YYYY-MM-DD"></label>
<span id="progress" class="meta"></span>
<button id="export" type="button">review-labels.json 내려받기</button></div>
<div class="wrap"><p class="meta">O = 질의에 직접 답하는 문서 · △ = 관련은 있으나 부분적 · X = 무관. 표시하지 않은 후보는 무시됩니다.
LLM 등급과 근거는 참고용입니다. 근거 문장은 코드로 본문 포함 여부를 확인했습니다(검증됨 표시).</p><div id="root"></div></div>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
var DATA=JSON.parse(document.getElementById("data").textContent);
var KEY="evalv2-review-"+DATA.run_id;
var SAFE_BASE=/^https?:\/\//i.test(DATA.doc_base||"")?DATA.doc_base:"";
var state={labels:{},reviewer:"",reviewed_on:""};
try{var s=localStorage.getItem(KEY);if(s){state=JSON.parse(s);}}catch(e){}
function save(){try{localStorage.setItem(KEY,JSON.stringify(state));}catch(e){}}
function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;}
function setMark(qid,eid,mark){
  var m=state.labels[qid]=state.labels[qid]||{};
  if(m[eid]===mark){delete m[eid];}else{m[eid]=mark;}
  save();render();
}
function render(){
  var root=document.getElementById("root");root.textContent="";
  var done=0;
  DATA.queries.forEach(function(q){
    var marks=state.labels[q.id]||{};
    if(Object.keys(marks).length)done++;
    var card=el("div","q");
    card.appendChild(el("h3",null,q.q));
    card.appendChild(el("div","meta",q.id+" · "+q.slice+" · "+q.source+(q.count?(" · 사용 "+q.count+"회"):"")));
    q.review.forEach(function(c){
      var row=el("div","c");var left=el("div");
      var t=el("div",null,c.title||c.external_id);left.appendChild(t);
      var meta=el("div","meta",c.external_id+" · "+(c.source_type||"")+" · arms: "+Object.keys(c.arms||{}).map(function(k){return k+"="+c.arms[k];}).join(","));
      left.appendChild(meta);
      var b=el("div");
      if(c.evidence_eligible===false)b.appendChild(el("span","badge ptr","pointer-only"));
      var llm=c.llm||{};
      if(llm.grade===2)b.appendChild(el("span","badge llm2","LLM 2"));
      else if(llm.grade===1)b.appendChild(el("span","badge llm1","LLM 1"));
      else if(llm.grade===0)b.appendChild(el("span","badge","LLM 0"));
      else b.appendChild(el("span","badge warn","LLM 미판정("+(llm.status||"?")+")"));
      if(c.arms&&c.arms.seed===0)b.appendChild(el("span","badge","seed"));
      if(SAFE_BASE){var a=el("a",null,"원문 열기");a.href=SAFE_BASE.replace(/\/$/,"")+"/doc.html?eid="+encodeURIComponent(c.external_id);a.target="_blank";a.rel="noopener";b.appendChild(a);}
      left.appendChild(b);
      if(llm.evidence){left.appendChild(el("div","ev","근거"+(llm.status==="ok"?"(검증됨)":"(미검증)")+": "+llm.evidence));}
      row.appendChild(left);
      var right=el("div");
      ["O","△","X"].forEach(function(mk){
        var btn=el("button","m"+(marks[c.external_id]===mk?" on-"+mk:""),mk);btn.type="button";
        btn.addEventListener("click",function(){setMark(q.id,c.external_id,mk);});right.appendChild(btn);
      });
      row.appendChild(right);card.appendChild(row);
    });
    root.appendChild(card);
  });
  document.getElementById("progress").textContent="검수 "+done+"/"+DATA.queries.length+" 질의";
}
var rv=document.getElementById("reviewer"),rd=document.getElementById("reviewed_on");
rv.value=state.reviewer||"";rd.value=state.reviewed_on||new Date().toISOString().slice(0,10);
state.reviewed_on=rd.value;
rv.addEventListener("input",function(){state.reviewer=rv.value;save();});
rd.addEventListener("input",function(){state.reviewed_on=rd.value;save();});
document.getElementById("export").addEventListener("click",function(){
  var out={reviewer:state.reviewer,reviewed_on:state.reviewed_on,labels:state.labels};
  var blob=new Blob([JSON.stringify(out,null,2)],{type:"application/json"});
  var a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="review-labels.json";document.body.appendChild(a);a.click();a.remove();
});
render();
})();
</script></body></html>
"""


def _safe_base(doc_base: str) -> str:
    """Only http(s) bases may produce links (blocks javascript:/data: hrefs)."""
    return doc_base if doc_base.lower().startswith(("http://", "https://")) else ""


def render(judged: dict[str, Any], *, doc_base: str = "", max_review: int = 8) -> str:
    data = {
        "run_id": judged.get("run_id") or "run",
        "doc_base": _safe_base(doc_base or ""),
        "queries": [
            {**{k: q.get(k) for k in ("id", "q", "slice", "source", "count")},
             "review": labels.candidates_for_review(q, max_n=max_review)}
            for q in judged["queries"]
        ],
    }
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return _TEMPLATE.replace("__DATA__", payload)
