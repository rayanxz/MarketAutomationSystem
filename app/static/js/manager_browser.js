// static/js/manager_browser.js
(() => {
  const list   = document.getElementById('browserList');
  const pager  = document.getElementById('pager');
  const pgJump = document.getElementById('pgJump');
  const pgBtns = pager ? pager.querySelectorAll('.pg-btn[data-go]') : [];
  const crumb  = document.getElementById('crumb');
  const levelL = document.getElementById('levelLabel');
  const back   = document.getElementById('btnBack');

  if (!list) return;

  const URLS = {
    collections: (page)     => `/manager/products/api/browser/collections/?page=${page}`,
    sets:        (cid,page) => `/manager/products/api/browser/sets/?cid=${cid}&page=${page}`,
    products:    (sid,page) => `/manager/products/api/browser/products/?sid=${sid}&page=${page}`,
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

  function highlightRowIfNeeded(row, it){
    if (level === 'collections' && hlCol && it.id === hlCol) {
      row.classList.add('active');
      setTimeout(()=>row.scrollIntoView({behavior:'smooth', block:'center'}), 0);
    }
    if (level === 'sets' && hlSet && it.id === hlSet) {
      row.classList.add('active');
      setTimeout(()=>row.scrollIntoView({behavior:'smooth', block:'center'}), 0);
    }
    if (level === 'products' && hlProd && it.id === hlProd) {
      row.classList.add('active');
      setTimeout(()=>row.scrollIntoView({behavior:'smooth', block:'center'}), 0);
    }
  }

  function renderItems(items){
    list.innerHTML = '';
    if (!items.length){
      list.innerHTML = `<div class="item muted">لا توجد عناصر.</div>`;
      return;
    }

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
      const code = el('div','code', level==='products' ? `#${it.code}` : (it.code || ''));

      row.style.display='flex';
      row.style.alignItems='center';
      row.style.gap='10px';
      row.append(ic,name,code);

      // Left-click
      row.addEventListener('click', () => {
        if (level === 'collections') {
          hlCol = it.id;
          col = { id: it.id, name: it.name, code: it.code };
          level = 'sets'; page = 1; load();
        } else if (level === 'sets') {
          hlSet = it.id;
          set = { id: it.id, name: it.name, code: it.code };
          level = 'products'; page = 1; load();
        } else {
          window.location.href = `/manager/products/${it.id}/edit/`;
        }
      });

      highlightRowIfNeeded(row, it);
      list.appendChild(row);
    });
  }

  async function fetchPage(){
    let url;
    if (level === 'collections') url = URLS.collections(page);
    else if (level === 'sets')   url = URLS.sets(col?.id, page);
    else                         url = URLS.products(set?.id, page);

    const res = await fetch(url, { headers:{'Accept':'application/json'} });
    if (!res.ok) throw new Error('fetch failed');
    const data = await res.json();
    if (!data.ok) throw new Error('bad response');

    totalPages = data.total_pages || 1;
    return data.items || [];
  }

  function bindPager(){
  pgBtns.forEach((b)=>{
    const go = b.dataset.go; // "1", "2", ..., "last"
    b.onclick = () => {
      const last = totalPages;
      page = (go==='last') ? last : Math.max(1, Math.min(last, parseInt(go,10)));
      load();
    };
  });
  if (pgJump){
    pgJump.value = String(page);
    pgJump.onchange = () => {
      const last = totalPages;
      const v = parseInt(pgJump.value || '1', 10);
      page = Math.max(1, Math.min(last, v));
      load();
    };
  }
}

  async function load(){
    setCrumb();
    setLabel();
    setBack();
    bindPager();

    try{
      const items = await fetchPage();
      renderItems(items);
      if (pgJump) pgJump.value = page;
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

  (function initFromQuery(){
    const params = new URLSearchParams(location.search);

    const cid = params.get('cid');
    const sid = params.get('sid');

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
        code: params.get('ccode') || null
      };
      page = 1; load(); return;
    }

    if (cid && sid) {
      level = 'products';
      col = {
        id: parseInt(cid,10),
        name: params.get('cname') || null,
        code: params.get('ccode') || null
      };
      set = {
        id: parseInt(sid,10),
        name: params.get('sname') || params.get('scode') || null,
        code: params.get('scode') || null
      };
      page = 1; load(); return;
    }

    load();
  })();
})();