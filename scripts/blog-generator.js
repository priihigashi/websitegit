const Anthropic = require('@anthropic-ai/sdk');
const fs = require('fs');
const { getRandomTopic } = require('./topics.js');
const { COMPANY } = require('./company-info.js');

const client = new Anthropic();

// ─── Configuration ────────────────────────────────────────────────────────────
const WP_URL          = process.env.WP_URL;
const WP_USERNAME     = process.env.WP_USERNAME;
const WP_APP_PASSWORD = process.env.WP_APP_PASSWORD;
const PEXELS_API_KEY  = process.env.PEXELS_API_KEY || '';
const MANUAL_TOPIC    = process.env.MANUAL_TOPIC || '';
const GOOGLE_SHEET_ID = process.env.GOOGLE_SHEET_ID;
const GOOGLE_SA_KEY   = process.env.GOOGLE_SA_KEY;

// Column indexes (0-based) matching the 22-column sheet
const COL = {
  dateAdded: 0, addedBy: 1, source: 2, sourceLink: 3, rawIdea: 4,
  topicDirection: 5, crossSignal: 6, focusKeyword: 7, secondaryKeyword: 8,
  hookProfessional: 9, hookEmotional: 10, hookGenZ: 11, masterHook: 12,
  readerPayoff: 13, idealFor: 14, targetAudience: 15, imageDirection: 16,
  wpCategoryId: 17, socialOneLiner: 18, status: 19, blogUrl: 20, notes: 21,
};

// ─── Google Sheet helpers ─────────────────────────────────────────────────────
async function getGoogleToken(saKey) {
  const now = Math.floor(Date.now() / 1000);
  const header  = { alg: 'RS256', typ: 'JWT' };
  const payload = {
    iss: saKey.client_email,
    scope: 'https://www.googleapis.com/auth/spreadsheets',
    aud: 'https://oauth2.googleapis.com/token',
    exp: now + 3600, iat: now,
  };
  const enc = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64url');
  const signingInput = `${enc(header)}.${enc(payload)}`;
  const { createSign } = await import('node:crypto');
  const sign = createSign('SHA256');
  sign.update(signingInput);
  const sig = sign.sign(saKey.private_key, 'base64url');
  const jwt = `${signingInput}.${sig}`;

  const res = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: `grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=${jwt}`,
  });
  const data = await res.json();
  if (!data.access_token) throw new Error(`Google auth failed: ${JSON.stringify(data)}`);
  return data.access_token;
}

// Read sheet and return the first Approved row that has no Blog URL yet
async function getApprovedTopicFromSheet() {
  if (!GOOGLE_SHEET_ID || !GOOGLE_SA_KEY) return null;
  try {
    const saKey = JSON.parse(Buffer.from(GOOGLE_SA_KEY, 'base64').toString('utf8'));
    const token = await getGoogleToken(saKey);

    const res = await fetch(
      `https://sheets.googleapis.com/v4/spreadsheets/${GOOGLE_SHEET_ID}/values/Content%20Ideas!A:V`,
      { headers: { Authorization: `Bearer ${token}` } }
    );
    if (!res.ok) { console.log('Could not read sheet, falling back to topics.js'); return null; }
    const data = await res.json();
    const rows = (data.values || []).slice(1); // skip header

    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      const status  = (row[COL.status] || '').trim();
      const blogUrl = (row[COL.blogUrl] || '').trim();
      const rawIdea = (row[COL.rawIdea] || '').trim();

      if (status === '✅ Approved' && !blogUrl && rawIdea) {
        console.log(`Sheet topic selected (row ${i + 2}): "${row[COL.topicDirection] || rawIdea}"`);
        return {
          rowIndex: i,        // 0-based data row index (excluding header)
          sheetRow: i + 2,    // 1-based sheet row number (header = row 1)
          token,
          saKey,
          rawIdea,
          topicDirection:   row[COL.topicDirection]   || rawIdea,
          focusKeyword:     row[COL.focusKeyword]     || '',
          secondaryKeyword: row[COL.secondaryKeyword] || '',
          masterHook:       row[COL.masterHook]       || '',
          readerPayoff:     row[COL.readerPayoff]     || '',
          targetAudience:   row[COL.targetAudience]   || 'Homeowner',
          imageDirection:   row[COL.imageDirection]   || '',
          wpCategoryId:     row[COL.wpCategoryId]     || '',
        };
      }
    }
    console.log('No approved rows without a Blog URL found — falling back to topics.js');
    return null;
  } catch (e) {
    console.log(`Sheet read failed: ${e.message} — falling back to topics.js`);
    return null;
  }
}

// After posting, mark the row as Draft Created and fill the Blog URL
async function markSheetRowDraft(sheetData, blogUrl) {
  try {
    const { token, sheetRow } = sheetData;
    const colLetter = (i) => {
      let r = '', n = i + 1;
      while (n > 0) { r = String.fromCharCode(64 + (n % 26 || 26)) + r; n = Math.floor((n - 1) / 26); }
      return r;
    };
    const updates = [
      { range: `Content Ideas!${colLetter(COL.status)}${sheetRow}`,  values: [['✍️ Draft Created']] },
      { range: `Content Ideas!${colLetter(COL.blogUrl)}${sheetRow}`, values: [[blogUrl]] },
    ];
    const res = await fetch(
      `https://sheets.googleapis.com/v4/spreadsheets/${GOOGLE_SHEET_ID}/values:batchUpdate`,
      {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ valueInputOption: 'USER_ENTERED', data: updates }),
      }
    );
    if (res.ok) console.log(`Sheet row ${sheetRow} updated → ✍️ Draft Created`);
    else console.log(`Sheet update failed: ${await res.text()}`);
  } catch (e) {
    console.log(`Could not update sheet: ${e.message}`);
  }
}

// ─── Step 2: Generate blog post with Claude ───────────────────────────────────
async function generatePost(topic, sheetData = null) {
  console.log('Calling Claude API...');

  // If we have pre-researched sheet data, give Claude the full context
  const keywordInstructions = sheetData && sheetData.focusKeyword
    ? `KEYWORD RESEARCH (already done — use exactly as given):
- Focus keyword: "${sheetData.focusKeyword}" — use this in title, first 100 words, 2+ headers, meta description, and 4-6x in body
- Secondary keyword: "${sheetData.secondaryKeyword || ''}"
- Suggested title angle: "${sheetData.masterHook || topic}"
- Reader payoff (what they walk away knowing): "${sheetData.readerPayoff || ''}"
- Target audience: ${sheetData.targetAudience || 'Homeowner'}
- Suggested featured image: "${sheetData.imageDirection || topic}"

Do NOT change the focus keyword. Build the entire post around it.`
    : `SEO REQUIREMENTS — choose ONE clear focus keyword (3-5 words) with HIRE intent for South Florida (e.g. "concrete patio contractor Broward County"). Use it in title, first 100 words, 2+ H2/H3 headers, meta description, and 4-6x in body.`;

  const message = await client.messages.create({
    model: 'claude-opus-4-6',
    max_tokens: 3500,
    messages: [{
      role: 'user',
      content: `You are an expert SEO content writer for ${COMPANY.name}, a ${COMPANY.license.status} based in ${COMPANY.location.headquarters}, serving ${COMPANY.location.primaryMarket} and surrounding areas including ${COMPANY.location.targetCities.slice(0,6).join(', ')}.

COMPANY BACKGROUND (never contradict this):
${COMPANY.origin}

Team: ${COMPANY.team.contractor} (licensed contractor) and ${COMPANY.team.projectManager} (project manager) — ${COMPANY.team.relationship}.

Services: ${COMPANY.services.core.join(', ')}.
Electrical, roofing, and plumbing are handled through trusted subcontractors as PART of full projects only — never standalone.

CONTENT RULES (strictly follow):
- LOCATION: Always reference South Florida, ${COMPANY.location.primaryMarket}, or specific local cities. NEVER say the company is in Illinois or the Midwest.
- COMPETITORS: ${COMPANY.contentRules.competitors}
- TRADE REFERRALS: ${COMPANY.contentRules.tradeReferrals}
- TONE: ${COMPANY.brandVoice.contentPhilosophy}
- AVOID: ${COMPANY.brandVoice.avoid.join('; ')}
- POLITICAL NEUTRALITY: ${COMPANY.contentRules.politicalNeutrality}

Topic: "${topic}"

${keywordInstructions}

ADDITIONAL SEO REQUIREMENTS:
- Title: under 60 characters, focus keyword near start
- Meta description: EXACTLY 150-160 characters — count carefully. Include focus keyword + natural CTA
- H2 for main sections (3-4), H3 for subsections
- Content: 1100-1300 words
- At least one bulleted or numbered list
- Mention ${COMPANY.location.primaryMarket} + at least 2 of: ${COMPANY.location.targetCities.slice(0,8).join(', ')}
- Include one inline image mid-body: insert <!-- INLINE_IMAGE: [3-5 word search query] --> where it fits

FORMAT: HTML only — h2, h3, p, ul, ol, li tags. NO html/head/body wrapper tags.
Structure: strong keyword intro → 3-4 detailed sections → conclusion with ONE natural CTA sentence.

Return ONLY this exact JSON (no markdown fences):
{
  "title": "Under 60 char title",
  "focus_keyword": "exact focus keyword used",
  "meta_description": "EXACTLY 150-160 chars with keyword and CTA",
  "image_search_query": "3-5 word featured photo search",
  "html_content": "<h2>...</h2><p>...</p>"
}`
    }]
  });

  let raw = message.content[0].text.trim();
  raw = raw.replace(/^```[a-z]*\n?/i, '').replace(/```$/, '').trim();

  const post = JSON.parse(raw);
  console.log(`Post generated: "${post.title}"`);
  console.log(`Meta description: ${post.meta_description.length} chars`);
  console.log(`Focus keyword: "${post.focus_keyword}"`);

  // ── Safety check ──────────────────────────────────────────────────────────
  const content = (post.title + post.html_content + post.meta_description).toLowerCase();
  const locationFlags = ['oak park il', 'oak park, il', 'illinois', 'chicagoland', 'chicago area'];
  const flagged = locationFlags.filter(f => content.includes(f));
  if (flagged.length > 0) throw new Error(`Safety check failed — wrong location: ${flagged.join(', ')}`);

  return post;
}

// ─── Step 3: Fetch featured image from Pexels ────────────────────────────────
async function fetchFeaturedImage(query) {
  if (!PEXELS_API_KEY) { console.log('No Pexels API key — skipping featured image.'); return null; }
  console.log(`Searching Pexels for: "${query}"...`);
  const res = await fetch(
    `https://api.pexels.com/v1/search?query=${encodeURIComponent(query)}&per_page=1&orientation=landscape`,
    { headers: { Authorization: PEXELS_API_KEY } }
  );
  if (!res.ok) { console.log('Pexels search failed, skipping image.'); return null; }
  const data = await res.json();
  if (!data.photos || data.photos.length === 0) { console.log('No Pexels results, skipping.'); return null; }
  const photo = data.photos[0];
  return { url: photo.src.large, photographer: photo.photographer, alt: query };
}

// ─── Step 4: Upload image to WordPress ───────────────────────────────────────
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

// ─── Step 4b: Resolve inline image placeholders ───────────────────────────────
async function resolveInlineImages(html) {
  if (!PEXELS_API_KEY) return html;
  const pattern = /<!-- INLINE_IMAGE: ([^>]+) -->/g;
  const matches = [...html.matchAll(pattern)];
  if (matches.length === 0) return html;
  let result = html;
  for (const match of matches) {
    const query = match[1].trim();
    console.log(`Fetching inline image: "${query}"...`);
    const imgInfo = await fetchFeaturedImage(query);
    if (!imgInfo) { result = result.replace(match[0], ''); continue; }
    const media = await uploadImageToWordPress(imgInfo, `inline-${Date.now()}.jpg`);
    if (!media) { result = result.replace(match[0], ''); continue; }
    result = result.replace(match[0], `<figure class="wp-block-image"><img src="${media.url}" alt="${query}" /></figure>`);
  }
  return result;
}

// ─── Step 5: Post to WordPress as Draft ──────────────────────────────────────
async function postToWordPress(post, featuredMediaId, wpCategoryId) {
  console.log('Posting to WordPress...');
  const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');
  const body = {
    title: post.title,
    content: post.html_content,
    excerpt: post.meta_description,
    status: 'draft',
    categories: wpCategoryId ? [parseInt(wpCategoryId)] : [],
  };
  if (featuredMediaId) body.featured_media = featuredMediaId;
  const response = await fetch(`${WP_URL}/wp-json/wp/v2/posts`, {
    method: 'POST',
    headers: { Authorization: `Basic ${credentials}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`WordPress API error ${response.status}: ${await response.text()}`);
  const result = await response.json();
  console.log(`Draft created! ID: ${result.id}`);
  return {
    id: result.id,
    title: post.title,
    link: result.link,
    editLink: `${WP_URL}/wp-admin/post.php?post=${result.id}&action=edit`,
  };
}

// ─── Main ─────────────────────────────────────────────────────────────────────
(async () => {
  try {
    // 1. Determine topic source: manual → sheet (Approved) → fallback random
    let topic, sheetData;

    if (MANUAL_TOPIC.trim()) {
      topic = MANUAL_TOPIC.trim();
      console.log(`Topic: manual — "${topic}"`);
    } else {
      sheetData = await getApprovedTopicFromSheet();
      if (sheetData) {
        topic = sheetData.topicDirection;
      } else {
        topic = getRandomTopic();
        console.log(`Topic: fallback random — "${topic}"`);
      }
    }

    // 2. Generate post
    const post = await generatePost(topic, sheetData);

    // 3. Images
    const imageQuery = (sheetData?.imageDirection) || post.image_search_query || topic;
    const imageInfo = await fetchFeaturedImage(imageQuery);
    const featuredMedia = await uploadImageToWordPress(imageInfo, 'featured-image.jpg');
    post.html_content = await resolveInlineImages(post.html_content);

    // 4. Post to WordPress
    const result = await postToWordPress(post, featuredMedia?.id || null, sheetData?.wpCategoryId);

    // 5. Update sheet row if topic came from sheet
    if (sheetData) {
      await markSheetRowDraft(sheetData, result.link || result.editLink);
    }

    // 6. Save output for workflow notification
    fs.writeFileSync('scripts/output.json', JSON.stringify(result, null, 2));

    console.log('\n✓ Done! Draft saved to WordPress.');
    console.log(`  Title: ${result.title}`);
    console.log(`  Edit:  ${result.editLink}`);
  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  }
})();
