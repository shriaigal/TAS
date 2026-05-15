/* Route planner front-end — UPGRADED.
 *
 * - Server-side autocomplete (debounced).
 * - Best route drawn as multi-coloured segments by congestion level.
 * - Alternate routes offset slightly to prevent overlap mess.
 * - Below-map analytics (built dynamically — does NOT touch /analytics page):
 *     1. Route comparison horizontal bar chart (click bar -> highlight route)
 *     2. 24-hour ML traffic forecast line chart (with current-hour marker)
 *     3. Best-departure recommendation card
 *     4. Route statistics grid (speed, congestion, bottleneck, etc.)
 */

const map = L.map('map', { zoomControl: true, preferCanvas: true })
            .setView([12.9716, 77.5946], 11);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '© OpenStreetMap'
}).addTo(map);

let routeLayers = [];
let markerLayers = [];
let altRouteData = [];
let bestRouteData = null;
let charts = { compare: null, forecast: null };

/* ---------- Autocomplete (unchanged behaviour, race-safe) ---------- */
function debounce(fn, wait) {
  let t = null;
  return function (...a) { if (t) clearTimeout(t); t = setTimeout(() => fn.apply(this, a), wait); };
}
async function fetchSuggestions(q) {
  const r = await fetch(`/get_locations?q=${encodeURIComponent(q)}&limit=10`,
    { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const d = await r.json();
  return Array.isArray(d.locations) ? d.locations : [];
}
function setupAutocomplete(inputId, listId) {
  const input = document.getElementById(inputId);
  const list  = document.getElementById(listId);
  if (!input || !list) return;
  let active = -1, lastReq = 0, lastSel = '';
  const close = () => { list.innerHTML = ''; list.classList.remove('show'); active = -1; };
  const render = matches => {
    list.innerHTML = ''; active = -1;
    if (!matches.length) { list.classList.remove('show'); return; }
    matches.forEach(loc => {
      const li = document.createElement('li');
      li.textContent = loc;
      li.addEventListener('mousedown', e => { e.preventDefault(); input.value = loc; lastSel = loc; close(); });
      list.appendChild(li);
    });
    list.classList.add('show');
  };
  const run = debounce(async q => {
    const id = ++lastReq;
    try { const m = await fetchSuggestions(q); if (id === lastReq) render(m); }
    catch { if (id === lastReq) close(); }
  }, 150);
  input.addEventListener('input', () => {
    const q = input.value.trim();
    if (q === lastSel) { close(); return; }
    lastSel = ''; if (!q) { close(); return; } run(q);
  });
  input.addEventListener('focus', () => { const q = input.value.trim(); if (q && q !== lastSel) run(q); });
  input.addEventListener('keydown', e => {
    const items = list.querySelectorAll('li');
    if (e.key === 'Escape') return close();
    if (!items.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); active = (active + 1) % items.length; mark(items); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = (active - 1 + items.length) % items.length; mark(items); }
    else if ((e.key === 'Enter' || e.key === 'Tab') && active >= 0) {
      e.preventDefault(); input.value = items[active].textContent; lastSel = input.value; close();
    }
  });
  function mark(items) {
    items.forEach(i => i.classList.remove('active'));
    if (active >= 0) { items[active].classList.add('active'); items[active].scrollIntoView({ block: 'nearest' }); }
  }
  document.addEventListener('click', e => { if (e.target !== input && !list.contains(e.target)) close(); });
}
setupAutocomplete('source', 'source-suggestions');
setupAutocomplete('destination', 'destination-suggestions');

/* ---------- Map drawing ---------- */
function clearMap() {
  routeLayers.forEach(l => map.removeLayer(l));
  markerLayers.forEach(m => map.removeLayer(m));
  routeLayers = []; markerLayers = [];
}
function levelClass(l) {
  return ({ Low: 'badge-low', Moderate: 'badge-moderate', High: 'badge-high', Severe: 'badge-severe' })[l] || 'badge-moderate';
}
function levelColor(l) {
  return ({ Low: '#22c55e', Moderate: '#eab308', High: '#f97316', Severe: '#ef4444' })[l] || '#3b6dff';
}

/* Offset polyline by ~tiny perpendicular degrees so alternates don't overlap */
function offsetCoords(coords, idx) {
  if (idx === 0 || coords.length < 2) return coords;
  const d = 0.00025 * idx * (idx % 2 === 0 ? 1 : -1);
  return coords.map(c => [c[0] + d, c[1] + d]);
}

/* Draw best route segment-by-segment (heatmap colour per congestion) */
function drawBestSegmented(best) {
  const coords = best.coordinates;
  if (!coords || coords.length < 2) return;
  const segs = best.segments || [];
  if (!segs.length) {
    const line = L.polyline(coords, { color: '#3b6dff', weight: 7, opacity: 0.95, smoothFactor: 1.2 }).addTo(map);
    routeLayers.push(line);
    return line;
  }
  // Distribute geometry vertices across logical segments (uniform binning).
  const n = coords.length, k = segs.length;
  for (let i = 0; i < k; i++) {
    const start = Math.floor((i * (n - 1)) / k);
    const end   = Math.floor(((i + 1) * (n - 1)) / k);
    const slice = coords.slice(start, end + 1);
    if (slice.length < 2) continue;
    const line = L.polyline(slice, {
      color: levelColor(segs[i].level), weight: 8, opacity: 0.95, lineCap: 'round', lineJoin: 'round'
    }).addTo(map);
    line.bindPopup(`<b>${segs[i].from} → ${segs[i].to}</b><br>${segs[i].level} traffic`);
    routeLayers.push(line);
  }
}

function drawRoutes(allRoutes, best) {
  clearMap();
  if (!best || !best.coordinates || !best.coordinates.length) {
    document.getElementById('error').textContent = 'No road data found.';
    return;
  }
  const bestKey = best.path.join('>');
  let altIdx = 0;
  allRoutes.forEach(r => {
    if (r.path.join('>') === bestKey) return;
    if (!r.coordinates || !r.coordinates.length) return;
    altIdx++;
    const offset = offsetCoords(r.coordinates, altIdx);
    const line = L.polyline(offset, { color: '#94a3b8', weight: 4, opacity: 0.55, dashArray: '6,8', smoothFactor: 1.5 }).addTo(map);
    line.bindPopup(`Route ${altIdx}<br>${r.travel_time} min · ${r.distance} km`);
    line.routeRef = r;
    routeLayers.push(line);
  });
  drawBestSegmented(best);
  const start = best.coordinates[0], end = best.coordinates[best.coordinates.length - 1];
  markerLayers.push(L.marker(start).addTo(map).bindPopup(`<b>Source:</b> ${best.path[0]}`));
  markerLayers.push(L.marker(end).addTo(map).bindPopup(`<b>Destination:</b> ${best.path.at(-1)}`));
  const bounds = L.latLngBounds(best.coordinates);
  map.fitBounds(bounds.pad(0.18));
}

/* ---------- Result cards ---------- */
function renderBest(best, meta, stats) {
  document.getElementById('bestRouteCard').classList.remove('hidden');
  document.getElementById('bestRouteInfo').innerHTML = `
    <div class="font-medium text-slate-700 dark:text-slate-200 break-words">${best.path.join(' → ')}</div>
    <div class="grid grid-cols-2 gap-2 mt-3">
      <!-- <div class="p-2 rounded-lg bg-slate-100 dark:bg-slate-800"><div class="text-xs text-slate-500">ETA (ML)</div><div class="font-bold">${best.travel_time} min</div></div> -->
      <div class="p-2 rounded-lg bg-slate-100 dark:bg-slate-800"><div class="text-xs text-slate-500">Speed</div><div class="font-bold">${best.osrm_duration ?? '—'} min</div></div>
      <div class="p-2 rounded-lg bg-slate-100 dark:bg-slate-800"><div class="text-xs text-slate-500">Distance</div><div class="font-bold">${best.distance} km</div></div>
      <div class="p-2 rounded-lg bg-slate-100 dark:bg-slate-800"><div class="text-xs text-slate-500">Traffic</div><span class="badge ${levelClass(best.traffic_level)}">${best.traffic_level}</span></div>
      <div class="p-2 rounded-lg bg-slate-100 dark:bg-slate-800"><div class="text-xs text-slate-500">Hops</div><div class="font-bold">${best.hops}</div></div>
      <!-- <div class="p-2 rounded-lg bg-slate-100 dark:bg-slate-800"><div class="text-xs text-slate-500">Hour</div><div class="font-bold">${meta.hour ?? 'Now'}</div></div> -->
    </div>`;

  if (stats) {
    document.getElementById('statsCard').classList.remove('hidden');
    document.getElementById('statsGrid').innerHTML = `
      <div class="stat"><span>Avg speed</span><b>${stats.avg_speed_kmh} km/h</b></div>
      <!-- <div class="stat"><span>Efficiency</span><b>${stats.efficiency_kmh} km/h</b></div> -->
      <div class="stat"><span>Efficiency</span> <b> ${ best.osrm_duration && best.osrm_duration > 0 ? (best.distance / (best.osrm_duration / 60)).toFixed(1) : '0.0' } km/h</b></div>
      <div class="stat"><span>Congestion</span><b>${stats.congestion_score_pct}%</b></div>
      <div class="stat"><span>Confidence</span><b>${stats.confidence_pct}%</b></div>
      <div class="stat"><span>Signal delay</span><b>${stats.total_signal_delay_sec}s</b></div>
      <div class="stat"><span>Straight-line</span><b>${stats.straight_km} km</b></div>
      <div class="stat col-span-2"><span>Bottleneck</span><b class="truncate">${stats.bottleneck}</b></div>
    `;
  }
}

/* ---------- Charts (built dynamically below the map) ---------- */
function destroyChart(name) { if (charts[name]) { charts[name].destroy(); charts[name] = null; } }

function renderCompareChart(best, all) {
  const list = [best, ...all.filter(r => r.path.join('>') !== best.path.join('>'))].slice(0, 6);
  const labels = list.map((r, i) => i === 0 ? 'Best' : `Alt ${i}`);
  const data = list.map(r => r.travel_time);
  const colors = list.map((_, i) => i === 0 ? '#3b6dff' : '#94a3b8');
  destroyChart('compare');
  const ctx = document.getElementById('compareChart');
  if (!ctx) return;
  document.getElementById('compareCard').classList.remove('hidden');
  charts.compare = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Travel time (min)', data, backgroundColor: colors, borderRadius: 6 }] },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        afterLabel: ctx => `${list[ctx.dataIndex].distance} km · ${list[ctx.dataIndex].traffic_level}`
      } } },
      scales: {
        x: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.15)' } },
        y: { ticks: { color: '#cbd5e1' }, grid: { display: false } },
      },
      onClick: (_, els) => {
        if (!els.length) return;
        const r = list[els[0].index];
        flashRoute(r);
      },
    },
  });
}

function flashRoute(r) {
  if (!r || !r.coordinates) return;
  const l = L.polyline(r.coordinates, { color: '#fbbf24', weight: 9, opacity: 0.9 }).addTo(map);
  setTimeout(() => map.removeLayer(l), 1500);
  map.fitBounds(L.latLngBounds(r.coordinates).pad(0.15));
}

async function renderForecast(src, dst, weather) {
  destroyChart('forecast');
  try {
    const r = await fetch('/api/predict', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({ source: src, destination: dst, weather }),
    });
    const d = await r.json();
    if (!r.ok || !d.forecast) return;
    document.getElementById('forecastCard').classList.remove('hidden');
    const labels = d.forecast.map(f => `${f.hour}:00`);
    const data = d.forecast.map(f => f.predicted);
    const nowH = new Date().getHours();
    const ptColors = d.forecast.map(f => f.hour === nowH ? '#fbbf24' : '#3b6dff');
    const ptRadius = d.forecast.map(f => f.hour === nowH ? 6 : 3);
    const ctx = document.getElementById('forecastChart');
    charts.forecast = new Chart(ctx, {
      type: 'line',
      data: { labels, datasets: [{
        label: 'Predicted travel time (min)', data,
        borderColor: '#3b6dff', backgroundColor: 'rgba(59,109,255,0.15)',
        tension: 0.35, fill: true, pointBackgroundColor: ptColors, pointRadius: ptRadius,
      }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { labels: { color: '#cbd5e1' } } },
        scales: {
          x: { ticks: { color: '#94a3b8', maxRotation: 0, autoSkip: true }, grid: { color: 'rgba(148,163,184,0.1)' } },
          y: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(148,163,184,0.15)' } },
        },
      },
    });
  } catch (e) { console.warn('forecast error', e); }
}

async function renderBestDeparture(src, dst, weather) {
  try {
    const r = await fetch('/api/best_departure', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify({ source: src, destination: dst, weather }),
    });
    const d = await r.json();
    if (!r.ok) return;
    document.getElementById('departureCard').classList.remove('hidden');
    const savings = d.savings_minutes;
    const msg = savings != null && savings > 0.5
      ? `Leaving at <b>${d.best_hour}:00</b> saves about <b>${savings.toFixed(1)} min</b> versus now.`
      : `You're already in a good window — depart now (best ETA ${d.best_minutes} min).`;
    document.getElementById('departureBody').innerHTML = `
      <div class="text-2xl font-bold">${d.best_hour}:00</div>
      <div class="text-xs text-slate-400 mt-1">Best departure within next 24h</div>
      <p class="text-sm mt-3">${msg}</p>
      <div class="text-xs text-slate-400 mt-2">Now: ${d.current_hour}:00 → ${d.current_minutes ?? '—'} min</div>`;
  } catch (e) { console.warn('best_departure error', e); }
}

/* ---------- Submit ---------- */
document.getElementById('findBtn').addEventListener('click', async () => {
  const source = document.getElementById('source').value.trim();
  const destination = document.getElementById('destination').value.trim();
  const errBox = document.getElementById('error');
  const btn = document.getElementById('findBtn');
  const loading = document.getElementById('loading');
  errBox.textContent = '';

  if (!source || !destination) { errBox.textContent = 'Enter both source and destination.'; return; }
  if (source === destination) { errBox.textContent = 'Source and destination cannot be the same.'; return; }

  const weather = 'Clear';
  btn.disabled = true; loading.classList.remove('hidden');
  ['compareCard', 'forecastCard', 'departureCard', 'statsCard', 'bestRouteCard'].forEach(id => {
    const el = document.getElementById(id); if (el) el.classList.add('hidden');
  });

  try {
    const res = await fetch('/get_routes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ source, destination, hour: null, weather }),
    });
    const data = await res.json();
    if (!res.ok) { errBox.textContent = data.error || 'Something went wrong.'; return; }

    bestRouteData = data.best_route;
    // altRouteData  = data.all_routes || [];
    renderBest(data.best_route, data, data.stats);
    drawRoutes([], data.best_route);
    // drawRoutes(data.all_routes || [], data.best_route);
    // renderCompareChart(data.best_route, data.all_routes || []);
    // renderForecast(source, destination, weather);
    // renderBestDeparture(source, destination, weather);
  } catch (err) {
    errBox.textContent = 'Network error: ' + err.message;
  } finally {
    btn.disabled = false; loading.classList.add('hidden');
  }
});
