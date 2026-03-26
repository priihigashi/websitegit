const EVERGREEN_TOPICS = [
  "home addition cost in Broward County",
  "commercial renovation contractor South Florida",
  "shell construction South Florida explained",
  "concrete construction benefits for South Florida businesses",
  "how to plan a home renovation in Fort Lauderdale",
  "home addition vs moving in South Florida",
  "questions to ask a contractor before hiring in Broward County",
  "how long does a home addition take in South Florida",
  "building permits in Broward County Florida",
  "signs your commercial property needs renovation in South Florida",
  "renovation vs remodeling South Florida",
  "how to budget for a construction project in Broward County",
  "why hire a local South Florida contractor",
  "what is shell construction and who needs it",
  "hurricane-resistant construction in South Florida",
  "new construction home vs renovation in Fort Lauderdale",
  "commercial build-out contractor Broward County",
  "residential construction tips for South Florida climate",
];

function getRandomTopic() {
  return EVERGREEN_TOPICS[Math.floor(Math.random() * EVERGREEN_TOPICS.length)];
}

module.exports = { EVERGREEN_TOPICS, getRandomTopic };
