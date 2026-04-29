const WP_URL = process.env.WP_URL;
const WP_USERNAME = process.env.WP_USERNAME;
const WP_APP_PASSWORD = process.env.WP_APP_PASSWORD;

const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');

// Unique safe posts to publish (duplicates left as draft)
const PUBLISH_IDS = [6683, 6686, 6705, 6708, 6711, 6714, 6720, 6729];

async function publishPost(id) {
  const res = await fetch(`${WP_URL}/wp-json/wp/v2/posts/${id}`, {
    method: 'POST',
    headers: { Authorization: `Basic ${credentials}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: 'publish' })
  });
  const data = await res.json();
  if (!res.ok) throw new Error(`Failed ${id}: ${JSON.stringify(data)}`);
  return data.link;
}

async function main() {
  console.log(`Publishing ${PUBLISH_IDS.length} safe unique posts...`);
  for (const id of PUBLISH_IDS) {
    try {
      const link = await publishPost(id);
      console.log(`✅ Published [${id}]: ${link}`);
    } catch (e) {
      console.log(`❌ Error [${id}]: ${e.message}`);
    }
  }
  console.log('\nDone. Duplicates left as draft: 6726, 6732, 6735, 6741, 6744, 6747');
}

main().catch(e => { console.error(e.message); process.exit(1); });
