// ─────────────────────────────────────────────────────────────────────────────
// OAK PARK CONSTRUCTION — Company Knowledge File
// This file is the source of truth for all AI-generated content.
// Claude must reference this before writing any blog post, caption, or copy.
// ─────────────────────────────────────────────────────────────────────────────

const COMPANY = {

  name: 'Oak Park Construction',

  tagline: null, // update if they have one

  origin: `Oak Park Construction was founded by brothers Matthew and Michael McFolling,
who are originally from Oak Park, Illinois — that's where the company name comes from.
They relocated to South Florida and now build their work here. The Chicago/Midwest
roots are part of the brand story and can be mentioned naturally when relevant
(e.g. "bringing Midwest work ethic to South Florida construction").`,

  team: {
    contractor: 'Matthew McFolling',   // licensed contractor
    projectManager: 'Michael McFolling', // project manager
    relationship: 'brothers'
  },

  location: {
    headquarters: 'Pompano Beach, Florida',
    primaryMarket: 'Broward County',
    alsoServes: ['Miami-Dade County', 'Palm Beach County'],
    targetCities: [
      'Pompano Beach',
      'Fort Lauderdale',   // popular, mention often
      'Hollywood',
      'Dania Beach',
      'Miramar',
      'Weston',            // good jobs here
      'Parkland',          // good jobs here
      'Deerfield Beach',
      'Coral Springs',
      'Boca Raton',
    ],
    neverMention: ['Oak Park Illinois', 'Chicago', 'Illinois', 'Chicagoland'],
    // ^ These are the origin story ONLY — never use as service location
  },

  license: {
    status: 'Licensed General Contractor in Florida',
    mentionInBlogs: true,    // say "licensed" — adds trust
    mentionNumber: false,    // license number NOT in blog content
  },

  services: {
    core: [
      'Residential construction',
      'Commercial construction',
      'Full home renovation',
      'Commercial renovation and build-outs',
      'New additions (room additions, second stories)',
      'Shell construction',
      'Concrete work (patios, porches, driveways, slabs)',
      'Deck construction',
      'Screen porch enclosures',
    ],
    includedAsPartOfFullProject: [
      'Electrical (through trusted subcontractors)',
      'Roofing (through trusted subcontractors)',
      'Plumbing (through trusted subcontractors)',
    ],
    // These trades are ONLY handled as part of a larger project
    // Never imply they offer standalone electrical/roofing/plumbing-only jobs
  },

  doNotTarget: [
    'Standalone roofing-only jobs',
    'Standalone plumbing-only jobs',
    'Standalone electrical-only jobs',
    'Handyman work (installing a single sink, hanging items, minor repairs)',
    'Very small one-off repairs (re-caulking pavers, small patch jobs)',
  ],
  // NOTE: They sometimes accept small jobs that lead to bigger projects,
  // but blog content should focus on full construction/renovation projects.
  // Do NOT write posts targeting "how to install a sink" type content.

  brandVoice: {
    tone: 'Professional, trustworthy, expert, warm — not corporate, not salesy',
    avoid: [
      'Saying "why hire us" or "why choose Oak Park Construction" as a section header',
      'Generic contractor clichés ("we go above and beyond", "customer satisfaction is our priority")',
      'Overpromising or exaggerating',
      'Sounding like a robot or AI',
    ],
    contentPhilosophy: `Provide real value. Teach the reader something. Answer their actual question
fully. Make them smarter. The CTA at the end is ONE natural sentence — not a sales pitch.
Posts should read like advice from a knowledgeable South Florida contractor who genuinely
wants to help, not like a company trying to sell something.`,
  },

  contentRules: {
    competitors: 'NEVER mention any competitor contractor by name, positively or negatively.',
    products: 'Product/store mentions are OK (Home Depot, Lioher cabinets, etc.) when relevant and natural. Do not advertise for them intentionally.',
    tradeReferrals: `Do NOT tell readers to "hire a roofer" or "call a plumber" as standalone advice.
Oak Park Construction handles those trades as part of full projects.
It is OK to say "a licensed contractor will handle the electrical and plumbing" in context.`,
    location: `ALWAYS reference South Florida, Broward County, or specific local cities.
NEVER imply the company is in Illinois or the Midwest.
The Illinois origin is a brand story only — mention it warmly when telling the company story,
never in a way that confuses service location.`,
    safety: `Before any post is published, check:
1. No wrong location mentioned as service area
2. No competitor named
3. No advice that sends customers to hire a specific competing trade
4. Content is relevant to Oak Park Construction's actual services
5. No handyman-only content that doesn't lead to a real construction project`,
    politicalNeutrality: `When writing about topics that involve politics, immigration, labor, or social issues:
- Write ONLY from the contractor/business-owner perspective
- Report facts and practical impact — never editorialize or take a political side
- Do NOT frame any government enforcement action, policy, or political party as "right" or "good"
- Do NOT use language that defends, promotes, or dismisses a political position
- Acceptable: "Here is how ICE raids at construction sites affect your project timeline and what to ask your contractor"
- Not acceptable: "ICE is protecting communities" or "ICE is terrorizing workers" — neither framing belongs in our content
- If a topic cannot be written about without taking a political side, mark it as 🆕 Idea and do not auto-approve`,
  },

  cta: {
    // Update phone and email when available
    phone: null,    // TODO: add phone number
    email: null,    // TODO: add contact email or contact page URL
    contactPage: 'https://oakpark-construction.com/contact',
    defaultCta: 'Contact Oak Park Construction for a free consultation.',
  },

};

module.exports = { COMPANY };
