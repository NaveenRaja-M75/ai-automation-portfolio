"""Local dashboard on 127.0.0.1 only: a page, a JSON snapshot, and a Server-Sent Events
stream. Bound to loopback so nothing on your network can read positions or balances."""

from __future__ import annotations

import asyncio
import json

from aiohttp import web

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jevbot Lab</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#14161a;--mute:#5d6470;--line:#e2e5ea;--up:#0f8a4a;--down:#c43131;--warn:#a86a00;--acc:#3b5bdb}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0d0f12;--card:#151920;--ink:#e8eaee;--mute:#8a93a1;--line:#252b35;--up:#3ccf7f;--down:#ff6b6b;--warn:#f0b43c;--acc:#7c9cff}}
:root[data-theme="dark"]{--bg:#0d0f12;--card:#151920;--ink:#e8eaee;--mute:#8a93a1;--line:#252b35;--up:#3ccf7f;--down:#ff6b6b;--warn:#f0b43c;--acc:#7c9cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:16px}
header{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px}
h1{font-size:18px;margin:0 12px 0 0}h2{font-size:14px;margin:0 0 10px;color:var(--mute);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.pill{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:3px 10px;font-size:12px}
.pill b{font-variant-numeric:tabular-nums}.live{border-color:var(--down);color:var(--down);font-weight:700}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-bottom:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}
.sym{display:flex;justify-content:space-between;align-items:baseline}.sym .px{font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}
.act{display:inline-block;margin:8px 0 4px;padding:2px 8px;border-radius:6px;font-weight:700;font-size:12px}
.buy{background:color-mix(in srgb,var(--up) 18%,transparent);color:var(--up)}.sell{background:color-mix(in srgb,var(--down) 18%,transparent);color:var(--down)}.hold{background:color-mix(in srgb,var(--mute) 15%,transparent);color:var(--mute)}
.reason{color:var(--mute);font-size:12px;min-height:18px}
.bar{display:grid;grid-template-columns:76px 1fr 44px;gap:8px;align-items:center;margin:5px 0;font-size:12px}
.track{height:8px;background:var(--line);border-radius:4px;position:relative;overflow:hidden}
.fill{position:absolute;top:0;bottom:0;left:0;border-radius:4px;background:var(--acc)}.mid{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--mute)}
.words{margin-top:8px;font-size:12px;color:var(--mute)}.words span{display:inline-block;margin:2px 6px 0 0}
.tbl{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{color:var(--mute);font-weight:600}.pos{color:var(--up)}.neg{color:var(--down)}
.v{font-weight:700;font-size:11px}.EDGE_PLAUSIBLE{color:var(--up)}.NEGATIVE_EDGE{color:var(--down)}.NO_EDGE_DEMONSTRATED,.INSUFFICIENT,.INSUFFICIENT_DATA{color:var(--mute)}
.note{color:var(--mute);font-size:12px;margin:8px 0 0}
select{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:3px 6px}
</style></head><body><div class="wrap">
<header><h1>Jevbot Lab</h1><span id="mode" class="pill">…</span><span id="feed" class="pill">feed …</span>
<span id="eq" class="pill">equity …</span><span id="jev" class="pill">jev off</span><span id="cp" class="pill">copy …</span><span id="ks" class="pill">kill switch off</span><span id="gd" class="pill"></span><span id="up" class="pill"></span></header>
<div class="card" id="alloccard" style="margin-bottom:14px;display:none"><h2>Allocation (paper) &mdash; 200-day trend filter</h2><div id="alloc"></div>
<p class="note">The backtest's most consistent rule: 100% crypto, stepping down to 50% after a close more than 6% below the 200-day average, back to 100% after a close more than 3% above. About 3 trades a year. Every sale goes to schedule_vda.csv for the tax return.</p></div>
<div id="syms" class="grid"></div>
<div class="card" style="margin-bottom:14px"><h2>Signal scoreboard <select id="hz"></select></h2><div class="tbl"><table id="score"></table></div>
<p class="note">Skill: Brier skill vs the base rate (above 0 means better than always guessing the base rate). Net %: average simulated long trade, non-overlapping, after spread and fees, with and without 1% TDS. Verdict comes from your core.metrics scorecard, corrected for every signal, horizon and symbol tested.</p></div>
<div class="card" style="margin-bottom:14px"><h2>Performance (closed trades, after fees)</h2><div class="tbl"><table id="perf"></table></div>
<p class="note">Expectancy is the average profit per trade, and the number that matters most. Win rate alone means nothing: a 90% win rate can still lose if the losses are big.</p></div>
<div class="card" id="newscard" style="margin-bottom:14px;display:none"><h2>News judged by Jev</h2><div id="newsst" class="reason"></div><div class="tbl"><table id="news"></table></div></div>
<div class="card"><h2>Trades</h2><div class="tbl"><table id="trades"></table></div></div>
</div><script>
const $=id=>document.getElementById(id);let S=null,H=null;
const pct=(x,d=3)=>x==null?'–':(x>0?'+':'')+x.toFixed(d)+'%';
function renderHeader(){const m=S.mode.toUpperCase();$('mode').textContent=m;$('mode').className='pill'+(m==='LIVE'?' live':'');
const ages=Object.values(S.feed.age_s);$('feed').innerHTML=`feed <b>${S.feed.connected?'connected':'DOWN'}</b> · max age ${Math.max(...ages).toFixed(1)}s · reconnects ${S.feed.reconnects}`;
$('eq').innerHTML=`equity <b>${S.equity.toFixed(2)} USDT</b>`;$('ks').innerHTML=S.kill_switch?'<b style="color:var(--down)">KILL SWITCH ON</b>':'kill switch off';
$('up').textContent=`up ${Math.floor(S.uptime_s/60)}m · TDS ${S.apply_tds?'on':'off'}`;
if(S.guards){const g=S.guards;$('gd').innerHTML=`P&L closed <b class="${g.net_closed>=0?'pos':'neg'}">${g.net_closed>=0?'+':''}${g.net_closed.toFixed(2)}</b> · ${g.wins}/${g.closed} wins · fees ${g.fees.toFixed(2)} · DD ${g.drawdown_pct.toFixed(2)}% · streak ${g.loss_streak} · today ${g.trades_today}${g.paused_s?` · <b class=neg>PAUSED ${g.paused_s}s</b>`:''}`}
if(S.copy){$('cp').innerHTML=`copy <b>${S.copy.status}</b>`}
if(S.jev){const j=S.jev;$('jev').innerHTML=`jev <b>${j.ok}/${j.calls}</b> ok · ${j.last_latency_ms??'–'}ms · $${j.spend_today_usd.toFixed(4)}/$${j.budget_usd} today`+(j.credit?` · credit $${j.credit.balance.toFixed(2)} (used $${j.credit.total_used.toFixed(2)})`:'')+(j.credit_guard?` · <b class=neg>PAUSED: ${j.credit_guard}</b>`:j.last_error?` · <span class=neg title="${j.last_error.replace(/"/g,"'")}">err</span>`:'')}
if(!H){H=String(S.trade_horizon_s);$('hz').innerHTML=S.horizons_s.map(h=>`<option value="${h}" ${String(h)===H?'selected':''}>horizon ${h>=120?h/60+' min':h+' s'}</option>`).join('');}}
function renderSyms(){$('syms').innerHTML=S.symbols.map(s=>{const L=S.latest[s]||{status:'starting'};
if(L.status!=='ok')return`<div class="card"><div class="sym"><b>${s}</b></div><p class="reason">${L.status}</p></div>`;
const bars=Object.entries(L.probs).map(([k,v])=>{const p=v[String(S.trade_horizon_s)];if(p==null)return'';
return`<div class="bar"><span>${k}${k===L.primary?' ★':''}</span><div class="track"><div class="fill" style="width:${p*100}%"></div><div class="mid"></div></div><span>${(p*100).toFixed(0)}%</span></div>`}).join('');
const w=L.words;const words=[...Object.entries(w.price_trend).map(([k,v])=>`${k}: ${v}`),`flow 1m: ${w.taker_flow['last 1 minute']}`,`book: ${w.order_book['within 0.10% of price']}`,`activity: ${w.trading_activity}`,`vol: ${w.volatility}`].map(x=>`<span>${x}</span>`).join('');
return`<div class="card"><div class="sym"><b>${s}</b><span class="px">${L.mid.toLocaleString(undefined,{maximumFractionDigits:4})}</span></div>
<div><span class="act ${L.action}">${L.action.toUpperCase()}</span> <span class="reason">${L.position>0?'holding '+L.position:''}</span></div><div class="reason">${L.reason}</div>${L.p_cal!=null?`<div class="reason">track record says p = ${(L.p_cal*100).toFixed(0)}% (signal claims ${(L.p_up*100).toFixed(0)}%)</div>`:''}
<div style="margin-top:6px">${bars||'<p class="reason">no signals yet</p>'}</div><div class="words">${words}</div></div>`}).join('')}
function renderScores(){const rows=S.scores.filter(r=>String(r.horizon)===H);
$('score').innerHTML='<tr><th>signal</th><th>symbol</th><th>n</th><th>hit rate</th><th>skill</th><th>trades</th><th>net % (TDS)</th><th>net % (no TDS)</th><th>t / needed</th><th>verdict</th></tr>'+
rows.map(r=>`<tr><td>${r.signal}</td><td>${r.symbol}</td><td>${r.n}</td><td>${(r.hit_rate*100).toFixed(1)}%</td><td class="${r.skill>0?'pos':'neg'}">${r.skill==null?'–':r.skill.toFixed(3)}</td><td>${r.trades}</td>
<td class="${r.avg_net_pct>0?'pos':'neg'}">${pct(r.avg_net_pct)}</td><td class="${r.avg_no_tds_pct>0?'pos':'neg'}">${pct(r.avg_no_tds_pct)}</td><td>${r.t_stat??'–'} / ${r.t_required??'–'}</td><td class="v ${r.verdict}">${r.verdict.replaceAll('_',' ')}</td></tr>`).join('')}
function renderTrades(){$('trades').innerHTML='<tr><th>time</th><th>symbol</th><th>side</th><th>qty</th><th>price</th><th>fee</th><th>tds</th><th>P&L</th><th>reason</th></tr>'+
S.trades.map(t=>`<tr><td>${new Date(t.ts*1000).toLocaleTimeString()}</td><td>${t.symbol}</td><td class="${t.side==='buy'?'pos':'neg'}">${t.side}</td><td>${t.qty}</td><td>${t.price.toFixed(4)}</td><td>${t.fee}</td><td>${t.tds??''}</td><td class="${t.pnl>0?'pos':t.pnl<0?'neg':''}">${t.pnl==null?'':(t.pnl>0?'+':'')+t.pnl.toFixed(2)}</td><td style="text-align:left">${t.reason}${t.simulated?' (paper)':''}</td></tr>`).join('')}
function renderNews(){if(!S.news)return;$('newscard').style.display='';$('newsst').textContent=S.news.status+' · '+Object.entries(S.news.tone).map(([k,v])=>k+': '+v).join(' · ');
$('news').innerHTML='<tr><th>headline</th><th>source</th><th>age</th><th>good</th><th>bad</th></tr>'+S.news.recent.map(h=>`<tr><td style="text-align:left;white-space:normal">${h.title.replace(/</g,'&lt;')}</td><td>${h.source}</td><td>${h.age_min}m</td><td class="pos">${(h.good*100).toFixed(0)}%</td><td class="neg">${(h.bad*100).toFixed(0)}%</td></tr>`).join('')}
function renderPerf(){const g=S.guards;if(!g)return;const f=(x,d=2)=>x==null?'–':(typeof x==='number'?x.toFixed(d):x);
const cells=[['trades',g.closed],['win rate',g.win_rate==null?'–':(g.win_rate*100).toFixed(0)+'%'],['expectancy / trade',f(g.expectancy,3)],['avg win',f(g.avg_win,3)],['avg loss',f(g.avg_loss,3)],['profit factor',f(g.profit_factor)],['net P&L',f(g.net_closed)],['fees paid',f(g.fees)],['max drawdown',f(g.max_drawdown_pct)+'%'],['best',f(g.best_trade)],['worst',f(g.worst_trade)],['peak equity',f(g.peak_equity)]];
$('perf').innerHTML='<tr>'+cells.map(c=>`<th>${c[0]}</th>`).join('')+'</tr><tr>'+cells.map(c=>`<td>${c[1]}</td>`).join('')+'</tr>'}
function renderAlloc(){const L=S.alloc;if(!L||!L.length)return;$('alloccard').style.display='';
const pct=x=>x==null?'–':(x*100).toFixed(1)+'%';
const rows=L.map(a=>a.equity==null?`<tr><td>${a.name}</td><td colspan="12" style="text-align:left">${a.status}</td></tr>`:
`<tr><td><b>${a.name}</b></td><td class="${a.on?'pos':'neg'}">${a.on?'trend on':'trend off'}</td><td>${pct(a.target)}</td><td>${pct(a.weight)}</td><td>${a.equity.toFixed(2)} / ${a.capital}</td><td class="${a.ratio_vs_sma200>=0?'pos':'neg'}">${pct(a.ratio_vs_sma200)}</td><td>${pct(a.exit_below)}</td><td>+${pct(a.enter_above)}</td><td>${a.trades}</td><td>${a.tax_paid}</td><td>${a.tax_if_sold_today}</td><td>${a.last_flip??'–'}</td><td>${a.started}</td></tr>`).join('');
$('alloc').innerHTML=`<div class="tbl"><table><tr><th>book</th><th>regime</th><th>target</th><th>actual crypto</th><th>equity</th><th>vs 200-day avg</th><th>steps down below</th><th>steps up above</th><th>trades</th><th>tax paid</th><th>tax if sold today</th><th>last flip</th><th>since</th></tr>${rows}</table></div>`}
function render(){if(!S)return;renderAlloc();renderPerf();renderHeader();renderSyms();renderScores();renderTrades();renderNews()}
$('hz').onchange=e=>{H=e.target.value;renderScores()};
fetch('/api/state').then(r=>r.json()).then(d=>{S=d;render()});
const es=new EventSource('/events');es.onmessage=e=>{const m=JSON.parse(e.data);
if(m.kind==='state')S=m.data;else if(S&&m.kind==='scores'){S.scores=m.data.scores;S.ml_samples=m.data.ml_samples}else if(S&&m.kind==='trade')S.trades.unshift(m.data);render()};
</script></body></html>"""


async def serve(engine, port: int) -> web.AppRunner:
    async def index(_):
        return web.Response(text=PAGE, content_type="text/html")

    async def state(_):
        return web.json_response(json.loads(json.dumps(engine.snapshot(), default=str)))

    async def events(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(request)
        q: asyncio.Queue = asyncio.Queue()
        engine.subscribers.add(q)
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    await resp.write(f"data: {msg}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": keepalive\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            engine.subscribers.discard(q)
        return resp

    app = web.Application()
    app.add_routes([web.get("/", index), web.get("/api/state", state), web.get("/events", events)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner
