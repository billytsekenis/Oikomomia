const { chromium, devices } = require('playwright');
(async () => {
  const b = await chromium.launch(); const m = await b.newContext({ ...devices['iPhone 13'] }); const p = await m.newPage();
  await p.goto('http://localhost:8000/'); await p.fill('#email', process.env.E2E_EMAIL || 'test@example.com'); await p.fill('#pw', process.env.E2E_PASSWORD || 'testpass123');
  await p.click('button:has-text("Sign in")'); await p.waitForTimeout(2000);
  for (const path of ['/stock/AAPL', '/discover', '/portfolio', '/paper', '/backtest', '/track-record', '/watchlists', '/system', '/analyst', '/']) {
    await p.goto('http://localhost:8000' + path); await p.waitForTimeout(6000);
    const r = await p.evaluate(() => {
      const W = document.documentElement.clientWidth; const out = [];
      document.querySelectorAll('body *').forEach(el => { const rc = el.getBoundingClientRect(); if (rc.right > W + 1 && rc.width > 0) {
        let s = el, scrolls = false; while (s && s !== document.body) { const cs = getComputedStyle(s); if (['auto','scroll','hidden'].includes(cs.overflowX) && s !== el) { scrolls = true; break; } s = s.parentElement; }
        if (!scrolls) out.push(el.tagName + '.' + (el.className.baseVal ?? el.className).toString().slice(0, 70) + ' w=' + Math.round(rc.width)); } });
      return { scrollW: document.documentElement.scrollWidth, W, out: out.slice(0, 6) };
    });
    console.log(path, JSON.stringify(r));
  }
  await b.close();
})();
