/* RRR Relic theme: the only storefront script. ~1KB, no libraries.
   Everything works without it; this just adds conveniences. */
(function () {
  document.documentElement.classList.add('js');

  // Close any open mega menu / drawer / filter when clicking outside or pressing Escape.
  var closables = function () { return document.querySelectorAll('details[data-mega][open], details.facet[open]'); };
  document.addEventListener('click', function (e) {
    closables().forEach(function (d) { if (!d.contains(e.target)) d.removeAttribute('open'); });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    document.querySelectorAll('details[open]').forEach(function (d) {
      if (d.matches('[data-mega], [data-drawer], .facet')) { d.removeAttribute('open'); d.querySelector('summary').focus(); }
    });
  });
  // Only one mega panel open at a time.
  document.querySelectorAll('details[data-mega]').forEach(function (d) {
    d.addEventListener('toggle', function () {
      if (!d.open) return;
      document.querySelectorAll('details[data-mega][open]').forEach(function (o) { if (o !== d) o.removeAttribute('open'); });
    });
  });
  // Mobile drawer sits right under the header.
  var header = document.querySelector('.header');
  var drawer = document.querySelector('[data-drawer]');
  if (header && drawer) {
    drawer.addEventListener('toggle', function () {
      document.documentElement.style.setProperty('--header-bottom', header.getBoundingClientRect().bottom + 'px');
      document.body.style.overflow = drawer.open ? 'hidden' : '';
    });
  }

  // Filters + sort: submit on change (the Apply button is the no-JS fallback).
  document.querySelectorAll('form[data-facets]').forEach(function (form) {
    form.addEventListener('change', function () {
      // Drop empty price fields so URLs stay clean for Google.
      form.querySelectorAll('input[type=number]').forEach(function (i) { if (!i.value) i.disabled = true; });
      form.submit();
    });
  });

  // Cart quantity: update on change (the Update button is the no-JS fallback).
  document.querySelectorAll('[data-autosubmit-qty]').forEach(function (input) {
    input.addEventListener('change', function () {
      var btn = document.createElement('input');
      btn.type = 'hidden'; btn.name = 'update'; btn.value = '1';
      input.form.appendChild(btn); input.form.submit();
    });
  });

  // Product variant select: update price + button + URL without a page reload.
  var select = document.querySelector('[data-variant-select]');
  if (select) {
    select.addEventListener('change', function () {
      var opt = select.options[select.selectedIndex];
      var price = document.querySelector('.product [data-price] .price__now');
      var btn = document.querySelector('[data-add-button]');
      if (price) price.innerHTML = opt.dataset.price;
      if (btn) {
        var ok = opt.dataset.available === 'true';
        btn.disabled = !ok;
        btn.textContent = ok ? 'Add to cart' : 'Sold, this one found a home';
      }
      var url = new URL(location.href); url.searchParams.set('variant', select.value);
      history.replaceState(null, '', url);
    });
  }
})();
