// contextmenu.js — global right-click control for the whole site
(() => {
  const menu = document.getElementById('ctx');
  if (!menu) return;

  // Block native context menu everywhere, except editable fields or .allow-context
  document.addEventListener('contextmenu', (e) => {
    const t = e.target;
    const allow = t.closest('input, textarea, select, [contenteditable="true"], .allow-context');
    if (e.shiftKey || allow) return;   // hold Shift to get the browser menu (dev escape hatch)
    e.preventDefault();

    // Build payload from nearest attributes (optional)
    const host = t.closest('[data-ctx-id], [data-context]');
    const payload = host ? {
      id: host.getAttribute('data-ctx-id') || null,
      name: host.getAttribute('data-ctx-name') || '',
      context: host.getAttribute('data-context') || 'default'
    } : { context: 'default' };

    // Let pages customize items before showing
    const ev = new CustomEvent('ctx:beforeopen', { detail: { payload, target: t, menu } });
    document.dispatchEvent(ev);

    // Position & show
    const x = Math.min(e.clientX, window.innerWidth  - 12 - 180);
    const y = Math.min(e.clientY, window.innerHeight - 12 - 10);
    menu.style.setProperty('--x', x + 'px');
    menu.style.setProperty('--y', y + 'px');
    menu.dataset.payload = JSON.stringify(payload);
    menu.classList.add('show');
    menu.setAttribute('aria-hidden', 'false');
  }, { capture: true });

  function closeMenu(){
    if (!menu.classList.contains('show')) return;
    menu.classList.remove('show');
    menu.setAttribute('aria-hidden', 'true');
    menu.dataset.payload = '{}';
  }
  document.addEventListener('click', (e)=>{ if (!menu.contains(e.target)) closeMenu(); });
  document.addEventListener('keydown', (e)=>{ if (e.key === 'Escape') closeMenu(); });
  window.addEventListener('resize', closeMenu);
  window.addEventListener('scroll', closeMenu, true);

  // Handle clicks on menu items
  menu.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const act = btn.dataset.act;
    const payload = JSON.parse(menu.dataset.payload || '{}');
    closeMenu();
    document.dispatchEvent(new CustomEvent('ctx:action', { detail: { act, payload } }));
  });

  // Default actions (optional)
  document.addEventListener('ctx:action', ({ detail:{ act } }) => {
    if (act === 'refresh') location.reload();
  });
})();
