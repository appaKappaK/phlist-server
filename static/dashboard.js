/* dashboard.js — phlist-server dashboard interactivity */
(function () {
  "use strict";

  // ── Helpers ─────────────────────────────────────────────────────────────────

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }

  function relativeTime(unixSeconds) {
    const delta = Math.floor(Date.now() / 1000 - unixSeconds);
    if (delta < 60)        return "just now";
    if (delta < 3600)      return Math.floor(delta / 60) + " min ago";
    if (delta < 86400)     return Math.floor(delta / 3600) + " hr ago";
    if (delta < 86400 * 2) return "yesterday";
    if (delta < 86400 * 7) return Math.floor(delta / 86400) + " days ago";
    return new Date(unixSeconds * 1000).toLocaleDateString();
  }

  function toast(msg, type) {
    const container = document.getElementById("toast-container");
    const el = document.createElement("div");
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

  // ── On load ──────────────────────────────────────────────────────────────────

  // Relative timestamps
  document.querySelectorAll(".card-time[data-mtime]").forEach(function (el) {
    el.textContent = relativeTime(parseFloat(el.dataset.mtime));
  });

  // Human-readable sizes on cards
  document.querySelectorAll(".card-size").forEach(function (el) {
    var card = el.closest(".list-card");
    if (card && card.dataset.size) {
      el.textContent = formatBytes(parseInt(card.dataset.size, 10));
    }
  });

  // Total size stat
  var statSize = document.getElementById("stat-size");
  if (statSize) {
    var total = 0;
    document.querySelectorAll(".list-card[data-size]").forEach(function (el) {
      total += parseInt(el.dataset.size, 10) || 0;
    });
    statSize.textContent = formatBytes(total);
  }

  // ── Copy URL ─────────────────────────────────────────────────────────────────

  document.querySelectorAll(".btn-copy").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var url = btn.dataset.url;
      navigator.clipboard.writeText(url).then(function () {
        btn.classList.add("copied");
        toast("Copied!", "success");
        setTimeout(function () { btn.classList.remove("copied"); }, 2000);
      }).catch(function () {
        toast("Copy failed — select and copy manually", "error");
      });
    });
  });

  // ── Preview ──────────────────────────────────────────────────────────────────

  document.querySelectorAll(".btn-preview").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var slug       = btn.dataset.slug;
      var url        = btn.dataset.url;
      var previewEl  = document.getElementById("preview-" + slug);
      var pre        = previewEl.querySelector(".preview-content");

      if (!previewEl.classList.contains("hidden")) {
        previewEl.classList.add("hidden");
        btn.textContent = "Preview";
        return;
      }

      btn.textContent = "Loading…";
      btn.disabled    = true;

      fetch(url)
        .then(function (r) { return r.text(); })
        .then(function (text) {
          pre.textContent = text.split("\n").slice(0, 50).join("\n");
          previewEl.classList.remove("hidden");
          btn.textContent = "Hide";
          btn.disabled    = false;
        })
        .catch(function () {
          toast("Failed to load preview", "error");
          btn.textContent = "Preview";
          btn.disabled    = false;
        });
    });
  });

  // ── Delete modal ─────────────────────────────────────────────────────────────

  var _pendingSlug  = null;
  var overlay       = document.getElementById("modal-overlay");
  var modalSlugText = document.getElementById("modal-slug-text");
  var keyInput      = document.getElementById("modal-key-input");
  var cancelBtn     = document.getElementById("modal-cancel");
  var confirmBtn    = document.getElementById("modal-confirm");

  function openModal(slug) {
    _pendingSlug          = slug;
    modalSlugText.textContent = "Delete \"" + slug + ".txt\"?";
    keyInput.value        = sessionStorage.getItem("phlist_api_key") || "";
    keyInput.classList.remove("input-error");
    overlay.classList.remove("hidden");
    setTimeout(function () {
      (keyInput.value ? confirmBtn : keyInput).focus();
    }, 50);
  }

  function closeModal() {
    overlay.classList.add("hidden");
    _pendingSlug = null;
  }

  document.querySelectorAll(".btn-delete-card").forEach(function (btn) {
    btn.addEventListener("click", function () { openModal(btn.dataset.slug); });
  });

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

  confirmBtn.addEventListener("click", function () {
    var key = keyInput.value.trim();
    if (!key) {
      keyInput.classList.add("input-error");
      keyInput.focus();
      return;
    }
    keyInput.classList.remove("input-error");
    sessionStorage.setItem("phlist_api_key", key);

    confirmBtn.disabled    = true;
    confirmBtn.textContent = "Deleting…";

    fetch("/lists/" + _pendingSlug + ".txt", {
      method:  "DELETE",
      headers: { "Authorization": "Bearer " + key },
    })
      .then(function (r) {
        if (r.ok) {
          var card = document.getElementById("card-" + _pendingSlug);
          if (card) {
            card.classList.add("card-fade");
            card.addEventListener("transitionend", function () { card.remove(); });
          }
          toast("Deleted \"" + _pendingSlug + ".txt\"", "success");
          closeModal();
        } else if (r.status === 403) {
          toast("Wrong API key", "error");
          sessionStorage.removeItem("phlist_api_key");
          keyInput.value = "";
          keyInput.classList.add("input-error");
          keyInput.focus();
        } else {
          toast("Delete failed (" + r.status + ")", "error");
        }
      })
      .catch(function () {
        toast("Delete failed — network error", "error");
      })
      .finally(function () {
        confirmBtn.disabled    = false;
        confirmBtn.textContent = "Delete";
      });
  });

})();
