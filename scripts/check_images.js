const WP_URL = process.env.WP_URL;
const WP_USERNAME = process.env.WP_USERNAME;
const WP_APP_PASSWORD = process.env.WP_APP_PASSWORD;
const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');
const headers = { Authorization: `Basic ${credentials}` };

async function getPosts() {
  const res = await fetch(`${WP_URL}/wp-json/wp/v2/posts?status=publish&per_page=30&orderby=date&order=desc`, { headers });
  return res.json();
}

async function getMediaUrl(mediaId) {
  if (!mediaId) return null;
  const res = await fetch(`${WP_URL}/wp-json/wp/v2/media/${mediaId}`, { headers });
  const d = await res.json();
  return d.source_url || null;
}

async function main() {
  const posts = await getPosts();
  console.log(`Checking ${posts.length} published posts for duplicate images...\n`);

  const imageMap = {}; // mediaId → [post titles]
  const postDetails = [];

  for (const p of posts) {
    const mediaId = p.featured_media || null;
    postDetails.push({ id: p.id, title: p.title.rendered, mediaId, date: p.date.slice(0,10), link: p.link });
    if (mediaId) {
      if (!imageMap[mediaId]) imageMap[mediaId] = [];
      imageMap[mediaId].push({ id: p.id, title: p.title.rendered, date: p.date.slice(0,10) });
    }
  }

  // Find duplicates
  const duplicateGroups = Object.entries(imageMap).filter(([, posts]) => posts.length > 1);

  if (duplicateGroups.length === 0) {
    console.log('No duplicate images found.');
    return;
  }

  console.log(`Found ${duplicateGroups.length} image(s) shared across multiple posts:\n`);
  for (const [mediaId, posts] of duplicateGroups) {
    const url = await getMediaUrl(parseInt(mediaId));
    const filename = url ? url.split('/').pop() : '?';
    console.log(`IMAGE [${mediaId}] ${filename}`);
    for (const p of posts) {
      console.log(`  → [${p.id}] ${p.date} "${p.title}"`);
    }
    console.log();
  }
}

main().catch(e => { console.error(e.message); process.exit(1); });
