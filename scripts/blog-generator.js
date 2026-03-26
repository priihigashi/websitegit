const Anthropic = require('@anthropic-ai/sdk');
const fs = require('fs');
const { getRandomTopic } = require('./topics.js');

const client = new Anthropic();

// ─── Configuration ────────────────────────────────────────────────────────────
const WP_URL = process.env.WP_URL;
const WP_USERNAME = process.env.WP_USERNAME;
const WP_APP_PASSWORD = process.env.WP_APP_PASSWORD;
const PEXELS_API_KEY = process.env.PEXELS_API_KEY || '';
const MANUAL_TOPIC = process.env.MANUAL_TOPIC || '';

// ─── Step 1: Pick a topic ─────────────────────────────────────────────────────
const topic = MANUAL_TOPIC.trim() || getRandomTopic();
console.log(`Topic selected: ${topic}`);

// ─── Step 2: Generate blog post with Claude ───────────────────────────────────
async function generatePost(topic) {
  console.log('Calling Claude API...');

  const message = await client.messages.create({
    model: 'claude-opus-4-6',
    max_tokens: 3000,
    messages: [
      {
        role: 'user',
        content: `You are an expert SEO content writer for Oak Park Construction, a licensed general contractor based in South Florida serving Broward County and surrounding areas including Fort Lauderdale, Hollywood, Pompano Beach, Dania Beach, and Miramar. Despite the name, Oak Park Construction is NOT in Oak Park Illinois — they are a South Florida company. They specialize in residential construction, commercial construction, renovation, new additions, shell construction, and concrete construction.

The user suggested this topic as a direction: "${topic}"
Use it as inspiration but write the best possible SEO post for our South Florida audience. You can adjust the angle, title, and focus to maximize SEO value — don't follow the topic wording exactly if a better version exists.

SEO REQUIREMENTS (target score 80+ on AIOSEO):
- Choose ONE clear focus keyword phrase (3-5 words) that people in South Florida actually search
- Use that focus keyword in: the title, first paragraph (within first 100 words), at least 2 H2/H3 headers, meta description, and 4-6 times naturally in body
- Title: under 60 characters, focus keyword near the beginning, compelling and clickable
- Meta description: EXACTLY 150-160 characters — count carefully. Must include focus keyword and end with a call to action like "Call Oak Park Construction today" or "Get a free estimate"
- Headers: H2 for main sections (3-4 of them), H3 for subsections — keywords in at least 2 headers
- Content length: 1100-1300 words (longer = better ranking)
- Include at least one bulleted or numbered list (boosts featured snippet chances)
- Local SEO: mention South Florida, Broward County, and at least 2 specific cities (Fort Lauderdale, Hollywood, Pompano Beach, Dania Beach, or Miramar) naturally throughout
- Internal link opportunity: mention "contact Oak Park Construction" with context at least twice
- Also return a focus_keyword field with the exact keyword phrase you chose
- Also return image_search_query (3-5 words) for the ideal featured photo

CONTENT REQUIREMENTS:
- Format: HTML only — h2, h3, p, ul, ol, li tags. NO html/head/body tags
- Tone: professional, trustworthy, knowledgeable — like expert advice from a seasoned South Florida contractor
- Structure: strong intro with keyword → 3-4 detailed sections → conclusion with clear CTA
- End with a strong call to action paragraph to contact Oak Park Construction for a free consultation

Return ONLY this exact JSON (no markdown fences, no other text):
{
  "title": "Under 60 char title with focus keyword",
  "focus_keyword": "the exact 3-5 word focus keyword phrase",
  "meta_description": "EXACTLY 150-160 characters including focus keyword and CTA",
  "image_search_query": "3-5 word photo search",
  "html_content": "<h2>...</h2><p>...</p>"
}`
      }
    ]
  });

  let raw = message.content[0].text.trim();
  raw = raw.replace(/^```[a-z]*\n?/i, '').replace(/```$/, '').trim();

  const post = JSON.parse(raw);
  console.log(`Post generated: "${post.title}"`);
  console.log(`Meta description: ${post.meta_description.length} chars`);
  console.log(`Focus keyword: "${post.focus_keyword}"`);
  console.log(`Image search query: "${post.image_search_query}"`);
  return post;
}

// ─── Step 3: Fetch featured image from Pexels ────────────────────────────────
async function fetchFeaturedImage(query) {
  if (!PEXELS_API_KEY) {
    console.log('No Pexels API key — skipping featured image.');
    return null;
  }
  console.log(`Searching Pexels for: "${query}"...`);
  const res = await fetch(
    `https://api.pexels.com/v1/search?query=${encodeURIComponent(query)}&per_page=1&orientation=landscape`,
    { headers: { Authorization: PEXELS_API_KEY } }
  );
  if (!res.ok) { console.log('Pexels search failed, skipping image.'); return null; }
  const data = await res.json();
  if (!data.photos || data.photos.length === 0) { console.log('No Pexels results, skipping image.'); return null; }
  const photo = data.photos[0];
  console.log(`Found image: ${photo.src.large}`);
  return { url: photo.src.large, photographer: photo.photographer, alt: query };
}

// ─── Step 4: Upload image to WordPress media library ─────────────────────────
async function uploadImageToWordPress(imageInfo) {
  if (!imageInfo) return null;
  console.log('Downloading and uploading image to WordPress...');
  const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');

  const imgRes = await fetch(imageInfo.url);
  if (!imgRes.ok) { console.log('Failed to download image.'); return null; }
  const buffer = await imgRes.arrayBuffer();

  const uploadRes = await fetch(`${WP_URL}/wp-json/wp/v2/media`, {
    method: 'POST',
    headers: {
      Authorization: `Basic ${credentials}`,
      'Content-Disposition': `attachment; filename="featured-image.jpg"`,
      'Content-Type': 'image/jpeg',
    },
    body: buffer,
  });
  if (!uploadRes.ok) { console.log('Image upload failed, skipping.'); return null; }
  const media = await uploadRes.json();
  console.log(`Image uploaded to WordPress, media ID: ${media.id}`);
  return media.id;
}

// ─── Step 5: Post to WordPress as Draft ──────────────────────────────────────
async function postToWordPress(post, featuredMediaId) {
  console.log('Posting to WordPress...');

  const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');

  const body = {
    title: post.title,
    content: post.html_content,
    excerpt: post.meta_description,
    status: 'draft',
    categories: [],
    tags: [],
  };
  if (featuredMediaId) body.featured_media = featuredMediaId;

  const response = await fetch(`${WP_URL}/wp-json/wp/v2/posts`, {
    method: 'POST',
    headers: {
      'Authorization': `Basic ${credentials}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body)
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`WordPress API error ${response.status}: ${error}`);
  }

  const result = await response.json();
  console.log(`Draft created! ID: ${result.id}`);

  return {
    id: result.id,
    title: post.title,
    link: result.link,
    editLink: `${WP_URL}/wp-admin/post.php?post=${result.id}&action=edit`
  };
}

// ─── Main ─────────────────────────────────────────────────────────────────────
(async () => {
  try {
    const post = await generatePost(topic);
    const imageInfo = await fetchFeaturedImage(post.image_search_query || topic);
    const featuredMediaId = await uploadImageToWordPress(imageInfo);
    const result = await postToWordPress(post, featuredMediaId);

    fs.writeFileSync('scripts/output.json', JSON.stringify(result, null, 2));

    console.log('\n✓ Done! Draft saved to WordPress.');
    console.log(`  Title: ${result.title}`);
    console.log(`  Edit:  ${result.editLink}`);
  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  }
})();
