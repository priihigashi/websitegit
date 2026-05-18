#!/usr/bin/env node
/**
 * record_motion.js — Record a single-slide motion HTML as a video using Playwright.
 * The HTML must show ONE slide at full 1080x1350. Videos autoplay when present.
 * Output: a .webm file at the given output path (caller converts to mp4 + gif via ffmpeg).
 * Usage: node record_motion.js <input.html> <output.webm> [duration_seconds=5]
 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

async function run(htmlPath, outputPath, duration) {
  const abs = path.resolve(htmlPath);
  if (!fs.existsSync(abs)) {
    console.error(`Not found: ${abs}`);
    process.exit(1);
  }

  const outputDir = path.dirname(path.resolve(outputPath));
  fs.mkdirSync(outputDir, { recursive: true });

  const browser = await chromium.launch({ headless: true });

  // Playwright recordVideo writes to a dir with a generated filename — we rename after
  const videoDir = path.join(outputDir, `_rec_${Date.now()}`);
  fs.mkdirSync(videoDir, { recursive: true });

  const context = await browser.newContext({
    viewport: { width: 1080, height: 1350 },
    recordVideo: {
      dir: videoDir,
      size: { width: 1080, height: 1350 },
    },
  });

  const page = await context.newPage();
  // networkidle can hang forever when <video autoplay> keeps connections open; use domcontentloaded + explicit wait
  await page.goto(`file://${abs}`, { waitUntil: 'domcontentloaded', timeout: 15000 });

  // Wait for fonts, <img> tags, inline background-image URLs, AND any video element.
  // Without the bg-image and font waits, the first recorded frame can show a flash
  // where Fraunces fell back to a system serif AND the kb-bg photo hadn't decoded —
  // producing a half-black slide with reflowed text. Priscila saw this in 2026-05-18 proof.
  await page.evaluate(async () => {
    if (document.fonts && document.fonts.ready) {
      await document.fonts.ready;
    }
    const imgs = Array.from(document.images).filter(im => !im.complete);
    await Promise.all(imgs.map(im => new Promise(res => {
      im.addEventListener('load', res, { once: true });
      im.addEventListener('error', res, { once: true });
      setTimeout(res, 3000);
    })));
    const bgEls = Array.from(document.querySelectorAll('[style*="background-image"]'));
    await Promise.all(bgEls.map(el => {
      const m = el.style.backgroundImage.match(/url\(["']?(.+?)["']?\)/);
      if (!m) return Promise.resolve();
      return new Promise(res => {
        const probe = new Image();
        probe.onload = probe.onerror = res;
        probe.src = m[1];
        setTimeout(res, 3000);
      });
    }));
    const videos = Array.from(document.querySelectorAll('video'));
    await Promise.all(videos.map(video => {
      if (video.readyState >= 3) return Promise.resolve();
      return new Promise(resolve => {
        const done = () => resolve();
        video.addEventListener('canplay', done, { once: true });
        video.addEventListener('loadeddata', done, { once: true });
        setTimeout(done, 2500);
      });
    }));
  });
  await page.waitForTimeout(500);  // settle font/photo/video composite — 200ms was too short
  await page.waitForTimeout(duration * 1000);  // animation duration

  await context.close();  // finalizes the video file
  await browser.close();

  // Find the generated webm and rename to expected output path
  const files = fs.readdirSync(videoDir).filter(f => f.endsWith('.webm'));
  if (!files.length) {
    console.error('Playwright produced no video file');
    process.exit(1);
  }

  const src = path.join(videoDir, files[0]);
  fs.renameSync(src, path.resolve(outputPath));
  fs.rmdirSync(videoDir, { recursive: true });

  const size = fs.statSync(path.resolve(outputPath)).size;
  console.log(`Recorded: ${path.basename(outputPath)} (${Math.round(size / 1024)}KB)`);
}

const [, , input, output, dur] = process.argv;
if (!input || !output) {
  console.log('Usage: node record_motion.js <input.html> <output.webm> [duration_s]');
  process.exit(1);
}
run(input, output, parseInt(dur || '5', 10)).catch(e => {
  console.error(e);
  process.exit(1);
});
