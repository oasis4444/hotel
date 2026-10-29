/* Hôtel et Restaurant : thème, menu, compte à rebours, temps réel (polling), disponibilité, signature */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* Thème clair/sombre */
  var root = document.documentElement;
  var saved = null;
  try { saved = localStorage.getItem("hotelrest-theme"); } catch (e) {}
  if (saved) root.setAttribute("data-theme", saved);
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-toggle-theme]");
    if (t) {
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("hotelrest-theme", next); } catch (er) {}
    }
    if (e.target.closest("[data-toggle-nav]")) document.body.classList.toggle("nav-open");
    else if (document.body.classList.contains("nav-open") && !e.target.closest(".side")) document.body.classList.remove("nav-open");
  });

  /* Menu latéral : drill-down module -> sous-menu, avec retour */
  var side = $(".side");
  if (side) {
    document.addEventListener("click", function (e) {
      var open = e.target.closest("[data-nav-open]");
      if (open) side.setAttribute("data-open", open.getAttribute("data-nav-open"));
      else if (e.target.closest("[data-nav-back]")) side.removeAttribute("data-open");
    });
  }

  /* Horloge */
  function clock() {
    var c = $("#clock"); if (!c) return;
    var d = new Date();
    c.textContent = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  /* Comptes à rebours */
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function fmt(ms) {
    var s = Math.floor(Math.abs(ms) / 1000), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + pad(m) : pad(m)) + ":" + pad(x);
  }
  function tickCountdowns() {
    var now = Date.now();
    $$(".cd").forEach(function (el) {
      var ms = parseInt(el.getAttribute("data-end"), 10) - now;
      var warn = parseInt(el.getAttribute("data-warn") || "10", 10) * 60000;
      el.textContent = (ms < 0 ? "+" : "") + fmt(ms);
      el.classList.toggle("over", ms < 0);
      el.classList.toggle("warn", ms >= 0 && ms <= warn);
      var tile = el.closest(".tile");
      if (tile) tile.classList.toggle("over", ms < 0);
    });
  }

  /* Alerte sonore */
  var soundOn = false;
  try { soundOn = localStorage.getItem("hotelrest-sound") === "1"; } catch (e) {}
  var audio = null;
  function beep() {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      [0, 0.22].forEach(function (d) {
        var o = audio.createOscillator(), g = audio.createGain();
        o.frequency.value = 880; o.connect(g); g.connect(audio.destination);
        g.gain.setValueAtTime(0.0001, audio.currentTime + d);
        g.gain.exponentialRampToValueAtTime(0.25, audio.currentTime + d + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + d + 0.18);
        o.start(audio.currentTime + d); o.stop(audio.currentTime + d + 0.2);
      });
    } catch (e) {}
  }
  function syncSoundBtn() {
    var b = $("[data-toggle-sound]"); if (!b) return;
    b.setAttribute("aria-pressed", soundOn ? "true" : "false");
    b.querySelector(".lbl").textContent = soundOn ? "Son activé" : "Son coupé";
  }
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-toggle-sound]")) {
      soundOn = !soundOn;
      try { localStorage.setItem("hotelrest-sound", soundOn ? "1" : "0"); } catch (er) {}
      if (soundOn) beep();
      syncSoundBtn();
    }
  });

  /* Rafraîchissement en direct (polling, compatible PythonAnywhere) */
  var live = $("#live");
  var known = null;
  function soundKeys() {
    var n = $("#live [data-sound]");
    return n ? (n.getAttribute("data-sound") || "").split(",").filter(Boolean) : [];
  }
  if (live) {
    known = soundKeys();
    setInterval(function () {
      if (document.hidden) return;
      fetch(live.getAttribute("data-url"), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.ok ? r.text() : Promise.reject(); })
        .then(function (html) {
          live.innerHTML = html;
          var keys = soundKeys();
          if (soundOn && keys.some(function (k) { return known.indexOf(k) === -1; })) beep();
          known = keys;
          tickCountdowns();
          var st = $("#live-status"); if (st) st.textContent = "Mis à jour à " + new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        })
        .catch(function () { var st = $("#live-status"); if (st) st.textContent = "Connexion instable : nouvel essai…"; });
    }, 8000);
  }
  var pubRooms = $("#pub-rooms");
  if (pubRooms) setInterval(function () {
    if (document.hidden) return;
    fetch(pubRooms.getAttribute("data-url")).then(function (r) { return r.text(); }).then(function (h) { pubRooms.innerHTML = h; }).catch(function () {});
  }, 15000);

  /* Disponibilité en direct dans les formulaires de réservation */
  var form = $("[data-avail]");
  if (form) {
    var url = form.getAttribute("data-avail");
    var exclude = form.getAttribute("data-exclude") || "";
    var sel = $("#room-select", form), info = $("#slot-info", form);
    var wanted = sel ? sel.getAttribute("data-current") : "";
    var timer = null;
    var money = function (n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, "\u202f") + "\u00a0Ar"; };
    function refresh() {
      var f = form.elements["formula"].value, s = form.elements["start"].value;
      if (!f || !s) { info.textContent = "Choisissez une formule et l'heure d'arrivée."; return; }
      var q = url + "?formula=" + encodeURIComponent(f) + "&start=" + encodeURIComponent(s) + (exclude ? "&exclude=" + exclude : "");
      info.textContent = "Recherche des chambres libres…";
      fetch(q, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
        var current = sel.value || wanted;
        sel.innerHTML = "";
        if (d.error) { info.textContent = d.error; info.className = "hint err"; return; }
        if (!d.rooms.length) { info.textContent = "Aucune chambre libre sur ce créneau."; info.className = "hint err"; return; }
        d.rooms.forEach(function (r) {
          var o = document.createElement("option"); o.value = r.id; o.textContent = "Chambre " + r.number + " · " + r.type;
          if (String(r.id) === String(current)) o.selected = true; sel.appendChild(o);
        });
        info.className = "hint";
        info.textContent = d.rooms.length + " chambre(s) libre(s) · du " + d.start + " au " + d.end + " · " + money(d.price);
      }).catch(function () { info.textContent = "Impossible de vérifier la disponibilité."; });
    }
    function later() { clearTimeout(timer); timer = setTimeout(refresh, 250); }
    form.elements["formula"].addEventListener("change", later);
    form.elements["start"].addEventListener("change", later);
    refresh();
  }

  /* Signature numérique */
  var canvas = $("#sig-canvas");
  if (canvas) {
    var ctx = canvas.getContext("2d"), drawing = false, target = $("#id_signature");
    function size() { var r = canvas.getBoundingClientRect(); canvas.width = r.width; canvas.height = r.height; ctx.lineWidth = 2.2; ctx.lineCap = "round"; ctx.strokeStyle = "#0B3540"; }
    size();
    function pos(e) { var r = canvas.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
    canvas.addEventListener("pointerdown", function (e) { drawing = true; var p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y); canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", function (e) { if (!drawing) return; var p = pos(e); ctx.lineTo(p.x, p.y); ctx.stroke(); });
    ["pointerup", "pointercancel"].forEach(function (ev) { canvas.addEventListener(ev, function () { if (!drawing) return; drawing = false; target.value = canvas.toDataURL("image/png"); }); });
    var clr = $("#sig-clear"); if (clr) clr.addEventListener("click", function () { ctx.clearRect(0, 0, canvas.width, canvas.height); target.value = ""; });
  }

  /* Confirmation des actions sensibles */
  document.addEventListener("submit", function (e) {
    var m = e.target.getAttribute("data-confirm");
    if (m && !window.confirm(m)) e.preventDefault();
  });

  syncSoundBtn(); clock(); tickCountdowns();
  setInterval(function () { clock(); tickCountdowns(); }, 1000);
})();
