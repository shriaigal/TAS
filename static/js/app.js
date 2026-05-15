(function () {
  // ── Theme init: default dark ──────────────────────────────
  var saved = null;
  try { saved = localStorage.getItem('tas-theme'); } catch (e) {}

  // If no saved preference, default to dark
  if (saved === 'light') {
    document.documentElement.classList.remove('dark');
  } else {
    document.documentElement.classList.add('dark');
    try { if (!saved) localStorage.setItem('tas-theme', 'dark'); } catch (e) {}
  }

  // ── Theme toggle button ───────────────────────────────────
  var btn = document.getElementById('themeToggle');
  if (btn) {
    btn.addEventListener('click', function () {
      var dark = document.documentElement.classList.toggle('dark');
      try { localStorage.setItem('tas-theme', dark ? 'dark' : 'light'); } catch (e) {}
    });
  }

  // ── Mobile menu ───────────────────────────────────────────
  var mb = document.getElementById('mobileMenuBtn');
  var mm = document.getElementById('mobileMenu');
  if (mb && mm) {
    mb.addEventListener('click', function () { mm.classList.toggle('hidden'); });
  }
})();