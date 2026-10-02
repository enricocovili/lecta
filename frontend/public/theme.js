// Theme: system by default; the moon/sun button forces light or dark (remembered per browser).
// Loaded as a blocking script in <head> so the page never flashes the wrong theme.
(function () {
  // The app used to be called Appunti: carry this browser's remembered settings (theme, layout, unsaved lesson drafts)
  // over to the new key names once. Runs before anything reads them.
  try {
    var old = [];
    for (var i = 0; i < localStorage.length; i++) {
      var k = localStorage.key(i);
      if (k && /^appunti[.:]/.test(k)) old.push(k);
    }
    old.forEach(function (k) {
      var next = "lecta" + k.slice("appunti".length);
      if (localStorage.getItem(next) === null) localStorage.setItem(next, localStorage.getItem(k));
      localStorage.removeItem(k);
    });
  } catch (e) {}
  var KEY = "lecta.theme";
  var root = document.documentElement;
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function stored() {
    try {
      return localStorage.getItem(KEY);
    } catch (e) {
      return null;
    }
  }
  function apply() {
    var forced = stored();
    if (forced === "light" || forced === "dark") root.setAttribute("data-theme", forced);
    else root.removeAttribute("data-theme");
    var dark = forced ? forced === "dark" : !!(mq && mq.matches);
    root.setAttribute("data-mode", dark ? "dark" : "light");
  }
  apply();
  if (mq && mq.addEventListener) mq.addEventListener("change", apply);
  document.addEventListener("click", function (e) {
    var t = e.target && e.target.closest ? e.target.closest("[data-theme-toggle]") : null;
    if (!t) return;
    var next = root.getAttribute("data-mode") === "dark" ? "light" : "dark";
    var system = mq && mq.matches ? "dark" : "light";
    try {
      if (next === system) localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, next);
    } catch (err) {}
    apply();
    window.dispatchEvent(new CustomEvent("lecta:theme", { detail: next }));
  });
})();
