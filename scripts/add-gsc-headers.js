// ─────────────────────────────────────────────────────────────────────────────
// One-time script: adds GSC tracking column headers to existing sheet
// Run once from: Actions → Run workflow → select "Add GSC Headers"
// Safe to run on a sheet that already has content — only touches row 1, cols W-AA
// ─────────────────────────────────────────────────────────────────────────────

const GOOGLE_SHEET_ID = process.env.GOOGLE_SHEET_ID;
const GOOGLE_SA_KEY   = process.env.GOOGLE_SA_KEY;

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
  if (!data.access_token) throw new Error(`Auth failed: ${JSON.stringify(data)}`);
  return data.access_token;
}

(async () => {
  const saKey = JSON.parse(Buffer.from(GOOGLE_SA_KEY, 'base64').toString('utf8'));
  const token = await getGoogleToken(saKey);

  const updates = [
    { range: 'Content Ideas!W1', values: [['GSC Impressions (90d)']] },
    { range: 'Content Ideas!X1', values: [['GSC Clicks (90d)']] },
    { range: 'Content Ideas!Y1', values: [['GSC Avg Position']] },
    { range: 'Content Ideas!Z1', values: [['GSC CTR']] },
    { range: 'Content Ideas!AA1', values: [['GSC Last Updated']] },
  ];

  const res = await fetch(
    `https://sheets.googleapis.com/v4/spreadsheets/${GOOGLE_SHEET_ID}/values:batchUpdate`,
    {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ valueInputOption: 'USER_ENTERED', data: updates }),
    }
  );

  if (!res.ok) throw new Error(`Failed: ${await res.text()}`);
  console.log('✓ GSC column headers added to sheet (columns W–AA, row 1).');
  console.log('  Your existing content rows are untouched.');
})();
