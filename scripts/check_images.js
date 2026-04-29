const WP_URL = process.env.WP_URL;
const WP_USERNAME = process.env.WP_USERNAME;
const WP_APP_PASSWORD = process.env.WP_APP_PASSWORD;
const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');
const headers = { Authorization: `Basic ${credentials}` };

async function getPosts() {
  const res = await fetch(`${WP_URL}/wp-json/wp/v2/posts?status=publish&per_page=30&orderby=date&order=desc`, { headers });
  return res.json();
}

async function getMediaInfo(mediaId) {
  if (!mediaId) return null;
  const res = await fetch(`${WP_URL}/wp-json/wp/v2/media/${mediaId}`, { headers });
  const d = await res.json();
  return { url: d.source_url, filename: (d.source_url || '').split('/').pop(), alt: d.alt_text, title: d.title?.rendered };
}

async function main() {
  const posts = await getPosts();
  console.log(`Checking ${posts.length} posts...\n`);

  const postDetails = [];
  for (const p of posts) {
    const media = p.featured_media ? await getMediaInfo(p.featured_media) : null;
    postDetails.push({
      id: p.id, title: p.title.rendered, date: p.date.slice(0,10),
      mediaId: p.featured_media, mediaUrl: media?.url, mediaFile: media?.filename
    });
  }

  // Group by filename (same Pexels photo uploaded multiple times = same filename base)
  const byFile = {};
  for (const p of postDetails) {
    if (!p.mediaFile) continue;
    // Strip size suffixes like -300x200 to get base filename
    const base = p.mediaFile.replace(/-\d+x\d+/, '').replace(/\?.*$/, '');
    if (!byFile[base]) byFile[base] = [];
    byFile[base].push(p);
  }

  const dupes = Object.entries(byFile).filter(([, posts]) => posts.length > 1);

  if (dupes.length === 0) {
    // Also check by URL path similarity (same pexels image ID in URL)
    console.log('No exact filename dupes. Checking Pexels source IDs...\n');
    const byPexelsId = {};
    for (const p of postDetails) {
      if (!p.mediaUrl) continue;
      // Pexels URLs contain the photo ID
      const match = p.mediaUrl.match(/photos\/(\d+)\//);
      const key = match ? match[1] : p.mediaFile;
      if (!byPexelsId[key]) byPexelsId[key] = [];
      byPexelsId[key].push(p);
    }
    const pexelsDupes = Object.entries(byPexelsId).filter(([, posts]) => posts.length > 1);
    if (pexelsDupes.length > 0) {
      console.log(`Found ${pexelsDupes.length} Pexels photo(s) reused:\n`);
      for (const [id, posts] of pexelsDupes) {
        console.log(`Pexels photo ${id}:`);
        for (const p of posts) console.log(`  → [${p.id}] ${p.date} "${p.title}" | media:${p.mediaId}`);
        console.log();
      }
    } else {
      console.log('No duplicates found by Pexels ID either.');
    }
  } else {
    console.log(`Found ${dupes.length} duplicate image(s):\n`);
    for (const [file, posts] of dupes) {
      console.log(`File: ${file}`);
      for (const p of posts) console.log(`  → [${p.id}] ${p.date} "${p.title}"`);
      console.log();
    }
  }

  // Print all posts with their images for full overview
  console.log('\n--- ALL POSTS + IMAGES ---');
  for (const p of postDetails) {
    console.log(`[${p.id}] ${p.date} | media:${p.mediaId} | file:${p.mediaFile} | "${p.title.slice(0,50)}"`);
  }
}

main().catch(e => { console.error(e.message); process.exit(1); });
