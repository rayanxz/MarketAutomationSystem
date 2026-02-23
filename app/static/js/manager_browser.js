// static/js/manager_browser.js
(() => {
  const list   = document.getElementById('browserList');
  const pager  = document.getElementById('pager');
  const pgJump = document.getElementById('pgJump');
  const pgBtns = pager ? pager.querySelectorAll('.pg-btn[data-go]') : [];
  const crumb  = document.getElementById('crumb');
  const levelL = document.getElementById('levelLabel');
  const back   = document.getElementById('btnBack');
  const showDisabledToggle = document.getElementById('showDisabledProducts');
  const SHOW_DISABLED_KEY = 'mgr.browser.show_disabled';

  if (!list) return;

  const msgs = document.querySelectorAll('.messages .message.success');
  if (msgs.length) {
    setTimeout(() => msgs.forEach(m => m.remove()), 4000);
  }

  const URLS = {
    collections: (page)     => `/manager/products/api/browser/collections/?page=${page}`,
    sets:        (cid,page) => `/manager/products/api/browser/sets/?cid=${cid}&page=${page}`,
    products:    (sid,page,showDisabled) => `/manager/products/api/browser/products/?sid=${sid}&page=${page}&show_disabled=${showDisabled ? '1' : '0'}`,
  };

  const ICON = { collection: '📁', set: '👥', product: '📦' };

  let level = 'collections';
  let page  = 1;
  let totalPages = 1;

  let col = null;
  let set = null;

  let hlCol  = null;
  let hlSet  = null;
  let hlProd = null;

  const el = (t,c,txt)=>{ const e=document.createElement(t); if(c) e.className=c; if(txt!=null) e.textContent=txt; return e; };

  function readStoredShowDisabled() {
    return localStorage.getItem(SHOW_DISABLED_KEY) === '1';
  }

  function storeShowDisabled(v) {
    localStorage.setItem(SHOW_DISABLED_KEY, v ? '1' : '0');
  }

  function syncShowDisabledInUrl(v) {
    const u = new URL(window.location.href);
    u.searchParams.set('show_disabled', v ? '1' : '0');
    history.replaceState(null, '', u.toString());
  }

  function publishContext(){
    document.dispatchEvent(new CustomEvent('mgr:context', { detail: { level, col, set } }));
  }

  function setCrumb(){
    if (!crumb) return;
    crumb.textContent =
      level === 'collections' ? '/' :
      level === 'sets'        ? `${col?.name || ''}/` :
                                `${col?.name || ''}/${set?.name || ''}/`;
  }

  function setLabel(){
    if (!levelL) return;
    levelL.textContent = (level === 'collections')
      ? 'الزُمَر'
      : (level === 'sets' ? 'المجموعات الأب' : 'المنتجات');
  }

  function setBack(){
    if (!back) return;
    const isTop = (level === 'collections');
    back.disabled = isTop;
    back.style.visibility = isTop ? 'hidden' : 'visible';
  }

  let didHighlight = false;

  function clearSearchState(){
    const u = new URL(window.location.href);
    u.searchParams.delete('hl_col');
    u.searchParams.delete('hl_set');
    u.searchParams.delete('hl_prod');
    u.searchParams.delete('page');
    u.searchParams.delete('cname');
    u.searchParams.delete('ccode');
    u.searchParams.delete('sname');
    u.searchParams.delete('scode');
    history.replaceState(null, '', u.toString());
    hlCol = null;
    hlSet = null;
    hlProd = null;
  }

  function highlightRowIfNeeded(row, it){
    if (level === 'collections' && hlCol && it.id === hlCol) {
      row.classList.add('active');
      setTimeout(()=>row.scrollIntoView({behavior:'smooth', block:'center'}), 0);
      didHighlight = true;
    }
    if (level === 'sets' && hlSet && it.id === hlSet) {
      row.classList.add('active');
      setTimeout(()=>row.scrollIntoView({behavior:'smooth', block:'center'}), 0);
      didHighlight = true;
    }
    if (level === 'products' && hlProd && it.id === hlProd) {
      row.classList.add('active');
      setTimeout(()=>row.scrollIntoView({behavior:'smooth', block:'center'}), 0);
      didHighlight = true;
    }
  }

  function renderItems(items){
    list.innerHTML = '';
    if (!items.length){
      list.innerHTML = `<div class="item muted">لا توجد عناصر.</div>`;
      return;
    }

    didHighlight = false;
    items.forEach(it=>{
      const row  = el('div','item');
      row.dataset.id = it.id;

      // Context payload only for collections
      if (level === 'collections') {
        row.setAttribute('data-context', 'collection');
        row.setAttribute('data-ctx-id', String(it.id));
        row.setAttribute('data-ctx-name', it.name || '');
      } else {
        row.removeAttribute('data-context');
        row.removeAttribute('data-ctx-id');
        row.removeAttribute('data-ctx-name');
      }

      const iconName = (level==='collections') ? ICON.collection
                     : (level==='sets')        ? ICON.set
                                               : ICON.product;

      const ic   = el('span','icon', iconName);
      const name = el('div','name', it.name);
      const code = el('div','code', level==='products' ? `${it.code}` : (it.code || ''));
      if (level === 'products' && it.is_active === false) {
        row.classList.add('is-disabled');
      }
      row.append(ic,name,code);
      row.style.display='flex';
      row.style.alignItems='center';
      row.style.gap='10px';

      // Left-click
      row.addEventListener('click', () => {
        if (level === 'collections') {
          hlCol = it.id;
          col = { id: it.id, name: it.name, code: it.code, can_be_hard_deleted: !!it.can_be_hard_deleted };
          level = 'sets'; page = 1; load();
        } else if (level === 'sets') {
          hlSet = it.id;
          set = { id: it.id, name: it.name, code: it.code, can_be_hard_deleted: !!it.can_be_hard_deleted };
          level = 'products'; page = 1; load();
        } else {
          window.location.href = `/manager/products/${it.id}/edit/`;
        }
      });

      highlightRowIfNeeded(row, it);
      list.appendChild(row);
    });
    if (didHighlight) {
      clearSearchState();
      autoJumped = true;
    }
  }

  async function fetchPage(pageOverride){
    const pg = pageOverride || page;
    const showDisabled = !!showDisabledToggle?.checked;
    let url;
    if (level === 'collections') url = URLS.collections(pg);
    else if (level === 'sets')   url = URLS.sets(col?.id, pg);
    else                         url = URLS.products(set?.id, pg, showDisabled);

    const res = await fetch(url, { headers:{'Accept':'application/json'} });
    if (!res.ok) throw new Error('fetch failed');
    const data = await res.json();
    if (!data.ok) throw new Error('bad response');
    if (level === 'sets' && col) {
      col.can_be_hard_deleted = !!data.collection_can_be_hard_deleted;
    }
    if (level === 'products' && set) {
      set.can_be_hard_deleted = !!data.set_can_be_hard_deleted;
    }

    if (!pageOverride) totalPages = data.total_pages || 1;
    return data.items || [];
  }

  function bindPager(){
    if (!pager) return;
    const btnPrev = pager.querySelector('.pg-btn[data-go="prev"]');
    const btnNext = pager.querySelector('.pg-btn[data-go="next"]');
    const btnFirst = pager.querySelector('.pg-btn[data-go="first"]');
    const btnLast = pager.querySelector('.pg-btn[data-go="last"]');
    const numBtns = Array.from(pager.querySelectorAll('.pg-btn[data-go="1"], .pg-btn[data-go="2"], .pg-btn[data-go="3"]'));

    const last = totalPages;
    let start = Math.max(1, page - 1);
    let end = Math.min(last, start + 2);
    start = Math.max(1, end - 2);

    numBtns.forEach((b, i) => {
      const n = start + i;
      b.hidden = (n > last);
      b.dataset.go = String(n);
      b.textContent = `<${n}>`;
      b.classList.toggle('active', n === page);
    });

    if (btnFirst){
      const dis = (page <= 1);
      btnFirst.hidden = false;
      btnFirst.disabled = dis;
      btnFirst.classList.toggle('btn-disabled', dis);
      btnFirst.onclick = () => { if (!dis){ page = 1; load(); } };
    }
    if (btnLast){
      const dis = (page >= last);
      btnLast.hidden = false;
      btnLast.disabled = dis;
      btnLast.classList.toggle('btn-disabled', dis);
      btnLast.onclick = () => { if (!dis){ page = last; load(); } };
    }

    if (btnPrev){
      const dis = (page <= 1);
      btnPrev.hidden = false;
      btnPrev.disabled = dis;
      btnPrev.classList.toggle('btn-disabled', dis);
      btnPrev.onclick = () => { if (!dis){ page -= 1; load(); } };
    }
    if (btnNext){
      const dis = (page >= last);
      btnNext.hidden = false;
      btnNext.disabled = dis;
      btnNext.classList.toggle('btn-disabled', dis);
      btnNext.onclick = () => { if (!dis){ page += 1; load(); } };
    }

    numBtns.forEach((b)=>{
      b.onclick = () => {
        const go = parseInt(b.dataset.go || '1', 10);
        page = Math.max(1, Math.min(last, go));
        load();
      };
    });

    if (pgJump){
      pgJump.value = String(page);
      pgJump.onchange = () => {
        const v = parseInt(pgJump.value || '1', 10);
        page = Math.max(1, Math.min(last, v));
        load();
      };
    }
  }

  let autoJumped = false;

  async function maybeAutoJump(items){
    if (autoJumped) return;
    const targetId =
      (level === 'collections') ? hlCol :
      (level === 'sets')        ? hlSet :
      (level === 'products')    ? hlProd : null;
    if (!targetId || totalPages <= 1) return;
    if (items.some(it => it.id === targetId)) return;
    autoJumped = true;
    for (let p = 1; p <= totalPages; p++) {
      if (p === page) continue;
      const it = await fetchPage(p);
      if (it.some(x => x.id === targetId)) {
        page = p;
        autoJumped = false;
        const u = new URL(window.location.href);
        u.searchParams.delete('hl_col');
        u.searchParams.delete('hl_set');
        u.searchParams.delete('hl_prod');
        u.searchParams.delete('page');
        u.searchParams.delete('cname');
        u.searchParams.delete('ccode');
        u.searchParams.delete('sname');
        u.searchParams.delete('scode');
        history.replaceState(null, '', u.toString());
        await load();
        return;
      }
    }
  }

  async function load(){
    setCrumb();
    setLabel();
    setBack();

    try{
      const items = await fetchPage();
      renderItems(items);
      bindPager();
      if (pgJump) pgJump.value = page;
      await maybeAutoJump(items);
    }catch(e){
      list.innerHTML = `<div class="item muted">تعذّر تحميل العناصر.</div>`;
    }

    publishContext(); // << notify actions bar
  }

  if (back){
    back.addEventListener('click', () => {
      if (level === 'products') {
        level = 'sets'; page = 1; hlProd = null; load(); return;
      }
      if (level === 'sets') {
        level = 'collections'; page = 1; set = null; col = null; hlSet = null; load(); return;
      }
    });
  }

  if (showDisabledToggle) {
    showDisabledToggle.addEventListener('change', () => {
      const showDisabled = !!showDisabledToggle.checked;
      storeShowDisabled(showDisabled);
      syncShowDisabledInUrl(showDisabled);
      if (level === 'products') {
        page = 1;
        load();
      }
    });
  }

  (function initFromQuery(){
    const params = new URLSearchParams(location.search);

    const cid = params.get('cid');
    const sid = params.get('sid');
    const showDisabledRaw = params.get('show_disabled');
    const qp = parseInt(params.get('page') || '0', 10);
    if (qp > 0) page = qp;

    if (showDisabledToggle) {
      const fromQuery = (showDisabledRaw != null)
        ? ['1', 'true', 'yes'].includes(String(showDisabledRaw).toLowerCase())
        : null;
      const showDisabled = (fromQuery == null) ? readStoredShowDisabled() : fromQuery;
      showDisabledToggle.checked = !!showDisabled;
      storeShowDisabled(!!showDisabled);
      syncShowDisabledInUrl(!!showDisabled);
    }

    hlCol  = params.get('hl_col')  ? parseInt(params.get('hl_col'),10)  : null;
    hlSet  = params.get('hl_set')  ? parseInt(params.get('hl_set'),10)  : null;
    hlProd = params.get('hl_prod') ? parseInt(params.get('hl_prod'),10) : null;

    if (!cid && (hlCol || !hlSet)) {
      level = 'collections'; page = 1; load(); return;
    }

    if (cid && !sid) {
      level = 'sets';
      col = {
        id: parseInt(cid,10),
        name: params.get('cname') || null,
        code: params.get('ccode') || null,
        can_be_hard_deleted: true
      };
      page = 1; load(); return;
    }

    if (cid && sid) {
      level = 'products';
      col = {
        id: parseInt(cid,10),
        name: params.get('cname') || null,
        code: params.get('ccode') || null,
        can_be_hard_deleted: true
      };
      set = {
        id: parseInt(sid,10),
        name: params.get('sname') || params.get('scode') || null,
        code: params.get('scode') || null,
        can_be_hard_deleted: true
      };
      page = 1; load(); return;
    }

    load();
  })();
})();
