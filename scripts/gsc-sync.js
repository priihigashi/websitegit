// ─────────────────────────────────────────────────────────────────────────────
// Oak Park Construction — Google Search Console Sync
// Reads published blog URLs from the sheet, fetches GSC performance data,
// and writes Impressions, Clicks, Avg Position, CTR back to the sheet.
// New columns added at the end: W=Impressions, X=Clicks, Y=Avg Position, Z=CTR, AA=GSC Updated
// ─────────────────────────────────────────────────────────────────────────────

const GOOGLE_SHEET_ID  = process.env.GOOGLE_SHEET_ID;
const GOOGLE_SA_KEY    = process.env.GOOGLE_SA_KEY;
const GSC_SITE_URL     = process.env.GSC_SITE_URL; // e.g. https://oakpark-construction.com

// Column indexes (0-based)
const COL_BLOG_URL    = 20; // U — existing Blog URL column
const COL_STATUS      = 19; // T — Status
const COL_IMPRESSIONS = 22; // W
const COL_CLICKS      = 23; // X
const COL_POSITION    = 24; // Y
const COL_CTR         = 25; // Z
const COL_GSC_UPDATED = 26; // AA

const TODAY = new Date().toLocaleDateString('en-US', { timeZone: 'America/New_York' });
const DATE_90_DAYS_AGO = new Date(Date.now() - 90 * 24 * 60 * 60 * 1000)
  .toISOString().slice(0, 10);
const DATE_TODAY = new Date().toISOString().slice(0, 10);

function colLetter(index) {
  let result = '', n = index + 1;
  while (n > 0) {
    const rem = (n - 1) % 26;
    result = String.fromCharCode(65 + rem) + result;
    n = Math.floor((n - 1) / 26);
  }
  return result;
}

async function getGoogleToken(saKey) {
  const now = Math.floor(Date.now() / 1000);
  const header  = { alg: 'RS256', typ: 'JWT' };
  const payload = {
    iss: saKey.client_email,
    scope: [
      'https://www.googleapis.com/auth/spreadsheets',
      'https://www.googleapis.com/auth/webmasters.readonly',
    ].join(' '),
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

async function readSheet(token) {
  const res = await fetch(
    `https://sheets.googleapis.com/v4/spreadsheets/${GOOGLE_SHEET_ID}/values/Content%20Ideas!A:AA`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!res.ok) throw new Error(`Sheet read failed: ${await res.text()}`);
  const data = await res.json();
  return data.values || [];
}

async function fetchGSCData(token, pageUrl) {
  // Normalize URL — GSC is sensitive about trailing slashes and exact match
  const url = pageUrl.trim().replace(/\?.*$/, ''); // strip query params like ?p=123 (draft URLs)
  if (!url || url.includes('wp-admin') || url.includes('?p=')) return null;

  const res = await fetch(
    `https://searchconsole.googleapis.com/webmasters/v3/sites/${encodeURIComponent(GSC_SITE_URL)}/searchAnalytics/query`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        startDate: DATE_90_DAYS_AGO,
        endDate: DATE_TODAY,
        dimensions: ['page'],
        dimensionFilterGroups: [{
          filters: [{
            dimension: 'page',
            operator: 'equals',
            expression: url,
          }],
        }],
        rowLimit: 1,
      }),
    }
  );

  if (!res.ok) {
    const err = await res.text();
    // 403 usually means page hasn't appeared in search yet — not an error worth throwing
    if (res.status === 403 || res.status === 400) return null;
    throw new Error(`GSC API error ${res.status}: ${err}`);
  }

  const data = await res.json();
  const row = data.rows?.[0];
  if (!row) return null; // page hasn't appeared in search yet

  return {
    impressions: row.impressions || 0,
    clicks: row.clicks || 0,
    position: row.position ? Math.round(row.position * 10) / 10 : 0,
    ctr: row.ctr ? Math.round(row.ctr * 10000) / 100 : 0, // as percentage
  };
}

async function updateSheetRow(token, sheetRow, gscData) {
  const updates = [
    { range: `Content Ideas!${colLetter(COL_IMPRESSIONS)}${sheetRow}`, values: [[gscData.impressions]] },
    { range: `Content Ideas!${colLetter(COL_CLICKS)}${sheetRow}`,      values: [[gscData.clicks]] },
    { range: `Content Ideas!${colLetter(COL_POSITION)}${sheetRow}`,    values: [[gscData.position]] },
    { range: `Content Ideas!${colLetter(COL_CTR)}${sheetRow}`,         values: [[`${gscData.ctr}%`]] },
    { range: `Content Ideas!${colLetter(COL_GSC_UPDATED)}${sheetRow}`, values: [[TODAY]] },
  ];

  const res = await fetch(
    `https://sheets.googleapis.com/v4/spreadsheets/${GOOGLE_SHEET_ID}/values:batchUpdate`,
    {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ valueInputOption: 'USER_ENTERED', data: updates }),
    }
  );
  if (!res.ok) throw new Error(`Row update failed: ${await res.text()}`);
}

async function ensureGSCHeaders(token, rows) {
  // Add GSC column headers if they don't exist yet
  const header = rows[0] || [];
  if (header[COL_IMPRESSIONS]) return; // already set

  const updates = [
    { range: `Content Ideas!${colLetter(COL_IMPRESSIONS)}1`, values: [['GSC Impressions (90d)']] },
    { range: `Content Ideas!${colLetter(COL_CLICKS)}1`,      values: [['GSC Clicks (90d)']] },
    { range: `Content Ideas!${colLetter(COL_POSITION)}1`,    values: [['GSC Avg Position']] },
    { range: `Content Ideas!${colLetter(COL_CTR)}1`,         values: [['GSC CTR']] },
    { range: `Content Ideas!${colLetter(COL_GSC_UPDATED)}1`, values: [['GSC Last Updated']] },
  ];
  await fetch(
    `https://sheets.googleapis.com/v4/spreadsheets/${GOOGLE_SHEET_ID}/values:batchUpdate`,
    {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ valueInputOption: 'USER_ENTERED', data: updates }),
    }
  );
  console.log('GSC column headers added to sheet.');
}

(async () => {
  try {
    if (!GOOGLE_SHEET_ID || !GOOGLE_SA_KEY) {
      console.log('No Google Sheet credentials. Exiting.');
      process.exit(0);
    }
    if (!GSC_SITE_URL) {
      console.log('GSC_SITE_URL not set. Add it as a GitHub secret. Exiting.');
      process.exit(0);
    }

    const saKey = JSON.parse(Buffer.from(GOOGLE_SA_KEY, 'base64').toString('utf8'));
    const token = await getGoogleToken(saKey);

    console.log('Reading sheet...');
    const allRows = await readSheet(token);
    await ensureGSCHeaders(token, allRows);

    // Find rows that have a real published URL (not draft wp-admin links)
    const toCheck = allRows.slice(1)
      .map((row, i) => ({ row, sheetRow: i + 2 }))
      .filter(({ row }) => {
        const url = (row[COL_BLOG_URL] || '').trim();
        const status = (row[COL_STATUS] || '').trim();
        return url &&
          !url.includes('wp-admin') &&
          !url.includes('?p=') &&
          (status === '📤 Published' || status === '✍️ Draft Created');
      });

    if (toCheck.length === 0) {
      console.log('No published URLs to check. Mark posts as 📤 Published in the sheet to track them.');
      process.exit(0);
    }

    console.log(`Checking ${toCheck.length} URLs in Google Search Console...`);
    let updated = 0;

    for (const { row, sheetRow } of toCheck) {
      const url = row[COL_BLOG_URL].trim();
      console.log(`  Checking: ${url}`);
      const gscData = await fetchGSCData(token, url);
      if (gscData) {
        await updateSheetRow(token, sheetRow, gscData);
        console.log(`    → ${gscData.impressions} impressions, ${gscData.clicks} clicks, pos ${gscData.position}`);
        updated++;
      } else {
        console.log(`    → No data yet (page may not be indexed)`);
      }
    }

    console.log(`\n✓ Done. Updated GSC data for ${updated}/${toCheck.length} rows.`);

  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  }
})();
