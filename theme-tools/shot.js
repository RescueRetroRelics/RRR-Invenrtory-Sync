const { chromium, devices } = require('playwright');
(async () => {
  const b = await chromium.launch();
  for (const [name, dev] of [['mobile', devices['Pixel 7']], ['desktop', { viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 }]]) {
    const ctx = await b.newContext(dev); const p = await ctx.newPage();
    for (const pg of ['home', 'product', 'collection', 'team']) {
      await p.goto('http://localhost:8099/' + pg + '/', { waitUntil: 'networkidle' });
      await p.evaluate(async () => { for (let y = 0; y < document.body.scrollHeight; y += 400) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 60)); } window.scrollTo(0, 0); });
      await p.waitForLoadState('networkidle'); await p.waitForTimeout(300);
      await p.screenshot({ path: `shots/${pg}-${name}.png`, fullPage: pg === 'team' || pg === 'home' || (name === 'desktop' && pg !== 'collection') });
    }
    if (name === 'desktop') { await p.goto('http://localhost:8099/home/'); await p.click('.mega__details summary'); await p.screenshot({ path: 'shots/mega-desktop.png' }); }
    await ctx.close();
  }
  await b.close();
})();
