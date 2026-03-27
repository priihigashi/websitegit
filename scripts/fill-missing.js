// ─────────────────────────────────────────────────────────────────────────────
// Oak Park Construction — Fill Missing Sheet Columns
// Runs daily via GitHub Actions
// Reads all rows from Google Sheet, finds ones with empty key columns,
// sends them to Claude to fill in the blanks, writes back to sheet
// ─────────────────────────────────────────────────────────────────────────────

const Anthropic = require('@anthropic-ai/sdk');
const { COMPANY } = require('./company-info.js');

const client = new Anthropic();

const GOOGLE_SHEET_ID = process.env.GOOGLE_SHEET_ID;
const GOOGLE_SA_KEY   = process.env.GOOGLE_SA_KEY;

// Column index map (0-based, matching the 22-column sheet layout)
const COLS = {
  dateAdded:        0,
  addedBy:          1,
  source:           2,
  sourceLink:       3,
  rawIdea:          4,
  topicDirection:   5,
  crossSignal:      6,
  focusKeyword:     7,
  secondaryKeyword: 8,
  hookProfessional: 9,
  hookEmotional:    10,
  hookGenZ:         11,
  masterHook:       12,
  readerPayoff:     13,
  idealFor:         14,
  targetAudience:   15,
  imageDirection:   16,
  wpCategoryId:     17,
  socialOneLiner:   18,
  status:           19,
  blogUrl:          20,
  notes:            21,
};

// Columns Claude should fill (if empty)
const FILLABLE_COLS = [
  'topicDirection', 'focusKeyword', 'secondaryKeyword',
  'hookProfessional', 'hookEmotional', 'hookGenZ',
  'masterHook', 'readerPayoff', 'idealFor',
  'targetAudience', 'imageDirection', 'socialOneLiner',
];

function rowNeedsFilling(row) {
  // Must have a raw idea to work from
  const rawIdea = row[COLS.rawIdea];
  if (!rawIdea || rawIdea.trim() === '') return false;

  // Check if any fillable column is empty
  return FILLABLE_COLS.some(col => {
    const val = row[COLS[col]];
    return !val || val.trim() === '';
  });
}

async function getGoogleToken(saKey) {
  const now = Math.floor(Date.now() / 1000);
  const header  = { alg: 'RS256', typ: 'JWT' };
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

async function readSheet(token) {
  const res = await fetch(
    `https://sheets.googleapis.com/v4/spreadsheets/${GOOGLE_SHEET_ID}/values/Content%20Ideas!A:V`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  if (!res.ok) throw new Error(`Sheet read failed: ${await res.text()}`);
  const data = await res.json();
  return data.values || [];
}

async function updateRow(token, rowIndex, filled) {
  // rowIndex is 0-based from values array; sheet row = rowIndex + 1 (1-indexed, +1 for header)
  const sheetRow = rowIndex + 2; // +1 for header, +1 for 1-indexing

  // Build individual cell updates for only the columns that changed
  const updates = FILLABLE_COLS
    .filter(col => filled[col] !== undefined)
    .map(col => ({
      range: `Content Ideas!${colLetter(COLS[col])}${sheetRow}`,
      values: [[filled[col]]],
    }));

  if (updates.length === 0) return;

  const res = await fetch(
    `https://sheets.googleapis.com/v4/spreadsheets/${GOOGLE_SHEET_ID}/values:batchUpdate`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        valueInputOption: 'USER_ENTERED',
        data: updates,
      }),
    }
  );
  if (!res.ok) throw new Error(`Row update failed: ${await res.text()}`);
}

function colLetter(index) {
  // Convert 0-based column index to letter (A, B, ... Z, AA, etc.)
  let result = '';
  let n = index + 1;
  while (n > 0) {
    const rem = (n - 1) % 26;
    result = String.fromCharCode(65 + rem) + result;
    n = Math.floor((n - 1) / 26);
  }
  return result;
}

async function fillWithClaude(rows) {
  // rows = array of { rowIndex, row }
  const BATCH_SIZE = 8;
  const results = [];

  for (let i = 0; i < rows.length; i += BATCH_SIZE) {
    const batch = rows.slice(i, i + BATCH_SIZE);

    const items = batch.map((r, idx) => {
      const row = r.row;
      const missing = FILLABLE_COLS.filter(col => {
        const val = row[COLS[col]];
        return !val || val.trim() === '';
      });
      return {
        num: idx + 1,
        rawIdea: row[COLS.rawIdea] || '',
        sourceLink: row[COLS.sourceLink] || '',
        source: row[COLS.source] || '',
        existing: {
          topicDirection: row[COLS.topicDirection] || '',
          focusKeyword: row[COLS.focusKeyword] || '',
        },
        missing,
      };
    });

    const message = await client.messages.create({
      model: 'claude-opus-4-6',
      max_tokens: 6000,
      system: `You are a content strategist for ${COMPANY.name}, a licensed general contractor in ${COMPANY.location.headquarters} serving ${COMPANY.location.primaryMarket}. ${COMPANY.contentRules.politicalNeutrality}`,
      messages: [{
        role: 'user',
        content: `The following content ideas from our spreadsheet are missing some columns. Fill ONLY the missing fields for each item. Use the raw idea title and source link to understand the topic.

${items.map(item => `Item ${item.num}:
  Raw idea: "${item.rawIdea}"
  Source: ${item.source} — ${item.sourceLink}
  Already has: ${Object.entries(item.existing).filter(([,v]) => v).map(([k,v]) => `${k}: "${v}"`).join(', ') || 'nothing'}
  Missing fields to fill: ${item.missing.join(', ')}`).join('\n\n')}

For each item return ONLY the missing fields. All content should be relevant to South Florida construction clients.

Return ONLY a JSON array, no markdown fences:
[{"num":1,"topic_direction":"...","focus_keyword":"...","secondary_keyword":"...","hook_professional":"...","hook_emotional":"...","hook_genz":"...","master_hook":"...","reader_payoff":"...","ideal_for":"Both","target_audience":"Homeowner","image_direction":"...","social_one_liner":"..."}]
Only include fields that were listed as missing for each item.`,
      }],
    });

    let raw = message.content[0].text.trim();
    raw = raw.replace(/^```[a-z]*\n?/i, '').replace(/```$/, '').trim();
    const filled = JSON.parse(raw);

    for (const item of filled) {
      const original = batch[item.num - 1];
      results.push({ rowIndex: original.rowIndex, filled: item });
    }
  }

  return results;
}

(async () => {
  try {
    if (!GOOGLE_SHEET_ID || !GOOGLE_SA_KEY) {
      console.log('No Google Sheet credentials configured. Exiting.');
      process.exit(0);
    }

    const saKey = JSON.parse(Buffer.from(GOOGLE_SA_KEY, 'base64').toString('utf8'));
    const token = await getGoogleToken(saKey);

    console.log('Reading sheet...');
    const allRows = await readSheet(token);

    // Skip header row (index 0)
    const dataRows = allRows.slice(1);
    const needsFilling = dataRows
      .map((row, i) => ({ rowIndex: i, row }))
      .filter(r => rowNeedsFilling(r.row));

    if (needsFilling.length === 0) {
      console.log('All rows are complete. Nothing to fill.');
      process.exit(0);
    }

    console.log(`Found ${needsFilling.length} rows with missing columns. Sending to Claude...`);

    const results = await fillWithClaude(needsFilling);

    console.log(`Updating ${results.length} rows in sheet...`);
    for (const { rowIndex, filled } of results) {
      const mapped = {
        topicDirection:   filled.topic_direction,
        focusKeyword:     filled.focus_keyword,
        secondaryKeyword: filled.secondary_keyword,
        hookProfessional: filled.hook_professional,
        hookEmotional:    filled.hook_emotional,
        hookGenZ:         filled.hook_genz,
        masterHook:       filled.master_hook,
        readerPayoff:     filled.reader_payoff,
        idealFor:         filled.ideal_for,
        targetAudience:   filled.target_audience,
        imageDirection:   filled.image_direction,
        socialOneLiner:   filled.social_one_liner,
      };
      // Only update columns that have new values
      const toUpdate = Object.fromEntries(
        Object.entries(mapped).filter(([, v]) => v !== undefined && v !== '')
      );
      await updateRow(token, rowIndex, toUpdate);
    }

    console.log(`✓ Done. Filled missing columns for ${results.length} rows.`);

  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  }
})();
