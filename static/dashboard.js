/* dashboard.js — phlist-server dashboard interactivity */
(function () {
  "use strict";

  var POLL_LISTS_MS = 5000;   // card sync interval
  var POLL_STATS_MS = 10000;  // sidebar stats interval

  // Half-circle arc path length: π × r = π × 45 ≈ 141.4
  var GAUGE_LEN = 141.4;

  // ── Helpers ──────────────────────────────────────────────────────────────────

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
    return (bytes / 1024 / 1024 / 1024).toFixed(2) + " GB";
  }

  function relativeTime(unixSeconds) {
    var delta = Math.floor(Date.now() / 1000 - unixSeconds);
    if (delta < 60)        return "just now";
    if (delta < 3600)      return Math.floor(delta / 60) + " min ago";
    if (delta < 86400)     return Math.floor(delta / 3600) + " hr ago";
    if (delta < 86400 * 2) return "yesterday";
    if (delta < 86400 * 7) return Math.floor(delta / 86400) + " days ago";
    return new Date(unixSeconds * 1000).toLocaleDateString();
  }

  function formatUptime(s) {
    if (!s) return "—";
    var d = Math.floor(s / 86400);
    var h = Math.floor((s % 86400) / 3600);
    var m = Math.floor((s % 3600) / 60);
    if (d > 0) return d + "d " + h + "h " + m + "m";
    if (h > 0) return h + "h " + m + "m";
    return m + "m";
  }

  function toast(msg, type) {
    var container = document.getElementById("toast-container");
    var el = document.createElement("div");
    el.className = "toast toast-" + (type || "info");
    el.textContent = msg;
    container.appendChild(el);
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { el.classList.add("toast-visible"); });
    });
    setTimeout(function () {
      el.classList.remove("toast-visible");
      el.addEventListener("transitionend", function () { el.remove(); });
    }, 3000);
  }

  function copyToClipboard(text, onOk, onFail) {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(onOk).catch(function () {
        _execCopy(text, onOk, onFail);
      });
    } else {
      _execCopy(text, onOk, onFail);
    }
  }

  function _execCopy(text, onOk, onFail) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      var ok = document.execCommand("copy");
      document.body.removeChild(ta);
      if (ok) { onOk(); } else { onFail(); }
    } catch (e) {
      document.body.removeChild(ta);
      onFail();
    }
  }

  // ── Sidebar stats ─────────────────────────────────────────────────────────────

  function setText(id, val) {
    var el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  function setWidth(id, pct) {
    var el = document.getElementById(id);
    if (el) el.style.width = Math.min(100, Math.max(0, pct)) + "%";
  }

  function updateSidebar(s) {
    if (s.hostname) setText("stat-hostname", s.hostname);
    setText("stat-uptime", s.uptime_s != null ? formatUptime(s.uptime_s) : "—");

    // CPU arc gauge
    if (s.cpu_pct != null) {
      var pct    = Math.min(100, Math.max(0, s.cpu_pct));
      var filled = (pct / 100) * GAUGE_LEN;
      var gauge  = document.getElementById("gauge-cpu");
      if (gauge) {
        gauge.setAttribute("stroke-dasharray", filled.toFixed(1) + " " + GAUGE_LEN.toFixed(1));
        gauge.style.stroke = pct < 70 ? "var(--accent)" : pct < 90 ? "var(--yellow)" : "var(--red)";
      }
      setText("stat-cpu-pct", pct.toFixed(0) + "%");
    }

    // CPU temp with colour class
    var tempEl = document.getElementById("stat-cpu-temp");
    if (tempEl) {
      if (s.cpu_temp_c != null) {
        var t = s.cpu_temp_c;
        tempEl.textContent = t.toFixed(1) + "°C";
        tempEl.className   = "stat-val " + (t < 60 ? "temp-ok" : t < 75 ? "temp-warm" : "temp-hot");
      } else {
        tempEl.textContent = "—";
        tempEl.className   = "stat-val";
      }
    }

    // Load avg
    if (s.load_avg) setText("stat-load", s.load_avg.join(" / "));

    // RAM
    if (s.mem_pct != null) {
      setText("stat-mem-pct", s.mem_pct.toFixed(0) + "%");
      setWidth("bar-ram", s.mem_pct);
      if (s.mem_used_mb != null) {
        setText("stat-mem-detail", s.mem_used_mb + " / " + s.mem_total_mb + " MB");
      }
    }

    // Disk
    if (s.disk_pct != null) {
      setText("stat-disk-pct", s.disk_pct.toFixed(0) + "%");
      setWidth("bar-disk", s.disk_pct);
      if (s.disk_used_gb != null) {
        setText("stat-disk-detail", s.disk_used_gb + " / " + s.disk_total_gb + " GB");
      }
    }
  }

  function pollStats() {
    fetch("/api/stats")
      .then(function (r) { return r.json(); })
      .then(updateSidebar)
      .catch(function () { /* ignore transient errors */ });
  }

  // ── Card rendering ────────────────────────────────────────────────────────────

  function renderCard(item) {
    var url = location.protocol + "//" + location.host + "/lists/" + item.slug + ".txt";
    var div = document.createElement("div");
    div.className    = "list-card";
    div.id           = "card-" + item.slug;
    div.dataset.slug  = item.slug;
    div.dataset.mtime = item.mtime;
    div.dataset.lines = item.lines;
    div.dataset.size  = item.size;
    div.dataset.url   = url;

    var header = document.createElement("div");
    header.className = "card-header";
    var slugSpan = document.createElement("span");
    slugSpan.className = "card-slug";
    slugSpan.textContent = item.slug;
    var linesSpan = document.createElement("span");
    linesSpan.className = "card-lines";
    linesSpan.textContent = item.lines.toLocaleString() + " lines";
    header.appendChild(slugSpan);
    header.appendChild(linesSpan);

    var meta = document.createElement("div");
    meta.className = "card-meta";
    var sizeSpan = document.createElement("span");
    sizeSpan.className = "card-size";
    sizeSpan.textContent = formatBytes(item.size);
    var sepSpan = document.createElement("span");
    sepSpan.className = "card-sep";
    sepSpan.textContent = "·";
    var timeSpan = document.createElement("span");
    timeSpan.className = "card-time";
    timeSpan.dataset.mtime = item.mtime;
    timeSpan.textContent = relativeTime(item.mtime);
    meta.appendChild(sizeSpan);
    meta.appendChild(sepSpan);
    meta.appendChild(timeSpan);

    var urlRow = document.createElement("div");
    urlRow.className = "card-url-row";
    var urlSpan = document.createElement("span");
    urlSpan.className = "card-url";
    urlSpan.textContent = url;
    var copyBtn = document.createElement("button");
    copyBtn.className = "btn-icon btn-copy";
    copyBtn.dataset.url = url;
    copyBtn.title = "Copy URL";
    copyBtn.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
        '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>' +
        '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>' +
      '</svg>';
    urlRow.appendChild(urlSpan);
    urlRow.appendChild(copyBtn);

    var actions = document.createElement("div");
    actions.className = "card-actions";
    var previewBtn = document.createElement("button");
    previewBtn.className = "btn btn-preview";
    previewBtn.dataset.slug = item.slug;
    previewBtn.dataset.url = url + "?preview=1";
    previewBtn.textContent = "Preview";
    var deleteBtn = document.createElement("button");
    deleteBtn.className = "btn btn-delete-card";
    deleteBtn.dataset.slug = item.slug;
    deleteBtn.textContent = "Delete";
    actions.appendChild(previewBtn);
    actions.appendChild(deleteBtn);

    var preview = document.createElement("div");
    preview.className = "card-preview hidden";
    preview.id = "preview-" + item.slug;
    var pre = document.createElement("pre");
    pre.className = "preview-content";
    preview.appendChild(pre);

    div.appendChild(header);
    div.appendChild(meta);
    div.appendChild(urlRow);
    div.appendChild(actions);
    div.appendChild(preview);
    return div;
  }

  // ── Summary update ────────────────────────────────────────────────────────────

  function updateSummary() {
    var cards = document.querySelectorAll(".list-card");
    var countEl = document.getElementById("stat-list-count");
    var lblEl   = countEl && countEl.nextElementSibling;
    var sizeEl  = document.getElementById("stat-total-size");

    if (countEl) countEl.textContent = cards.length;
    if (lblEl)   lblEl.textContent   = cards.length === 1 ? "list" : "lists";
    if (sizeEl) {
      var total = 0;
      cards.forEach(function (c) { total += parseInt(c.dataset.size, 10) || 0; });
      sizeEl.textContent = formatBytes(total);
    }
  }

  // ── Live sync from /lists/ ────────────────────────────────────────────────────

  function syncCards(data) {
    var grid  = document.getElementById("card-grid");
    var empty = grid.querySelector(".empty-state");
    var current = {};
    document.querySelectorAll(".list-card").forEach(function (c) {
      current[c.dataset.slug] = c;
    });

    var incoming = {};
    data.forEach(function (item) { incoming[item.slug] = item; });

    // Add new cards
    data.forEach(function (item) {
      if (!current[item.slug]) {
        var card = renderCard(item);
        if (empty) { grid.insertBefore(card, empty); } else { grid.appendChild(card); }
        toast("New list: " + item.slug, "success");
      }
    });

    // Fade-remove deleted cards
    Object.keys(current).forEach(function (slug) {
      if (!incoming[slug]) {
        var card = current[slug];
        card.classList.add("card-fade");
        card.addEventListener("transitionend", function () { card.remove(); updateSummary(); });
      }
    });

    if (empty) empty.style.display = data.length > 0 ? "none" : "";
    updateSummary();
  }

  function pollLists() {
    fetch("/lists/")
      .then(function (r) { return r.json(); })
      .then(syncCards)
      .catch(function () { /* ignore */ });
  }

  // ── On load ───────────────────────────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", function () {

    // Initial render: timestamps + human sizes
    document.querySelectorAll(".card-time[data-mtime]").forEach(function (el) {
      el.textContent = relativeTime(parseFloat(el.dataset.mtime));
    });
    document.querySelectorAll(".card-size").forEach(function (el) {
      var card = el.closest(".list-card");
      if (card && card.dataset.size) {
        el.textContent = formatBytes(parseInt(card.dataset.size, 10));
      }
    });

    updateSummary();
    pollStats();
    pollLists();
    setInterval(pollStats, POLL_STATS_MS);
    setInterval(pollLists, POLL_LISTS_MS);

    // ── Event delegation ──────────────────────────────────────────────────────

    var grid = document.getElementById("card-grid");

    grid.addEventListener("click", function (e) {
      var copyBtn    = e.target.closest(".btn-copy");
      var previewBtn = e.target.closest(".btn-preview");
      var deleteBtn  = e.target.closest(".btn-delete-card");

      if (copyBtn) {
        var url = copyBtn.dataset.url;
        copyToClipboard(
          url,
          function () {
            copyBtn.classList.add("copied");
            toast("URL copied!", "success");
            setTimeout(function () { copyBtn.classList.remove("copied"); }, 2000);
          },
          function () { toast("Copy failed — select URL manually", "error"); }
        );
      }

      if (previewBtn) {
        var slug      = previewBtn.dataset.slug;
        var pUrl      = previewBtn.dataset.url; // already has ?preview=1
        var previewEl = document.getElementById("preview-" + slug);
        var pre       = previewEl.querySelector(".preview-content");

        if (!previewEl.classList.contains("hidden")) {
          previewEl.classList.add("hidden");
          previewBtn.textContent = "Preview";
          return;
        }

        previewBtn.textContent = "Loading…";
        previewBtn.disabled    = true;

        fetch(pUrl)
          .then(function (r) { return r.text(); })
          .then(function (text) {
            pre.textContent        = text;
            previewEl.classList.remove("hidden");
            previewBtn.textContent = "Hide";
            previewBtn.disabled    = false;
          })
          .catch(function () {
            toast("Failed to load preview", "error");
            previewBtn.textContent = "Preview";
            previewBtn.disabled    = false;
          });
      }

      if (deleteBtn) {
        var dSlug    = deleteBtn.dataset.slug;
        var savedKey = sessionStorage.getItem("phlist_api_key");
        if (savedKey) {
          // Key already cached — skip modal, confirm with native dialog
          if (!confirm('Delete "' + dSlug + '.txt"?')) return;
          doDelete(dSlug, savedKey);
        } else {
          openModal(dSlug);
        }
      }
    });

    // ── Delete ────────────────────────────────────────────────────────────────

    function doDelete(slug, key) {
      fetch("/lists/" + slug + ".txt", {
        method:  "DELETE",
        headers: { "Authorization": "Bearer " + key },
      })
        .then(function (r) {
          if (r.ok) {
            var card = document.getElementById("card-" + slug);
            if (card) {
              card.classList.add("card-fade");
              card.addEventListener("transitionend", function () { card.remove(); updateSummary(); });
            }
            toast('Deleted "' + slug + '.txt"', "success");
            closeModal();
          } else if (r.status === 403) {
            sessionStorage.removeItem("phlist_api_key");
            toast("Wrong API key — enter it again", "error");
            openModal(slug);
          } else {
            toast("Delete failed (" + r.status + ")", "error");
          }
        })
        .catch(function () {
          toast("Delete failed — network error", "error");
        });
    }

    // ── Modal ─────────────────────────────────────────────────────────────────

    var _pendingSlug  = null;
    var overlay       = document.getElementById("modal-overlay");
    var modalSlugText = document.getElementById("modal-slug-text");
    var keyInput      = document.getElementById("modal-key-input");
    var cancelBtn     = document.getElementById("modal-cancel");
    var confirmBtn    = document.getElementById("modal-confirm");
    var forgetBtn     = document.getElementById("modal-forget-key");

    function openModal(slug) {
      _pendingSlug              = slug;
      modalSlugText.textContent = 'Delete "' + slug + '.txt"?';
      keyInput.value            = "";
      keyInput.classList.remove("input-error");
      if (forgetBtn) forgetBtn.classList.add("hidden");
      overlay.classList.remove("hidden");
      setTimeout(function () { keyInput.focus(); }, 50);
    }

    function closeModal() {
      overlay.classList.add("hidden");
      _pendingSlug = null;
    }

    cancelBtn.addEventListener("click", closeModal);

    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) closeModal();
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeModal();
    });

    keyInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") confirmBtn.click();
    });

    if (forgetBtn) {
      forgetBtn.addEventListener("click", function () {
        sessionStorage.removeItem("phlist_api_key");
        forgetBtn.classList.add("hidden");
        toast("Saved key cleared", "info");
      });
    }

    confirmBtn.addEventListener("click", function () {
      var key = keyInput.value.trim();
      if (!key) {
        keyInput.classList.add("input-error");
        keyInput.focus();
        return;
      }
      keyInput.classList.remove("input-error");
      sessionStorage.setItem("phlist_api_key", key);
      if (forgetBtn) forgetBtn.classList.remove("hidden");

      confirmBtn.disabled    = true;
      confirmBtn.textContent = "Deleting…";
      doDelete(_pendingSlug, key);
      setTimeout(function () {
        confirmBtn.disabled    = false;
        confirmBtn.textContent = "Delete";
      }, 1500);
    });

  }); // end DOMContentLoaded

})();
