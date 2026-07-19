export const meta = {
  name: 'market-brief',
  description: 'Audited US/global cross-asset pre-open or close brief: deterministic data + multi-agent research, written in Chinese.',
  whenToUse: 'args: {date, mode, profile, dataPath, historyPath, writeModel?, writeEffort?}',
  phases: [{ title: 'Load' }, { title: 'Route' }, { title: 'Scan' }, { title: 'Verify' }, { title: 'Source audit' }, { title: 'Postmortem' }, { title: 'Rank' }, { title: 'Challenge' }, { title: 'Write' }],
}

// ---- args (the -p orchestrator passes args as a JSON *string*, so `args`
//      can arrive as a string rather than an object; tolerate both) ----------
let _args = args ?? {}
if (typeof _args === 'string') {
  try { _args = JSON.parse(_args) }
  catch (e) { throw new Error('args arrived as a non-JSON string: ' + e.message) }
}
const { date, dataPath, historyPath, mode: md, profile: pf, writeModel: wm, writeEffort: we, fallbackWriteModel: fwm } = _args
if (!date || !dataPath || !historyPath) throw new Error('need args {date, mode, profile, dataPath, historyPath}; got ' + JSON.stringify(_args))
const mode = md || 'intraday'
const profile = pf || 'standard'
if (!['preopen', 'close', 'intraday'].includes(mode)) throw new Error('invalid mode: ' + mode)
if (!['standard', 'deep'].includes(profile)) throw new Error('invalid profile: ' + profile)
const writeModel = wm || 'opus'                 // A/B knob; default opus
const writeEffort = we || 'high'                // A/B knob; default high (never max)
const fallbackWriteModel = fwm || 'sonnet'      // used if a judge stage returns null

// opus/sonnet take effort; haiku silently ignores it — never attach it there.
function modelOpts(label, phase, model, effort) {
  const o = { label, phase, model }
  if (model !== 'haiku' && effort) o.effort = effort
  return o
}

// Rank/Write with ONE fallback retry on fallbackWriteModel (effort high) if the primary
// returns null — e.g. Opus quota exhausted at 07:40. Records degradation; throws if the
// fallback is also null (a brief that can't be written must fail loud, not silently).
let degraded = false
async function judge(label, phase, prompt, schema) {
  const mk = (l, m, e) => { const o = modelOpts(l, phase, m, e); return schema ? { ...o, schema } : o }
  let out = await agent(prompt, mk(label, writeModel, writeEffort))
  if (out) return out
  degraded = true
  out = await agent(prompt, mk(`${label}:fallback`, fallbackWriteModel, 'high'))
  if (!out) throw new Error(`${label}: primary (${writeModel}) and fallback (${fallbackWriteModel}) both returned nothing`)
  return out
}

// ---- scan angles: dynamically routed after the official-calendar pass -------
const NORMAL_ANGLES = [
  ['macro', "today's US data releases: actual vs consensus vs prior; next 24h calendar"],
  ['central_banks', 'Fed/ECB/BoJ/BoE/PBOC communication, rate pricing and sovereign-rate catalysts'],
  ['us_equities', 'US equity session drivers, earnings, guidance, M&A and material company news'],
  ['technology', 'AI, semiconductors, memory, cloud, networking and hyperscaler capex'],
  ['global_markets', 'Asia, Europe and emerging-market equity drivers plus cross-border flows'],
  ['credit_vol', 'credit spreads, funding, volatility, dealer positioning and liquidity stress'],
  ['commodities_fx', 'oil, gas, metals, agriculture, dollar and major FX drivers'],
  ['geopolitics', 'geopolitics, trade, sanctions and policy actions with direct market transmission'],
  ['calendar', 'next 24 hours and next 1-5 trading days: official releases, central-bank events and major earnings'],
]
const DEEP_ONLY_ANGLES = [
  ['china_asia', 'China and Asia macro, policy, property, technology and currency transmission'],
  ['europe', 'Europe macro, ECB, fiscal policy, energy, banks and regional earnings'],
  ['positioning', 'flows, positioning, options, systematic strategies and crowded-trade risks'],
]
const QUIET_KEYS = new Set(['macro', 'us_equities', 'global_markets', 'credit_vol', 'commodities_fx', 'geopolitics'])
const SPECIALIST_ANGLES = {
  inflation: [
    ['inflation_detail', 'CPI/PCE/PPI components, revisions, breadth, shelter/wages and inflation persistence'],
    ['rates_repricing', 'inflation-driven Treasury curve, real-yield and policy-path repricing'],
  ],
  fomc: [
    ['fed_decision', 'FOMC statement, projections, press conference, dissents and reaction function'],
    ['rates_liquidity', 'Fed-day curve, liquidity, reserves, funding and financial-conditions transmission'],
  ],
  tech_earnings: [
    ['tech_earnings', 'major technology earnings: guidance, revisions, valuation and read-throughs'],
    ['tech_supply_chain', 'semiconductor/cloud/AI supply-chain demand, capex, inventory and second-order beneficiaries'],
  ],
  employment: [
    ['labor_detail', 'payrolls, unemployment, participation, wages, revisions and Fed transmission'],
  ],
  central_bank: [
    ['global_cb_decision', 'scheduled global central-bank decision, guidance and cross-market transmission'],
  ],
  treasury: [
    ['treasury_supply', 'Treasury auctions, refunding, issuance mix, term premium and dealer-balance-sheet absorption'],
  ],
  geopolitics: [
    ['geopolitical_transmission', 'event-specific energy, shipping, sanctions, FX and risk-premium transmission channels'],
  ],
}

function routedAngles(route) {
  let selected = route.day_type === 'quiet'
    ? NORMAL_ANGLES.filter(([key]) => QUIET_KEYS.has(key))
    : NORMAL_ANGLES.slice()
  if (profile === 'deep') selected = selected.concat(DEEP_ONLY_ANGLES)
  for (const flag of Object.keys(SPECIALIST_ANGLES)) {
    if ((route.flags || []).includes(flag)) selected = selected.concat(SPECIALIST_ANGLES[flag])
  }
  const seen = new Set()
  const cap = profile === 'deep' ? 16 : 13
  return selected.filter(([key]) => !seen.has(key) && seen.add(key)).slice(0, cap)
}

// numbers already captured deterministically by fetch_data.py — scouts skip them
const ALREADY_COVERED =
  'Already covered deterministically — do NOT search for or restate these: index / ' +
  'futures / sector-ETF / FX / commodity / crypto levels and daily changes; 2Y and 10Y ' +
  'Treasury yields and the 10Y-2Y spread; the latest FRED prints for CPI / core CPI / ' +
  'PCE / core PCE / PPI / payrolls / unemployment / claims / retail sales / industrial ' +
  'production / average hourly earnings; US-listed factor, credit and regional ETFs; ' +
  'major Asia/Europe local indices; option-chain aggregates; CFTC weekly positioning; ' +
  'FINRA daily short-sale activity. Report the NEWS and causal driver, not duplicate levels.'

// ---- schemas ----------------------------------------------------------------
const LOAD = { type: 'object', required: ['data_date', 'data_mode', 'history_date', 'history_mode', 'is_session'], additionalProperties: false,
  properties: {
    data_date: { type: 'string' }, data_mode: { type: 'string' },
    history_date: { type: 'string' }, history_mode: { type: 'string' },
    is_session: { type: 'boolean' },
  } }

const ROUTE_EVENT = { type: 'object', required: ['name', 'category', 'importance', 'scheduled_at', 'url', 'ts', 'excerpt'], additionalProperties: false,
  properties: {
    name: { type: 'string' },
    category: { enum: ['inflation', 'fomc', 'tech_earnings', 'central_bank', 'employment', 'treasury', 'geopolitics', 'other'] },
    importance: { enum: ['low', 'medium', 'high'] },
    scheduled_at: { type: ['string', 'null'] }, url: { type: 'string' },
    ts: { type: 'string' }, excerpt: { type: 'string', maxLength: 300 },
  } }
const ROUTE = { type: 'object', required: ['day_type', 'flags', 'events', 'reason'], additionalProperties: false,
  properties: {
    day_type: { enum: ['quiet', 'normal', 'high_impact'] },
    flags: { type: 'array', uniqueItems: true, items: { enum: ['inflation', 'fomc', 'tech_earnings', 'central_bank', 'employment', 'treasury', 'geopolitics'] } },
    events: { type: 'array', maxItems: 8, items: ROUTE_EVENT }, reason: { type: 'string' },
  } }

const CLAIM = { type: 'object', required: ['claim', 'url', 'excerpt', 'ts'], additionalProperties: false,
  properties: {
    claim:   { type: 'string' },
    url:     { type: 'string' },
    excerpt: { type: 'string', maxLength: 300 },   // never full page text
    ts:      { type: 'string' },                   // source publication date/time
  } }
const CLAIMS = { type: 'object', required: ['claims'], additionalProperties: false,
  properties: { claims: { type: 'array', maxItems: 5, items: CLAIM } } }

// macro angle also returns structured releases for ref_period reconciliation
const RELEASE = { type: 'object', required: ['printed_today', 'stat', 'reference_period',
    'actual', 'consensus', 'prior', 'url', 'ts', 'excerpt'], additionalProperties: false,
  properties: {
    printed_today:    { type: 'boolean' },
    stat:             { type: 'string' },
    reference_period: { type: ['string', 'null'] },
    actual:           { type: ['string', 'null'] },
    consensus:        { type: ['string', 'null'] },
    prior:            { type: ['string', 'null'] },
    url:              { type: 'string' },
    ts:               { type: 'string' },
    excerpt:          { type: 'string', maxLength: 300 },
  } }
const MACRO_SCAN = { type: 'object', required: ['releases', 'claims'], additionalProperties: false,
  properties: {
    releases: { type: 'array', minItems: 1, maxItems: 8, items: RELEASE },
    claims:   { type: 'array', maxItems: 5, items: CLAIM },
  } }

const V1 = { type: 'object', required: ['verdicts'], additionalProperties: false,
  properties: { verdicts: { type: 'array', items: {
    type: 'object', required: ['index', 'verdict'], additionalProperties: false,
    properties: {
      index:   { type: 'integer' },
      verdict: { enum: ['supported', 'refuted', 'unclear'] },
      reason:  { type: 'string' },
    } } } } }
const V2 = { type: 'object', required: ['verdict'], additionalProperties: false,
  properties: { verdict: { enum: ['supported', 'refuted', 'unclear'] }, reason: { type: 'string' } } }

const AUDIT_ITEM = { type: 'object', required: ['index', 'verdict', 'reason'], additionalProperties: false,
  properties: {
    index: { type: 'integer' },
    verdict: { enum: ['confirmed', 'unavailable', 'mismatch', 'unsupported'] },
    reason: { type: 'string' },
    canonical_url: { type: ['string', 'null'] },
    observed_ts: { type: ['string', 'null'] },
  } }
const AUDIT = { type: 'object', required: ['results'], additionalProperties: false,
  properties: { results: { type: 'array', items: AUDIT_ITEM } } }

const POSTMORTEM = { type: 'object', required: ['results'], additionalProperties: false,
  properties: { results: { type: 'array', maxItems: 24, items: {
    type: 'object', required: ['catalyst_id', 'horizon', 'verdict', 'reason', 'evidence_keys'], additionalProperties: false,
    properties: {
      catalyst_id: { type: 'string' }, horizon: { enum: [1, 5] },
      verdict: { enum: ['confirmed', 'invalidated', 'mixed', 'not_evaluable'] },
      reason: { type: 'string' },
      evidence_keys: { type: 'array', items: { type: 'string' } },
    },
  } } } }

const CHALLENGES = { type: 'object', required: ['objections'], additionalProperties: false,
  properties: { objections: { type: 'array', maxItems: 5, items: {
    type: 'object', required: ['target', 'challenge', 'evidence', 'severity'], additionalProperties: false,
    properties: {
      target: { type: 'string' }, challenge: { type: 'string' }, evidence: { type: 'string' },
      severity: { enum: ['low', 'medium', 'high'] },
    },
  } } } }

const CATALYST = { type: 'object', required: ['name', 'thesis_link', 'horizon_days', 'confirm_condition', 'invalidate_condition', 'sources', 'source_angles'], additionalProperties: false,
  properties: {
    name: { type: 'string' }, thesis_link: { type: 'string' },
    horizon_days: { type: 'integer', minimum: 1, maximum: 5 },
    confirm_condition: { type: 'string' }, invalidate_condition: { type: 'string' },
    sources: { type: 'array', minItems: 1, items: { type: 'string' } },
    source_angles: { type: 'array', minItems: 1, items: { type: 'string' } },
  } }
const RANK = { type: 'object', required: ['thesis', 'ranked', 'catalysts', 'dropped'], additionalProperties: false,
  properties: {
    thesis: { type: 'string' },
    ranked: { type: 'array', minItems: 6, maxItems: 15, items: {
      type: 'object', required: ['point', 'why_it_matters', 'section', 'sources', 'source_angles'], additionalProperties: false,
      properties: {
        point:          { type: 'string' },
        why_it_matters: { type: 'string' },
        section:        { type: 'integer', minimum: 1, maximum: 9 },
        sources:        { type: 'array', minItems: 1, items: { type: 'string' } },
        source_angles:  { type: 'array', minItems: 1, items: { type: 'string' } },
      } } },
    catalysts: { type: 'array', minItems: 2, maxItems: 6, items: CATALYST },
    dropped: { type: 'array', items: {
      type: 'object', required: ['point', 'reason'], additionalProperties: false,
      properties: { point: { type: 'string' }, reason: { type: 'string' } } } },
  } }

// ---- prompts ----------------------------------------------------------------
const loadPrompt = (p, hp) =>
`Read both files at exactly these paths with the Read tool:
DATA: ${p}
HISTORY: ${hp}
Return ONLY DATA's top-level date/mode as data_date/data_mode, DATA.run_context.is_session as is_session, and HISTORY's top-level date/mode as history_date/history_mode. Use Read only.`

const routePrompt = (d) =>
`Build a bounded research route for the US/global financial-market brief dated ${d}, mode=${mode}.
Check only official calendars/releases: Federal Reserve, BLS/BEA/Census/Treasury, major central banks, and company investor-relations pages for genuinely market-moving technology earnings.
Look at TODAY and the next US trading day. Budget <=3 searches and <=4 fetches.
Set flags only when an event is scheduled or released in that window. CPI/PCE/PPI => inflation; FOMC decision/minutes/major Chair event => fomc; payrolls/claims/JOLTS => employment; major market-moving tech earnings => tech_earnings.
day_type=high_impact for a high-impact event, quiet only when there are no high-impact events and few material scheduled catalysts, otherwise normal.
Each event needs its official URL, source timestamp/date, scheduled time if known, and a <=300-character excerpt copied from the fetched official page. A search snippet is not evidence. Do not include market levels.`

const scanPrompt = (d, q, searchBudget) =>
`US/global cross-asset ${mode} market brief for ${d}. Research profile: ${profile}. Your angle: ${q}.
Budget: <=${searchBudget} searches, <=${searchBudget} fetches. Prefer primary sources, then Reuters/Bloomberg/WSJ/FT/CNBC/Nikkei.
${ALREADY_COVERED}
Return <=4 falsifiable, dated claims that materially move or frame today's markets. Each: a specific assertion, its source url, a <=300-char excerpt COPIED from the fetched page, and ts = the source's publication date/time.
Never paste full page text — excerpt only. If nothing solid, return an empty claims array.`

const macroScanPrompt = (d) =>
`US/global ${mode} market brief for ${d}. Angle: today's / this week's US macro data. You are a load-bearing scout.
FIRST: did anything print at 07:30 CT (08:30 ET) TODAY — CPI, PPI, PCE, payrolls, jobless claims, retail sales, GDP, etc.?
- If YES: for EACH release add a \`releases\` entry with printed_today=true, stat, reference_period (e.g. "2026-06"), and actual / consensus / prior as SEPARATE fields, plus source url, ts, and a <=300-char excerpt.
- If NOTHING printed today: return one \`releases\` entry with printed_today=false describing the NEXT scheduled release (stat, reference_period, consensus if reported, url, ts).
Then add <=5 other dated macro \`claims\` (Fed-relevant revisions, etc.) in the generic claim shape.
Budget <=2 searches, <=3 fetches. Prefer primary sources (BLS, BEA, Census) then Reuters/Bloomberg/WSJ/FT/CNBC.
${ALREADY_COVERED}
Your job is TODAY's release (actual vs consensus vs prior) and its interpretation, not restating stale levels.
Never paste full page text — <=300-char excerpts only.`

const verifyPrompt = (claims) =>
`You are a READING-COMPREHENSION checker, not a researcher. For each item decide ONLY whether its excerpt supports its claim:
- "supported": the excerpt clearly states the claim.
- "refuted": the excerpt contradicts the claim.
- "unclear": the excerpt is insufficient to tell.
Use ONLY the excerpt. Do NOT bring in outside knowledge. Return one verdict per item, keyed by its index.
Items:
${JSON.stringify(claims.map((c, i) => ({ index: i, claim: c.claim, excerpt: c.excerpt, url: c.url })))}`

const recheckPrompt = (c) =>
`Re-check ONE claim against its excerpt. Excerpt ONLY, no outside knowledge.
"supported" only if the excerpt clearly states it; "refuted" if it contradicts; "unclear" if insufficient.
Claim: ${c.claim}
Excerpt: ${c.excerpt}
Source: ${c.url}`

const auditPrompt = (batch) =>
`Re-fetch and audit this shortlist for a financial publication. Fetch EACH supplied URL exactly once.
For every item confirm source identity, publication date/time consistency, excerpt presence, and claim support.
Return one result per index. A paywall or fetch failure is "unavailable", never "confirmed".
Items:
${JSON.stringify(batch)}`

const challengePrompt = (d, p, rank, claims) =>
`Act as the independent red-team editor for the ${mode} market brief on ${d}.
Read the deterministic JSON at ${p}. Use only that file, the rank skeleton, and the source-confirmed claims below; do not browse.
Try to falsify the thesis and causal story. Look for contradictory cross-asset signals, stale/cached data, correlation presented as causation, session-timing mismatch, crowded consensus, and missing invalidation conditions.
Return <=5 specific objections. Empty is allowed only if there is genuinely no material challenge.
Rank:
${JSON.stringify(rank)}
Source-confirmed claims:
${JSON.stringify(claims)}`

const postmortemPrompt = (d, p, hp, claims) =>
`You are the POSTMORTEM stage for ${d}. Read the deterministic DATA JSON and HISTORY JSON at exactly:
DATA: ${p}
HISTORY: ${hp}
The HISTORY pending_evaluations list is the complete allow-list. Return exactly one result per pending item and no other catalyst IDs.
Use only deterministic current values, the history comparisons, and the source-confirmed current claims below. No browsing.
Judge the original written conditions literally:
- confirmed: confirmation condition clearly occurred and invalidation did not;
- invalidated: invalidation condition clearly occurred;
- mixed: both/partial evidence;
- not_evaluable: required evidence is absent, stale, or the condition was not objectively measurable.
Never convert CFTC weekly positioning or FINRA short-sale volume into net fund flow. evidence_keys must be exact JSON paths or confirmed-claim URLs. Concise reasons, no hindsight rewrite.
Current source-confirmed claims:
${JSON.stringify(claims)}`

const readNumbers = (p) =>
  `FIRST: Read the JSON at ${p}. It is the authoritative source for every number. ` +
  `Quote its values verbatim — never rewrite, round, recompute, or infer. ` +
  `Mind mode, run_context, data_quality, coverage, session_date, bar_complete, ` +
  `direction_usable, chg_pct_meaning, and futures_dating. A null direction is forbidden, not missing data.`

const rankPrompt = (d, p, hp, ok, releases, route, routeEvents, postmortem) =>
`You are the RANK stage of a ${mode} US/global cross-asset brief for ${d}. Decide what matters; do NOT write prose.
${readNumbers(p)}
Also Read ${hp}. Use its comparisons to identify what changed versus the prior 1/5/20 same-mode observations; never invent history when available=false.
Sections: 1=core view, 2=US equities/breadth/factors, 3=global equities, 4=macro/rates/central banks, 5=FX/commodities/crypto, 6=credit/vol/liquidity, 7=earnings/themes, 8=1-5 day scenarios/catalysts, 9=data quality.
Source-audited claims ({claim,url,excerpt,ts,audit}):
${JSON.stringify(ok)}
Scout macro releases (reconciled vs deterministic macro downstream):
${JSON.stringify(releases)}
Dynamic route (routing itself is not publishable evidence):
${JSON.stringify(route)}
Source-audited official route events:
${JSON.stringify(routeEvents)}
Prior catalyst postmortem:
${JSON.stringify(postmortem)}
Output schema-locked: thesis (ONE sentence, today's dominant regime/driver); ranked (${profile === 'deep' ? '8-15' : '6-12'} items; cover every material asset class, each with why_it_matters, section 1..9, sources as clickable urls or "deterministic:<json path>", and source_angles naming active contributing scout keys or "deterministic"); catalysts (2-6 objectively testable 1-5 trading-day items, each with separate confirmation and invalidation conditions, using only the same audited/deterministic sources and source_angles); dropped (excluded candidates + one-line reason).
Rank ONLY source-audited claims and deterministic numbers. Separate observed fact from interpretation. Invent nothing.`

const writePrompt = (d, p, hp, rank, challenges, unsure, ok, releases, routeEvents, postmortem) =>
`You are the WRITE stage of a ${mode} US/global cross-asset brief for ${d}. Write the report in Simplified Chinese (简体中文). Profile=${profile}.
${readNumbers(p)}
Read ${hp}. If its 1d/5d/20d comparison is available, add a compact subsection “与昨日/5日/20日相比” using ONLY its exact values; say “样本尚未积累” for unavailable horizons.
Follow the Rank skeleton EXACTLY — its thesis and its ordering. You MAY NOT introduce facts absent from the ranked list or the deterministic numbers.
Structure:
Title must be exactly mode-aware: preopen=美股与全球市场盘前简报, close=美股与全球市场收盘简报, intraday=美股与全球市场盘中简报.
At top add a compact metadata line: generated/asof time, mode, data freshness, profile, degraded status.
① 核心观点与市场状态（risk-on/off/rotation/stress，说明证据）。
② 美国市场：指数、宽度、集中度、因子、板块；区分 live/complete bar。
③ 全球市场：亚洲、欧洲、新兴市场，说明各地 bar 是否已收盘。
④ 宏观、利率与央行：actual vs consensus vs prior，解释传导链。
⑤ 外汇、商品与加密资产。
⑥ 信用、波动率与流动性：在本节加入有用的期权与持仓字段及其来源日期/频率。Never call them real-time fund flow.
⑦ 财报、产业与主题。
⑧ 未来 1-5 个交易日：事件日历 + base/bull/bear 条件、确认信号和失效条件；不是价格预测。
⑨ 数据质量与存疑。
⑩ 来源：列出正文使用的可点击 Markdown 链接、来源时间；deterministic 数据列 JSON 路径。
HARD RULES:
- Deterministic numbers are quoted VERBATIM from the JSON you read — never rewrite, round, or recompute.
- MACRO RECONCILIATION by ref_period — compare each deterministic macro[...].ref_period against the scout release.reference_period:
  • FRED behind (release.reference_period newer than FRED ref_period): the scout's number is TODAY'S actual — label 电讯稿来源 with source+timestamp; FRED's released is the PRIOR. (Expected at 07:40 CT.)
  • FRED caught up (same reference_period): they should agree; if they DISAGREE, put it in 存疑 — do not silently pick one.
  • Nothing printed today (printed_today=false): say so; do NOT fill the section from FRED and imply it is news.
- Mind futures_dating / bar_complete / chg_pct_meaning: label overnight moves correctly; never call a live futures bar "today's close".
- If run_context.is_session=false, label the title/metadata as a non-trading-day review, identify the previous cash session, and never describe stale Friday bars or option volume as Sunday's live market action.
- Every key fact annotated with source + timestamp (deterministic figures cite "FRED" or "Yahoo <session_date>"). Every web-derived body claim must have a clickable Markdown URL in ⑩.
- Never infer direction from a null chg/chg_pct or direction_usable:false. Do not print a redacted raw direction even if it appears elsewhere in this prompt.
- If data_quality.cache_fallbacks is non-empty, label those figures as cached with cached_asof/cache_age_hours; never call them live.
- Options: OI is clearing-cycle data and volume is cumulative; do not infer trade direction or dealer positioning. No GEX claim unless actual directional dealer-inventory data exists (it does not in the free fallback).
- CFTC COT is weekly positioning; FINRA Reg SHO is reported short-sale activity, not short interest and not net fund flow. State those limitations when used.
- Explain WHY each fact matters; do not restate headlines.
UNVERIFIED claims (below) go ONLY in a separate 存疑 section, never in the body:
${JSON.stringify(unsure)}
Rank skeleton:
${JSON.stringify(rank)}
Independent red-team objections (address material ones explicitly; do not hide them):
${JSON.stringify(challenges)}
Verified claims (for citation urls/timestamps):
${JSON.stringify(ok)}
Scout macro releases:
${JSON.stringify(releases)}
Source-audited route events:
${JSON.stringify(routeEvents)}
1日/5日事后评分（如无到期项目则简短说明尚无样本）:
${JSON.stringify(postmortem)}
Target length: ${profile === 'deep' ? '4,500-7,500' : '3,000-5,000'} Chinese characters. Output ONLY the report as Chinese Markdown. No preamble, no code fences, no rank JSON.`

// ---- Load: fetch only the date, cross-check it, do NOT transcribe numbers ---
phase('Load')
const facts = await agent(loadPrompt(dataPath, historyPath), { label: 'load', phase: 'Load', model: 'haiku', schema: LOAD })
if (!facts) throw new Error('Load: agent returned nothing')
if (facts.data_date !== date || facts.history_date !== date)
  throw new Error(`Load: date mismatch data=${facts.data_date}, history=${facts.history_date}, args=${date}`)
if (facts.data_mode !== mode || facts.history_mode !== mode)
  throw new Error(`Load: mode mismatch data=${facts.data_mode}, history=${facts.history_mode}, args=${mode}`)
const nonSession = facts.is_session === false

// ---- Route: one cheap official-calendar pass controls scan breadth ----------
phase('Route')
const route = await agent(routePrompt(date), {
  label: 'calendar-route', phase: 'Route', model: 'haiku',
  agentType: 'calendar-router', schema: ROUTE,
}) || { day_type: 'normal', flags: [], events: [], reason: 'router unavailable; safe normal route' }
const ANGLES = nonSession
  ? NORMAL_ANGLES.filter(([key]) => ['macro', 'geopolitics', 'calendar'].includes(key))
  : routedAngles(route)
const searchBudget = (nonSession || route.day_type === 'quiet') ? 1 : 2
log(`Route: ${route.day_type}, flags=[${route.flags.join(',')}], ${ANGLES.length} scouts`)

// ---- Scan (dynamic angles, parallel, haiku news-scout) ----------------------
phase('Scan')
const scanned = await parallel(ANGLES.map(([k, q]) => async () => {
  const isMacro = k === 'macro'
  const r = await agent(
    isMacro ? macroScanPrompt(date) : scanPrompt(date, q, searchBudget),
    { label: `scan:${k}`, phase: 'Scan', model: 'haiku', agentType: 'news-scout',
      schema: isMacro ? MACRO_SCAN : CLAIMS })
  if (!r) return { angle: k, claims: [], releases: [], failed: true }
  return { angle: k, claims: r.claims || [], releases: r.releases || [], failed: false }
})).then(rs => rs.map((r, i) => r || { angle: ANGLES[i][0], claims: [], releases: [], failed: true }))
const failedScouts = scanned.filter(s => s.failed).map(s => s.angle)
const macroReleases = (scanned.find(s => s.angle === 'macro') || {}).releases || []
log(`Scan: ${scanned.reduce((n, s) => n + s.claims.length, 0)} claims, ${macroReleases.length} macro releases, ${failedScouts.length} scouts failed`)
if (failedScouts.includes('macro') || macroReleases.length === 0)
  throw new Error('Load-bearing macro scout failed or returned no release/calendar record')

// ---- Verify (batched per angle; only "unclear" escalates haiku -> sonnet/low)
phase('Verify')
const checked = await parallel(scanned.map(s => async () => {
  if (!s.claims.length) return { angle: s.angle, ok: [], unsure: [] }
  const v = await agent(verifyPrompt(s.claims),
    { label: `verify:${s.angle}`, phase: 'Verify', model: 'haiku', agentType: 'verifier', schema: V1 })
  if (!v) return { angle: s.angle, ok: [],                       // verifier down -> UNVERIFIED, never refuted
    unsure: s.claims.map(c => ({ ...c, verdict: 'unverified', reason: 'verifier unavailable' })) }
  const byIdx = {}; (v.verdicts || []).forEach(vd => { byIdx[vd.index] = vd })
  const ok = [], unclear = []
  s.claims.forEach((c, i) => {
    const vd = byIdx[i] || { verdict: 'unclear' }
    if (vd.verdict === 'supported') ok.push({ ...c, angle: s.angle, verdict: 'excerpt-supported' })
    else if (vd.verdict === 'unclear') unclear.push(c)
    // 'refuted' -> dropped (filtered out, like /deep-research)
  })
  const rescued = await parallel(unclear.map(c => () =>
    agent(recheckPrompt(c),
      { label: `v2:${s.angle}`, phase: 'Verify', model: 'sonnet', effort: 'low', agentType: 'verifier', schema: V2 })
      .then(r => ({ c, r }))))
  const unsure = []
  rescued.forEach(x => {
    if (!x || !x.c) return                                       // thunk-throw path -> parallel gave null
    const r = x.r
    if (r && r.verdict === 'supported') ok.push({ ...x.c, angle: s.angle, verdict: 'excerpt-supported' })
    else if (r && r.verdict === 'refuted') { /* drop */ }
    else unsure.push({ ...x.c, verdict: 'unverified', reason: (r && r.reason) || 'unclear on recheck' })
  })
  return { angle: s.angle, ok, unsure }
}))
const excerptSupported = checked.flatMap(c => c.ok)
const verifyUnverified = checked.flatMap(c => c.unsure)
log(`Verify: ${excerptSupported.length} excerpt-supported, ${verifyUnverified.length} unverified`)
if (excerptSupported.length === 0 && !nonSession)
  throw new Error(`No verified claims after Verify — ${failedScouts.length} scout(s) failed [${failedScouts.join(', ') || 'none'}].`)

// ---- Source audit: re-fetch a bounded, angle-balanced shortlist --------------
phase('Source audit')
const releaseItems = macroReleases.map((r, releaseIndex) => ({
  claim: r.printed_today
    ? `${r.stat} released: actual ${r.actual}, consensus ${r.consensus}, prior ${r.prior}`
    : `Next scheduled macro release: ${r.stat}, reference period ${r.reference_period}`,
  url: r.url, excerpt: r.excerpt, ts: r.ts, angle: 'macro-release',
  _kind: 'release', _releaseIndex: releaseIndex,
}))
const routeItems = (route.events || []).map((event, routeIndex) => ({
  claim: `Scheduled ${event.category} event: ${event.name} at ${event.scheduled_at || 'time not stated'}`,
  url: event.url, excerpt: event.excerpt, ts: event.ts, angle: 'calendar_router',
  _kind: 'route_event', _routeIndex: routeIndex,
}))
const queues = checked.map(c => c.ok.slice())
const balanced = []
const claimLimit = profile === 'deep' ? 24 : 16
while (balanced.length < claimLimit && queues.some(q => q.length)) {
  for (const q of queues) {
    if (q.length && balanced.length < claimLimit) balanced.push(q.shift())
  }
}
const auditCandidates = releaseItems.concat(routeItems, balanced).map((c, index) => ({ ...c, index }))
const batches = []
for (let i = 0; i < auditCandidates.length; i += 4) batches.push(auditCandidates.slice(i, i + 4))
const auditedBatches = await parallel(batches.map((batch, bi) => async () => {
  const input = batch.map(c => ({ index: c.index, claim: c.claim, url: c.url, excerpt: c.excerpt, ts: c.ts }))
  const r = await agent(auditPrompt(input), {
    label: `source-audit:${bi}`, phase: 'Source audit', model: 'haiku',
    agentType: 'source-auditor', schema: AUDIT,
  })
  return r ? r.results : batch.map(c => ({ index: c.index, verdict: 'unavailable', reason: 'source auditor unavailable' }))
}))
const auditByIndex = {}
auditedBatches.flat().filter(Boolean).forEach(r => { auditByIndex[r.index] = r })
const okClaims = []
const auditedReleases = []
const auditedRouteEvents = []
const auditUnverified = []
auditCandidates.forEach(c => {
  const audit = auditByIndex[c.index] || { verdict: 'unavailable', reason: 'missing audit result' }
  if (audit.verdict === 'confirmed') {
    if (c._kind === 'release') auditedReleases.push({ ...macroReleases[c._releaseIndex], audit })
    else if (c._kind === 'route_event') {
      auditedRouteEvents.push({ ...route.events[c._routeIndex], audit })
      okClaims.push({ ...c, verdict: 'source-confirmed', audit })
    }
    else okClaims.push({ ...c, verdict: 'source-confirmed', audit })
  } else {
    auditUnverified.push({ ...c, verdict: 'unverified', reason: `source audit ${audit.verdict}: ${audit.reason}` })
  }
})
if (auditedReleases.length === 0)
  throw new Error('No macro release/calendar record survived source audit')
if (okClaims.length === 0 && !nonSession)
  throw new Error('No market-news claim survived source audit')
const notAudited = excerptSupported
  .filter(c => !balanced.includes(c))
  .map(c => ({ ...c, verdict: 'unverified', reason: 'not selected for bounded source-audit budget' }))
const unverified = verifyUnverified.concat(auditUnverified, notAudited).slice(0, 16)
log(`Source audit: ${okClaims.length} claims + ${auditedReleases.length} macro records + ${auditedRouteEvents.length} routed events confirmed; ${auditUnverified.length} rejected/unavailable`)

// ---- Postmortem: score due 1d/5d conditions before creating today's view -----
phase('Postmortem')
const postmortem = await agent(postmortemPrompt(date, dataPath, historyPath, okClaims), {
  label: 'postmortem', phase: 'Postmortem', model: 'haiku',
  agentType: 'postmortem-analyst', schema: POSTMORTEM,
}) || { results: [] }
log(`Postmortem: ${postmortem.results.length} due evaluations scored`)

// ---- Rank -> Write (two writeModel/writeEffort calls) ------------------------
phase('Rank')
const rank = await judge('rank', 'Rank', rankPrompt(date, dataPath, historyPath, okClaims, auditedReleases, route, auditedRouteEvents, postmortem), RANK)

phase('Challenge')
const challengeModel = profile === 'deep' ? 'sonnet' : 'haiku'
const challengeOpts = { label: 'risk-challenge', phase: 'Challenge', model: challengeModel,
  agentType: 'risk-challenger', schema: CHALLENGES }
if (challengeModel !== 'haiku') challengeOpts.effort = 'low'
const challenges = await agent(challengePrompt(date, dataPath, rank, okClaims), challengeOpts)
  || { objections: [{ target: 'pipeline', challenge: 'red-team agent unavailable', evidence: 'no structured response', severity: 'medium' }] }

phase('Write')
let report = await judge('write', 'Write', writePrompt(date, dataPath, historyPath, rank, challenges, unverified, okClaims, auditedReleases, auditedRouteEvents, postmortem), null)
if (typeof report !== 'string' || !report.trim()) throw new Error('Write: no report produced')

// If any judge stage fell back off the primary writer, flag it — machine-readable
// (degraded) and human-visible (a banner atop the report).
if (degraded)
  report = `> ⚠️ 主模型 ${writeModel} 不可用，本篇由 ${fallbackWriteModel} 降级生成。\n\n` + report

// ---- return: report text + VISIBLE required rank block ----------------------
// An HTML comment gets dropped by the -p orchestrator's relay (observed on the
// first live run), so the machine block is visible, sentinel-delimited, and
// labeled as required output. run.sh makes its absence fatal.
const angleMetrics = {}
for (const [angle] of ANGLES) {
  const scan = scanned.find(s => s.angle === angle) || { claims: [], releases: [] }
  const verified = checked.find(s => s.angle === angle) || { ok: [] }
  angleMetrics[angle] = {
    scouted: scan.claims.length + scan.releases.length,
    excerpt_supported: verified.ok.length,
    source_confirmed: okClaims.filter(c => c.angle === angle).length +
      (angle === 'macro' ? auditedReleases.length : 0),
  }
}
angleMetrics.calendar_router = {
  scouted: (route.events || []).length,
  excerpt_supported: (route.events || []).length,
  source_confirmed: auditedRouteEvents.length,
}
const diagnostics = { mode, profile, day_type: route.day_type, route_flags: route.flags,
  scout_count: ANGLES.length, active_angles: ANGLES.map(([key]) => key), failed_scouts: failedScouts,
  excerpt_supported: excerptSupported.length, source_confirmed: okClaims.length,
  source_audit_unverified: auditUnverified.length, macro_records_confirmed: auditedReleases.length,
  route_events_confirmed: auditedRouteEvents.length, postmortem_scored: postmortem.results.length,
  red_team_objections: challenges.objections.length, angle_metrics: angleMetrics }
const confirmedClaims = okClaims.map(c => ({ angle: c.angle, claim: c.claim, url: c.url, ts: c.ts }))
const rankBlock = JSON.stringify({ rank, route, postmortem, challenges, unverified,
  confirmed_claims: confirmedClaims, diagnostics, report_chars: report.length, degraded })
return report +
  '\n\n<<RANK_JSON_BEGIN>> (required output — reproduce this block verbatim; do not summarize or omit)\n' +
  rankBlock +
  '\n<<RANK_JSON_END>>\n'
