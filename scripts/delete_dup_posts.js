const WP_URL = process.env.WP_URL;
const WP_USERNAME = process.env.WP_USERNAME;
const WP_APP_PASSWORD = process.env.WP_APP_PASSWORD;
const credentials = Buffer.from(`${WP_USERNAME}:${WP_APP_PASSWORD}`).toString('base64');
const headers = { Authorization: `Basic ${credentials}` };

const TO_DELETE = [6711, 6683, 6705];

async function main() {
  for (const id of TO_DELETE) {
    const res = await fetch(`${WP_URL}/wp-json/wp/v2/posts/${id}?force=true`, {
      method: 'DELETE',
      headers
    });
    const data = await res.json();
    if (res.ok) {
      console.log(`DELETED [${id}] "${data.previous?.title?.rendered || data.title?.rendered || '?'}"`);
    } else {
      console.error(`FAILED [${id}]: ${JSON.stringify(data)}`);
    }
  }
}

main().catch(e => { console.error(e.message); process.exit(1); });
