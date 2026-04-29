const Anthropic = require('@anthropic-ai/sdk');

const WP_URL = process.env.WP_URL;
const WP_USERNAME = process.env.WP_USERNAME;
const WP_APP_PASSWORD = process.env.WP_APP_PASSWORD;

const client = new Anthropic();

async function getDrafts() {
  const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');
  const res = await fetch(`${WP_URL}/wp-json/wp/v2/posts?status=draft&per_page=50&orderby=date&order=asc`, {
    headers: { Authorization: `Basic ${credentials}` }
  });
  if (!res.ok) throw new Error(`WP fetch failed: ${res.status} ${await res.text()}`);
  return res.json();
}

async function analyzeDrafts(drafts) {
  const topicsText = drafts.map((p, i) => 
    `${i+1}. Title: "${p.title.rendered}" | Excerpt: "${(p.excerpt?.rendered || '').replace(/<[^>]+>/g,'').slice(0,200)}"`
  ).join('\n');

  const response = await client.messages.create({
    model: 'claude-haiku-4-5-20251001',
    max_tokens: 800,
    messages: [{
      role: 'user',
      content: `You are reviewing draft blog posts for Oak Park Construction — a licensed general contractor in South Florida (Broward County).

A post is SAFE to publish if ALL 5 are true:
1. SERVICE: About construction services homeowners hire for (additions, renovations, concrete, permits, kitchen/bath, ADU, materials, hiring tips)
2. EVERGREEN: Not tied to breaking news or "right now" legislation  
3. NEUTRAL: No political, immigration, labor-workforce, or divisive angle
4. GEOGRAPHIC: South Florida / Broward / Miami-Dade OR broad homeowner advice
5. LEGAL-SAFE: No lawsuit context, no specific cost promises, no competitor names

Reply ONLY with number and PUBLISH or DRAFT per line. Nothing else.

${topicsText}`
    }]
  });

  return response.content[0].text.trim();
}

async function main() {
  const drafts = await getDrafts();
  console.log(`Found ${drafts.length} draft posts`);
  
  if (drafts.length === 0) { console.log('No drafts to analyze.'); return; }
  
  drafts.forEach((p, i) => {
    console.log(`${i+1}. [${p.id}] ${p.title.rendered} | Created: ${p.date.slice(0,10)}`);
  });

  console.log('\nAnalyzing safety...\n');
  const results = await analyzeDrafts(drafts);
  console.log('RESULTS:\n' + results);

  // Output structured for parsing
  const lines = results.split('\n');
  console.log('\n--- SUMMARY ---');
  const publish = [], draft = [];
  lines.forEach(line => {
    const m = line.match(/^(\d+)\.\s*(PUBLISH|DRAFT)/i);
    if (!m) return;
    const idx = parseInt(m[1]) - 1;
    if (idx < 0 || idx >= drafts.length) return;
    const post = drafts[idx];
    if (m[2].toUpperCase() === 'PUBLISH') publish.push(`[${post.id}] ${post.title.rendered}`);
    else draft.push(`[${post.id}] ${post.title.rendered}`);
  });
  console.log(`SAFE TO PUBLISH (${publish.length}):`);
  publish.forEach(p => console.log('  ✅ ' + p));
  console.log(`KEEP AS DRAFT (${draft.length}):`);
  draft.forEach(p => console.log('  ✍️ ' + p));
}

main().catch(e => { console.error(e.message); process.exit(1); });
