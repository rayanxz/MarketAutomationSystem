// app/static/js/notifications.js
(() => {
  "use strict";

  const root  = document.getElementById("notifRoot");
  const btn   = document.getElementById("notifBtn");
  const list  = document.getElementById("notifList");
  const badge = document.getElementById("notifBadge");
  if (!root || !btn || !list || !badge) return;

  let loading = false;

  // ---- Fetch & render ----
  async function loadNotifs() {
    if (loading) return;
    loading = true;
    try {
      const res = await fetch("/manager/notifications/list/", {
        headers: { "Accept": "application/json" },
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      const items = Array.isArray(data.items) ? data.items : [];
      renderList(items);
      updateBadge(items);
    } catch (err) {
      console.error("Notifications load failed:", err);
      list.innerHTML = `<div class="notif-empty">فشل تحميل الإشعارات</div>`;
      // keep badge as-is on failure
    } finally {
      loading = false;
    }
  }

  function renderList(items) {
    if (!items.length) {
      list.innerHTML = `<div class="notif-empty">لا توجد إشعارات</div>`;
      return;
    }
    const frag = document.createDocumentFragment();
    for (const n of items) {
      const div = document.createElement("div");
      div.className = "notif-item" + (n.is_read ? "" : " unread");
      div.textContent = `${n.title} (${n.created})`;
      div.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        div.classList.remove("unread");
        await markRead(n.id);
        // after marking read, refresh count (cheap re-fetch)
        loadNotifs();
      });
      frag.appendChild(div);
    }
    list.innerHTML = "";
    list.appendChild(frag);
  }

  function updateBadge(items) {
    const unread = items.reduce((c, n) => c + (n.is_read ? 0 : 1), 0);
    if (unread > 0) {
      badge.textContent = unread > 99 ? "99+" : String(unread);
      badge.hidden = false;
    } else {
      badge.hidden = true;
      badge.textContent = "0";
    }
  }

  async function markRead(id) {
    try {
      await fetch(`/manager/notifications/${id}/read/`, {
        method: "POST",
        headers: {
          "X-CSRFToken": getCsrf(),
          "Accept": "application/json",
        },
        credentials: "same-origin",
      });
    } catch (e) {
      console.warn("mark read failed:", e);
    }
  }

  // ---- Toggle panel ----
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    const willShow = list.hidden;
    list.hidden = !willShow;
    btn.setAttribute("aria-expanded", String(willShow));
    if (willShow) await loadNotifs();
  });

  // Close on outside click
  document.addEventListener("click", (e) => {
    if (!list.hidden && !root.contains(e.target)) {
      list.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    }
  });

  // Initial load for badge (don’t open panel)
  loadNotifs();

  // Optional: poll every 60s to keep badge fresh
  setInterval(loadNotifs, 60000);

  function getCsrf() {
    const m = document.cookie.match(/(?:^|;)\s*csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
})();
