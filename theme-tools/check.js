const { toolkitCheck, check, themeCheckRun } = require('@shopify/theme-check-node');
(async () => {
  const root = process.argv[2];
  const mod = require('@shopify/theme-check-node');
  const run = mod.themeCheckRun || mod.toolkitCheck;
  const res = await run(root, undefined, () => {});
  const offenses = res.offenses || res;
  for (const o of offenses) {
    const f = (o.uri || '').replace(/.*\/theme\//, '');
    console.log(`${['ERR','WARN','INFO'][o.severity] || o.severity} ${o.check} ${f}:${o.start?.line+1} ${o.message}`);
  }
  console.log('TOTAL', offenses.length);
})().catch(e => { console.error(e); process.exit(1); });
