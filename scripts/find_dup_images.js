const WP_URL = process.env.WP_URL;
const WP_USERNAME = process.env.WP_USERNAME;
const WP_APP_PASSWORD = process.env.WP_APP_PASSWORD;
const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');
const headers = { Authorization: `Basic ${credentials}` };

async function getPosts() {
  const res = await fetch(`${WP_URL}/wp-json/wp/v2/posts?status=publish&per_page=50&orderby=date&order=desc&_embed=1`, { headers });
  return res.json();
}

async function main() {
  const posts = await getPosts();
  console.log(`Scanning ${posts.length} posts for duplicate featured images (by URL)...\n`);

  const byUrl = {}; // sourceUrl → [{id,title,date,mediaId}]
  for (const p of posts) {
    const media = p._embedded && p._embedded['wp:featuredmedia'] && p._embedded['wp:featuredmedia'][0];
    const url = media && media.source_url ? media.source_url : null;
    if (!url) continue;
    // Strip -scaled / size suffixes to detect re-uploads of same source
    const baseUrl = url.replace(/-\d+x\d+(?=\.[a-z]+$)/, '').replace(/-scaled(?=\.[a-z]+$)/, '');
    if (!byUrl[baseUrl]) byUrl[baseUrl] = [];
    byUrl[baseUrl].push({
      id: p.id,
      title: p.title.rendered,
      date: p.date.slice(0,10),
      mediaId: p.featured_media,
      url
    });
  }

  // Also detect by image hash via fetching first 2KB
  const dups = Object.entries(byUrl).filter(([, arr]) => arr.length > 1);
  console.log(`URL-based duplicates: ${dups.length} group(s)\n`);
  for (const [url, arr] of dups) {
    console.log(`IMAGE: ${url}`);
    for (const p of arr) console.log(`  [${p.id}] ${p.date} "${p.title}"`);
    console.log();
  }

  // Hash-based check: download featured image (head only) and compare content-length
  console.log('\n--- Checking by image content (size+filename pattern) ---\n');
  const sizeMap = {};
  for (const p of posts) {
    const media = p._embedded && p._embedded['wp:featuredmedia'] && p._embedded['wp:featuredmedia'][0];
    if (!media || !media.source_url) continue;
    try {
      const res = await fetch(media.source_url, { method: 'HEAD' });
      const len = res.headers.get('content-length');
      const key = `${len}`;
      if (!sizeMap[key]) sizeMap[key] = [];
      sizeMap[key].push({ id: p.id, title: p.title.rendered, date: p.date.slice(0,10), url: media.source_url, len });
    } catch (e) {}
  }
  const sizeDups = Object.entries(sizeMap).filter(([, arr]) => arr.length > 1);
  console.log(`Same content-length groups: ${sizeDups.length}\n`);
  for (const [len, arr] of sizeDups) {
    console.log(`SIZE: ${len} bytes`);
    for (const p of arr) console.log(`  [${p.id}] ${p.date} "${p.title}" → ${p.url}`);
    console.log();
  }
}

main().catch(e => { console.error(e.message); process.exit(1); });
