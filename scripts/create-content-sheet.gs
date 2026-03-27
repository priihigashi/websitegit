// ─────────────────────────────────────────────────────────────────────────────
// Oak Park Construction — Content Ideas Sheet Setup
// HOW TO USE:
// 1. Go to sheets.google.com → create a new blank sheet
// 2. Click Extensions → Apps Script
// 3. Delete everything in the editor, paste this entire script
// 4. Click Run → authorize → done. Your sheet is ready.
// ─────────────────────────────────────────────────────────────────────────────

function createContentSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName('Content Ideas');
  if (sheet) ss.deleteSheet(sheet);
  sheet = ss.insertSheet('Content Ideas');

  // ── Column definitions ────────────────────────────────────────────────────
  const columns = [
    { name: 'Date Added',        width: 110 },
    { name: 'Added By',          width: 100 },  // dropdown
    { name: 'Source',            width: 120 },  // dropdown
    { name: 'Source Link',       width: 220 },
    { name: 'Raw Idea',          width: 260 },
    { name: 'Topic Direction',   width: 260 },
    { name: 'Cross-Signal?',     width: 110 },  // dropdown
    { name: 'Focus Keyword',     width: 220 },
    { name: 'Secondary Keyword', width: 220 },
    { name: 'Hook: Professional',width: 300 },
    { name: 'Hook: Emotional',   width: 300 },
    { name: 'Hook: GenZ',        width: 300 },
    { name: 'Master Hook',       width: 340 },
    { name: 'Reader Payoff',     width: 300 },
    { name: 'Ideal For',         width: 130 },  // dropdown
    { name: 'Target Audience',   width: 140 },  // dropdown
    { name: 'Image Direction',   width: 240 },
    { name: 'WP Category ID',    width: 140 },  // dropdown
    { name: 'Social One-Liner',  width: 300 },
    { name: 'Status',            width: 130 },  // dropdown
    { name: 'Blog URL',          width: 240 },
    { name: 'Notes',             width: 220 },
  ];

  // ── Write headers ─────────────────────────────────────────────────────────
  const headers = columns.map(c => c.name);
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);

  // ── Style header row — Black / White / Neon (Lato font) ──────────────────
  const headerRange = sheet.getRange(1, 1, 1, headers.length);
  headerRange
    .setBackground('#000000')
    .setFontColor('#39FF14')       // neon green
    .setFontWeight('bold')
    .setFontSize(11)
    .setFontFamily('Lato')
    .setWrap(false);

  // Neon cyan accent on key creative columns (hooks + master hook + payoff)
  const accentCols = [10, 11, 12, 13, 14]; // Hook cols + Reader Payoff
  accentCols.forEach(col => {
    sheet.getRange(1, col, 1, 1)
      .setBackground('#000000')
      .setFontColor('#00FFFF');    // neon cyan for hook/payoff headers
  });

  // Neon pink accent on status column header
  sheet.getRange(1, 20, 1, 1)
    .setBackground('#000000')
    .setFontColor('#FF00FF');      // neon pink for status

  sheet.setFrozenRows(1);

  // ── Column widths ─────────────────────────────────────────────────────────
  columns.forEach((col, i) => {
    sheet.setColumnWidth(i + 1, col.width);
  });

  // ── Row height for data rows ──────────────────────────────────────────────
  sheet.setRowHeightsForced(2, 500, 80);

  // ── Dropdowns ─────────────────────────────────────────────────────────────
  const dataRange = (col) => sheet.getRange(2, col, 500, 1);
  const dropdown = (values) => SpreadsheetApp.newDataValidation()
    .requireValueInList(values, true).setAllowInvalid(false).build();

  // Added By (col 2)
  dataRange(2).setDataValidation(dropdown(['AI', 'Manual']));

  // Source (col 3)
  dataRange(3).setDataValidation(dropdown(['Reddit', 'YouTube', 'Google Trends', 'News', 'SerpAPI', 'Manual']));

  // Cross-Signal? (col 7)
  dataRange(7).setDataValidation(dropdown(['✅ Yes — multiple sources', '— No']));

  // Ideal For (col 15)
  dataRange(15).setDataValidation(dropdown(['Both', 'Blog Only', 'Reels Only', 'Needs Adapting']));

  // Target Audience (col 16)
  dataRange(16).setDataValidation(dropdown(['Homeowner', 'Investor', 'Commercial', 'All']));

  // WP Category ID (col 18)
  dataRange(18).setDataValidation(dropdown([
    '59 — Kitchen',
    '75 — Investment Property',
    '76 — New Construction',
    '55 — News Flash',
    'Uncategorized'
  ]));

  // Status (col 20)
  dataRange(20).setDataValidation(dropdown([
    '🆕 Idea',
    '✅ Approved',
    '✍️ Draft Created',
    '📤 Published',
    '❌ Skipped'
  ]));

  // ── Conditional formatting for Status column — neon on black ─────────────
  const statusCol = sheet.getRange(2, 20, 500, 1);
  const rules = [
    { text: '📤 Published',     bg: '#000000', fg: '#39FF14' }, // neon green
    { text: '✍️ Draft Created', bg: '#000000', fg: '#FFD700' }, // neon yellow
    { text: '✅ Approved',      bg: '#000000', fg: '#00FFFF' }, // neon cyan
    { text: '❌ Skipped',       bg: '#000000', fg: '#FF3131' }, // neon red
    { text: '🆕 Idea',          bg: '#1a1a1a', fg: '#ffffff' }, // white on near-black
  ];
  const cfRules = rules.map(r =>
    SpreadsheetApp.newConditionalFormatRule()
      .whenTextContains(r.text.replace(/[^\w\s]/g, '').trim().split(' ')[0])
      .setBackground(r.bg)
      .setFontColor(r.fg)
      .setRanges([statusCol])
      .build()
  );
  sheet.setConditionalFormatRules(cfRules);

  // ── Row colors — alternating dark/darker for all data rows ───────────────
  for (let r = 2; r <= 501; r++) {
    const bg = r % 2 === 0 ? '#111111' : '#1a1a1a';
    sheet.getRange(r, 1, 1, headers.length)
      .setBackground(bg)
      .setFontColor('#ffffff')
      .setFontFamily('Lato')
      .setFontSize(10);
  }

  // ── Wrap text for hook/payoff columns ────────────────────────────────────
  [10,11,12,13,14,19].forEach(col => {
    sheet.getRange(2, col, 500, 1).setWrap(true).setVerticalAlignment('top');
  });

  // ── Add sample rows from brainstorm ──────────────────────────────────────
  const samples = [
    ['=TODAY()','Manual','Manual','','Quartz vs Granite Countertops','Countertop material comparison for South FL homes','— No','quartz vs granite countertops Broward','best countertop material South Florida','Quartz vs. Granite: Which Countertop Is Right for Your South Florida Home?','You\'re About to Spend $4,000 — Make Sure You Pick the Right One','quartz or granite?? i asked a contractor so you don\'t have to 👀','Quartz or Granite? A South Florida Contractor Settles This Once and For All','They know exactly which material fits their budget, lifestyle & Florida humidity','Both','Homeowner','Split shot: quartz slab left, granite right, bright kitchen','59 — Kitchen','Quartz or granite — here\'s what we always tell our clients in Fort Lauderdale 🔨','🆕 Idea','',''],
    ['=TODAY()','Manual','Manual','','Concrete Porch Install – Time-lapse','Concrete porch/slab installation South FL','— No','concrete porch installation South Florida','concrete contractor Broward County','What Goes Into a Concrete Porch Install in South Florida?','Before We Poured 500 Sq Ft — Here\'s What You Need to Know','watch us pour a 500 sq ft slab 👷 this took 6 hours but here\'s 15 sec','500 Sq Ft of Concrete in One Day — What South Florida Homeowners Don\'t See','They understand the process, timeline & what to expect when hiring for concrete','Both','Homeowner','Concrete pour action shot, workers with tools, wet slab','76 — New Construction','Watch us pour a full porch slab in Pompano Beach 🏗️ time-lapse 👇','🆕 Idea','',''],
    ['=TODAY()','Manual','Manual','','Commercial Boutique Remodel – Before & After','Commercial retail renovation showcase Broward County','— No','commercial renovation contractor Broward County','retail space remodel South Florida','Inside a Full Boutique Remodel in Broward County','This Space Was Empty and Falling Apart — Now Look At It','this boutique glow-up took 3 weeks 😭 before vs after thread 🧵','From Empty Shell to Boutique: A Broward County Commercial Remodel','Commercial owners see what\'s possible + trust Oak Park for commercial work','Both','Commercial','Before: raw empty space. After: finished boutique interior','75 — Investment Property','Before vs after: we transformed this boutique in Fort Lauderdale 🏪✨','🆕 Idea','',''],
    ['=TODAY()','Manual','Manual','','Cove LED Strip Integration Demo','Modern lighting upgrades during renovation South FL','— No','LED cove lighting renovation South Florida','modern lighting upgrade contractor Broward','How Cove LED Lighting Transforms a South Florida Renovation','The One Detail That Makes Every Room Look Expensive','hidden glow-up ceiling hack that makes rooms look 10x more expensive ✨','The $300 Lighting Trick That Makes Every Renovation Look Luxury','They learn a specific upgrade that adds perceived value without huge cost','Both','Homeowner','Dark room with warm cove LED glow, modern ceiling detail','59 — Kitchen','This hidden LED strip hack makes any ceiling look custom 🔆 Broward install 👇','🆕 Idea','',''],
    ['=TODAY()','Manual','Manual','','Whole-House Remodel – Lotto Home','Full home renovation investment property flip South FL','— No','whole house renovation South Florida','investment property remodel Broward County','Inside a Full Whole-House Renovation in Broward County','This House Was a Disaster — Now It\'s Someone\'s Dream Home','we turned a fixer upper into a dream home and it took 90 days 😤🏠','From Fixer-Upper to Dream Home: A Full Broward County Renovation Story','Investors see ROI potential. Homeowners see what full renovation looks like','Both','All','Dramatic before/after — neglected exterior → finished curb appeal','75 — Investment Property','Fixer upper → dream home in 90 days 🏗️ Broward County full renovation 👇','🆕 Idea','',''],
  ];
  sheet.getRange(2, 1, samples.length, headers.length).setValues(samples);

  // ── Done ──────────────────────────────────────────────────────────────────
  SpreadsheetApp.getUi().alert('✅ Content Ideas sheet created successfully!\n\nYour sheet has:\n• 22 columns with dropdowns\n• 5 sample rows from your brainstorm\n• Color-coded status column\n• Ready for AI auto-research');
}
