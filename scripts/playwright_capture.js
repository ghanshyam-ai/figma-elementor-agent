#!/usr/bin/env node
/**
 * Capture full-page screenshots with Playwright.
 *
 * Single-viewport (legacy):
 *   node playwright_capture.js --url https://example.com/home --out live.png --width 1920
 *
 * Multi-viewport — writes <out>.desktop.png / .tablet.png / .mobile.png:
 *   node playwright_capture.js --url https://example.com/home --out live.png --viewports default
 *   node playwright_capture.js --url ... --out live.png \
 *        --viewports '[{"name":"desktop","width":1920,"height":1080},
 *                       {"name":"tablet","width":768,"height":1024},
 *                       {"name":"mobile","width":375,"height":812}]'
 */
const { chromium } = require('playwright');
const path = require('path');

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : def;
}

const DEFAULT_VIEWPORTS = [
  { name: 'desktop', width: 1920, height: 1080 },
  { name: 'tablet',  width: 768,  height: 1024 },
  { name: 'mobile',  width: 375,  height: 812 },
];

function parseViewports(raw, fallbackWidth) {
  if (!raw || raw === 'default') return DEFAULT_VIEWPORTS;
  if (raw === 'desktop-only') {
    return [{ name: 'desktop', width: fallbackWidth, height: 1080 }];
  }
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error('viewports JSON must be an array');
    return parsed.map((vp, i) => ({
      name: String(vp.name || `vp${i}`),
      width: parseInt(vp.width, 10),
      height: parseInt(vp.height || 1080, 10),
    }));
  } catch (err) {
    console.error(`Could not parse --viewports JSON (${err.message}); using default set.`);
    return DEFAULT_VIEWPORTS;
  }
}

function outPathFor(base, suffix) {
  if (!suffix) return base;
  const ext = path.extname(base);
  const stem = base.slice(0, base.length - ext.length);
  return `${stem}.${suffix}${ext}`;
}

(async () => {
  const url = arg('url');
  const out = arg('out', 'live.png');
  const width = parseInt(arg('width', '1920'), 10);
  const viewportsRaw = arg('viewports');

  if (!url) {
    console.error('--url is required');
    process.exit(2);
  }

  // Backwards compatibility: when no --viewports flag was passed, just
  // run the legacy single-shot capture at --width.
  const viewports = viewportsRaw
    ? parseViewports(viewportsRaw, width)
    : [{ name: '', width, height: 1080 }];

  const browser = await chromium.launch({ headless: true });
  try {
    for (const vp of viewports) {
      const context = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        deviceScaleFactor: 2,
        isMobile: vp.width <= 480,
      });
      const page = await context.newPage();
      const targetPath = outPathFor(out, vp.name);
      console.error(`→ goto ${url} @ ${vp.width}x${vp.height} (${vp.name || 'single'})`);
      await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });

      // Hide chrome that perturbs diffs.
      await page.addStyleTag({
        content: `
          #wpadminbar, .cookie-notice, #cookie-notice, .gdpr-cookie-notice,
          .pum-overlay, .modal-backdrop { display: none !important; }
          html, body { margin: 0 !important; }
        `,
      });

      try {
        await page.evaluate(() => document.fonts && document.fonts.ready);
      } catch (_) { /* noop */ }
      await page.waitForTimeout(750);

      await page.screenshot({ path: targetPath, fullPage: true });
      console.error(`✓ wrote ${targetPath}`);
      await context.close();
    }
  } finally {
    await browser.close();
  }
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
