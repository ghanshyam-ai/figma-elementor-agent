#!/usr/bin/env node
/**
 * Capture a full-page screenshot with Playwright.
 *
 * Usage:
 *   node playwright_capture.js --url https://example.com/home --out live.png --width 1920
 */
const { chromium } = require('playwright');

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : def;
}

(async () => {
  const url = arg('url');
  const out = arg('out', 'live.png');
  const width = parseInt(arg('width', '1920'), 10);

  if (!url) {
    console.error('--url is required');
    process.exit(2);
  }

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width, height: 1080 },
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();

  console.error(`→ goto ${url}`);
  await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });

  // Hide common chrome that can perturb diffs (admin bar, cookie banners).
  await page.addStyleTag({
    content: `
      #wpadminbar, .cookie-notice, #cookie-notice, .gdpr-cookie-notice,
      .pum-overlay, .modal-backdrop { display: none !important; }
      html, body { margin: 0 !important; }
    `,
  });

  // Wait for fonts and lazy images.
  await page.evaluate(() => document.fonts && document.fonts.ready);
  await page.waitForTimeout(750);

  await page.screenshot({ path: out, fullPage: true });
  console.error(`✓ wrote ${out}`);

  await browser.close();
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
