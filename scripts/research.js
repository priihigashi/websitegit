// ─────────────────────────────────────────────────────────────────────────────
// Oak Park Construction — Content Research Script
// Runs twice daily (7am + 2pm EST) via GitHub Actions
// Pulls from Reddit, YouTube, NewsAPI, SerpAPI
// Claude scores each idea for relevance + auto-approves if relevant
// Writes approved ideas to Google Sheet
// ─────────────────────────────────────────────────────────────────────────────

const Anthropic = require('@anthropic-ai/sdk');
const { COMPANY } = require('./company-info.js');

const client = new Anthropic();

// ─── Config ───────────────────────────────────────────────────────────────────
const YOUTUBE_API_KEY  = process.env.YOUTUBE_API_KEY;
const NEWS_API_KEY     = process.env.NEWS_API_KEY;
const SERP_API_KEY     = process.env.SERP_API_KEY;
const GOOGLE_SHEET_ID  = process.env.GOOGLE_SHEET_ID;
const GOOGLE_SA_KEY    = process.env.GOOGLE_SA_KEY; // service account JSON (base64)

const TODAY = new Date().toLocaleDateString('en-US', { timeZone: 'America/New_York' });

// ─── Keywords we search for across all sources ────────────────────────────────
const SEARCH_QUERIES = [
  'home renovation South Florida',
  'home addition Broward County',
  'contractor Fort Lauderdale',
  'concrete patio South Florida',
  'kitchen remodel Florida',
  'commercial renovation Florida',
  'shell construction Florida',
  'deck construction South Florida',
  'bathroom remodel Broward',
  'new construction Pompano Beach',
];

// ─── SOURCE 1: Reddit (public JSON — no API key needed) ───────────────────────
async function fetchReddit() {
  const subreddits = ['HomeImprovement', 'DIY', 'florida', 'realestateinvesting'];
  const results = [];

  for (const sub of subreddits) {
    try {
      const res = await fetch(
        `https://www.reddit.com/r/${sub}/hot.json?limit=10`,
        { headers: { 'User-Agent': 'OakParkConstruction/1.0' } }
      );
      if (!res.ok) continue;
      const data = await res.json();
      const posts = data?.data?.children || [];

      for (const post of posts.slice(0, 5)) {
        const p = post.data;
        if (p.score < 50) continue; // only posts with traction
        results.push({
          source: 'Reddit',
          sourceLink: `https://reddit.com${p.permalink}`,
          rawIdea: p.title,
          score: p.score,
          comments: p.num_comments,
        });
      }
    } catch (e) {
      console.log(`Reddit r/${sub} failed: ${e.message}`);
    }
  }
  console.log(`Reddit: ${results.length} posts collected`);
  return results;
}

// ─── SOURCE 2: YouTube ────────────────────────────────────────────────────────
async function fetchYouTube() {
  if (!YOUTUBE_API_KEY) { console.log('YouTube: no key, skipping'); return []; }
  const results = [];

  for (const query of SEARCH_QUERIES.slice(0, 4)) {
    try {
      const url = `https://www.googleapis.com/youtube/v3/search?part=snippet&q=${encodeURIComponent(query)}&type=video&order=viewCount&regionCode=US&maxResults=3&key=${YOUTUBE_API_KEY}`;
      const res = await fetch(url);
      if (!res.ok) continue;
      const data = await res.json();

      for (const item of (data.items || [])) {
        results.push({
          source: 'YouTube',
          sourceLink: `https://youtube.com/watch?v=${item.id.videoId}`,
          rawIdea: item.snippet.title,
          description: item.snippet.description?.slice(0, 120),
        });
      }
    } catch (e) {
      console.log(`YouTube query failed: ${e.message}`);
    }
  }
  console.log(`YouTube: ${results.length} videos collected`);
  return results;
}

// ─── SOURCE 3: NewsAPI ────────────────────────────────────────────────────────
async function fetchNews() {
  if (!NEWS_API_KEY) { console.log('News: no key, skipping'); return []; }
  const results = [];
  const queries = ['home renovation Florida', 'construction Broward County', 'real estate South Florida'];

  for (const q of queries) {
    try {
      const url = `https://newsapi.org/v2/everything?q=${encodeURIComponent(q)}&language=en&sortBy=publishedAt&pageSize=3&apiKey=${NEWS_API_KEY}`;
      const res = await fetch(url);
      if (!res.ok) continue;
      const data = await res.json();

      for (const article of (data.articles || [])) {
        if (!article.title || article.title === '[Removed]') continue;
        results.push({
          source: 'News',
          sourceLink: article.url,
          rawIdea: article.title,
          description: article.description?.slice(0, 120),
        });
      }
    } catch (e) {
      console.log(`News query failed: ${e.message}`);
    }
  }
  console.log(`News: ${results.length} articles collected`);
  return results;
}

// ─── SOURCE 4: SerpAPI (Google Search) ────────────────────────────────────────
async function fetchSerp() {
  if (!SERP_API_KEY) { console.log('SerpAPI: no key, skipping'); return []; }
  const results = [];
  const queries = ['home renovation contractor Broward County', 'concrete patio installation South Florida'];

  for (const q of queries) {
    try {
      const url = `https://serpapi.com/search.json?q=${encodeURIComponent(q)}&location=Fort+Lauderdale,Florida&hl=en&gl=us&api_key=${SERP_API_KEY}`;
      const res = await fetch(url);
      if (!res.ok) continue;
      const data = await res.json();

      for (const result of (data.organic_results || []).slice(0, 3)) {
        results.push({
          source: 'SerpAPI',
          sourceLink: result.link,
          rawIdea: result.title,
          description: result.snippet?.slice(0, 120),
        });
      }
    } catch (e) {
      console.log(`SerpAPI query failed: ${e.message}`);
    }
  }
  console.log(`SerpAPI: ${results.length} results collected`);
  return results;
}

// ─── Claude: Score + enrich a batch of ideas ─────────────────────────────────
async function enrichBatch(items, offset) {
  const SYSTEM = `You are a content strategist for ${COMPANY.name}, a licensed general contractor in ${COMPANY.location.headquarters} serving ${COMPANY.location.primaryMarket}. Services: ${COMPANY.services.core.slice(0,5).join(', ')}.`;

  const message = await client.messages.create({
    model: 'claude-opus-4-6',
    max_tokens: 8000,
    system: SYSTEM,
    messages: [{
      role: 'user',
      content: `Review these raw content ideas collected from Reddit, YouTube, News, and Google.
For each idea, decide if it's relevant enough to write a blog post about.

AUTO-APPROVE if the idea:
- Relates to construction, renovation, home improvement, additions, concrete, decks, commercial build-outs
- Could attract homeowners, investors, or commercial clients in South Florida
- Is NOT a handyman-only topic (installing a sink, hanging a picture, minor repairs)
- Is NOT exclusively about a trade we don't offer standalone (roofing only, plumbing only, electrical only)

HOLD AS IDEA (don't approve) if:
- Completely unrelated to construction/renovation
- Only relevant to a different region with no South Florida angle
- Pure DIY with no contractor value
- Ambiguous or could embarrass the company

For EVERY idea (both Approved and Idea), generate ALL of these fields — no exceptions:
topic_direction, focus_keyword, secondary_keyword, hook_professional, hook_emotional, hook_genz, master_hook, reader_payoff, ideal_for (Both/Blog Only/Reels Only), target_audience (Homeowner/Investor/Commercial/All), image_direction, social_one_liner, status (✅ Approved or 🆕 Idea)

Only skip an item entirely if it is completely unrelated to construction, real estate, or South Florida business (e.g. celebrity gossip, sports scores, cooking recipes).

IMPORTANT — Content neutrality rule:
If a topic involves political, immigration, or social issues (e.g. ICE raids, labor policy, housing regulations):
- Write from the contractor/business-owner perspective ONLY
- Report facts and practical impact on construction clients — do not editorialize
- Do NOT frame one political side as correct or the other as wrong
- Do NOT use language that promotes or defends government enforcement actions
- A hook like "Here's what to expect and how to protect your project timeline" is fine
- A hook like "ICE is just doing their job — here's why it's actually good" is NOT fine

Raw ideas:
${items.map((item, i) => `${offset+i+1}. [${item.source}] "${item.rawIdea}" — ${item.description || ''}`).join('\n')}

Return ONLY a valid JSON array, no markdown fences, no extra text. Use the original index numbers.
Example: [{"index":1,"status":"✅ Approved","topic_direction":"...","focus_keyword":"...","secondary_keyword":"...","hook_professional":"...","hook_emotional":"...","hook_genz":"...","master_hook":"...","reader_payoff":"...","ideal_for":"Both","target_audience":"Homeowner","image_direction":"...","social_one_liner":"..."}]`
    }]
  });

  let raw = message.content[0].text.trim();
  raw = raw.replace(/^```[a-z]*\n?/i, '').replace(/```$/, '').trim();
  return JSON.parse(raw);
}

async function enrichWithClaude(items) {
  if (items.length === 0) return [];

  console.log(`\nSending ${items.length} raw ideas to Claude for scoring...`);

  const BATCH_SIZE = 10;
  const all = [];

  for (let i = 0; i < items.length; i += BATCH_SIZE) {
    const batch = items.slice(i, i + BATCH_SIZE);
    console.log(`Processing batch ${Math.floor(i/BATCH_SIZE)+1} (ideas ${i+1}–${i+batch.length})...`);
    const result = await enrichBatch(batch, i);
    all.push(...result);
  }

  console.log(`Claude approved/enriched ${all.length} ideas`);
  return all;
}

// ─── Write to Google Sheet ────────────────────────────────────────────────────
async function writeToSheet(items, rawItems) {
  if (!GOOGLE_SHEET_ID || !GOOGLE_SA_KEY) {
    console.log('\nNo Google Sheet credentials — printing results instead:\n');
    items.forEach((item, i) => {
      const raw = rawItems[item.index - 1];
      console.log(`\n[${item.status}] ${item.master_hook}`);
      console.log(`  Keyword: ${item.focus_keyword}`);
      console.log(`  Audience: ${item.target_audience} | Ideal for: ${item.ideal_for}`);
      console.log(`  Source: ${raw?.sourceLink}`);
    });
    return;
  }

  // Decode service account key — accept either raw JSON or base64-encoded JSON
  let saKey;
  const trimmed = GOOGLE_SA_KEY.trim();
  if (trimmed.startsWith('{')) {
    saKey = JSON.parse(trimmed);
  } else {
    saKey = JSON.parse(Buffer.from(trimmed, 'base64').toString('utf8'));
  }

  // Get access token via JWT
  const token = await getGoogleToken(saKey);

  const rows = items.map(item => {
    const raw = rawItems[item.index - 1] || {};
    return [
      TODAY,                      // Date Added
      'AI',                       // Added By
      raw.source || '',           // Source
      raw.sourceLink || '',       // Source Link
      raw.rawIdea || '',          // Raw Idea
      item.topic_direction || '', // Topic Direction
      '',                         // Cross-Signal? (filled later)
      item.focus_keyword || '',   // Focus Keyword
      item.secondary_keyword || '',// Secondary Keyword
      item.hook_professional || '',// Hook: Professional
      item.hook_emotional || '',  // Hook: Emotional
      item.hook_genz || '',       // Hook: GenZ
      item.master_hook || '',     // Master Hook
      item.reader_payoff || '',   // Reader Payoff
      item.ideal_for || 'Both',   // Ideal For
      item.target_audience || '', // Target Audience
      item.image_direction || '', // Image Direction
      '',                         // WP Category ID (manual)
      item.social_one_liner || '',// Social One-Liner
      item.status || '🆕 Idea',  // Status
      '',                         // Blog URL
      '',                         // Notes
    ];
  });

  const res = await fetch(
    `https://sheets.googleapis.com/v4/spreadsheets/${GOOGLE_SHEET_ID}/values/Content%20Ideas!A:V:append?valueInputOption=USER_ENTERED`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ values: rows }),
    }
  );

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Google Sheets write failed: ${err}`);
  }
  console.log(`\n✓ Written ${rows.length} rows to Google Sheet`);
}

// ─── Google JWT Auth ──────────────────────────────────────────────────────────
async function getGoogleToken(saKey) {
  const now = Math.floor(Date.now() / 1000);
  const header = { alg: 'RS256', typ: 'JWT' };
  const payload = {
    iss: saKey.client_email,
    scope: 'https://www.googleapis.com/auth/spreadsheets',
    aud: 'https://oauth2.googleapis.com/token',
    exp: now + 3600,
    iat: now,
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

// ─── Main ─────────────────────────────────────────────────────────────────────
(async () => {
  try {
    console.log(`\n=== Oak Park Construction Research Script — ${TODAY} ===\n`);

    // Collect from all sources in parallel
    const [reddit, youtube, news, serp] = await Promise.all([
      fetchReddit(),
      fetchYouTube(),
      fetchNews(),
      fetchSerp(),
    ]);

    const allRaw = [...reddit, ...youtube, ...news, ...serp];
    console.log(`\nTotal raw ideas collected: ${allRaw.length}`);

    if (allRaw.length === 0) {
      console.log('No ideas found this run. Exiting.');
      process.exit(0);
    }

    // Claude scores + enriches
    const enriched = await enrichWithClaude(allRaw);

    // Write to sheet (or print if no sheet connected)
    await writeToSheet(enriched, allRaw);

    const approved = enriched.filter(i => i.status === '✅ Approved').length;
    console.log(`\n✓ Done. ${approved} approved, ${enriched.length - approved} held as ideas.`);

  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  }
})();
