/* Rescue Retro Relics theme: the only theme script (~1.5 KB). No jQuery, no frameworks. */
(function () {
  var d = document;

  // Mobile menu button
  d.addEventListener('click', function (e) {
    var t = e.target.closest('[data-menu-toggle]');
    if (t) {
      var nav = d.getElementById(t.getAttribute('aria-controls'));
      var open = t.getAttribute('aria-expanded') === 'true';
      t.setAttribute('aria-expanded', String(!open));
      if (open) nav.removeAttribute('data-open'); else nav.setAttribute('data-open', '');
      return;
    }
    // Close open mega menus / filter panels when clicking outside them
    d.querySelectorAll('details[data-closeable][open]').forEach(function (el) {
      if (!el.contains(e.target)) el.removeAttribute('open');
    });
  });

  // Only one mega menu open at a time
  d.addEventListener('toggle', function (e) {
    var el = e.target;
    if (!el.matches || !el.matches('details[data-group]') || !el.open) return;
    d.querySelectorAll('details[data-group="' + el.dataset.group + '"][open]').forEach(function (o) {
      if (o !== el) o.removeAttribute('open');
    });
  }, true);

  // Escape closes menus
  d.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    d.querySelectorAll('details[data-closeable][open]').forEach(function (el) {
      el.removeAttribute('open');
      var s = el.querySelector('summary'); if (s) s.focus();
    });
  });

  // Filters + sort submit on change
  d.addEventListener('change', function (e) {
    var f = e.target.form;
    if (f && f.hasAttribute('data-autosubmit') && e.target.type !== 'number') f.submit();
  });

  // "You may also like": fetched only when scrolled near, so it never slows the page
  var recs = d.querySelectorAll('[data-recs-url]');
  if (!recs.length) return;
  var load = function (el) {
    fetch(el.dataset.recsUrl).then(function (r) { return r.text(); }).then(function (html) {
      var tmp = d.createElement('div'); tmp.innerHTML = html;
      var inner = tmp.querySelector('[data-recs-url]');
      if (inner && inner.innerHTML.trim()) el.innerHTML = inner.innerHTML; else el.remove();
    }).catch(function () {});
  };
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { io.unobserve(en.target); load(en.target); } });
    }, { rootMargin: '400px' });
    recs.forEach(function (el) { io.observe(el); });
  } else recs.forEach(load);
})();
