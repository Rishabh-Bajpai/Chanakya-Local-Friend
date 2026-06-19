/* =====================================================================
 * Chanakya Configuration UI — behaviour helpers
 * ---------------------------------------------------------------------
 * Small additions that sit on top of the existing inline scripts in
 * index.html / work.html. The original JS (toggleSidebarPanel,
 * setMainMode, expandSidebarPanel, etc.) is left untouched; this file
 * only adds:
 *   • aria-expanded sync on panel headers (now <button>s)
 *   • Sticky sub-nav with scroll-spy
 *   • Form dirty-state tracker (visual highlight + beforeunload warn)
 *   • Toast helper for save success/error messages
 *   • Persist "last opened panel" in localStorage
 *   • Smooth-scroll to anchored panels
 * ===================================================================== */

(function () {
  "use strict";

  /* ---------- helpers ------------------------------------------------ */

  const root = document.body || document.documentElement;
  const REDUCED_MOTION =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn, { once: true });
    } else {
      fn();
    }
  }

  function $(sel, ctx) {
    return (ctx || document).querySelector(sel);
  }
  function $$(sel, ctx) {
    return Array.from((ctx || document).querySelectorAll(sel));
  }

  function safeStorageGet(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function safeStorageSet(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* ignore */ }
  }
  function safeStorageRemove(key) {
    try { window.localStorage.removeItem(key); } catch (e) { /* ignore */ }
  }

  /* ---------- toast helper ------------------------------------------ */

  const TOAST_HOST_ID = "configToastHost";

  function ensureToastHost() {
    let host = document.getElementById(TOAST_HOST_ID);
    if (!host) {
      host = document.createElement("div");
      host.id = TOAST_HOST_ID;
      host.className = "config-toast-host";
      host.setAttribute("role", "region");
      host.setAttribute("aria-label", "Notifications");
      document.body.appendChild(host);
    }
    return host;
  }

  function iconFor(level) {
    if (level === "success") return "✓";
    if (level === "error") return "!";
    if (level === "warn") return "!";
    return "i";
  }

  window.cfgToast = function cfgToast(message, options) {
    const opts = options || {};
    const level = opts.level || "info";
    const duration = typeof opts.duration === "number" ? opts.duration : 3600;
    const host = ensureToastHost();
    const node = document.createElement("div");
    node.className = "config-toast is-" + level;
    node.setAttribute("role", level === "error" ? "alert" : "status");

    const icon = document.createElement("span");
    icon.className = "toast-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = iconFor(level);

    const body = document.createElement("span");
    body.className = "toast-body";
    body.textContent = String(message);

    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "toast-dismiss";
    dismiss.setAttribute("aria-label", "Dismiss notification");
    dismiss.textContent = "×";

    node.appendChild(icon);
    node.appendChild(body);
    node.appendChild(dismiss);
    host.appendChild(node);

    let removed = false;
    function remove() {
      if (removed) return;
      removed = true;
      node.style.transition = "opacity 200ms ease, transform 200ms ease";
      node.style.opacity = "0";
      node.style.transform = "translateY(4px)";
      setTimeout(function () {
        if (node.parentNode) node.parentNode.removeChild(node);
      }, 220);
    }
    dismiss.addEventListener("click", remove);
    if (duration > 0) setTimeout(remove, duration);
    return remove;
  };

  /* ---------- panel aria-expanded sync ------------------------------ */

  function syncPanelAria() {
    $$(".sidebar-panel[data-sidebar-panel]").forEach(function (panel) {
      const header = panel.querySelector(".sidebar-panel-header");
      if (!header) return;
      if (header.tagName !== "BUTTON") {
        // Existing inline onclick handler is on a <div>; we do NOT swap
        // its tag here because that would orphan the inline handler.
        // Just sync aria-expanded via a class watcher below.
      }
      const collapsed = panel.classList.contains("collapsed");
      header.setAttribute("aria-expanded", collapsed ? "false" : "true");
      if (!header.id) {
        const id = "panel-" + panel.getAttribute("data-sidebar-panel") + "-header";
        header.id = id;
      }
      const body = panel.querySelector(".sidebar-panel-body");
      if (body) {
        body.id = body.id || ("panel-" + panel.getAttribute("data-sidebar-panel") + "-body");
        header.setAttribute("aria-controls", body.id);
      }
    });
  }

  // Keep aria-expanded in sync as panels collapse/expand (class change).
  function watchPanelCollapseStates() {
    $$(".sidebar-panel[data-sidebar-panel]").forEach(function (panel) {
      const header = panel.querySelector(".sidebar-panel-header");
      if (!header) return;
      new MutationObserver(function () {
        header.setAttribute(
          "aria-expanded",
          panel.classList.contains("collapsed") ? "false" : "true"
        );
      }).observe(panel, {
        attributes: true,
        attributeFilter: ["class"],
      });
    });
  }

  /* ---------- sub-nav (sticky tabs) --------------------------------- */

  function setupSubNav() {
    const nav = $("#configSubnav");
    if (!nav) return;
    const wrap = nav.closest(".config-subnav-wrap") || nav.parentNode;
    const links = $$(".config-subnav-link", nav);
    if (links.length === 0) return;

    const prevBtn = $("#configSubnavPrev");
    const nextBtn = $("#configSubnavNext");
    const ARROW_STEP = 220;

    function scrollSubNavBy(delta) {
      nav.scrollBy({
        left: delta,
        behavior: REDUCED_MOTION ? "auto" : "smooth",
      });
    }

    function updateSubNavOverflow() {
      if (!wrap) return;
      const overflow = nav.scrollWidth - nav.clientWidth;
      if (overflow <= 4) {
        wrap.classList.remove("has-overflow-start", "has-overflow-end");
        if (prevBtn) prevBtn.hidden = true;
        if (nextBtn) nextBtn.hidden = true;
        return;
      }
      const atStart = nav.scrollLeft <= 1;
      const atEnd = nav.scrollLeft + nav.clientWidth >= nav.scrollWidth - 1;
      wrap.classList.toggle("has-overflow-start", !atStart);
      wrap.classList.toggle("has-overflow-end", !atEnd);
      if (prevBtn) prevBtn.hidden = atStart;
      if (nextBtn) nextBtn.hidden = atEnd;
    }

    if (prevBtn) prevBtn.addEventListener("click", function () { scrollSubNavBy(-ARROW_STEP); });
    if (nextBtn) nextBtn.addEventListener("click", function () { scrollSubNavBy(ARROW_STEP); });

    nav.addEventListener("scroll", updateSubNavOverflow, { passive: true });
    window.addEventListener("resize", updateSubNavOverflow);

    links.forEach(function (link) {
      link.addEventListener("click", function (event) {
        const href = link.getAttribute("href") || "";
        if (href.charAt(0) !== "#") return;
        const target = document.getElementById(href.slice(1));
        if (!target) return;
        event.preventDefault();
        target.scrollIntoView({
          behavior: REDUCED_MOTION ? "auto" : "smooth",
          block: "start",
        });
        // Expand the target panel if it was collapsed.
        if (target.classList.contains("collapsed")) {
          target.classList.remove("collapsed");
        }
        // Move focus to the panel header for screen readers.
        const header = target.querySelector(".sidebar-panel-header");
        if (header) {
          header.setAttribute("tabindex", "-1");
          header.focus({ preventScroll: true });
        }
        safeStorageSet("chanakya-config-last-panel", href);
        // Make sure the clicked tab is fully visible in the subnav strip.
        if (link.scrollIntoView) {
          link.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
        }
      });
    });

    // Keyboard navigation: ←/→ cycle through subnav links while focus is inside.
    nav.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End") {
        return;
      }
      const active = document.activeElement;
      const list = links;
      let idx = list.indexOf(active);
      if (idx === -1) {
        // Focus the currently-active link if any, otherwise the first.
        const current = nav.querySelector(".config-subnav-link[aria-current='true']") || list[0];
        if (current) current.focus();
        event.preventDefault();
        return;
      }
      let next = idx;
      if (event.key === "ArrowLeft") next = (idx - 1 + list.length) % list.length;
      else if (event.key === "ArrowRight") next = (idx + 1) % list.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = list.length - 1;
      if (list[next]) {
        list[next].focus();
        event.preventDefault();
      }
    });

    // Scroll-spy via IntersectionObserver.
    if ("IntersectionObserver" in window) {
      const linkById = {};
      links.forEach(function (link) {
        const id = (link.getAttribute("href") || "").slice(1);
        if (id) linkById[id] = link;
      });

      const observer = new IntersectionObserver(function (entries) {
        // Find the topmost visible panel.
        const visible = entries
          .filter(function (e) { return e.isIntersecting; })
          .sort(function (a, b) { return a.boundingClientRect.top - b.boundingClientRect.top; });
        if (visible.length === 0) return;
        const id = visible[0].target.id;
        links.forEach(function (l) { l.removeAttribute("aria-current"); });
        const active = linkById[id];
        if (active) {
          active.setAttribute("aria-current", "true");
          // Keep the active tab scrolled into view inside the subnav strip.
          if (active.scrollIntoView) {
            active.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
          }
        }
      }, {
        root: $(".config-shell-body") || null,
        rootMargin: "-20% 0px -55% 0px",
        threshold: [0, 0.25, 0.5, 1],
      });

      links.forEach(function (link) {
        const id = (link.getAttribute("href") || "").slice(1);
        if (!id) return;
        const target = document.getElementById(id);
        if (target) observer.observe(target);
      });
    }

    // Defer the first overflow check until layout settles.
    requestAnimationFrame(function () {
      requestAnimationFrame(updateSubNavOverflow);
    });
  }

  /* ---------- form dirty tracker ------------------------------------ */

  const TRACKED_FORMS = [
    "#agentForm",
    "#notificationForm",
    "#workForm",
  ];

  function setupDirtyTracker() {
    const dirtyKey = "chanakya-config-dirty";
    const forms = TRACKED_FORMS.map(function (s) { return $(s); }).filter(Boolean);
    if (forms.length === 0) return;

    function markDirty() { safeStorageSet(dirtyKey, "1"); }
    function markClean() { safeStorageRemove(dirtyKey); }

    forms.forEach(function (form) {
      let pristine = true;
      const fields = $$(form.tagName === "FORM" ? "input, textarea, select" : "*", form);
      fields.forEach(function (el) {
        if (el.matches('input[type="file"]')) return;
        el.addEventListener("input", function () { if (pristine) { markDirty(); pristine = false; } });
        el.addEventListener("change", function () { if (pristine) { markDirty(); pristine = false; } });
      });
      form.addEventListener("submit", function () { markClean(); pristine = true; });
      // Cancel / reset buttons also clear.
      form.addEventListener("reset", function () { markClean(); pristine = true; });
    });

    // Try to mark clean when the user successfully saves the runtime config.
    document.addEventListener("click", function (event) {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.closest("#runtimeConfigApplyButton")) {
        // The runtime config is saved via a separate button; mark clean on click.
        setTimeout(markClean, 50);
      }
    });

    window.addEventListener("beforeunload", function (event) {
      if (safeStorageGet(dirtyKey) === "1") {
        event.preventDefault();
        event.returnValue = "";
        return "";
      }
    });
  }

  /* ---------- bring up the saved panel ------------------------------ */

  function restoreLastPanel() {
    const last = safeStorageGet("chanakya-config-last-panel");
    if (!last || last.charAt(0) !== "#") return;
    const target = document.getElementById(last.slice(1));
    if (!target) return;
    if (target.classList.contains("collapsed")) {
      target.classList.remove("collapsed");
    }
    // Mark the matching subnav link as the current one on initial load.
    const nav = $("#configSubnav");
    if (nav) {
      const id = last.slice(1);
      const link = nav.querySelector(".config-subnav-link[href='#" + id + "']");
      if (link) {
        $$(".config-subnav-link", nav).forEach(function (l) {
          l.removeAttribute("aria-current");
        });
        link.setAttribute("aria-current", "true");
        // Scroll the active tab into view inside the strip.
        requestAnimationFrame(function () {
          if (link.scrollIntoView) {
            link.scrollIntoView({ behavior: "auto", block: "nearest", inline: "nearest" });
          }
        });
      }
    }
  }

  /* ---------- keyboard shortcut: Cmd/Ctrl + , ----------------------- */

  function setupConfigShortcut() {
    document.addEventListener("keydown", function (event) {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key !== "," && event.code !== "Comma") return;
      const tag = (event.target && event.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (event.target && event.target.isContentEditable)) {
        return;
      }
      event.preventDefault();
      const shell = $("#configShell");
      const isOpen = shell && shell.classList.contains("is-active");
      // Prefer the page's own setMainMode if available, otherwise toggle manually.
      if (typeof window.setMainMode === "function") {
        window.setMainMode(isOpen ? "chat" : "config");
      } else if (shell) {
        shell.classList.toggle("is-active", !isOpen);
        const chat = $("#chatShell");
        if (chat) chat.hidden = !isOpen;
        const back = $("#modeBackButton");
        if (back) back.hidden = isOpen;
        const fwd = $("#modeSettingsButton");
        if (fwd) fwd.hidden = !isOpen;
      }
    });
  }

  /* ---------- intercept form/button save events to toast ------------- */

  // Wrap fetch to detect runtime config + agent + MCP + notification saves.
  function setupSaveToasts() {
    const originalFetch = window.fetch && window.fetch.bind(window);
    if (!originalFetch) return;

    window.fetch = function patchedFetch(input, init) {
      const method = (init && init.method) || (input && input.method) || "GET";
      const url = typeof input === "string" ? input : (input && input.url) || "";

      return originalFetch(input, init).then(function (response) {
        try {
          if (method === "POST" || method === "PUT" || method === "DELETE") {
            if (response.ok && /\/api\/(runtime-config|agents|notifications\/ntfy|tools\/config)/.test(url)) {
              const label = describeSave(url, method);
              if (label) {
                setTimeout(function () {
                  window.cfgToast(label + " saved.", { level: "success" });
                }, 80);
              }
            } else if (!response.ok && /\/api\/(runtime-config|agents|notifications\/ntfy|tools\/config)/.test(url)) {
              setTimeout(function () {
                window.cfgToast("Save failed (" + response.status + ").", { level: "error" });
              }, 80);
            }
          }
        } catch (e) { /* ignore */ }
        return response;
      });
    };

    function describeSave(url, method) {
      if (url.indexOf("/api/runtime-config") !== -1) return "Settings";
      if (url.indexOf("/api/agents") !== -1) return method === "DELETE" ? "Worker" : "Worker";
      if (url.indexOf("/api/notifications/ntfy") !== -1) return "Notifications";
      if (url.indexOf("/api/tools/config") !== -1) return "MCP config";
      return null;
    }
  }

  /* ---------- init --------------------------------------------------- */

  onReady(function () {
    syncPanelAria();
    watchPanelCollapseStates();
    setupSubNav();
    setupDirtyTracker();
    setupSaveToasts();
    setupConfigShortcut();
    restoreLastPanel();
  });
})();
