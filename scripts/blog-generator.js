const Anthropic = require('@anthropic-ai/sdk');
const fetch = require('node-fetch');
const fs = require('fs');
const { getRandomTopic } = require('./topics.js');

const client = new Anthropic();

// ─── Configuration ────────────────────────────────────────────────────────────
const WP_URL = process.env.WP_URL;
const WP_USERNAME = process.env.WP_USERNAME;
const WP_APP_PASSWORD = process.env.WP_APP_PASSWORD;
const MANUAL_TOPIC = process.env.MANUAL_TOPIC || '';

// ─── Step 1: Pick a topic ─────────────────────────────────────────────────────
const topic = MANUAL_TOPIC.trim() || getRandomTopic();
console.log(`Topic selected: ${topic}`);

// ─── Step 2: Generate blog post with Claude ───────────────────────────────────
async function generatePost(topic) {
  console.log('Calling Claude API...');

  const message = await client.messages.create({
    model: 'claude-opus-4-6',
    max_tokens: 2048,
    messages: [
      {
        role: 'user',
        content: `You are a professional blog writer for Oak Park Construction, a construction company in the Oak Park / Chicago area that specializes in residential construction, commercial construction, renovation, new additions, shell construction, and concrete construction.\n\nWrite a complete, SEO-optimized blog post about the following topic:\n"${topic}"\n\nRequirements:\n- Length: 800-1200 words\n- Format: HTML (use <h2>, <h3>, <p>, <ul>, <li> tags — no <html>, <head>, or <body> tags)\n- Include a compelling title (return it separately, not in the HTML)\n- Include a meta description (150-160 characters, return it separately)\n- Use natural keywords related to construction, renovation, Oak Park, and Chicago area\n- Tone: professional, trustworthy, helpful — not salesy\n- End with a brief call to action to contact Oak Park Construction\n- Where images would help, insert: <!-- IMAGE: [description of ideal photo here] -->\n\nReturn your response in this exact JSON format:\n{\n  "title": "The Blog Post Title Here",\n  "meta_description": "150-160 char meta description here",\n  "html_content": "<h2>First section...</h2><p>...</p>"\n}\n\nReturn ONLY valid JSON. Do not wrap in markdown code fences. No other text.`
      }
    ]
  });

  // Strip markdown code fences if Claude includes them
  let raw = message.content[0].text.trim();
  raw = raw.replace(/^```[a-z]*\n?/i, '').replace(/```$/,'').trim();

  const post = JSON.parse(raw);
  console.log(`Post generated: "${post.title}"`);
  return post;
}

// ─── Step 3: Post to WordPress as Draft ──────────────────────────────────────
async function postToWordPress(post) {
  console.log('Posting to WordPress...');

  const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');

  const response = await fetch(`${WP_URL}/wp-json/wp/v2/posts`, {
    method: 'POST',
    headers: {
      'Authorization': `Basic ${credentials}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      title: post.title,
      content: post.html_content,
      excerpt: post.meta_description,
      status: 'draft',
      categories: [],
      tags: [],
    })
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`WordPress API error ${response.status}: ${error}`);
  }

  const result = await response.json();
  console.log(`Draft created! ID: ${result.id}`);
  console.log(`Edit link: ${result.link}`);

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
    const result = await postToWordPress(post);

    fs.writeFileSync('scripts/output.json', JSON.stringify(result, null, 2));

    console.log('\n✓ Done! Draft saved to WordPress.');
    console.log(`  Title: ${result.title}`);
    console.log(`  Edit:  ${result.editLink}`);
  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  }
})();
