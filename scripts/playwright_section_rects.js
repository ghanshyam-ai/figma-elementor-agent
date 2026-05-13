#!/usr/bin/env node
/**
 * Capture top-level Elementor section bounding rects on a live page.
 *
 * Outputs a single JSON line with:
 *   [{ data_id: "el00012", x, y, width, height }, ...]
 *
 * Top-level sections are detected by the same selector Elementor uses
 * for `elementor-top-section` (legacy) or `e-con.e-parent` (flex
 * containers in Elementor 3.16+). We deliberately filter to direct
 * children of the document body's main container so nested rows don't
 * pollute the list.
 *
 * Usage:
 *   node playwright_section_rects.js --url https://site/home --width 1920
 */
const { chromium } = require('playwright');

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : def;
}

(async () => {
  const url = arg('url');
  const width = parseInt(arg('width', '1920'), 10);
  if (!url) {
    console.error('--url is required');
    process.exit(2);
  }

  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width, height: 1080 },
      deviceScaleFactor: 2,
    });
    const page = await context.newPage();
    await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
    await page.addStyleTag({
      content: `
        #wpadminbar, .cookie-notice, #cookie-notice, .gdpr-cookie-notice,
        .pum-overlay, .modal-backdrop { display: none !important; }
        html, body { margin: 0 !important; }
      `,
    });
    try { await page.evaluate(() => document.fonts && document.fonts.ready); }
    catch (_) { /* noop */ }
    await page.waitForTimeout(750);

    const rects = await page.evaluate(() => {
      // Top-level sections: legacy `.elementor-top-section` OR flex
      // containers that are direct descendants of `.elementor`'s root.
      const out = [];
      const seen = new Set();
      const addRect = (el) => {
        const id = el.getAttribute('data-id');
        if (!id || seen.has(id)) return;
        const r = el.getBoundingClientRect();
        // Use page coordinates, not viewport — Playwright fullPage screenshot
        // includes the whole document height.
        out.push({
          data_id: id,
          x: Math.round(r.left + window.scrollX),
          y: Math.round(r.top + window.scrollY),
          width: Math.round(r.width),
          height: Math.round(r.height),
        });
        seen.add(id);
      };
      document.querySelectorAll('.elementor-top-section').forEach(addRect);
      // Flex containers — only direct children of the page wrapper.
      // Elementor wraps the page content in `.elementor[data-elementor-type]`.
      const wrappers = document.querySelectorAll('[data-elementor-type="wp-page"], .elementor');
      wrappers.forEach((wrapper) => {
        wrapper.querySelectorAll(':scope > [data-id]').forEach(addRect);
      });
      // Sort top-to-bottom for stable ordering.
      out.sort((a, b) => a.y - b.y);
      return out;
    });

    process.stdout.write(JSON.stringify(rects) + '\n');
  } finally {
    await browser.close();
  }
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
