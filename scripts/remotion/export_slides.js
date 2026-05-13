#!/usr/bin/env node
/**
 * export_slides.js — minimal in-repo screenshotter for clipmine carousels.
 *
 * Used by scripts/research/evidence_carousel.py (Phase 3 render) when the
 * external Content Templates exporter is unavailable (e.g. inside CI).
 *
 * Usage:
 *   node scripts/remotion/export_slides.js <input.html> <output_dir>
 *
 * Each `.slide` element in the page is captured to its own PNG. Naming:
 *   01_<kind>.png  02_<kind>.png  ...
 * where <kind> = data-kind attribute when present, or "slide".
 *
 * Differences from scripts/content_creator/export_variants.js:
 *   - No channel: 'chrome' (works with bundled `playwright install chromium`)
 *   - No COLOR_MAP — clipmine carousel has no v1/v2/v3 variants
 *   - Larger viewport so 1080x1350 .slide elements render in full
 */

const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

async function run(htmlPath, outputDir) {
  const abs = path.resolve(htmlPath);
  if (!fs.existsSync(abs)) {
    console.error(`Not found: ${abs}`);
    process.exit(1);
  }
  fs.mkdirSync(outputDir, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1200, height: 1500 },
    deviceScaleFactor: 1,
  });
  await page.goto(`file://${abs}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);

  // NN-S11: auto-shrink overflow text before any screenshot.
  await page.evaluate(() => {
    const TEXT_SELECTORS = [
      '.headline', '.headline-main', '.headline-italic',
      '.tip-big', '.src-head', '.stat-big',
      '.slide-body', '.body', '.caption-text', '.hook-text',
      '.cover-title', '.cover-subtitle', '.slide-title', '.slide-text',
      'h1', 'h2', 'h3',
    ];
    // Per-selector floors: headlines must stay readable even after shrink.
    const MIN_FS_MAP = {
      '.headline': 48, '.headline-main': 48, '.headline-italic': 32,
      '.tip-big': 40, '.src-head': 28, '.stat-big': 48,
      '.cover-title': 40, '.cover-subtitle': 24,
      '.slide-title': 32, '.slide-text': 16,
      'h1': 48, 'h2': 36, 'h3': 28,
    };
    const DEFAULT_MIN_FS = 16;
    TEXT_SELECTORS.forEach(sel => {
      const minFs = MIN_FS_MAP[sel] !== undefined ? MIN_FS_MAP[sel] : DEFAULT_MIN_FS;
      document.querySelectorAll(sel).forEach(el => {
        if (el.scrollHeight <= el.clientHeight && el.scrollWidth <= el.clientWidth) return;
        let fs = parseFloat(window.getComputedStyle(el).fontSize) || 32;
        const startFs = fs;
        while ((el.scrollHeight > el.clientHeight || el.scrollWidth > el.clientWidth) && fs > minFs) {
          fs -= 2;
          el.style.fontSize = fs + 'px';
          el.style.lineHeight = '1.1';
        }
        if (fs < startFs) {
          console.log(`[auto-shrink] ${sel}: ${startFs.toFixed(0)}px → ${fs.toFixed(0)}px (floor ${minFs}px)`);
        }
        if ((el.scrollHeight > el.clientHeight || el.scrollWidth > el.clientWidth) && fs <= minFs) {
          console.error(`[BLOCK] ${sel} still overflows at floor ${minFs}px — text too long for container. Do not approve.`);
        }
      });
    });
  });

  const slides = page.locator('.slide');
  const n = await slides.count();
  console.log(`${n} slides found in ${path.basename(abs)}`);

  let written = 0;
  for (let i = 0; i < n; i++) {
    const el = slides.nth(i);
    const kind = (await el.getAttribute('data-kind')) || 'slide';
    const nn = String(i + 1).padStart(2, '0');
    const out = path.join(outputDir, `${nn}_${kind}.png`);
    await el.screenshot({ path: out, type: 'png' });
    const size = fs.statSync(out).size;
    console.log(`  ${nn} ${kind} -> ${path.basename(out)} (${(size / 1024).toFixed(1)} KB)`);
    if (size < 5_000) {
      console.warn(`  ! WARN: ${path.basename(out)} is ${size}B (likely blank)`);
    }
    written++;
  }
  await browser.close();
  if (written === 0) {
    console.error('No .slide elements found.');
    process.exit(2);
  }
}

const [, , htmlPath, outputDir] = process.argv;
if (!htmlPath || !outputDir) {
  console.error('Usage: node export_slides.js <input.html> <output_dir>');
  process.exit(1);
}

run(htmlPath, outputDir).catch((err) => {
  console.error(err);
  process.exit(1);
});
