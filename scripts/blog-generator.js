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
        console.log(`Sheet topic selected (row ${i + 2}) [APPROVED → will publish]: "${row[COL.topicDirection] || rawIdea}"`);
        return buildSheetData(row, i, token, '✅ Approved');
      }
    }

    // Fallback: pick first Idea row if no Approved rows remain
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      const status  = (row[COL.status] || '').trim();
      const blogUrl = (row[COL.blogUrl] || '').trim();
      const rawIdea = (row[COL.rawIdea] || '').trim();

      if (status === '🆕 Idea' && !blogUrl && rawIdea) {
        console.log(`Sheet topic selected (row ${i + 2}) [IDEA → will draft]: "${row[COL.topicDirection] || rawIdea}"`);
        return buildSheetData(row, i, token, '🆕 Idea');
      }
    }

    console.log('No usable sheet rows found — falling back to topics.js');
    return null;
  } catch (e) {
    console.log(`Sheet read failed: ${e.message} — falling back to topics.js`);
    return null;
  }
}

function buildSheetData(row, i, token, originalStatus) {
  return {
    rowIndex: i,
    sheetRow: i + 2,
    token,
    originalStatus,
    // Pass every researched column so Claude has full context
    source:           row[COL.source]           || '',
    sourceLink:       row[COL.sourceLink]       || '',
    rawIdea:          row[COL.rawIdea]          || '',
    topicDirection:   row[COL.topicDirection]   || row[COL.rawIdea] || '',
    focusKeyword:     row[COL.focusKeyword]     || '',
    secondaryKeyword: row[COL.secondaryKeyword] || '',
    hookProfessional: row[COL.hookProfessional] || '',
    hookEmotional:    row[COL.hookEmotional]    || '',
    hookGenZ:         row[COL.hookGenZ]         || '',
    masterHook:       row[COL.masterHook]       || '',
    readerPayoff:     row[COL.readerPayoff]     || '',
    idealFor:         row[COL.idealFor]         || 'Both',
    targetAudience:   row[COL.targetAudience]   || 'Homeowner',
    imageDirection:   row[COL.imageDirection]   || '',
    wpCategoryId:     row[COL.wpCategoryId]     || '',
    socialOneLiner:   row[COL.socialOneLiner]   || '',
  };
}

// After posting, update the sheet row status and Blog URL
async function markSheetRowPosted(sheetData, blogUrl, wpStatus) {
  const newStatus = wpStatus === 'publish' ? '📤 Published' : '✍️ Draft Created';
  try {
    const { token, sheetRow } = sheetData;
    const colLetter = (i) => {
      let r = '', n = i + 1;
      while (n > 0) { r = String.fromCharCode(64 + (n % 26 || 26)) + r; n = Math.floor((n - 1) / 26); }
      return r;
    };
    const updates = [
      { range: `Content Ideas!${colLetter(COL.status)}${sheetRow}`,  values: [[newStatus]] },
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
    if (res.ok) console.log(`Sheet row ${sheetRow} updated → ${newStatus}`);
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
    ? `PRE-RESEARCHED CONTENT BRIEF (use this — don't ignore it):
- Original source: ${sheetData.source || 'research'} ${sheetData.sourceLink ? `(${sheetData.sourceLink})` : ''}
- Raw idea: "${sheetData.rawIdea}"
- Topic direction: "${sheetData.topicDirection}"
- Focus keyword: "${sheetData.focusKeyword}" ← use this in title, intro, 2+ subheadings, meta, and 4-6x in body
- Secondary keyword: "${sheetData.secondaryKeyword || ''}"
- Target audience: ${sheetData.targetAudience || 'Homeowner'}
- Reader payoff: "${sheetData.readerPayoff || ''}" ← this is what the reader walks away knowing — make sure the post delivers it
- Ideal format: ${sheetData.idealFor || 'Both'}
- Featured image concept: "${sheetData.imageDirection || ''}"

TITLE — choose the strongest option from these pre-written hooks, or combine them:
  Hook A (professional): "${sheetData.hookProfessional || ''}"
  Hook B (emotional):    "${sheetData.hookEmotional || ''}"
  Hook C (Gen Z/casual): "${sheetData.hookGenZ || ''}"
  Master hook:           "${sheetData.masterHook || ''}"

Adapt the best hook into a 50-60 character SEO title that:
- Includes the focus keyword naturally within the first 5 words
- Reads like a human wrote it — NOT like a keyword list
- Has a power word (Cost, Guide, Why, How, What, Full, Real, Honest) when natural
- Example: "Concrete Slab Contractors Broward: Why DIY Fails in Florida's Heat"`
    : `KEYWORD: Choose ONE clear focus keyword (3-5 words) with HIRE intent for South Florida (e.g. "home addition contractor Broward County"). Craft a compelling human title around it — do NOT use the raw keyword as the title. Use keyword in title, first 100 words, 2+ subheadings, meta description, and 4-6x in body.`;

  const message = await client.messages.create({
    model: 'claude-opus-4-6',
    max_tokens: 6000,
    system: `You are the content writer for ${COMPANY.name}, a ${COMPANY.license.status} based in ${COMPANY.location.headquarters}, serving ${COMPANY.location.primaryMarket} and surrounding areas.

COMPANY:
${COMPANY.origin}
Team: ${COMPANY.team.contractor} (contractor) and ${COMPANY.team.projectManager} (PM) — brothers.
Services: ${COMPANY.services.core.join(', ')}.

VOICE: Practical, direct, calm, contractor-led, homeowner-friendly. Expert without being technical. Persuasive without hype.
Good: "Here's what affects cost." / "Here's what to verify before hiring." / "Here's why this matters in Broward."
Bad: hype, vague dream-project language, robotic SEO phrasing, "we go above and beyond."

EDITORIAL STANDARD — every post must do all 5:
1. Attract search traffic (title/topic matches a real search query)
2. Educate clearly (plain but expert language)
3. Prove local relevance (South Florida conditions must actually CHANGE the advice — not just be mentioned)
4. Build trust in Oak Park (sounds like a contractor who knows the work, not a generic writer)
5. Convert the right reader (CTA matches the exact article topic)

REQUIRED ARTICLE STRUCTURE (adapt freely — this is a guide, not a template):
- Intro: Name the reader's exact problem → tie it to South Florida immediately → explain the consequence of misunderstanding it → promise what the article will clarify
- Section 1: Define the term or frame the decision in plain language
- Section 2: Break down the main drivers (cost, structure, timeline, code, materials)
- Section 3: South Florida-specific realities — hurricanes, flood zones, concrete block expectations, water table, insurance impact, permitting, coastal exposure (pick what's relevant)
- Section 4: Decision guidance — when it makes sense, when it doesn't, what to ask a contractor, what mistakes to avoid
- Section 5 (optional): What owners often miss or underestimate
- Closing: ONE topic-specific CTA that finishes: "If you're trying to figure out [exact article problem], the next step is…"

PROOF SIGNALS — include at least 2-3 per article (these replace citations and build authority):
- A real-world scenario or common mistake homeowners make
- A permit, inspection, or code note specific to South Florida
- A contractor-selection tip (what to verify, what to ask)
- A scope distinction (what's included vs. what gets added later)
- A cost driver that reflects real local tradeoffs
- A market-specific warning ("In Broward, flood zone requirements mean…")
Examples of good authority signals:
"This is where homeowners usually underestimate the cost."
"This is one of the first things to confirm before signing a contract."
"The total shifts fast when you add a bathroom, second story, or panel upgrade."
"This matters more in South Florida because…"

PRICING LANGUAGE RULE — critical:
Never write prices with false certainty. Always use bounded, realistic, local language.
Good: "In Broward County, many projects fall within this range depending on scope and finishes."
Good: "A realistic starting point in South Florida is often…"
Good: "Costs can move significantly depending on structure, municipality, and finish level."
Bad: Fixed national-average figures presented as South Florida facts
Bad: Pretending a rough contractor estimate is a universal rule
If using price ranges: keep them local, realistic, clearly variable, and tied to specific scope conditions.

HEADING RULES:
- WordPress adds the post title as H1 automatically — do NOT use any H1 tags in the HTML body
- Start body HTML with H2 for main sections, H3 for subsections
- Headings must be natural and readable — written for humans first
- Good: "What Affects the Cost of a Home Addition in Broward County?" / "What to Ask Before Hiring a Contractor"
- Bad: "Home Addition Cost Broward County Florida Contractor Services Near Me"
- H3 should feel calmer and more functional than H2 — not competing for attention
- At least 2 headings must include the focus keyword or a natural variation
- Do not make every section feel like a new headline moment — some content should breathe

CTA RULE: The closing CTA must match the article topic exactly. Never use generic "contact us" language alone.
- Shell article → "Send your plans for a free shell-scope review"
- Addition cost → "Request a realistic Broward County budget range"
- Comparison → "Talk through which option makes sense for your lot and budget"

FEATURED IMAGE RULE — the image_search_query you return must follow this hierarchy:
The featured image must match the article in: property type, project type, project stage, and reader expectation.
If the article is residential (home addition, remodel, kitchen, patio, etc.):
  GOOD search terms: "home addition exterior renovation", "house framing addition", "residential contractor house plans", "single family home remodel", "homeowner contractor exterior"
  BAD search terms: "construction building", "concrete building", "commercial construction", "high rise", "parking garage", "crane tower"
If someone sees the image alone, they should roughly guess: house, renovation/addition, homeowner project, residential scale.
For commercial articles: office renovation, retail build-out, commercial interior framing — not residential.
Quick approval test: Does the image match the building type? The job type? The project scale? If 2 of 3 are wrong, find a better search term.

FRESHNESS AND ATTRIBUTION RULE — for current-event, enforcement, or policy topics:
Do not write time-sensitive claims as if they are permanent facts.
Use anchored or softened language:
  Good: "as enforcement pressure increases" / "in the current environment" / "in some recent cases" / "depending on how enforcement plays out locally"
  Risky: "contractors across Broward are experiencing this" / "the effects are measurable and ongoing" — only use these if there is clear current basis
When making a market-wide local claim, either attach it to a named source/reporting pattern OR write it as a practical risk rather than a declared county-wide fact.
Current-event posts should be reviewed at 30–60 days — add a note at the end of the article's HTML: <!-- FRESHNESS CHECK: review this post by [month 60 days out] -->

CONTENT RULES:
- LOCATION: South Florida, ${COMPANY.location.primaryMarket}, or specific local cities ONLY. Never imply the company is in Illinois. Illinois is origin story only.
- COMPETITORS: Never name any competitor.
- TRADE REFERRALS: Never tell readers to "hire a roofer/plumber" as standalone advice. Oak Park handles those as part of full projects.
- POLITICAL NEUTRALITY: ${COMPANY.contentRules.politicalNeutrality}`,

    messages: [{
      role: 'user',
      content: `Topic: "${topic}"

${keywordInstructions}

SEO REQUIREMENTS:
- Title: 50-60 characters, focus keyword near start, reads like a human wrote it
- Meta description: EXACTLY 150-160 characters — count carefully. Focus keyword + natural CTA
- H2 for main sections, H3 for subsections
- 1100-1300 words
- Focus keyword used in: title, first 100 words, at least 2 subheadings, meta description, and 4-6x in body naturally
- Mention ${COMPANY.location.primaryMarket} + at least 2 of: ${COMPANY.location.targetCities.slice(0,8).join(', ')}
- 1-2 outbound links to credible sources (floridabuilding.org, myflorida.com, energy.gov, fema.gov, or similar) in <a href="URL" target="_blank" rel="noopener"> tags
- One inline image placeholder mid-body: <!-- INLINE_IMAGE: [3-5 word search query] -->

READABILITY:
- Sentences under 20 words average
- Paragraphs 2-4 sentences max, start with the main idea
- Transition words to open at least 30% of sentences
- Active voice — avoid passive constructions
- Vary rhythm: mix short punchy sentences with longer explanations
- End each section with a takeaway, not just a stop

FORMAT: HTML only — h2, h3, p, ul, ol, li, a tags. No html/head/body wrappers. NO h1 tags — WordPress outputs the title as h1 automatically.

Return ONLY this exact JSON (no markdown fences, no extra text):
{
  "title": "50-60 char title",
  "focus_keyword": "exact focus keyword",
  "meta_description": "EXACTLY 150-160 chars",
  "image_search_query": "3-5 words MAX, plain ASCII only, no punctuation (e.g. 'residential concrete patio slab')",
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
  // Sanitize: replace em/en dash, strip all non-ASCII, collapse spaces, cap at 60 chars
  const safeQuery = query
    .replace(/[\u2013\u2014]/g, '-')
    .replace(/[^\x00-\x7F]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 60);
  console.log(`Searching Pexels for: "${safeQuery}"...`);
  const res = await fetch(
    `https://api.pexels.com/v1/search?query=${encodeURIComponent(safeQuery)}&per_page=1&orientation=landscape`,
    { headers: { Authorization: PEXELS_API_KEY } }
  );
  if (!res.ok) { console.log('Pexels search failed, skipping image.'); return null; }
  const data = await res.json();
  if (!data.photos || data.photos.length === 0) { console.log('No Pexels results, skipping.'); return null; }
  const photo = data.photos[0];
  return { url: photo.src.large, photographer: photo.photographer, alt: safeQuery };
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
async function postToWordPress(post, featuredMediaId, wpCategoryId, wpStatus = 'draft') {
  console.log(`Posting to WordPress as ${wpStatus}...`);
  const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');
  const body = {
    title: post.title,
    content: post.html_content,
    excerpt: post.meta_description,
    status: wpStatus,
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

    // 4. Post to WordPress — Approved rows publish directly, everything else is draft
    const wpStatus = (sheetData?.originalStatus === '✅ Approved') ? 'publish' : 'draft';
    const result = await postToWordPress(post, featuredMedia?.id || null, sheetData?.wpCategoryId, wpStatus);

    // 5. Update sheet row if topic came from sheet
    if (sheetData) {
      await markSheetRowPosted(sheetData, result.link || result.editLink, wpStatus);
    }

    // 6. Save output for workflow notification
    fs.writeFileSync('scripts/output.json', JSON.stringify(result, null, 2));

    console.log(`\n✓ Done! Post ${wpStatus === 'publish' ? 'published live' : 'saved as draft'}.`);
    console.log(`  Title: ${result.title}`);
    console.log(`  Edit:  ${result.editLink}`);
  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  }
})();
