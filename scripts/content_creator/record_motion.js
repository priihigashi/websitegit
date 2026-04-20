#!/usr/bin/env node
/**
 * record_motion.js — Record a single-slide motion HTML as a video using Playwright.
 * The HTML must show ONE slide at full 1080x1350. Videos autoplay, Ken Burns CSS runs.
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

  const browser = await chromium.launch({ headless: true, channel: 'chrome' });

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
  await page.goto(`file://${abs}`, { waitUntil: 'networkidle' });

  // Wait for fonts + CSS animations to start, then let the clip play
  await page.waitForTimeout(500);  // font load buffer
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
