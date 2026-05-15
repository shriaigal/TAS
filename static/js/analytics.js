(async function () {
  const isDark = document.documentElement.classList.contains('dark');
  const grid = isDark ? 'rgba(148,163,184,.18)' : 'rgba(148,163,184,.3)';
  const tick = isDark ? '#cbd5e1' : '#334155';
  Chart.defaults.color = tick;
  Chart.defaults.borderColor = grid;

  let data;
  try {
    const r = await fetch('/api/analytics');
    data = await r.json();
    if (data.error) throw new Error(data.error);
  } catch (e) { console.error(e); return; }

  const k = data.kpis;
  document.getElementById('kpiLocs').textContent = k.locations;
  document.getElementById('kpiEdges').textContent = k.edges;
  document.getElementById('kpiSpeed').textContent = k.avg_speed;
  document.getElementById('kpiCong').textContent = k.avg_congestion + '%';
  document.getElementById('kpiRecords').textContent = k.records.toLocaleString();
  document.getElementById('kpiModel').textContent = `${k.model} · R²=${(k.metrics.R2 || 0).toFixed(3)}`;

  const hours = Array.from({ length: 24 }, (_, i) => i + ':00');

  new Chart(document.getElementById('chartHourly'), {
    type: 'line',
    data: { labels: hours, datasets: [{ label: 'Avg Vehicles', data: data.hourly_vehicles, borderColor: '#3b6dff', backgroundColor: 'rgba(59,109,255,.18)', fill: true, tension: .35, pointRadius: 2 }] },
    options: { responsive: true, plugins: { legend: { display: false } } }
  });
  new Chart(document.getElementById('chartSpeed'), {
    type: 'bar',
    data: { labels: hours, datasets: [{ label: 'Speed', data: data.hourly_speed, backgroundColor: '#10b981' }] },
    options: { responsive: true, plugins: { legend: { display: false } } }
  });
  // const sev = data.severity;
  // new Chart(document.getElementById('chartSeverity'), {
  //   type: 'doughnut',
  //   data: { labels: Object.keys(sev), datasets: [{ data: Object.values(sev), backgroundColor: ['#22c55e', '#eab308', '#f97316', '#ef4444'] }] },
  //   options: { responsive: true }
  // });
  // new Chart(document.getElementById('chartWeather'), {
  //   type: 'bar',
  //   data: {
  //     labels: data.weather.map(w => w.weather),
  //     datasets: [{ label: 'Avg Vehicles', data: data.weather.map(w => Math.round(w.vehicles)), backgroundColor: ['#3b6dff', '#94a3b8', '#a78bfa', '#06b6d4'] }]
  //   },
  //   options: { responsive: true, plugins: { legend: { display: false } } }
  // });

  // const cTbl = document.getElementById('tblCongested');
  // data.top_congested.forEach(r => {
  //   cTbl.insertAdjacentHTML('beforeend',
  //     `<tr class="border-b border-slate-100 dark:border-slate-800"><td class="py-2">${r.source_location} → ${r.destination_location}</td><td>${(r.density * 100).toFixed(0)}%</td><td>${Math.round(r.vehicles)}</td><td>${r.speed.toFixed(1)}</td></tr>`);
  // });
  const aTbl = document.getElementById('tblAreas');
  data.top_areas.forEach(r => {
    aTbl.insertAdjacentHTML('beforeend',
      `<tr class="border-b border-slate-100 dark:border-slate-800"><td class="py-2">${r.source_location}</td><td>${Math.round(r.avg_vehicles)}</td><td>${r.avg_speed.toFixed(1)}</td></tr>`);
  });

  const map = L.map('map').setView([12.9716, 77.5946], 11);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(map);
  if (window.L && L.heatLayer && data.heatmap.length) {
    L.heatLayer(data.heatmap, { radius: 18, blur: 22, maxZoom: 14 }).addTo(map);
  }
})();
