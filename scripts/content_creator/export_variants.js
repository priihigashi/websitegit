#!/usr/bin/env node
/**
 * export_variants.js — Screenshot each .slide element as its own PNG.
 * Usage: node export_variants.js <input.html> <output_dir>
 * Each .slide gets its label (v1/v2/v3) or index captured as filename.
 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

async function run(htmlPath, outputDir) {
  const abs = path.resolve(htmlPath);
  if (!fs.existsSync(abs)) { console.error(`Not found: ${abs}`); process.exit(1); }
  fs.mkdirSync(outputDir, { recursive: true });
  const base = path.basename(abs, '.html');

  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox', '--allow-file-access-from-files'] });
  const page = await browser.newPage({ viewport: { width: 1200, height: 1400 } });
  await page.goto(`file://${abs}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1800);

  // NN-S11: auto-shrink overflow text before any screenshot.
  // Shrinks font-size on elements whose text clips inside overflow:hidden containers.
  // Text selectors cover all carousel templates (OPC + Brazil + USA).
  await page.evaluate(() => {
    const TEXT_SELECTORS = [
      '.headline', '.headline-main', '.headline-italic',
      '.tip-big', '.src-head', '.stat-big',
      '.slide-body', '.body', '.caption-text', '.hook-text',
      '.cover-title', '.cover-subtitle', '.slide-title', '.slide-text',
      'h1', 'h2', 'h3',
    ];
    // Per-selector floors: headlines must stay readable even after shrink.
    // Global 14px floor caused titles to go microscopic on long headlines.
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
      });
    });
  });

  const slides = page.locator('.slide');
  const n = await slides.count();
  console.log(`${n} slides in ${base}`);

  // Naming convention: <color>_<NN>_<slide-name>_<tool>.png (see project_png_naming_convention.md)
  // Color mapping — update per niche / brand when adding more variants
  const COLOR_MAP = {
    v1: 'black',   // Brazil: obsidian
    v2: 'cream',   // Brazil: paper
    v3: 'blue',    // USA archive blue OR OPC lime (override per-project if needed)
  };
  const tool = 'html';
  // Track per-color slide index so numbering restarts per variant set (black_01, black_02... cream_01...)
  const counters = {};
  for (let i = 0; i < n; i++) {
    const el = slides.nth(i);
    const cls = await el.getAttribute('class') || '';
    const variantKey = (cls.match(/\bv\d+\b/) || ['v1'])[0];
    const color = COLOR_MAP[variantKey] || variantKey;
    const slideType = (cls.match(/slide-([a-z0-9]+)/) || [null, String(i+1).padStart(2,'0')])[1];
    counters[color] = (counters[color] || 0) + 1;
    const nn = String(counters[color]).padStart(2, '0');
    const out = path.join(outputDir, `${color}_${nn}_${slideType}_${tool}.png`);
    await el.screenshot({ path: out, type: 'png' });
    const size = fs.statSync(out).size;
    console.log(`  ${color} ${nn} ${slideType} → ${path.basename(out)} (${(size/1024).toFixed(1)} KB)`);
  }

  await browser.close();
}

const [, , input, output] = process.argv;
if (!input || !output) { console.log('Usage: node export_variants.js <input.html> <output_dir>'); process.exit(1); }
run(input, output);
