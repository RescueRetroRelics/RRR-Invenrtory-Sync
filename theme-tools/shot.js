const { chromium, devices } = require('playwright');
(async () => {
  const b = await chromium.launch();
  for (const [name, dev] of [['mobile', devices['Pixel 7']], ['desktop', { viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 }]]) {
    const ctx = await b.newContext(dev); const p = await ctx.newPage();
    for (const pg of ['home', 'product', 'collection', 'team']) {
      await p.goto('http://localhost:8099/' + pg + '/', { waitUntil: 'networkidle' });
      await p.screenshot({ path: `shots/${pg}-${name}.png`, fullPage: name === 'desktop' && pg !== 'collection' });
    }
    if (name === 'desktop') { await p.goto('http://localhost:8099/home/'); await p.click('.mega__details summary'); await p.screenshot({ path: 'shots/mega-desktop.png' }); }
    await ctx.close();
  }
  await b.close();
})();
