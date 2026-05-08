// ─────────────────────────────────────────────────────────────────────────────
// Oak Park Construction — Fill Missing Sheet Columns
// Runs daily via GitHub Actions
// Reads all rows from Google Sheet, finds ones with empty key columns,
// sends them to Claude to fill in the blanks, writes back to sheet
// ─────────────────────────────────────────────────────────────────────────────

const Anthropic = require('@anthropic-ai/sdk');
const { COMPANY } = require('./company-info.js');

const GOOGLE_SHEET_ID = process.env.GOOGLE_SHEET_ID;
const SHEETS_TOKEN    = process.env.SHEETS_TOKEN;
const ANTHROPIC_KEY   = process.env.CLAUDE_KEY_4_CONTENT || process.env.ANTHROPIC_API_KEY || '';
const OPENAI_KEY      = process.env.OPENAI_API_KEY || '';
const MAX_ROWS_PER_RUN = Math.max(1, Number.parseInt(process.env.MAX_ROWS_PER_RUN || '60', 10) || 60);
const WRITE_CHUNK_SIZE = Math.max(1, Number.parseInt(process.env.WRITE_CHUNK_SIZE || '500', 10) || 500);
const client = ANTHROPIC_KEY ? new Anthropic({ apiKey: ANTHROPIC_KEY }) : null;
let anthropicUnavailable = false;

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

async function updateRows(token, results) {
  const updates = [];
  for (const { rowIndex, filled } of results) {
    const sheetRow = rowIndex + 2; // +1 for header, +1 for 1-indexing
    for (const col of FILLABLE_COLS) {
      if (filled[col] === undefined) continue;
      updates.push({
        range: `Content Ideas!${colLetter(COLS[col])}${sheetRow}`,
        values: [[filled[col]]],
      });
    }
  }

  for (let i = 0; i < updates.length; i += WRITE_CHUNK_SIZE) {
    const chunk = updates.slice(i, i + WRITE_CHUNK_SIZE);
    console.log(`Writing cells ${i + 1}-${i + chunk.length} of ${updates.length}...`);
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
          data: chunk,
        }),
      }
    );
    if (!res.ok) throw new Error(`Rows batch update failed: ${await res.text()}`);
  }
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
    console.log(`Filling batch ${Math.floor(i / BATCH_SIZE) + 1}/${Math.ceil(rows.length / BATCH_SIZE)} (${batch.length} rows)...`);

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

    const system = `You are a content strategist for ${COMPANY.name}, a licensed general contractor in ${COMPANY.location.headquarters} serving ${COMPANY.location.primaryMarket}. ${COMPANY.contentRules.politicalNeutrality}`;
    const prompt = `The following content ideas from our spreadsheet are missing some columns. Fill ONLY the missing fields for each item. Use the raw idea title and source link to understand the topic.

${items.map(item => `Item ${item.num}:
  Raw idea: "${item.rawIdea}"
  Source: ${item.source} — ${item.sourceLink}
  Already has: ${Object.entries(item.existing).filter(([,v]) => v).map(([k,v]) => `${k}: "${v}"`).join(', ') || 'nothing'}
  Missing fields to fill: ${item.missing.join(', ')}`).join('\n\n')}

For each item return ONLY the missing fields. All content should be relevant to South Florida construction clients.

Return ONLY a JSON array, no markdown fences:
[{"num":1,"topic_direction":"...","focus_keyword":"...","secondary_keyword":"...","hook_professional":"...","hook_emotional":"...","hook_genz":"...","master_hook":"...","reader_payoff":"...","ideal_for":"Both","target_audience":"Homeowner","image_direction":"...","social_one_liner":"..."}]
Only include fields that were listed as missing for each item.`;

    let raw = '';
    if (client && !anthropicUnavailable) {
      try {
        const message = await client.messages.create({
          model: 'claude-sonnet-4-5',
          max_tokens: 6000,
          system,
          messages: [{ role: 'user', content: prompt }],
        });
        raw = message.content[0].text.trim();
      } catch (err) {
        console.log(`Claude fill failed (${err.message}); trying OpenAI fallback...`);
        if ((err.message || '').toLowerCase().includes('credit balance is too low')) {
          anthropicUnavailable = true;
        }
      }
    }
    if (!raw) {
      raw = await fillWithOpenAI(system, prompt);
    }
    const filled = parseJsonArray(raw);
    console.log(`Parsed ${filled.length} filled item(s) for batch.`);

    for (const item of filled) {
      const itemNum = Number.parseInt(item.num, 10);
      if (!Number.isInteger(itemNum) || itemNum < 1 || itemNum > batch.length) {
        console.log(`Skipping filled item with invalid num: ${JSON.stringify(item).slice(0, 200)}`);
        continue;
      }
      const original = batch[itemNum - 1];
      results.push({ rowIndex: original.rowIndex, filled: item });
    }
  }

  return results;
}

function parseJsonArray(raw) {
  let cleaned = (raw || '').trim().replace(/^```[a-z]*\n?/i, '').replace(/```$/i, '').trim();
  try {
    const parsed = JSON.parse(cleaned);
    if (Array.isArray(parsed)) return parsed;
    if (Array.isArray(parsed.items)) return parsed.items;
    if (Array.isArray(parsed.results)) return parsed.results;
  } catch (_) {
    // Fall through to bracket extraction.
  }
  const start = cleaned.indexOf('[');
  const end = cleaned.lastIndexOf(']');
  if (start >= 0 && end > start) {
    return JSON.parse(cleaned.slice(start, end + 1));
  }
  throw new Error(`AI returned non-array JSON: ${cleaned.slice(0, 300)}`);
}

async function fillWithOpenAI(system, prompt) {
  if (!OPENAI_KEY) throw new Error('No AI fill provider available: CLAUDE_KEY_4_CONTENT/ANTHROPIC_API_KEY and OPENAI_API_KEY missing or unusable');
  const res = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${OPENAI_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: 'gpt-4o-mini',
      temperature: 0.2,
      messages: [
        { role: 'system', content: system },
        { role: 'user', content: prompt },
      ],
    }),
  });
  if (!res.ok) throw new Error(`OpenAI fill failed: ${await res.text()}`);
  const data = await res.json();
  return (data.choices?.[0]?.message?.content || '').trim();
}

(async () => {
  try {
    if (!GOOGLE_SHEET_ID || !SHEETS_TOKEN) {
      console.log('No Google Sheet credentials configured. Exiting.');
      process.exit(0);
    }

    const token = await getOAuthToken();

    console.log('Reading sheet...');
    const allRows = await readSheet(token);

    // Skip header row (index 0)
    const dataRows = allRows.slice(1);
    const needsFilling = dataRows
      .map((row, i) => ({ rowIndex: i, row }))
      .filter(r => rowNeedsFilling(r.row));
    const rowsThisRun = needsFilling.slice(0, MAX_ROWS_PER_RUN);

    if (needsFilling.length === 0) {
      console.log('All rows are complete. Nothing to fill.');
      process.exit(0);
    }

    console.log(`Found ${needsFilling.length} rows with missing columns. Processing ${rowsThisRun.length} this run...`);

    const results = await fillWithClaude(rowsThisRun);

    console.log(`Updating ${results.length} rows in sheet...`);
    const mappedResults = results.map(({ rowIndex, filled }) => ({
      rowIndex,
      filled: Object.fromEntries(
        Object.entries({
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
        }).filter(([, v]) => v !== undefined && v !== '')
      ),
    })).filter(({ filled }) => Object.keys(filled).length > 0);
    await updateRows(token, mappedResults);

    console.log(`✓ Done. Filled missing columns for ${mappedResults.length} rows.`);

  } catch (err) {
    console.error('Error:', err.message);
    process.exit(1);
  }
})();
