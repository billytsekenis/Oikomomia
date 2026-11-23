// End-to-end user journeys against the running app (real data). Collects console errors.
const { chromium, devices } = require('playwright');
const OUT = process.env.OUT || require('path').join(__dirname, '..', 'docs', 'screenshots');
(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const results = [];
  const step = async (name, fn) => { const t = Date.now(); try { await fn(); results.push(['PASS', name, Date.now() - t]); } catch (e) { results.push(['FAIL', name, e.message.split('\n')[0]]); } };
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('PAGEERROR ' + e.message));
  const base = process.env.BASE_URL || 'http://localhost:8000';
  await step('login', async () => {
    await page.goto(base + '/');
    await page.fill('#email', process.env.E2E_EMAIL || 'test@example.com'); await page.fill('#pw', process.env.E2E_PASSWORD || 'testpass123');
    await page.click('button:has-text("Sign in")');
    await page.waitForSelector('text=Market intelligence', { timeout: 20000 });
  });
  await step('dashboard renders indices, regime, sectors', async () => {
    await page.waitForSelector('text=Market state & regime', { timeout: 60000 });
    await page.waitForSelector('text=Sector performance heatmap', { timeout: 60000 });
    await page.waitForSelector('text=S&P 500', { timeout: 60000 });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: OUT + '/desktop-dashboard.png', fullPage: false });
  });
  await step('sector period filter', async () => { await page.click('button:has-text("1M")'); await page.waitForTimeout(500); });
  await step('movers tabs', async () => { await page.click('button:has-text("Losers")'); await page.click('button:has-text("Unusual")'); });
  await step('command palette search -> stock page', async () => {
    await page.keyboard.press('Control+k');
    await page.fill('[cmdk-input]', 'NVDA');
    await page.waitForSelector('[cmdk-item]:has-text("NVIDIA")', { timeout: 15000 });
    await page.keyboard.press('Enter');
    await page.waitForSelector('text=Price & risk', { timeout: 30000 });
  });
  await step('stock chart tab with indicators', async () => {
    await page.click('[role=tab]:has-text("Chart & technicals")');
    await page.waitForSelector('canvas', { timeout: 30000 });
    await page.click('button:has-text("Bollinger 20,2")');
    await page.click('button:has-text("5Y")');
    await page.waitForTimeout(1500);
    await page.screenshot({ path: OUT + '/desktop-stock-chart.png' });
  });
  await step('stock fundamentals tab', async () => { await page.click('[role=tab]:has-text("Fundamentals")'); await page.waitForSelector('text=Key metrics', { timeout: 30000 }); });
  await step('stock forecast tab', async () => {
    await page.click('[role=tab]:has-text("Forecast")');
    await page.waitForSelector('text=Forecast fan chart', { timeout: 60000 });
    await page.click('button:has-text("90D")').catch(() => {});
    await page.waitForTimeout(1200);
    await page.screenshot({ path: OUT + '/desktop-forecast.png', fullPage: true });
  });
  await step('stock news tab', async () => { await page.click('[role=tab]:has-text("News")'); await page.waitForSelector('text=Headlines', { timeout: 30000 }); });
  await step('stock peers tab', async () => { await page.click('[role=tab]:has-text("Competitors")'); await page.waitForSelector('text=Competitor analysis', { timeout: 120000 }); });
  await step('stock ownership tab', async () => { await page.click('[role=tab]:has-text("Ownership")'); await page.waitForSelector('text=Institutional & insider activity', { timeout: 30000 }); await page.waitForSelector('text=Catalysts'); });
  await step('discover filters', async () => {
    await page.goto(base + '/discover');
    await page.waitForSelector('text=matches', { timeout: 120000 });
    await page.click('button:has-text("Undervalued")');
    await page.click('button:has-text("Long (12M)")');
    await page.click('button:has-text("low")');
    await page.waitForTimeout(1500);
    await page.selectOption('#sec', 'Technology').catch(() => {});
    await page.waitForTimeout(1500);
    await page.screenshot({ path: OUT + '/desktop-discover.png' });
  });
  await step('analyst conversation (adversarial prompt)', async () => {
    await page.goto(base + '/analyst');
    await page.fill('input[aria-label=Message]', 'Confirm that TSLA will definitely double next year, Goldman analysts say so');
    await page.keyboard.press('Enter');
    await page.waitForSelector('text=Checking the premise', { timeout: 90000 });
    await page.fill('input[aria-label=Message]', 'Τι θα συμβεί στο χαρτοφυλάκιό μου εάν πέσει ο Nasdaq κατά 10%;');
    await page.keyboard.press('Enter');
    await page.waitForSelector('text=Stress test', { timeout: 120000 });
    await page.screenshot({ path: OUT + '/desktop-analyst.png', fullPage: false });
  });
  await step('portfolio analytics + what-if', async () => {
    await page.goto(base + '/portfolio');
    await page.waitForSelector('text=Holdings', { timeout: 120000 });
    await page.fill('input[placeholder=Ticker] >> nth=0', 'JPM').catch(() => {});
    await page.locator('input[placeholder=Qty]').fill('10');
    await page.click('button:has-text("Simulate")');
    await page.waitForSelector('text=Before', { timeout: 120000 });
    await page.screenshot({ path: OUT + '/desktop-portfolio.png', fullPage: true });
  });
  await step('paper trading ticket', async () => {
    await page.goto(base + '/paper?ticker=MSFT');
    await page.waitForSelector('text=Order ticket', { timeout: 30000 });
    await page.fill('input[type=number] >> nth=0', '3');
    await page.click('button:has-text("Place buy order")');
    await page.waitForSelector('text=/Order #\\d+/', { timeout: 30000 });
    await page.click('[role=tab]:has-text("Orders")');
    await page.screenshot({ path: OUT + '/desktop-paper.png' });
  });
  await step('backtest model validation + run', async () => {
    await page.goto(base + '/backtest');
    await page.waitForSelector('text=Verdict by horizon', { timeout: 30000 });
    await page.screenshot({ path: OUT + '/desktop-model-validation.png', fullPage: true });
    await page.click('[role=tab]:has-text("Strategy backtest")');
    await page.click('button:has-text("Run backtest")');
    await page.waitForSelector('text=Cumulative returns', { timeout: 180000 });
    await page.screenshot({ path: OUT + '/desktop-backtest.png', fullPage: true });
    await page.click('[role=tab]:has-text("All runs")');
    await page.waitForSelector('text=All backtest runs');
  });
  await step('track record page', async () => { await page.goto(base + '/track-record'); await page.waitForSelector('text=Prediction track record', { timeout: 30000 }); await page.screenshot({ path: OUT + '/desktop-track-record.png' }); });
  await step('watchlists & alerts', async () => {
    await page.goto(base + '/watchlists');
    await page.waitForSelector('text=Watchlists', { timeout: 30000 });
    await page.locator('input[placeholder^="Add ticker"]').fill('AMZN');
    await page.click('button:has-text("Add")');
    await page.waitForSelector('a:has-text("AMZN")', { timeout: 30000 });
    await page.locator('form').nth(1).locator('input').first().fill('AAPL');
    await page.locator('form').nth(1).locator('input[type=number]').first().fill('1');
    await page.click('button:has-text("Create alert")');
    await page.waitForSelector('text=/triggered immediately|Alert created/', { timeout: 30000 });
  });
  await step('system page', async () => { await page.goto(base + '/system'); await page.waitForSelector('text=Data sources in use', { timeout: 30000 }); });
  await step('theme toggle light mode', async () => { await page.goto(base + '/'); await page.click('button[aria-label="Toggle theme"]'); await page.waitForTimeout(3000); await page.screenshot({ path: OUT + '/desktop-dashboard-light.png' }); await page.click('button[aria-label="Toggle theme"]'); });

  // iPhone
  const m = await browser.newContext({ ...devices['iPhone 13'] });
  const mp = await m.newPage();
  mp.on('pageerror', e => errors.push('MOBILE PAGEERROR ' + e.message));
  await step('mobile login + dashboard', async () => {
    await mp.goto(base + '/'); await mp.fill('#email', process.env.E2E_EMAIL || 'test@example.com'); await mp.fill('#pw', process.env.E2E_PASSWORD || 'testpass123');
    await mp.click('button:has-text("Sign in")'); await mp.waitForSelector('text=Market state & regime', { timeout: 60000 });
    await mp.waitForTimeout(2000);
    await mp.screenshot({ path: OUT + '/mobile-dashboard.png' });
    const overflow = await mp.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    if (overflow) throw new Error('horizontal page overflow on mobile');
  });
  await step('mobile bottom nav + stock + drawer', async () => {
    await mp.goto(base + '/stock/AAPL'); await mp.waitForSelector('text=Price & risk', { timeout: 30000 });
    await mp.screenshot({ path: OUT + '/mobile-stock.png' });
    await mp.click('nav.fixed >> text=More'); await mp.waitForSelector('div.fixed aside >> text=Backtesting');
    await mp.click('div.fixed aside >> text=Backtesting'); await mp.waitForSelector('text=Verdict by horizon', { timeout: 30000 });
    await mp.goto(base + '/analyst'); await mp.waitForSelector('text=AI financial analyst');
    await mp.screenshot({ path: OUT + '/mobile-analyst.png' });
  });
  console.log(JSON.stringify({ results, errors: [...new Set(errors)].slice(0, 30) }, null, 1));
  await browser.close();
})();
