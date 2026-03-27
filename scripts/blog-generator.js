const Anthropic = require('@anthropic-ai/sdk');
const fs = require('fs');
const { getRandomTopic } = require('./topics.js');
const { COMPANY } = require('./company-info.js');

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
    max_tokens: 3500,
    messages: [
      {
        role: 'user',
        content: `You are an expert SEO content writer for ${COMPANY.name}, a ${COMPANY.license.status} based in ${COMPANY.location.headquarters}, serving ${COMPANY.location.primaryMarket} and surrounding areas including ${COMPANY.location.targetCities.slice(0,6).join(', ')}.

COMPANY BACKGROUND (read carefully — never contradict this):
${COMPANY.origin}

Team: ${COMPANY.team.contractor} (licensed contractor) and ${COMPANY.team.projectManager} (project manager) — ${COMPANY.team.relationship}.

Services: ${COMPANY.services.core.join(', ')}.
Electrical, roofing, and plumbing are handled through trusted subcontractors as PART of full projects only — never standalone.

CONTENT RULES (strictly follow):
- LOCATION: Always reference South Florida, ${COMPANY.location.primaryMarket}, or specific local cities. NEVER say the company is in Illinois or the Midwest. Illinois is origin story only.
- COMPETITORS: ${COMPANY.contentRules.competitors}
- TRADE REFERRALS: ${COMPANY.contentRules.tradeReferrals}
- PRODUCTS: ${COMPANY.contentRules.products}
- TONE: ${COMPANY.brandVoice.contentPhilosophy}
- AVOID: ${COMPANY.brandVoice.avoid.join('; ')}

The user suggested this topic as direction: "${topic}"
Use it as inspiration — write the best possible SEO post for our South Florida audience. Adjust the angle, title, and focus to maximize SEO value if a better version exists.

SEO REQUIREMENTS (target 80+ AIOSEO score):
- Choose ONE clear focus keyword (3-5 words) people in South Florida actively search with HIRE intent (not just research intent) — e.g. "concrete patio contractor Broward County" beats "best concrete material"
- Use focus keyword in: title, first 100 words, at least 2 H2/H3 headers, meta description, and 4-6 times naturally in body
- Title: under 60 characters, focus keyword near start, compelling and human-sounding
- Meta description: EXACTLY 150-160 characters — count carefully. Include focus keyword + natural CTA
- H2 for main sections (3-4), H3 for subsections — keywords in at least 2 headers
- Content: 1100-1300 words
- At least one bulleted or numbered list
- Mention ${COMPANY.location.primaryMarket} + at least 2 of: ${COMPANY.location.targetCities.slice(0,8).join(', ')}
- Include a second Pexels image mid-body: insert <!-- INLINE_IMAGE: [3-5 word search query] --> where it makes sense visually

FORMAT: HTML only — h2, h3, p, ul, ol, li, img tags. NO html/head/body tags.
Structure: strong keyword intro → 3-4 detailed sections with real advice → conclusion with ONE natural CTA sentence.

Return ONLY this exact JSON (no markdown fences, no other text):
{
  "title": "Under 60 char title",
  "focus_keyword": "exact 3-5 word hire-intent keyword",
  "meta_description": "EXACTLY 150-160 chars with keyword and CTA",
  "image_search_query": "3-5 word featured photo search",
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

  // ── Safety check ────────────────────────────────────────────────────────
  const content = (post.title + post.html_content + post.meta_description).toLowerCase();
  const locationFlags = ['oak park il', 'oak park, il', 'illinois', 'chicagoland', 'chicago area'];
  const flagged = locationFlags.filter(f => content.includes(f));
  if (flagged.length > 0) {
    throw new Error(`Safety check failed — wrong location detected: ${flagged.join(', ')}`);
  }

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
async function uploadImageToWordPress(imageInfo, filename = 'featured-image.jpg') {
  if (!imageInfo) return null;
  const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');

  const imgRes = await fetch(imageInfo.url);
  if (!imgRes.ok) { console.log('Failed to download image.'); return null; }
  const buffer = await imgRes.arrayBuffer();

  const uploadRes = await fetch(`${WP_URL}/wp-json/wp/v2/media`, {
    method: 'POST',
    headers: {
      Authorization: `Basic ${credentials}`,
      'Content-Disposition': `attachment; filename="${filename}"`,
      'Content-Type': 'image/jpeg',
      'X-WP-Alt-Text': imageInfo.alt || '',
    },
    body: buffer,
  });
  if (!uploadRes.ok) { console.log('Image upload failed, skipping.'); return null; }
  const media = await uploadRes.json();
  console.log(`Image uploaded: ${filename} → media ID ${media.id}`);
  return { id: media.id, url: media.source_url, alt: imageInfo.alt };
}

// ─── Step 4b: Replace inline image placeholders in HTML ──────────────────────
async function resolveInlineImages(html) {
  if (!PEXELS_API_KEY) return html;

  const pattern = /<!-- INLINE_IMAGE: ([^>]+) -->/g;
  const matches = [...html.matchAll(pattern)];
  if (matches.length === 0) return html;

  let result = html;
  for (const match of matches) {
    const query = match[1].trim();
    console.log(`Fetching inline image for: "${query}"...`);
    const imgInfo = await fetchFeaturedImage(query);
    if (!imgInfo) { result = result.replace(match[0], ''); continue; }
    const media = await uploadImageToWordPress(imgInfo, `inline-${Date.now()}.jpg`);
    if (!media) { result = result.replace(match[0], ''); continue; }
    result = result.replace(
      match[0],
      `<figure class="wp-block-image"><img src="${media.url}" alt="${query}" /></figure>`
    );
  }
  return result;
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
    const featuredMedia = await uploadImageToWordPress(imageInfo, 'featured-image.jpg');
    const featuredMediaId = featuredMedia ? featuredMedia.id : null;
    post.html_content = await resolveInlineImages(post.html_content);
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
