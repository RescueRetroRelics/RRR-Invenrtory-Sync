// Local preview renderer: renders the RRR Relic theme with real public store data using liquidjs
// and stand-ins for Shopify-only filters/tags. Approximation for speed testing only.
const { Liquid, Tag, Hash } = require('liquidjs');
const fs = require('fs');
const path = require('path');

const THEME = process.argv[2];
const OUT = path.join(__dirname, 'site');
const ORIGIN = 'http://localhost:8099';
const D = (f) => JSON.parse(fs.readFileSync(path.join(__dirname, 'data', f), 'utf8'));

// ---------- data mapping ----------
const cents = (s) => Math.round(parseFloat(s || 0) * 100);
const img = (i, alt) => i && { src: i.src, width: i.width, height: i.height, aspect_ratio: i.width / i.height, alt: i.alt || alt || '' , id: i.id };
function mapProduct(p, col) {
  const images = (p.images || []).map((i) => img(i, p.title));
  const variants = p.variants.map((v) => ({ id: v.id, title: v.title, price: cents(v.price), compare_at_price: cents(v.compare_at_price), available: v.available !== false, inventory_quantity: 1, inventory_management: 'shopify', inventory_policy: 'deny', sku: v.sku }));
  const media = images.map((i) => ({ id: i.id, preview_image: i, alt: i.alt }));
  const first = variants.find((v) => v.available) || variants[0];
  return {
    id: p.id, title: p.title, handle: p.handle, url: '/products/' + p.handle, vendor: p.vendor, description: p.body_html,
    featured_media: media[0] || null, featured_image: images[0], media, images, variants,
    selected_or_first_available_variant: first, price: Math.min(...variants.map((v) => v.price)),
    price_min: Math.min(...variants.map((v) => v.price)), price_varies: new Set(variants.map((v) => v.price)).size > 1,
    compare_at_price: first.compare_at_price, available: variants.some((v) => v.available),
    has_only_default_variant: variants.length === 1 && variants[0].title === 'Default Title',
    options: (p.options || []).map((o) => o.name), collections: col ? [col] : [],
  };
}
const colRaw = D('collection.json').products;
const collection = { title: 'Betty Boop', handle: 'betty-boop', url: '/collections/betty-boop', description: '<p>Boop-oop-a-doop! Vintage and retro Betty Boop collectibles, rescued and ready for a new home.</p>', products_count: colRaw.length, filters: [], sort_by: 'manual', default_sort_by: 'manual', sort_options: [{ value: 'manual', name: 'Featured' }, { value: 'price-ascending', name: 'Price, low to high' }, { value: 'created-descending', name: 'Newest' }] };
collection.products = colRaw.map((p) => mapProduct(p, collection));
collection.featured_image = collection.products[0] && collection.products[0].featured_image;
const collections = D('collections.json').collections.map((c) => ({ title: c.title, handle: c.handle, url: '/collections/' + c.handle, products_count: c.products_count || 1, featured_image: c.image ? img(c.image, c.title) : collection.products[(c.title.length) % collection.products.length].featured_image }));
const product = mapProduct(D('product.json').product, collection);

const mm = (t, u, links = []) => ({ title: t, url: u, links, current: false });
const linklists = {
  'main-menu': { title: 'Main menu', links: [
    mm('Shop', '/collections/all', [
      mm('Cartoons', '/collections/cartoon-network', [mm('Betty Boop', '/collections/betty-boop'), mm('Garfield', '/collections/all'), mm('Snoopy & Peanuts', '/collections/all')]),
      mm('Bags', '/collections/all-bags', [mm('Backpacks', '/collections/all-bags'), mm('Purses', '/collections/all-bags')]),
      mm('Bundles', '/collections/bundles'),
      mm('Toys & Figures', '/collections/all', [mm('Blokees', '/collections/all'), mm('Toy Story', '/collections/all')]),
    ]),
    mm('New Arrivals', '/collections/all'), mm('Meet the Team', '/pages/meet-the-team'), mm('Our Mission', '/pages/about'),
  ] },
  footer: { title: 'Footer', links: [mm('Search', '/search'), mm('Shipping', '/policies/shipping-policy'), mm('Contact', '/pages/contact')] },
};

// ---------- engine ----------
const engine = new Liquid({ root: [path.join(THEME, 'snippets')], extname: '.liquid', jsTruthy: false, strictFilters: false });
const kw = (args) => { const o = {}; const pos = []; for (const a of args) Array.isArray(a) ? (o[a[0]] = a[1]) : pos.push(a); return [pos, o]; };
const money = (c) => '$' + (Number(c || 0) / 100).toFixed(2);
const imgUrl = (i, ...args) => { const [, o] = kw(args); if (!i) return ''; const src = typeof i === 'string' ? i : i.src; const u = src + (src.includes('?') ? '&' : '?') + (o.width ? 'width=' + o.width : ''); const r = new String(u); r.__img = typeof i === 'string' ? null : i; r.__w = o.width; return r; };
engine.registerFilter('image_url', imgUrl);
engine.registerFilter('image_tag', (u, ...args) => {
  const [, o] = kw(args); const im = u.__img || {}; const base = String(u).split('?')[0];
  const w = Number(o.width) || Math.min(u.__w || im.width, im.width || 9999); const h = Number(o.height) || Math.round(w / (im.aspect_ratio || 1));
  const widths = (o.widths || '').split(',').map((s) => parseInt(s)).filter((n) => n && (!im.width || n <= im.width));
  let s = `<img src="${base}?width=${u.__w || w}" alt="${(o.alt ?? im.alt ?? '').toString().replace(/"/g, '&quot;')}"`;
  if (widths.length) s += ` srcset="${widths.map((n) => `${base}?width=${n} ${n}w`).join(', ')}"`;
  if (o.sizes) s += ` sizes="${o.sizes}"`;
  s += ` width="${w}" height="${h}"`;
  for (const k of ['loading', 'fetchpriority', 'class', 'id', 'style']) if (o[k] && o[k] !== 'auto') s += ` ${k}="${o[k]}"`;
  return s + '>';
});
engine.registerFilter('asset_url', (n) => '/assets/' + n);
engine.registerFilter('stylesheet_tag', (u) => `<link href="${u}" rel="stylesheet" type="text/css" media="all">`);
engine.registerFilter('money', money);
engine.registerFilter('money_with_currency', (c) => money(c) + ' USD');
engine.registerFilter('money_without_currency', (c) => (Number(c || 0) / 100).toFixed(2));
engine.registerFilter('font_url', () => '/fonts/arvo_n7.woff2');
engine.registerFilter('font_face', () => `@font-face{font-family:Arvo;font-weight:700;font-style:normal;font-display:swap;src:url(/fonts/arvo_n7.woff2) format("woff2")}`);
engine.registerFilter('preload_tag', (u, ...a) => `<link rel="preload" href="${u}" as="font" type="font/woff2" crossorigin="anonymous">`);
engine.registerFilter('json', (v) => JSON.stringify(v));
engine.registerFilter('color_mix', (a, b, p) => { const h = (x) => [1, 3, 5].map((i) => parseInt(x.slice(i, i + 2), 16)); const A = h(a), B = h(b); return '#' + A.map((v, i) => Math.round(v * p / 100 + B[i] * (1 - p / 100)).toString(16).padStart(2, '0')).join(''); });
engine.registerFilter('structured_data', (p) => JSON.stringify({ '@context': 'http://schema.org/', '@type': 'Product', name: p.title, url: ORIGIN + p.url, image: p.featured_image && p.featured_image.src, offers: p.variants.map((v) => ({ '@type': 'Offer', price: (v.price / 100).toFixed(2), priceCurrency: 'USD', availability: v.available ? 'http://schema.org/InStock' : 'http://schema.org/OutOfStock' })) }));
engine.registerFilter('payment_type_svg_tag', () => '');
engine.registerFilter('pluralize', (n, a, b) => (n == 1 ? a : b));
engine.registerFilter('t', (k) => k.split('.').pop());
engine.registerFilter('handle', (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-'));
engine.registerFilter('default_errors', () => '');
engine.registerFilter('format_code', (s) => s);

class Swallow extends Tag { constructor(t, r, l) { super(t, r, l); this.tpls = []; const s = r.readTo(`end${t.name}`); } *render() { return ''; } }
engine.registerTag('schema', class extends Tag { constructor(t, r, l) { super(t, r, l); while (r.length) { const x = r.shift(); if (x.name === 'endschema') return; } } *render() { return ''; } });
engine.registerTag('doc', class extends Tag { constructor(t, r, l) { super(t, r, l); while (r.length) { const x = r.shift(); if (x.name === 'enddoc') return; } } *render() { return ''; } });
const blockTag = (name, open, close, setup) => engine.registerTag(name, class extends Tag {
  constructor(t, r, l) { super(t, r, l); this.args = t.args; this.tpls = []; const stream = l.parser.parseStream(r).on(`tag:end${name}`, () => stream.stop()).on('template', (tp) => this.tpls.push(tp)).on('end', () => { throw new Error(`${name} not closed`); }); stream.start(); }
  *render(ctx, emitter) { const extra = setup ? setup(this.args, ctx) : {}; ctx.push(extra); emitter.write(typeof open === 'function' ? open(this.args) : open); yield this.liquid.renderer.renderTemplates(this.tpls, ctx, emitter); emitter.write(close); ctx.pop(); }
});
blockTag('style', '<style>', '</style>');
blockTag('form', (args) => { const type = (args.match(/'([^']+)'/) || [])[1]; const cls = (args.match(/class:\s*'([^']+)'/) || [])[1]; const action = type === 'product' ? '/cart/add' : '/password'; return `<form method="post" action="${action}" accept-charset="UTF-8"${cls ? ` class="${cls}"` : ''} enctype="multipart/form-data"><input type="hidden" name="form_type" value="${type}"><input type="hidden" name="utf8" value="✓">`; }, '</form>', () => ({ form: { errors: null } }));
blockTag('paginate', '', '', () => ({ paginate: { pages: 1, current_page: 1, parts: [] } }));

// ---------- sections ----------
const schemaOf = (type) => { const src = fs.readFileSync(path.join(THEME, 'sections', type + '.liquid'), 'utf8'); const m = src.match(/{%\s*schema\s*%}([\s\S]*?){%\s*endschema\s*%}/); return m ? JSON.parse(m[1]) : {}; };
const defaults = (arr = []) => Object.fromEntries(arr.filter((s) => 'default' in s).map((s) => [s.id, s.default]));
async function renderSection(id, cfg, index, ctxBase, overrides = {}) {
  const sch = schemaOf(cfg.type);
  const settings = { ...defaults(sch.settings), ...(cfg.settings || {}), ...(overrides.settings || {}) };
  const blocks = (cfg.block_order || Object.keys(cfg.blocks || {})).map((bid, i) => { const b = cfg.blocks[bid]; const bs = (sch.blocks || []).find((x) => x.type === b.type) || {}; return { id: bid, type: b.type, shopify_attributes: '', settings: { ...defaults(bs.settings), ...(b.settings || {}), ...((overrides.blocks || [])[i] || {}) } }; });
  const src = fs.readFileSync(path.join(THEME, 'sections', cfg.type + '.liquid'), 'utf8');
  const html = await engine.parseAndRender(src, { ...ctxBase, section: { id, index, index0: index - 1, settings, blocks } }, { globals: ctxBase });
  return `<div id="shopify-section-${id}" class="shopify-section ${sch.class || ''}">${html}</div>`;
}
async function renderGroup(name, ctx) { const g = JSON.parse(fs.readFileSync(path.join(THEME, 'sections', name + '.json'), 'utf8')); let out = ''; for (const id of g.order) out += await renderSection(id, g.sections[id], 0, ctx); return out; }

// ---------- settings ----------
const settingsSchema = JSON.parse(fs.readFileSync(path.join(THEME, 'config/settings_schema.json'), 'utf8'));
const settings = Object.assign({}, ...settingsSchema.map((g) => defaults(g.settings)));
settings.type_heading_font = { family: 'Arvo', fallback_families: 'serif', weight: 700, 'system?': false };

async function page(name, pageType, template, extra, overrides = {}) {
  const ctx = {
    settings, linklists, collections, product, collection: null, shop: { policies: [{ title: 'Refund policy', url: '/policies/refund-policy' }, { title: 'Privacy policy', url: '/policies/privacy-policy' }, { title: 'Terms of service', url: '/policies/terms-of-service' }, { title: 'Shipping policy', url: '/policies/shipping-policy' }], name: 'Rescue Retro Relics', description: 'Vintage and nostalgia that funds dog rescue.', customer_accounts_enabled: true, enabled_payment_types: [], shipping_policy: { body: 'x', url: '/policies/shipping-policy' } },
    page_description: 'Vintage and nostalgia that funds dog rescue.', request: { page_type: pageType, origin: ORIGIN, locale: { iso_code: 'en' } }, template: { name: pageType },
    routes: { root_url: '/', search_url: '/search', cart_url: '/cart', account_url: '/account', all_products_collection_url: '/collections/all' },
    cart: { item_count: 0, currency: { iso_code: 'USD' }, taxes_included: false }, canonical_url: ORIGIN + '/', content_for_header: '', current_page: 1, ...extra,
  };
  const t = JSON.parse(fs.readFileSync(path.join(THEME, 'templates', template + '.json'), 'utf8'));
  let body = ''; let i = 1;
  for (const id of t.order) body += await renderSection(id, t.sections[id], i++, ctx, overrides[id] || {});
  // Inline the section-group tags by pre-rendering.
  let layout = fs.readFileSync(path.join(THEME, 'layout/theme.liquid'), 'utf8');
  layout = layout.replace("{% sections 'header-group' %}", await renderGroup('header-group', ctx)).replace("{% sections 'footer-group' %}", await renderGroup('footer-group', ctx));
  const html = await engine.parseAndRender(layout, { ...ctx, content_for_layout: body }, { globals: ctx });
  fs.mkdirSync(path.join(OUT, name), { recursive: true });
  fs.writeFileSync(path.join(OUT, name, 'index.html'), html);
  console.log('rendered', name, html.length, 'bytes');
}

(async () => {
  for (const f of fs.readdirSync(path.join(THEME, 'assets'))) fs.copyFileSync(path.join(THEME, 'assets', f), path.join(OUT, 'assets', f));
  const heroStandIn = collection.products[0].featured_image; // stand-in for the Bailee artwork
  const cats = collections.slice(0, 4);
  await page('home', 'index', 'index', { page_title: 'Rescue Retro Relics', page_description: 'Vintage and nostalgia that funds dog rescue.' }, {
    featured: { settings: { collection } }, categories: { blocks: cats.map((c) => ({ collection: c })) },
  });
  await page('product', 'product', 'product', { page_title: product.title, page_description: 'Vintage find', page_image: product.featured_image });
  await page('collection', 'collection', 'collection', { collection, page_title: collection.title });
  await page('team', 'page', 'page.meet-the-team', { page_title: 'Meet the Team' });
  await page('faq', 'page', 'page.faq', { page_title: 'FAQ' });
  await page('about', 'page', 'page.about', { page_title: 'About' });
})().catch((e) => { console.error(e); process.exit(1); });
