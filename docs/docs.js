// Shared docs chrome: sidebar nav, theme, copy buttons, scrollspy TOC, mobile menu.
(function () {
  "use strict";

  var NAV = [
    { sec: "Start" },
    { file: "index.html", ic: "◆", title: "Overview" },
    { file: "setup.html", ic: "▸", title: "Setup & Run" },
    { sec: "Internals" },
    { file: "crawl.html", ic: "⟳", title: "Crawling & Data" },
    { file: "database.html", ic: "▤", title: "Database & Turso" },
    { file: "api.html", ic: "⇄", title: "API & Frontend" },
    { sec: "Operations" },
    { file: "maintenance.html", ic: "✦", title: "Maintenance" },
  ];

  var here = location.pathname.split("/").pop() || "index.html";

  // ---- sidebar ----
  var aside = document.getElementById("sidebar");
  if (aside) {
    var html =
      '<a class="brand" href="index.html">' +
      '<span class="logo">S</span>' +
      "<span><b>Class Checker</b><small>SNU sugang · docs</small></span></a>" +
      '<ul class="nav">';
    NAV.forEach(function (n) {
      if (n.sec) { html += '<li class="sec">' + n.sec + "</li>"; return; }
      var active = n.file === here ? " active" : "";
      html += '<li><a class="' + active.trim() + '" href="' + n.file + '">' +
        '<span class="ic">' + n.ic + "</span>" + n.title + "</a></li>";
    });
    html += "</ul>";
    html += '<div class="side-foot"><button class="theme-toggle" id="themeBtn">' +
      '<span id="themeIc">☾</span><span id="themeTx">Dark</span></button></div>';
    aside.innerHTML = html;
  }

  // ---- theme ----
  var root = document.documentElement;
  var saved = localStorage.getItem("ccdocs-theme");
  if (saved) root.setAttribute("data-theme", saved);
  function syncTheme() {
    var dark = root.getAttribute("data-theme") === "dark";
    var ic = document.getElementById("themeIc"), tx = document.getElementById("themeTx");
    if (ic) ic.textContent = dark ? "☀" : "☾";
    if (tx) tx.textContent = dark ? "Light" : "Dark";
  }
  syncTheme();
  document.addEventListener("click", function (e) {
    if (e.target.closest("#themeBtn")) {
      var dark = root.getAttribute("data-theme") === "dark";
      root.setAttribute("data-theme", dark ? "light" : "dark");
      localStorage.setItem("ccdocs-theme", dark ? "light" : "dark");
      syncTheme();
    }
    if (e.target.closest(".nav-toggle")) document.body.classList.toggle("nav-open");
    else if (!e.target.closest(".sidebar")) document.body.classList.remove("nav-open");
  });

  // ---- mobile menu button ----
  if (!document.querySelector(".nav-toggle")) {
    var b = document.createElement("button");
    b.className = "nav-toggle"; b.setAttribute("aria-label", "Menu"); b.textContent = "☰";
    document.body.appendChild(b);
  }

  // ---- anchored headings + copy buttons + TOC ----
  document.addEventListener("DOMContentLoaded", function () {
    var art = document.querySelector("article");
    if (!art) return;

    art.querySelectorAll("h2[id], h3[id]").forEach(function (h) {
      var a = document.createElement("a");
      a.href = "#" + h.id; a.className = "anchor"; a.textContent = "#";
      h.appendChild(a);
    });

    art.querySelectorAll("pre").forEach(function (pre) {
      var btn = document.createElement("button");
      btn.className = "copy"; btn.textContent = "Copy";
      btn.addEventListener("click", function () {
        var code = pre.querySelector("code") || pre;
        navigator.clipboard.writeText(code.innerText.replace(/\n$/, "")).then(function () {
          btn.textContent = "Copied"; setTimeout(function () { btn.textContent = "Copy"; }, 1400);
        });
      });
      pre.appendChild(btn);
    });

    var heads = [].slice.call(art.querySelectorAll("h2[id]"));
    if (heads.length > 2) {
      var toc = document.createElement("nav");
      toc.className = "toc";
      toc.innerHTML = "<b>On this page</b>" + heads.map(function (h) {
        return '<a href="#' + h.id + '">' + h.firstChild.textContent + "</a>";
      }).join("");
      document.body.appendChild(toc);
      var links = [].slice.call(toc.querySelectorAll("a"));
      var spy = function () {
        var y = window.scrollY + 90, cur = heads[0];
        heads.forEach(function (h) { if (h.offsetTop <= y) cur = h; });
        links.forEach(function (l) {
          l.classList.toggle("active", l.getAttribute("href") === "#" + cur.id);
        });
      };
      window.addEventListener("scroll", spy, { passive: true }); spy();
    }
  });
})();
