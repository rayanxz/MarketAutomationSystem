// static/js/manager_search.js — highlight-only for collection/set; open+highlight for product
(() => {
  const box = document.getElementById('searchBox');
  if (!box) return;

  const q   = document.getElementById('q');
  const sug = document.getElementById('suggestions');
  const err = document.getElementById('searchErr');
  if (!q || !sug || !err) return;

  const MODE_KEY = 'mgr.search.mode';
    let mode = localStorage.getItem(MODE_KEY) || 'barcode';

// reflect the stored mode in the radios on load
    box.querySelectorAll('input[name="mode"]').forEach(r=>{
    r.checked = (r.value === mode);
    r.addEventListener('change', ()=>{
        mode = r.value;
        localStorage.setItem(MODE_KEY, mode);
        sug.hidden = true; err.hidden = true;
        q.value = ''; q.focus();
    });
    });


  const apiSearch = '/manager/products/api/search/';
  const ICON = { collection: '📁', set: '👥', product: '📦' };

  // --- Deep-link helpers ---
  // Collection: stay on collections level and highlight
  // Collection: stay on collections level and just highlight that collection
function gotoCollection(item){
  const u = new URL('/manager/products/', window.location.origin);
  // DO NOT set cid here; we want to remain on collections level
  u.searchParams.set('hl_col', item.id);                  // highlight specific collection
  u.searchParams.set('cname', item.name || item.col_name || '');
  window.location.href = u.toString();
}

  // Set: go to sets level of its collection and highlight the set (don’t open products)
  function gotoSet(item){
    const u = new URL('/manager/products/', window.location.origin);
    u.searchParams.set('cid', item.col_id);
    u.searchParams.set('cname', item.col_name || item.col_code || '');
    u.searchParams.set('hl_set', item.id);
    // do NOT set sid
    window.location.href = u.toString();
  }
  // Product: open products level and highlight the product
  function gotoProduct(item){
    const u = new URL('/manager/products/', window.location.origin);
    u.searchParams.set('cid', item.col_id);
    if (item.set_id) u.searchParams.set('sid', item.set_id);
    u.searchParams.set('cname', item.col_name || item.col_code || '');
    if (item.set_name || item.set_code) u.searchParams.set('sname', item.set_name || item.set_code);
    u.searchParams.set('hl_prod', item.id);
    window.location.href = u.toString();
  }

  function onChoose(it){
    if (!it) return;
    if (it.type === 'collection') return gotoCollection(it);
    if (it.type === 'set')        return gotoSet(it);
    return gotoProduct(it);
  }

  function pathText(it){
  if (it.type === 'collection') return it.col_name || it.name || '';
  if (it.type === 'set')        return `${it.col_name || ''}`;
  // product
  const col = it.col_name || it.col_code || '';
  const set = it.set_name || it.set_code || '';
  return `${col} · ${set}`;
}


  let activeIndex = -1;
  let lastItems = [];

  function setActive(i){
    activeIndex = i;
    [...sug.querySelectorAll('li')].forEach((li,idx)=>{
      li.classList.toggle('active', idx===activeIndex);
    });
  }

  function renderSuggestions(items){
    lastItems = items || [];
    if(!lastItems.length){ sug.hidden = true; activeIndex = -1; return; }
    const ICONS = { collection:'📁', set:'👥', product:'📦' };
    sug.innerHTML = lastItems.map((p,i)=>`
      <li data-i="${i}">
        <span class="s-code">${ICONS[p.type] || ''}</span>
        <span>${p.name}</span>
        <span class="s-path">${pathText(p)}</span>
      </li>`).join('');
    sug.hidden = false;
    setActive(0);
    sug.querySelectorAll('li').forEach((li,i)=> li.addEventListener('click', ()=> onChoose(lastItems[i])));
  }

  // Typing (name / id / code modes)
  let debounce=null;
  q.addEventListener('input', ()=>{
    err.hidden = true;
    if (mode==='barcode'){ sug.hidden=true; return; }
    const val = q.value.trim();
    if (!val){ sug.hidden=true; return; }
    clearTimeout(debounce);
    debounce = setTimeout(async ()=>{
      const res = await fetch(`${apiSearch}?mode=${encodeURIComponent(mode)}&q=${encodeURIComponent(val)}`, { headers:{'Accept':'application/json'} });
      if (!res.ok) { sug.hidden=true; return; }
      const data = await res.json();
      if (!data.ok) { sug.hidden=true; return; }
      renderSuggestions(data.items || []);
    }, 150);
  });

  // Keyboard navigation: Tab / Shift+Tab / Arrows / Enter
  q.addEventListener('keydown', (e)=>{
    if (sug.hidden || !lastItems.length) return;
    if (e.key === 'Tab'){
      e.preventDefault();
      const next = (activeIndex + (e.shiftKey ? -1 : 1) + lastItems.length) % lastItems.length;
      setActive(next);
    } else if (e.key === 'ArrowDown'){
      e.preventDefault();
      setActive((activeIndex + 1) % lastItems.length);
    } else if (e.key === 'ArrowUp'){
      e.preventDefault();
      setActive((activeIndex - 1 + lastItems.length) % lastItems.length);
    } else if (e.key === 'Enter'){
      e.preventDefault();
      onChoose(lastItems[Math.max(0, activeIndex)]);
    }
  });

  // Barcode mode → highlight product (not edit)
  q.addEventListener('keypress', async (e)=>{
    if (mode!=='barcode' || e.key!=='Enter') return;
    const val = q.value.trim(); if(!val) return;
    const res = await fetch(`${apiSearch}?mode=barcode&q=${encodeURIComponent(val)}`, { headers:{'Accept':'application/json'} });
    if (!res.ok){ err.textContent='لم يتم العثور على نتيجة.'; err.hidden=false; return; }
    const data = await res.json();
    if (!data.ok || !(data.items && data.items.length)){
      err.textContent='لم يتم العثور على نتيجة.'; err.hidden=false; return;
    }
    gotoProduct({ ...(data.items[0] || {}), type: 'product' });
  });
})();
