"""Builds dashboard/index.html (interactive Plotly dashboard) from outputs/tables/*.csv.
Run from the repo root:  python3 dashboard/build_dashboard.py
"""
import csv, json, os

T = 'outputs/tables'
def load(name):
    with open(f'{T}/{name}.csv', encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k, v in r.items():
            try:
                r[k] = float(v)
            except (TypeError, ValueError):
                pass
    return rows

def cols(rows, *names):
    return {n: [r[n] for r in rows] for n in names}

monthly = load('q1_monthly')
city_m = load('q2_city_month')
stations = load('q3_station_perf_Q2_2025')
temp = load('q3_charge_time_vs_temp')
lots = load('q4_range_by_group_2025')
exposure = load('q4_bad_lot_exposure_month')
partners = load('q5_partner_month')
cohort = load('q6_cohort_churn')
riders = load('q6_new_riders')
bridge = load('q5_margin_bridge')

# churn vs first-month failure rate (riders with >= 5 attempts)
buckets = [(0, .05, '0–5%'), (.05, .10, '5–10%'), (.10, .20, '10–20%'), (.20, 1.01, '20%+')]
churn_fail = []
for lo, hi, lab in buckets:
    g = [r for r in riders if r['attempts_30'] >= 5 and lo <= r['fails_30'] / r['attempts_30'] < hi]
    churn_fail.append({'bucket': lab, 'riders': len(g), 'churn': sum(r['churned'] for r in g) / len(g)})

D = {
    'monthly': cols(monthly, 'month', 'swaps', 'revenue', 'fail_rate', 'no_battery_rate', 'abandon_rate', 'system_fail_rate',
                    'rev_per_swap', 'margin_per_swap', 'discount_per_swap', 'active_stations', 'swaps_per_station_day'),
    'city_month': cols(city_m, 'city', 'month', 'fail_rate', 'no_battery_rate'),
    'stations': cols(stations, 'station_id', 'city', 'wave', 'gen', 'location_type', 'swaps_per_day', 'fail_rate',
                     'stockout_hour_share', 'avg_charge_min', 'monthly_profit'),
    'temp': cols(temp, 'gen', 'cabinet_temp_bin', 'avg_charge_min', 'stockout_share'),
    'lots': cols(lots, 'grp', 'vehicle_class', 'avg_km', 'avg_soh'),
    'exposure': cols(exposure, 'month', 'share_swaps_bad_lot_pack'),
    'partners': cols(partners, 'month', 'partner_name', 'swaps', 'contribution_per_swap', 'discount_rate'),
    'cohort': cols(cohort, 'cohort', 'churn_rate', 'riders'),
    'churn_fail': churn_fail,
    'bridge': cols(bridge, 'step', 'inr_per_swap'),
}

html = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VoltRelay Energy – Root-cause dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
 :root{--bg:#f6f8fa;--card:#fff;--ink:#1f2328;--mute:#57606a;--blue:#1f6feb;--red:#cf222e;--green:#2da44e;--orange:#f0883e}
 *{box-sizing:border-box} body{margin:0;font-family:Segoe UI,Roboto,Arial,sans-serif;background:var(--bg);color:var(--ink)}
 header{background:#0d1117;color:#fff;padding:18px 28px} header h1{margin:0;font-size:22px} header p{margin:4px 0 0;color:#c9d1d9;font-size:14px}
 nav{display:flex;gap:6px;padding:10px 28px;background:#161b22;flex-wrap:wrap}
 nav button{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:7px 14px;cursor:pointer;font-size:14px}
 nav button.on{background:var(--blue);color:#fff;border-color:var(--blue)}
 main{padding:18px 28px;max-width:1400px;margin:auto}
 .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:14px}
 .kpi{background:var(--card);border-radius:10px;padding:14px 16px;box-shadow:0 1px 3px #0001}
 .kpi .l{font-size:12px;color:var(--mute);text-transform:uppercase;letter-spacing:.04em} .kpi .v{font-size:26px;font-weight:700;margin-top:4px}
 .kpi .d{font-size:13px;margin-top:2px} .up{color:var(--green)} .bad{color:var(--red)}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:14px} @media(max-width:900px){.grid{grid-template-columns:1fr}}
 .card{background:var(--card);border-radius:10px;padding:12px 14px;box-shadow:0 1px 3px #0001}
 .card h3{margin:2px 0 2px;font-size:15px} .card .note{font-size:13px;color:var(--mute);margin:0 0 6px}
 .plot{height:340px} .tab{display:none} .tab.on{display:block}
 select{padding:5px 8px;border-radius:6px;border:1px solid #d0d7de;font-size:13px;margin-left:6px}
 .insight{background:#fff8c5;border-left:4px solid #d4a72c;padding:10px 14px;border-radius:6px;margin:0 0 14px;font-size:14px}
 footer{color:var(--mute);font-size:12px;padding:16px 28px;text-align:center}
</style></head><body>
<header><h1>VoltRelay Energy · Why service, retention and margin slipped while volume grew</h1>
<p>Jan 2024 – Jun 2025 · 3.81M swap attempts · 150 stations · 6 cities · Data Analytics Hackathon (Gradient Learnings)</p></header>
<nav id="nav"></nav>
<main>
 <div class="kpis" id="kpis"></div>

 <section class="tab on" id="t-overview">
  <div class="insight"><b>Story in one line:</b> failures spike every summer at old Gen1 chargers in hot cities. Those failures drive new-rider churn, while a bad battery batch and a bigger fleet discount squeeze margin.</div>
  <div class="grid">
   <div class="card"><h3>Swaps and revenue per month</h3><p class="note">Volume grew 2.8×, revenue 3×</p><div class="plot" id="p-vol"></div></div>
   <div class="card"><h3>Service-failure rate by type</h3><p class="note">Spikes in Apr–Jun of both years: mostly "no charged battery"</p><div class="plot" id="p-fail"></div></div>
   <div class="card"><h3>Unit economics (₹ per swap)</h3><p class="note">Operating margin rose with utilisation; discount per swap jumped in Nov-24 (ZipDrop)</p><div class="plot" id="p-unit"></div></div>
   <div class="card"><h3>Margin bridge Q1-24 → Q2-25 (₹ per swap)</h3><p class="note">What moved operating margin</p><div class="plot" id="p-bridge"></div></div>
  </div>
 </section>

 <section class="tab" id="t-failures">
  <div class="insight">Failure rate doubles on hot days and in the hot cities with the most Gen1 chargers (Jaipur, Delhi NCR, Hyderabad).</div>
  <div class="grid">
   <div class="card"><h3>Failure rate by city <select id="s-city"></select></h3><p class="note">Pick cities to compare</p><div class="plot" id="p-city"></div></div>
   <div class="card"><h3>City × month heatmap</h3><p class="note">Darker = more failed swaps</p><div class="plot" id="p-heat"></div></div>
  </div>
 </section>

 <section class="tab" id="t-stations">
  <div class="insight">Gen1 charging slows from 88 to 190 minutes at 50°C, so cabinets run out of charged packs. New Gen3 stations perform best: the expansion is not the problem.</div>
  <div class="grid">
   <div class="card"><h3>Charge time vs cabinet temperature</h3><p class="note">Average minutes to charge a pack</p><div class="plot" id="p-temp"></div></div>
   <div class="card"><h3>Every station, Apr–Jun 2025 <select id="s-gen"><option>All</option><option>Gen1</option><option>Gen2</option><option>Gen3</option></select></h3><p class="note">Hover for station id; bubble size = swaps/day</p><div class="plot" id="p-st"></div></div>
  </div>
 </section>

 <section class="tab" id="t-batteries">
  <div class="insight">Kyron lots KY-2407/08/09 (1,461 packs) sit at 61% health vs 81% for other suppliers. They deliver 19% less range, and ₹4.79 crore of packs were retired in one day.</div>
  <div class="grid">
   <div class="card"><h3>km per charge by battery group (2025)</h3><p class="note">Pack returned by the rider</p><div class="plot" id="p-km"></div></div>
   <div class="card"><h3>Share of swaps handing out a bad-lot pack</h3><p class="note">Jumped to 23% from Sep 2024</p><div class="plot" id="p-exp"></div></div>
  </div>
 </section>

 <section class="tab" id="t-pricing">
  <div class="insight">ZipDrop's discount rose from 12% to 28% in Nov-24. Contribution per swap fell 25%, and its volume grew no faster than the network: ₹36.4 lakh of extra discount in 8 months.</div>
  <div class="grid">
   <div class="card"><h3>Contribution per swap by partner <select id="s-part"></select></h3><p class="note">Revenue minus energy, ₹</p><div class="plot" id="p-part"></div></div>
   <div class="card"><h3>Swaps per month by partner</h3><p class="note">Same selection</p><div class="plot" id="p-partv"></div></div>
  </div>
 </section>

 <section class="tab" id="t-churn">
  <div class="insight">The first 30 days decide retention: riders with under 5% failed swaps churn at 10.7%, those above 20% at 39.5%.</div>
  <div class="grid">
   <div class="card"><h3>Churn by first-month failure rate</h3><p class="note">New riders with ≥5 attempts; churn = no swap in days 61–90</p><div class="plot" id="p-cf"></div></div>
   <div class="card"><h3>Churn by signup month</h3><p class="note">Summer cohorts churn most</p><div class="plot" id="p-coh"></div></div>
  </div>
 </section>
</main>
<footer>Built from the organizer dataset with Python, DuckDB and Plotly. Source: notebooks/voltrelay_analysis.ipynb · outputs/tables</footer>
<script>
(function(){
const D = __DATA__;
const C = {blue:'#1f6feb',red:'#cf222e',green:'#2da44e',orange:'#f0883e',purple:'#8250df',grey:'#6e7781'};
const GEN = {Gen1:C.red, Gen2:C.orange, Gen3:C.green};
const cfg = {displaylogo:false, responsive:true, modeBarButtonsToRemove:['lasso2d','select2d']};
const base = {margin:{l:55,r:20,t:10,b:45}, legend:{orientation:'h',y:-0.18}, hovermode:'x unified', font:{size:12}};
const L = (o) => Object.assign({}, base, o);
const pct = {tickformat:'.0%'};
const mon = D.monthly.month.map(m => String(m).slice(0,10));
const avg = (a) => a.reduce((s,x)=>s+x,0)/a.length;

// KPI cards: Q1-2024 vs Q2-2025
const q = (k) => [avg(D.monthly[k].slice(0,3)), avg(D.monthly[k].slice(-3))];
const kp = [
 ['Swaps / month', q('swaps'), v=>Math.round(v/1000)+'k', true],
 ['Revenue / month', q('revenue'), v=>'₹'+(v/1e6).toFixed(1)+'M', true],
 ['Failure rate', q('fail_rate'), v=>(v*100).toFixed(1)+'%', false],
 ['Margin / swap', q('margin_per_swap'), v=>'₹'+v.toFixed(1), true],
 ['Swaps / station / day', q('swaps_per_station_day'), v=>v.toFixed(0), true],
 ['New-rider churn (90d)', [0.195, 0.218], v=>(v*100).toFixed(1)+'%', false],
];
document.getElementById('kpis').innerHTML = kp.map(([l,[a,b],f,goodUp]) => {
  const good = goodUp ? b>=a : b<=a;
  return `<div class="kpi"><div class="l">${l}</div><div class="v">${f(b)}</div><div class="d ${good?'up':'bad'}">from ${f(a)} in Q1-24</div></div>`;
}).join('');

// tabs
const tabs = [['overview','Overview'],['failures','Failures'],['stations','Stations'],['batteries','Batteries'],['pricing','Pricing & fleets'],['churn','Churn']];
const nav = document.getElementById('nav');
tabs.forEach(([id,lab],i) => { const b=document.createElement('button'); b.textContent=lab; if(!i) b.className='on';
  b.onclick=()=>{document.querySelectorAll('nav button').forEach(x=>x.className=''); b.className='on';
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on')); document.getElementById('t-'+id).classList.add('on');
    window.dispatchEvent(new Event('resize'));}; nav.appendChild(b); });

// overview
Plotly.newPlot('p-vol', [
 {x:mon, y:D.monthly.swaps, name:'Completed swaps', type:'bar', marker:{color:'#c8d9f5'}},
 {x:mon, y:D.monthly.revenue.map(v=>v/1e6), name:'Revenue (₹M)', yaxis:'y2', mode:'lines+markers', line:{color:C.green,width:3}}],
 L({yaxis:{title:'swaps'}, yaxis2:{title:'₹ million', overlaying:'y', side:'right'}}), cfg);
Plotly.newPlot('p-fail', [
 {x:mon, y:D.monthly.no_battery_rate, name:'No charged battery', stackgroup:'a', line:{color:C.red}},
 {x:mon, y:D.monthly.system_fail_rate, name:'System error', stackgroup:'a', line:{color:C.purple}},
 {x:mon, y:D.monthly.abandon_rate, name:'Abandoned / cancelled', stackgroup:'a', line:{color:C.orange}}],
 L({yaxis:pct}), cfg);
Plotly.newPlot('p-unit', [
 {x:mon, y:D.monthly.rev_per_swap, name:'Revenue / swap', mode:'lines+markers', line:{color:C.blue}},
 {x:mon, y:D.monthly.margin_per_swap, name:'Operating margin / swap', mode:'lines+markers', line:{color:C.red}},
 {x:mon, y:D.monthly.discount_per_swap, name:'Discount / swap', mode:'lines+markers', line:{color:C.orange,dash:'dot'}}],
 L({yaxis:{title:'₹ per swap'}}), cfg);
const st = D.bridge.step, sv = D.bridge.inr_per_swap;
Plotly.newPlot('p-bridge', [{type:'waterfall', x:st, y:sv,
  measure: st.map((s,i)=> i===0 ? 'absolute' : (i===st.length-1 ? 'total' : 'relative')),
  text: sv.map(v=>(v>0?'+':'')+v.toFixed(1)), textposition:'outside',
  increasing:{marker:{color:C.green}}, decreasing:{marker:{color:C.red}}, totals:{marker:{color:C.blue}}}],
 L({hovermode:'closest', showlegend:false, yaxis:{title:'₹ per swap'}}), cfg);

// failures
const cities = [...new Set(D.city_month.city)];
const sc = document.getElementById('s-city');
sc.innerHTML = '<option>All cities</option>' + cities.map(c=>`<option>${c}</option>`).join('');
function drawCity(){
  const pick = sc.value === 'All cities' ? cities : [sc.value];
  Plotly.react('p-city', pick.map(c => {
    const idx = D.city_month.city.map((x,i)=>x===c?i:-1).filter(i=>i>=0);
    return {x: idx.map(i=>String(D.city_month.month[i]).slice(0,10)), y: idx.map(i=>D.city_month.fail_rate[i]), name:c, mode:'lines+markers'};
  }), L({yaxis:pct}), cfg);
}
sc.onchange = drawCity; drawCity();
const months = [...new Set(D.city_month.month.map(m=>String(m).slice(0,7)))];
const z = cities.map(c => months.map(m => { const i = D.city_month.city.findIndex((x,j)=>x===c && String(D.city_month.month[j]).slice(0,7)===m); return i>=0 ? D.city_month.fail_rate[i] : null; }));
Plotly.newPlot('p-heat', [{type:'heatmap', x:months, y:cities, z:z, colorscale:'Reds', hovertemplate:'%{y} %{x}: %{z:.1%}<extra></extra>', colorbar:{tickformat:'.0%'}}],
 L({hovermode:'closest', margin:{l:80,r:20,t:10,b:45}}), cfg);

// stations
Plotly.newPlot('p-temp', ['Gen1','Gen2','Gen3'].map(g => {
  const idx = D.temp.gen.map((x,i)=>x===g?i:-1).filter(i=>i>=0);
  return {x: idx.map(i=>D.temp.cabinet_temp_bin[i]), y: idx.map(i=>D.temp.avg_charge_min[i]), name:g, mode:'lines+markers', line:{color:GEN[g],width:3}};
}), L({xaxis:{title:'Cabinet temperature (°C)'}, yaxis:{title:'minutes'}}), cfg);
const sg = document.getElementById('s-gen');
function drawSt(){
  const gens = sg.value === 'All' ? ['Gen1','Gen2','Gen3'] : [sg.value];
  Plotly.react('p-st', gens.map(g => {
    const idx = D.stations.gen.map((x,i)=>x===g?i:-1).filter(i=>i>=0);
    return {x: idx.map(i=>D.stations.stockout_hour_share[i]), y: idx.map(i=>D.stations.fail_rate[i]), name:g, mode:'markers',
      marker:{color:GEN[g], size: idx.map(i=>Math.max(6, D.stations.swaps_per_day[i]/6)), opacity:.7, line:{width:1,color:'#fff'}},
      text: idx.map(i=>`${D.stations.station_id[i]} · ${D.stations.city[i]} · ${D.stations.wave[i]}<br>${D.stations.swaps_per_day[i].toFixed(0)} swaps/day · profit ₹${Math.round(D.stations.monthly_profit[i]/1000)}k/mo`),
      hovertemplate:'%{text}<br>fail %{y:.1%} · empty hours %{x:.1%}<extra></extra>'};
  }), L({hovermode:'closest', xaxis:{title:'Hours with zero charged 2W packs', tickformat:'.0%'}, yaxis:{title:'Failure rate', tickformat:'.0%'}}), cfg);
}
sg.onchange = drawSt; drawSt();

// batteries
const lab = D.lots.grp.map((g,i)=>g+' · '+D.lots.vehicle_class[i]);
Plotly.newPlot('p-km', [{type:'bar', x:lab, y:D.lots.avg_km, marker:{color:D.lots.grp.map(g=>g.includes('bad')?C.red:C.blue)},
  text:D.lots.avg_km.map(v=>v.toFixed(1)+' km'), textposition:'outside', hovertemplate:'%{x}: %{y:.1f} km<extra></extra>'}],
 L({hovermode:'closest', showlegend:false, yaxis:{title:'km per charge'}, margin:{l:55,r:20,t:10,b:110}}), cfg);
Plotly.newPlot('p-exp', [{x:D.exposure.month.map(m=>String(m).slice(0,10)), y:D.exposure.share_swaps_bad_lot_pack, mode:'lines+markers', fill:'tozeroy', line:{color:C.red}, name:'share'}],
 L({yaxis:pct, showlegend:false}), cfg);

// pricing & fleets
const pnames = [...new Set(D.partners.partner_name)];
const tot = {}; D.partners.partner_name.forEach((p,i)=>tot[p]=(tot[p]||0)+D.partners.swaps[i]);
const topP = pnames.sort((a,b)=>tot[b]-tot[a]);
const sp = document.getElementById('s-part');
sp.innerHTML = '<option>Top 5</option>' + topP.map(p=>`<option>${p}</option>`).join('');
function drawPart(){
  const pick = sp.value === 'Top 5' ? topP.slice(0,5) : [sp.value];
  const series = (k) => pick.map(p => { const idx = D.partners.partner_name.map((x,i)=>x===p?i:-1).filter(i=>i>=0);
    return {x: idx.map(i=>String(D.partners.month[i]).slice(0,10)), y: idx.map(i=>D.partners[k][i]), name:p, mode:'lines+markers',
            line:{width: p==='ZipDrop'?4:2}}; });
  const mark = {shapes:[{type:'line', x0:'2024-11-01', x1:'2024-11-01', yref:'paper', y0:0, y1:1, line:{dash:'dash',color:C.grey}}],
                annotations:[{x:'2024-11-01', yref:'paper', y:1, text:'ZipDrop 28% deal', showarrow:false, xanchor:'left', font:{size:11,color:C.grey}}]};
  Plotly.react('p-part', series('contribution_per_swap'), L(Object.assign({yaxis:{title:'₹ per swap'}}, mark)), cfg);
  Plotly.react('p-partv', series('swaps'), L(Object.assign({yaxis:{title:'swaps'}}, mark)), cfg);
}
sp.onchange = drawPart; drawPart();

// churn
Plotly.newPlot('p-cf', [{type:'bar', x:D.churn_fail.map(r=>r.bucket), y:D.churn_fail.map(r=>r.churn),
  marker:{color:[C.green,C.orange,C.red,'#82071e']}, text:D.churn_fail.map(r=>(r.churn*100).toFixed(1)+'% of '+r.riders), textposition:'outside',
  hovertemplate:'%{x}: %{y:.1%}<extra></extra>'}],
 L({hovermode:'closest', showlegend:false, xaxis:{title:'Share of failed swaps in first 30 days'}, yaxis:Object.assign({title:'Churned by day 90', range:[0,0.48]}, pct)}), cfg);
Plotly.newPlot('p-coh', [
 {x:D.cohort.cohort.map(m=>String(m).slice(0,10)), y:D.cohort.churn_rate, mode:'lines+markers', line:{color:C.red,width:3}, name:'90-day churn'},
 {x:D.cohort.cohort.map(m=>String(m).slice(0,10)), y:D.cohort.riders, type:'bar', yaxis:'y2', name:'new riders', marker:{color:'#d0d7de'}}],
 L({yaxis:Object.assign({title:'churn'}, pct), yaxis2:{overlaying:'y', side:'right', title:'riders'}}), cfg);
})();
</script></body></html>
"""
os.makedirs('dashboard', exist_ok=True)
with open('dashboard/index.html', 'w', encoding='utf-8') as fh:
    fh.write(html.replace('__DATA__', json.dumps(D, separators=(',', ':'))))
print('wrote dashboard/index.html', os.path.getsize('dashboard/index.html') // 1024, 'KB')
