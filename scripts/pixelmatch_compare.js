#!/usr/bin/env node
/**
 * Pixel-level diff with region scoring.
 *
 * Both inputs are resized so widths match (using sharp). Heights are aligned
 * by clipping the longer image. The diff PNG marks differing pixels in red.
 *
 * Output: a single JSON line on stdout with:
 *   { width, height, totalPixels, diffPixels, drift, regions: [{y0, y1, drift}] }
 *
 * Usage:
 *   node pixelmatch_compare.js --live live.png --expected expected.png --diff diff.png
 */
const fs = require('fs');
const { PNG } = require('pngjs');
const pixelmatch = require('pixelmatch');
const sharp = require('sharp');

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : def;
}

async function loadAndAlign(livePath, expectedPath) {
  const liveMeta = await sharp(livePath).metadata();
  const targetWidth = liveMeta.width;

  const liveBuf = await sharp(livePath).raw().ensureAlpha().toBuffer({ resolveWithObject: true });
  const expectedBuf = await sharp(expectedPath)
    .resize({ width: targetWidth, fit: 'cover', position: 'top' })
    .raw()
    .ensureAlpha()
    .toBuffer({ resolveWithObject: true });

  const height = Math.min(liveBuf.info.height, expectedBuf.info.height);
  const sliceLen = targetWidth * height * 4;

  return {
    width: targetWidth,
    height,
    live: liveBuf.data.slice(0, sliceLen),
    expected: expectedBuf.data.slice(0, sliceLen),
  };
}

(async () => {
  const livePath = arg('live');
  const expectedPath = arg('expected');
  const diffPath = arg('diff', 'diff.png');
  const regionRows = parseInt(arg('regions', '20'), 10);

  if (!livePath || !expectedPath) {
    console.error('--live and --expected are required');
    process.exit(2);
  }

  const { width, height, live, expected } = await loadAndAlign(livePath, expectedPath);
  const diff = Buffer.alloc(width * height * 4);

  const diffPixels = pixelmatch(live, expected, diff, width, height, {
    threshold: 0.1,
    includeAA: true,
    alpha: 0.4,
    diffColor: [255, 0, 0],
  });

  // Write diff PNG
  const diffPng = new PNG({ width, height });
  diff.copy(diffPng.data);
  await new Promise((resolve, reject) => {
    diffPng.pack().pipe(fs.createWriteStream(diffPath)).on('finish', resolve).on('error', reject);
  });

  // Region-level drift (vertical bands)
  const bandHeight = Math.max(1, Math.floor(height / regionRows));
  const regions = [];
  for (let band = 0; band < regionRows; band++) {
    const y0 = band * bandHeight;
    const y1 = band === regionRows - 1 ? height : (band + 1) * bandHeight;
    const start = y0 * width * 4;
    const end = y1 * width * 4;
    const liveSlice = live.slice(start, end);
    const expSlice = expected.slice(start, end);
    const tmp = Buffer.alloc(end - start);
    const px = pixelmatch(liveSlice, expSlice, tmp, width, y1 - y0, { threshold: 0.1 });
    regions.push({ y0, y1, drift: px / (width * (y1 - y0)) });
  }

  const totalPixels = width * height;
  const drift = diffPixels / totalPixels;
  const out = { width, height, totalPixels, diffPixels, drift, regions };
  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
