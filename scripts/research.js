// ─────────────────────────────────────────────────────────────────────────────
// Oak Park Construction — Content Research Script
// Runs twice daily (7am + 2pm EST) via GitHub Actions
// Pulls from Reddit, YouTube, NewsAPI, SerpAPI
// Claude scores each idea for relevance + auto-approves if relevant
// Writes approved ideas to Google Sheet
// ─────────────────────────────────────────────────────────────────────────────

const Anthropic = require('@anthropic-ai/sdk');
const { COMPANY } = require('./company-info.js');

// ─── Config ───────────────────────────────────────────────────────────────────
const YOUTUBE_API_KEY  = process.env.YOUTUBE_API_KEY;
const NEWS_API_KEY     = process.env.NEWS_API_KEY;
const SERP_API_KEY     = process.env.SERP_API_KEY;
const GOOGLE_SHEET_ID  = process.env.GOOGLE_SHEET_ID;
const SHEETS_TOKEN     = process.env.SHEETS_TOKEN; // OAuth token JSON (refresh token flow)
const SUPADATA_API_KEY = process.env.SUPADATA_API_KEY || '';
const RESEARCH_FOCUS = (process.env.RESEARCH_FOCUS || '').trim();
const RESEARCH_DRIVE_ID = process.env.RESEARCH_DRIVE_ID || '0AIPzwsJD_qqzUk9PVA'; // Marketing shared drive
const RESEARCH_FOLDER_NAME = process.env.RESEARCH_FOLDER_NAME || 'Research';
const ANTHROPIC_API_KEY = (
  process.env.CLAUDE_KEY_4_CONTENT ||
  process.env.ANTHROPIC_API_KEY ||
  ''
).trim();
const OPENAI_API_KEY = (process.env.OPENAI_API_KEY || '').trim();
const ANTHROPIC_KEY_SOURCE = process.env.CLAUDE_KEY_4_CONTENT
  ? 'CLAUDE_KEY_4_CONTENT'
  : (process.env.ANTHROPIC_API_KEY ? 'ANTHROPIC_API_KEY' : 'MISSING');

if (!ANTHROPIC_API_KEY && !OPENAI_API_KEY) {
  throw new Error('No LLM key available: set CLAUDE_KEY_4_CONTENT or OPENAI_API_KEY.');
}

const client = ANTHROPIC_API_KEY ? new Anthropic({ apiKey: ANTHROPIC_API_KEY }) : null;

const TODAY = new Date().toLocaleDateString('en-US', { timeZone: 'America/New_York' });
const CURRENT_YEAR = new Date().toLocaleDateString('en-US', { timeZone: 'America/New_York', year: 'numeric' });
const THIN_CONTENT_THRESHOLD = 18;
const IS_SHEETS_FOCUS = /google sheets|spreadsheet|dashboard|sheet design|schema/i.test(RESEARCH_FOCUS);
const RESEARCH_DOC_RULES = [
  'Always create a Google Doc summary at the end of every successful run.',
  'Store the summary in Marketing shared drive inside the Research folder.',
  'Include run focus, tools used, source counts, step-by-step process, and top approved ideas.',
  'Include YouTube findings with links and transcript/context excerpts when available.',
  'Keep the summary as durable reference for future consultations and process improvements.',
];

// ─── Keywords we search for across all sources ────────────────────────────────
const DEFAULT_SEARCH_QUERIES = [
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
const SHEETS_SEARCH_QUERIES = [
  'Google Sheets dashboard design best practices',
  'Google Sheets data validation schema governance',
  'Google Sheets header based automation mapping',
  'Google Sheets filter views operations workflow',
  'Claude Google Sheets automation prompts',
  'Google Sheets protected ranges for teams',
];
const SEARCH_QUERIES = IS_SHEETS_FOCUS ? SHEETS_SEARCH_QUERIES : DEFAULT_SEARCH_QUERIES;

// ─── SOURCE 1: Reddit (public JSON — no API key needed) ───────────────────────
async function fetchReddit() {
  const subreddits = IS_SHEETS_FOCUS
    ? ['googlesheets', 'productivity', 'smallbusiness', 'dataisbeautiful']
    : ['HomeImprovement', 'DIY', 'florida', 'realestateinvesting'];
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
  const queries = IS_SHEETS_FOCUS
    ? ['Google Sheets dashboard', 'spreadsheet automation', 'workflow automation spreadsheet']
    : ['home renovation Florida', 'construction Broward County', 'real estate South Florida'];

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
  const queries = IS_SHEETS_FOCUS
    ? ['Google Sheets schema governance', 'Google Sheets filter views dashboard operations']
    : ['home renovation contractor Broward County', 'concrete patio installation South Florida'];

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

function getYoutubeVideoId(url = '') {
  const short = url.match(/youtu\.be\/([a-zA-Z0-9_-]{11})/);
  if (short) return short[1];
  const full = url.match(/[?&]v=([a-zA-Z0-9_-]{11})/);
  return full ? full[1] : '';
}

function collectTextChunks(node, out = []) {
  if (!node) return out;
  if (typeof node === 'string') {
    const s = node.trim();
    if (s.length >= 20) out.push(s);
    return out;
  }
  if (Array.isArray(node)) {
    node.forEach((v) => collectTextChunks(v, out));
    return out;
  }
  if (typeof node === 'object') {
    ['snippet', 'text', 'transcript', 'content', 'caption'].forEach((k) => {
      if (node[k]) collectTextChunks(node[k], out);
    });
    Object.values(node).forEach((v) => collectTextChunks(v, out));
  }
  return out;
}

async function getSerpTranscript(videoId) {
  if (!SERP_API_KEY || !videoId) return '';
  try {
    const url = `https://serpapi.com/search.json?engine=youtube_video_transcript&v=${videoId}&type=asr&language_code=en&api_key=${SERP_API_KEY}`;
    const res = await fetch(url);
    if (!res.ok) return '';
    const data = await res.json();
    const chunks = collectTextChunks(data.transcript || data.transcripts || data);
    return chunks.slice(0, 10).join(' ').slice(0, 300);
  } catch {
    return '';
  }
}

async function getSupadataTranscript(videoId, sourceLink) {
  if (!SUPADATA_API_KEY || !videoId) return '';
  const urls = [
    `https://api.supadata.ai/v1/youtube/transcript?videoId=${videoId}`,
    `https://api.supadata.ai/v1/transcript?videoId=${videoId}`,
    `https://api.supadata.ai/v1/transcript?url=${encodeURIComponent(sourceLink || '')}`,
  ];
  for (const u of urls) {
    try {
      const res = await fetch(u, { headers: { 'x-api-key': SUPADATA_API_KEY } });
      if (!res.ok) continue;
      const data = await res.json();
      const chunks = collectTextChunks(data.transcript || data.content || data);
      const text = chunks.slice(0, 10).join(' ').slice(0, 300);
      if (text) return text;
    } catch {
      // best-effort fallback
    }
  }
  return '';
}

async function fetchYoutubeTranscriptFallback(youtubeItems) {
  const byVideo = new Map();
  for (const item of youtubeItems) {
    const videoId = getYoutubeVideoId(item.sourceLink || '');
    if (videoId && !byVideo.has(videoId)) byVideo.set(videoId, item);
  }

  const fallback = [];
  let serpHits = 0;
  let supadataHits = 0;
  for (const [videoId, item] of Array.from(byVideo.entries()).slice(0, 4)) {
    let transcript = await getSerpTranscript(videoId);
    if (transcript) serpHits += 1;
    if (!transcript) {
      transcript = await getSupadataTranscript(videoId, item.sourceLink);
      if (transcript) supadataHits += 1;
    }
    if (!transcript) continue;

    fallback.push({
      source: 'YouTube Transcript Fallback',
      sourceLink: item.sourceLink,
      rawIdea: `${item.rawIdea} (transcript fallback)`,
      description: transcript,
    });
  }

  console.log(
    `YouTube transcript fallback: ${fallback.length} added (SerpApi=${serpHits}, Supadata=${supadataHits})`
  );
  return fallback;
}

function normalizeIdeaText(s = '') {
  return s
    .toLowerCase()
    .replace(/https?:\/\/\S+/g, '')
    .replace(/[^\w\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function dedupeRawIdeas(items) {
  const seen = new Set();
  const out = [];
  let dropped = 0;
  for (const item of items) {
    const key = normalizeIdeaText(item.rawIdea || '');
    if (!key) continue;
    if (seen.has(key)) {
      dropped += 1;
      continue;
    }
    seen.add(key);
    out.push(item);
  }
  if (dropped > 0) console.log(`Deduped raw ideas: removed ${dropped} near-duplicates`);
  return out;
}

// ─── Claude: Score + enrich a batch of ideas ─────────────────────────────────
async function callLlmText({ system, content, maxTokens = 4000, claudeModel = 'claude-sonnet-4-6', openaiModel = 'gpt-4o' }) {
  if (client) {
    try {
      const message = await client.messages.create({
        model: claudeModel,
        max_tokens: maxTokens,
        system,
        messages: [{ role: 'user', content }],
      });
      return message.content[0].text.trim();
    } catch (err) {
      console.log(`Claude failed (${err.message}); trying OpenAI fallback...`);
    }
  } else {
    console.log('Claude key missing; using OpenAI fallback...');
  }

  if (!OPENAI_API_KEY) throw new Error('OpenAI fallback unavailable: OPENAI_API_KEY not set');
  const res = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${OPENAI_API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: openaiModel,
      max_tokens: maxTokens,
      temperature: 0.2,
      messages: [
        { role: 'system', content: system },
        { role: 'user', content },
      ],
    }),
  });
  if (!res.ok) throw new Error(`OpenAI fallback failed: ${await res.text()}`);
  const data = await res.json();
  return (data.choices?.[0]?.message?.content || '').trim();
}

async function enrichBatch(items, offset) {
  const SYSTEM = IS_SHEETS_FOCUS
    ? 'You are a senior operations analyst for Google Sheets workflow design, schema governance, and AI-assisted spreadsheet automation.'
    : `You are a content strategist for ${COMPANY.name}, a licensed general contractor in ${COMPANY.location.headquarters} serving ${COMPANY.location.primaryMarket}. Services: ${COMPANY.services.core.slice(0,5).join(', ')}.`;
  const approvalRules = IS_SHEETS_FOCUS
    ? `AUTO-APPROVE if the idea:
- Improves spreadsheet clarity, dashboard usability, schema stability, or automation reliability
- Includes actionable structure (naming conventions, validation rules, filters, protections, mapping by headers)
- Helps prevent column drift, broken scripts, or inconsistent data entry

HOLD AS IDEA (don't approve) if:
- Generic productivity advice with no spreadsheet implementation details
- Tool promotion with no practical workflow pattern
- Not relevant to Google Sheets, dashboards, or AI spreadsheet operations`
    : `AUTO-APPROVE if the idea:
- Relates to construction, renovation, home improvement, additions, concrete, decks, commercial build-outs
- Could attract homeowners, investors, or commercial clients in South Florida
- Is NOT a handyman-only topic (installing a sink, hanging a picture, minor repairs)
- Is NOT exclusively about a trade we don't offer standalone (roofing only, plumbing only, electrical only)

HOLD AS IDEA (don't approve) if:
- Completely unrelated to construction/renovation
- Only relevant to a different region with no South Florida angle
- Pure DIY with no contractor value
- Ambiguous or could embarrass the company`;

  const userPrompt = `Review these raw content ideas collected from Reddit, YouTube, News, and Google.
For each idea, decide if it's relevant enough to write a blog post about.

${approvalRules}

RECENCY + COMPLETENESS RULES:
- Treat your recommendations as current best-practice framing for ${CURRENT_YEAR}.
- If a source appears outdated or incomplete, modernize the angle and avoid repeating stale specifics without caution.
- If a topic implies a process but omits steps, provide practical, high-level step guidance in topic_direction and reader_payoff.
- Do not invent legal/code guarantees; when uncertain, keep the angle useful and add verification language.
- Prefer ideas with concrete homeowner/investor/commercial value over generic commentary.
- If an idea depends on changing policy/pricing/code/regulation, include a "verify current local requirements" cue in reader_payoff.

For EVERY idea (both Approved and Idea), generate ALL of these fields — no exceptions:
topic_direction, focus_keyword, secondary_keyword, hook_professional, hook_emotional, hook_genz, master_hook, reader_payoff, ideal_for (Both/Blog Only/Reels Only), target_audience (Homeowner/Investor/Commercial/All), image_direction, social_one_liner, status (✅ Approved or 🆕 Idea)

Only skip an item entirely if it is completely unrelated to the current research goal.

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
Example: [{"index":1,"status":"✅ Approved","topic_direction":"...","focus_keyword":"...","secondary_keyword":"...","hook_professional":"...","hook_emotional":"...","hook_genz":"...","master_hook":"...","reader_payoff":"...","ideal_for":"Both","target_audience":"Homeowner","image_direction":"...","social_one_liner":"..."}]`;

  let raw = await callLlmText({
    system: SYSTEM,
    content: userPrompt,
    maxTokens: 8000,
    claudeModel: 'claude-opus-4-6',
    openaiModel: 'gpt-4o',
  });
  raw = raw.replace(/^```[a-z]*\n?/i, '').replace(/```$/, '').trim();
  return parseClaudeJsonArray(raw, offset, items.length);
}

function parseClaudeJsonArray(raw, offset, batchSize) {
  try {
    return JSON.parse(raw);
  } catch (firstErr) {
    const repaired = repairLikelyJson(raw);
    try {
      const parsed = JSON.parse(repaired);
      console.log(`Claude JSON parse repaired for batch starting at index ${offset + 1}`);
      return parsed;
    } catch (secondErr) {
      const preview = raw.slice(0, 600).replace(/\s+/g, ' ');
      throw new Error(
        `Claude JSON parse failed (batch ${offset + 1}-${offset + batchSize}). ` +
        `First error: ${firstErr.message}. Second error: ${secondErr.message}. ` +
        `Raw preview: ${preview}`
      );
    }
  }
}

function repairLikelyJson(raw) {
  let s = raw.trim();
  // Keep only the first JSON array block if Claude adds extra prose.
  const start = s.indexOf('[');
  const end = s.lastIndexOf(']');
  if (start !== -1 && end !== -1 && end > start) {
    s = s.slice(start, end + 1);
  }
  // Remove trailing commas before object/array close.
  s = s.replace(/,\s*([}\]])/g, '$1');
  // Normalize smart quotes occasionally emitted by models.
  s = s
    .replace(/[“”]/g, '"')
    .replace(/[‘’]/g, "'");
  return s;
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
  if (!GOOGLE_SHEET_ID || !SHEETS_TOKEN) {
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

  // Get access token via OAuth refresh
  const token = await getOAuthToken();

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
      (raw.description || '').slice(0, 500), // Notes (original source excerpt)
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

function buildResearchSummary(allRaw, enriched, meta) {
  const sourceCounts = allRaw.reduce((acc, i) => {
    const k = i.source || 'Unknown';
    acc[k] = (acc[k] || 0) + 1;
    return acc;
  }, {});
  const approved = enriched.filter((i) => i.status === '✅ Approved');
  const held = enriched.filter((i) => i.status !== '✅ Approved');
  const youtubeItems = allRaw.filter((i) => (i.source || '').toLowerCase().includes('youtube'));
  const topApproved = approved.slice(0, 15);

  const sourceLines = Object.entries(sourceCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `- ${k}: ${v}`)
    .join('\n');

  const ytLines = youtubeItems.slice(0, 20).map((i, idx) =>
    `${idx + 1}. ${i.rawIdea || '(no title)'}\n   Link: ${i.sourceLink || ''}\n   Extract: ${(i.description || '').slice(0, 260)}`
  ).join('\n\n');

  const approvedLines = topApproved.map((i, idx) => {
    const raw = allRaw[i.index - 1] || {};
    return `${idx + 1}. ${i.master_hook || i.topic_direction || '(no hook)'}\n` +
      `   Status: ${i.status || ''} | Audience: ${i.target_audience || ''} | Ideal: ${i.ideal_for || ''}\n` +
      `   Focus keyword: ${i.focus_keyword || ''}\n` +
      `   Reader payoff: ${i.reader_payoff || ''}\n` +
      `   Source: ${raw.source || ''} ${raw.sourceLink ? `(${raw.sourceLink})` : ''}`;
  }).join('\n\n');

  return [
    `Research Summary`,
    ``,
    `Date: ${TODAY}`,
    `Run mode: ${meta.mode}`,
    `Focus: ${meta.focus || 'default scheduled research'}`,
    `Total raw ideas: ${allRaw.length}`,
    `Enriched ideas: ${enriched.length}`,
    `Approved: ${approved.length}`,
    `Held as ideas: ${held.length}`,
    ``,
    `Tools and sources used`,
    `- Reddit JSON feed`,
    `- YouTube Data API`,
    `- NewsAPI`,
    `- SerpAPI Google Search`,
    `- LLM enrichment (Claude primary, OpenAI fallback)`,
    `- Google Sheets append write`,
    `- YouTube transcript fallback via SerpAPI transcript engine`,
    `${SUPADATA_API_KEY ? '- Supadata transcript fallback enabled' : '- Supadata transcript fallback not configured for this run'}`,
    ``,
    `Source counts`,
    sourceLines || '- none',
    ``,
    `Step-by-step what was researched`,
    `1. Pulled raw ideas from each configured source query set.`,
    `2. If source set was thin, attempted transcript fallback for YouTube items.`,
    `3. Deduplicated near-identical ideas.`,
    `4. Sent batches to LLM enrichment for structured qualification (Claude primary, OpenAI fallback).`,
    `5. Wrote structured rows into the Content Ideas sheet.`,
    `6. Created this run summary document for future consultation.`,
    ``,
    `YouTube findings (title + extract + link)`,
    ytLines || 'No YouTube items in this run.',
    ``,
    `Top approved ideas`,
    approvedLines || 'No approved ideas in this run.',
    ``,
    `Run rules enforced`,
    ...RESEARCH_DOC_RULES.map((r) => `- ${r}`),
    ``,
    `Notes`,
    `- This document is intended as durable research memory for future spreadsheet/website/process consultations.`,
  ].join('\n');
}

async function ensureResearchFolder(token) {
  const query = encodeURIComponent(
    `name='${RESEARCH_FOLDER_NAME.replace(/'/g, "\\'")}' and mimeType='application/vnd.google-apps.folder' and trashed=false`
  );
  const listUrl =
    `https://www.googleapis.com/drive/v3/files?q=${query}` +
    `&corpora=drive&driveId=${RESEARCH_DRIVE_ID}&includeItemsFromAllDrives=true` +
    `&supportsAllDrives=true&fields=files(id,name)&pageSize=20`;
  const listRes = await fetch(listUrl, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (listRes.ok) {
    const data = await listRes.json();
    if (data.files && data.files.length > 0) return data.files[0].id;
  }

  const createRes = await fetch(
    'https://www.googleapis.com/drive/v3/files?supportsAllDrives=true&fields=id,name,webViewLink',
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        name: RESEARCH_FOLDER_NAME,
        mimeType: 'application/vnd.google-apps.folder',
        parents: [RESEARCH_DRIVE_ID],
      }),
    }
  );
  if (!createRes.ok) {
    const err = await createRes.text();
    throw new Error(`Research folder create failed: ${err}`);
  }
  const created = await createRes.json();
  return created.id;
}

async function createResearchSummaryDoc(allRaw, enriched, meta) {
  if (!SHEETS_TOKEN) return null;
  const token = await getOAuthToken();
  const folderId = await ensureResearchFolder(token);
  const titleSafeFocus = (meta.focus || meta.mode || 'default')
    .replace(/[^\w\s-]/g, '')
    .slice(0, 60)
    .trim();
  const docName = `RESEARCH_${TODAY.replace(/\//g, '-')}_${titleSafeFocus || 'default'}`;

  const createDocRes = await fetch(
    'https://www.googleapis.com/drive/v3/files?supportsAllDrives=true&fields=id,name,webViewLink',
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        name: docName,
        mimeType: 'application/vnd.google-apps.document',
        parents: [folderId],
      }),
    }
  );
  if (!createDocRes.ok) {
    const err = await createDocRes.text();
    throw new Error(`Research doc create failed: ${err}`);
  }
  const doc = await createDocRes.json();

  const summaryText = buildResearchSummary(allRaw, enriched, meta);
  const updateRes = await fetch(
    `https://docs.googleapis.com/v1/documents/${doc.id}:batchUpdate`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        requests: [
          {
            insertText: {
              location: { index: 1 },
              text: summaryText,
            },
          },
        ],
      }),
    }
  );
  if (!updateRes.ok) {
    const err = await updateRes.text();
    throw new Error(`Research doc content write failed: ${err}`);
  }
  return doc.webViewLink || `https://docs.google.com/document/d/${doc.id}/edit`;
}

// ─── Google JWT Auth ──────────────────────────────────────────────────────────
async function getOAuthToken() {
  const raw = SHEETS_TOKEN;
  if (!raw) throw new Error('SHEETS_TOKEN not set');
  const td = JSON.parse(raw);
  const res = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      client_id: td.client_id,
      client_secret: td.client_secret,
      refresh_token: td.refresh_token,
      grant_type: 'refresh_token',
    }).toString(),
  });
  const data = await res.json();
  if (!data.access_token) throw new Error(`OAuth refresh failed: ${JSON.stringify(data)}`);
  return data.access_token;
}

// ─── Main ─────────────────────────────────────────────────────────────────────
(async () => {
  try {
    console.log(`\n=== Oak Park Construction Research Script — ${TODAY} ===\n`);
    console.log(`[AUTH] Anthropic key source: ${ANTHROPIC_KEY_SOURCE}; OpenAI fallback: ${OPENAI_API_KEY ? 'present' : 'missing'}`);
    if (RESEARCH_FOCUS) {
      console.log(`[FOCUS] ${RESEARCH_FOCUS}`);
      console.log(`[FOCUS_MODE] ${IS_SHEETS_FOCUS ? 'sheets' : 'default'}`);
    }

    // Collect from all sources in parallel
    const [reddit, youtube, news, serp] = await Promise.all([
      fetchReddit(),
      fetchYouTube(),
      fetchNews(),
      fetchSerp(),
    ]);

    let allRaw = [...reddit, ...youtube, ...news, ...serp];
    console.log(`\nTotal raw ideas collected: ${allRaw.length}`);

    // If primary sources are thin, append transcript-derived YouTube ideas at the bottom.
    if (allRaw.length < THIN_CONTENT_THRESHOLD && youtube.length > 0) {
      console.log(`Thin source set detected (${allRaw.length} < ${THIN_CONTENT_THRESHOLD}) — running transcript fallback...`);
      const transcriptFallbackItems = await fetchYoutubeTranscriptFallback(youtube);
      allRaw.push(...transcriptFallbackItems);
      console.log(`Total raw ideas after transcript fallback: ${allRaw.length}`);
    }

    allRaw = dedupeRawIdeas(allRaw);
    console.log(`Total raw ideas after dedupe: ${allRaw.length}`);

    if (allRaw.length === 0) {
      console.log('No ideas found this run. Exiting.');
      process.exit(0);
    }

    // Claude scores + enriches
    const enriched = await enrichWithClaude(allRaw);

    // Write to sheet (or print if no sheet connected)
    await writeToSheet(enriched, allRaw);
    const docLink = await createResearchSummaryDoc(allRaw, enriched, {
      mode: IS_SHEETS_FOCUS ? 'sheets-focus' : 'default',
      focus: RESEARCH_FOCUS,
    });
    if (docLink) console.log(`Research summary doc: ${docLink}`);

    const approved = enriched.filter(i => i.status === '✅ Approved').length;
    console.log(`\n✓ Done. ${approved} approved, ${enriched.length - approved} held as ideas.`);

  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  }
})();
