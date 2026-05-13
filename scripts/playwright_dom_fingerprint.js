#!/usr/bin/env node
/**
 * Capture per-section structural + text fingerprints from a live Elementor
 * page. Output is consumed by scripts/dom_diff.py to complement the
 * pixel-based visual diff.
 *
 * Fingerprint shape:
 *   { sections: [
 *       { data_id, kind_path: ["container","heading",...],
 *         text_hashes: [sha1[:12],...], depth_max: int }
 *     ] }
 *
 * `kind_path` walks each top-level section depth-first, recording the
 * Elementor widget type (data-widget_type attribute) or "container" for
 * flex containers. `text_hashes` are normalized + hashed text strings
 * found inside the section. We deliberately keep both lists order-
 * preserving here; the Python differ may compare them as multisets.
 *
 * Usage:
 *   node playwright_dom_fingerprint.js --url https://site/home --width 1920
 */
const { chromium } = require('playwright');
const crypto = require('crypto');

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : def;
}

function normText(t) {
  if (!t) return '';
  return String(t)
    .replace(/ /g, ' ')
    .replace(/[^\w\s]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
}

function hashText(t) {
  return crypto.createHash('sha1').update(normText(t)).digest('hex').slice(0, 12);
}

(async () => {
  const url = arg('url');
  const width = parseInt(arg('width', '1920'), 10);
  const timeoutMs = parseInt(arg('timeout', '60000'), 10);
  if (!url) { console.error('--url is required'); process.exit(2); }

  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({ viewport: { width, height: 1080 } });
    const page = await ctx.newPage();
    await page.goto(url, { waitUntil: 'networkidle', timeout: timeoutMs });
    await page.addStyleTag({
      content: `
        #wpadminbar, .cookie-notice, #cookie-notice, .gdpr-cookie-notice,
        .pum-overlay, .modal-backdrop { display: none !important; }
      `,
    });
    try { await page.evaluate(() => document.fonts && document.fonts.ready); } catch (_) {}
    await page.waitForTimeout(500);

    const payload = await page.evaluate(() => {
      const widgetKind = (el) => {
        // Elementor widgets carry `data-widget_type="heading.default"` etc.
        // Containers carry `data-element_type="container"`.
        const wt = el.getAttribute('data-widget_type');
        if (wt) return wt.split('.')[0];
        const et = el.getAttribute('data-element_type');
        if (et === 'container') return 'container';
        if (et === 'section') return 'section';
        if (et === 'column') return 'column';
        return et || 'unknown';
      };

      // Text extraction: visible text only. Skip script/style.
      const textOf = (el) => {
        // Use the widget's own innerText (cheap heuristic; works for
        // headings, paragraphs, buttons). We DON'T walk into nested
        // widget descendants — they'll be picked up individually.
        const t = (el.innerText || '').trim();
        return t;
      };

      const out = [];
      // Top-level sections — same selector strategy as
      // playwright_section_rects.js.
      const seen = new Set();
      const wrappers = document.querySelectorAll(
        '[data-elementor-type="wp-page"], [data-elementor-type="header"], ' +
        '[data-elementor-type="footer"], .elementor'
      );

      const collectSection = (sectionEl) => {
        const sid = sectionEl.getAttribute('data-id');
        if (!sid || seen.has(sid)) return;
        seen.add(sid);
        const kindPath = [];
        const texts = [];
        let maxDepth = 0;
        const walk = (node, depth) => {
          if (!(node instanceof Element)) return;
          // Skip Elementor's own animation wrappers
          if (node.classList.contains('elementor-element-overlay')) return;
          // Record this node's kind if it's an elementor element/widget.
          if (node.hasAttribute('data-id') || node.hasAttribute('data-element_type')) {
            kindPath.push(widgetKind(node));
            if (depth > maxDepth) maxDepth = depth;
          }
          // Collect text only at widget leaves (data-widget_type set).
          if (node.hasAttribute('data-widget_type')) {
            const t = textOf(node);
            if (t) texts.push(t);
          }
          for (const child of node.children) walk(child, depth + 1);
        };
        walk(sectionEl, 0);
        out.push({
          data_id: sid,
          kind_path: kindPath,
          texts: texts,
          depth_max: maxDepth,
        });
      };

      wrappers.forEach((wrap) => {
        wrap.querySelectorAll(':scope > [data-id]').forEach(collectSection);
      });
      // Also pick up legacy `.elementor-top-section` (older Elementor).
      document.querySelectorAll('.elementor-top-section').forEach(collectSection);

      return { sections: out, captured_at: new Date().toISOString() };
    });

    // Hash texts on the Node side so the Python differ doesn't need to
    // re-normalize the same strings.
    for (const sec of payload.sections) {
      sec.text_hashes = (sec.texts || []).map(hashText);
      delete sec.texts;
    }

    process.stdout.write(JSON.stringify(payload) + '\n');
  } finally {
    await browser.close();
  }
})().catch((err) => { console.error(err); process.exit(1); });
