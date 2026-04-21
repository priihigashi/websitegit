/**
 * export_proof.js — Proof-post Playwright renderer.
 * Renders each .slide div in the HTML as a separate PNG (1080×1350).
 * Output naming: proof_NN.png (no color variant system — proof posts are photo-only).
 * Usage: node export_proof.js <html_path> <output_dir>
 */
const { chromium } = require('playwright');
const path = require('path');
const fs   = require('fs');

async function main() {
    const htmlPath = process.argv[2];
    const outDir   = process.argv[3] || '/tmp/proof_export';

    if (!htmlPath) {
        console.error('Usage: node export_proof.js <html_path> <output_dir>');
        process.exit(1);
    }
    if (!fs.existsSync(htmlPath)) {
        console.error(`HTML file not found: ${htmlPath}`);
        process.exit(1);
    }

    fs.mkdirSync(outDir, { recursive: true });

    const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-setuid-sandbox'] });
    const page    = await browser.newPage();

    // Load with minimal viewport first to count slides
    await page.setViewportSize({ width: 1080, height: 1350 });

    // file:// protocol so base64 data-URI images render correctly
    const fileUrl = 'file://' + path.resolve(htmlPath);
    await page.goto(fileUrl, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(300);

    const slideCount = await page.$$eval('.slide', els => els.length);
    if (slideCount === 0) {
        console.error('No .slide elements found in HTML');
        await browser.close();
        process.exit(1);
    }

    // Resize viewport to fit all slides so clip coordinates are within bounds
    const totalHeight = slideCount * 1350;
    await page.setViewportSize({ width: 1080, height: totalHeight });
    await page.waitForTimeout(150);

    console.log(`Found ${slideCount} slide(s)`);

    const slides = await page.$$('.slide');
    for (let i = 0; i < slides.length; i++) {
        const nn  = String(i + 1).padStart(2, '0');
        const name = `proof_${nn}.png`;
        const out  = path.join(outDir, name);

        // Clip by computed position (slides stack vertically at i * 1350)
        await page.screenshot({
            path: out,
            clip: { x: 0, y: i * 1350, width: 1080, height: 1350 },
        });

        const size = fs.statSync(out).size;
        if (size < 10000) {
            console.warn(`  WARNING: proof_${nn}.png is suspiciously small (${size} bytes) — may be blank`);
        } else {
            console.log(`  proof_${nn}.png — ${Math.round(size / 1024)}KB`);
        }
    }

    await browser.close();
    console.log(`Done — ${slides.length} slides rendered to ${outDir}`);
}

main().catch(e => {
    console.error('export_proof.js failed:', e.message);
    process.exit(1);
});
