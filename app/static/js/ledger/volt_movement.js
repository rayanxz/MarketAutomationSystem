(() => {
  "use strict";

  const qs  = (s, r=document) => r.querySelector(s);
  const qsa = (s, r=document) => Array.from(r.querySelectorAll(s));

  const rows = qs("#rows");
  const sentinel = qs("#sentinel");
  const emptyState = qs("#emptyState");

  // Filters
  const fEffect = qs("#fEffect");
  const fType   = qs("#fType");
  const fFrom   = qs("#fFrom");
  const fTo     = qs("#fTo");
  const fMin    = qs("#fMin");
  const fMax    = qs("#fMax");
  const fPartyType = qs("#fPartyType");
  const fPartyName = qs("#fPartyName");
  const partyAC = qs("#partyAC");
  const btnSearch = qs("#btnSearch");
  const btnReset  = qs("#btnReset");

  const safeBalance = qs("#safeBalance");

  const API = {
    MOVES: "/manager/volt/api/movements/",
    SUMMARY: "/manager/volt/api/summary/",
    PARTY_AC: "/manager/volt/api/party-ac/",
  };

  let cursor = null;
  let loading = false;
  let reachedEnd = false;
  let lastQueryKey = "";

  function queryKey(){
    return [
      fEffect.value, fType.value, fFrom.value, fTo.value,
      fMin.value, fMax.value, fPartyType.value, fPartyName.value
    ].join("|");
  }

  async function fetchSummary(){
    try{
      const r = await fetch(API.SUMMARY);
      const j = await r.json();
      safeBalance.textContent = j.balance_disp || "—";
    }catch(_){}
  }

  function buildURL(){
    const p = new URLSearchParams();
    if (fEffect.value !== "both") p.set("effect", fEffect.value);
    if (fType.value !== "all") p.set("mtype", fType.value);
    if (fFrom.value) p.set("df", new Date(fFrom.value).toISOString());
    if (fTo.value)   p.set("dt", new Date(fTo.value).toISOString());
    if (fMin.value) p.set("min_amt", fMin.value);
    if (fMax.value) p.set("max_amt", fMax.value);
    if (fPartyType.value) p.set("party_type", fPartyType.value);
    if (fPartyName.value) p.set("party_name", fPartyName.value);
    if (cursor) p.set("cursor", cursor);
    p.set("limit", "50");
    return API.MOVES + "?" + p.toString();
  }

  function effCell(eff){
    if (eff === "UP") return `<span class="eff-up">↑ زيادة</span>`;
    return `<span class="eff-down">↓ نقصان</span>`;
  }

  function rowHTML(x){
  return `<tr>
    <td>${x.date_disp}</td>
    <td>${effCell(x.effect)}</td>
    <td>${x.amount_disp}</td>
    <td>${x.action || "—"}</td>
    <td>${x.party_type_disp || x.party_type || "—"}</td>
    <td>${x.party_name || "—"}</td>
  </tr>`;
  }


  async function loadMore(){
    if (loading || reachedEnd) return;
    loading = true;
    try{
      const url = buildURL();
      const r = await fetch(url);
      const j = await r.json();
      if (!j.rows || j.rows.length === 0){
        if (!cursor) emptyState.hidden = false;
        reachedEnd = true;
        observer.unobserve(sentinel);
        return;
      }
      emptyState.hidden = true;
      rows.insertAdjacentHTML("beforeend", j.rows.map(rowHTML).join(""));
      cursor = j.next_cursor || null;
      if (!cursor) {
        reachedEnd = true;
        observer.unobserve(sentinel);
      }
    }finally{
      loading = false;
    }
  }

  function resetList(){
    rows.innerHTML = "";
    cursor = null;
    reachedEnd = false;
    emptyState.hidden = true;
    observer.observe(sentinel);
  }

  btnSearch.addEventListener("click", () => {
    const k = queryKey();
    if (k !== lastQueryKey){
      lastQueryKey = k;
      resetList();
      loadMore();
      fetchSummary();
    }
  });

  btnReset.addEventListener("click", () => {
    fEffect.value = "both";
    fType.value   = "all";
    fFrom.value = fTo.value = fMin.value = fMax.value = "";
    fPartyType.value = "";
    fPartyName.value = "";
    lastQueryKey = queryKey();
    resetList();
    loadMore();
    fetchSummary();
  });

  // AC: providers
  let acTimer = null;
  fPartyName.addEventListener("input", () => {
    clearTimeout(acTimer);
    const q = fPartyName.value.trim();
    if (!q){ partyAC.hidden = true; partyAC.innerHTML = ""; return; }
    acTimer = setTimeout(async () => {
      const r = await fetch(API.PARTY_AC + "?q=" + encodeURIComponent(q));
      const j = await r.json();
      partyAC.innerHTML = (j.items || []).map(i => `<li data-type="${i.type}" data-name="${i.name}">${i.name}</li>`).join("");
      partyAC.hidden = !partyAC.innerHTML;
    }, 200);
  });
  partyAC.addEventListener("click", (e) => {
    const li = e.target.closest("li");
    if (!li) return;
    fPartyType.value = li.dataset.type || "";
    fPartyName.value = li.dataset.name || "";
    partyAC.hidden = true;
    btnSearch.click();
  });
  document.addEventListener("click", (e) => {
    if (!partyAC.contains(e.target) && e.target !== fPartyName) partyAC.hidden = true;
  });

  // Infinite scroll
  const observer = new IntersectionObserver((entries) => {
    if (entries.some(e => e.isIntersecting)) loadMore();
  }, { rootMargin: "600px" });

  // Initial load
  lastQueryKey = queryKey();
  observer.observe(sentinel);
  loadMore();
  fetchSummary();
})();
