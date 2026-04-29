const WP_URL = process.env.WP_URL;
const WP_USERNAME = process.env.WP_USERNAME;
const WP_APP_PASSWORD = process.env.WP_APP_PASSWORD;
const PEXELS_API_KEY = process.env.PEXELS_API_KEY;
const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');
const wpHeaders = { Authorization: `Basic ${credentials}` };

// The 4 newer posts that need a fresh unique image
const FIXES = [
  { postId: 6665, title: 'Remodeling Contractor Fort Lauderdale', query: 'home remodeling contractor Florida interior renovation' },
  { postId: 6652, title: 'Construction Financing South Florida', query: 'construction loan financing real estate South Florida' },
  { postId: 6649, title: 'Concrete Pouring Mistakes Florida', query: 'concrete pouring construction worker Florida outdoor' },
  { postId: 6646, title: 'Concrete Slab Contractor South Florida', query: 'concrete slab foundation construction outdoor Florida' },
];

async function searchPexels(query) {
  const res = await fetch(`https://api.pexels.com/v1/search?query=${encodeURIComponent(query)}&per_page=5&orientation=landscape`, {
    headers: { Authorization: PEXELS_API_KEY }
  });
  const data = await res.json();
  // Skip page 1 photo (take photo #2 to ensure it's different from what was likely used before)
  const photos = data.photos || [];
  return photos[1] || photos[0] || null;
}

async function downloadImage(url) {
  const res = await fetch(url);
  const buffer = await res.arrayBuffer();
  return Buffer.from(buffer);
}

async function uploadToWP(imageBuffer, filename, mimeType = 'image/jpeg') {
  const res = await fetch(`${WP_URL}/wp-json/wp/v2/media`, {
    method: 'POST',
    headers: {
      ...wpHeaders,
      'Content-Disposition': `attachment; filename="${filename}"`,
      'Content-Type': mimeType,
    },
    body: imageBuffer,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(`Upload failed: ${JSON.stringify(data)}`);
  return data.id;
}

async function setFeaturedImage(postId, mediaId) {
  const res = await fetch(`${WP_URL}/wp-json/wp/v2/posts/${postId}`, {
    method: 'POST',
    headers: { ...wpHeaders, 'Content-Type': 'application/json' },
    body: JSON.stringify({ featured_media: mediaId }),
  });
  if (!res.ok) throw new Error(`Set featured image failed: ${await res.text()}`);
  return true;
}

async function main() {
  for (const fix of FIXES) {
    try {
      console.log(`\nProcessing [${fix.postId}] "${fix.title}"...`);
      
      const photo = await searchPexels(fix.query);
      if (!photo) throw new Error('No Pexels result');
      console.log(`  Pexels photo ${photo.id}: ${photo.src.landscape}`);
      
      const imgBuffer = await downloadImage(photo.src.large2x || photo.src.large);
      const filename = `blog-${fix.postId}-featured.jpg`;
      const mediaId = await uploadToWP(imgBuffer, filename);
      console.log(`  Uploaded → media ID ${mediaId}`);
      
      await setFeaturedImage(fix.postId, mediaId);
      console.log(`  ✅ Updated post ${fix.postId} featured image`);
    } catch (e) {
      console.log(`  ❌ Failed [${fix.postId}]: ${e.message}`);
    }
  }
  console.log('\nDone.');
}

main().catch(e => { console.error(e.message); process.exit(1); });
