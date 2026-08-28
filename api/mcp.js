/* Remote MCP server for the Africa-EU labour mobility corridor tool.
   Streamable HTTP, spoken directly as JSON-RPC 2.0 over POST.

   Written without the MCP SDK on purpose. The site deploys to Vercel with no
   build and no install step (vercel.json sets buildCommand and installCommand
   to null), so taking a dependency would mean introducing a build pipeline to
   serve about two hundred lines of protocol. The surface actually needed is
   initialize / tools/list / tools/call / ping, which is small and stable.

   The data is require()d rather than fetched: Vercel bundles statically
   required files, so there is no runtime path resolution and no
   self-referential HTTP call back to the deployment that is serving this. */

const DATA = require('./corridors.json');

const SERVER = { name: 'africa-eu-corridors', version: '1.0.0' };

/* Echo the client's protocol version when it is one we know, so the connector
   is never refused over a version string. Anything unrecognised falls back to
   the version this was written against. */
const SUPPORTED = ['2025-06-18', '2025-03-26', '2024-11-05'];
const FALLBACK = '2025-06-18';

const CORRIDORS = DATA.corridors;
const META = DATA.meta || {};
const PRESET_KEYS = { cgd: 'score_cgd', ajf: 'score_ajf', balanced: 'score_balanced' };

// World Bank convention, matching the web app: Sudan counts as Sub-Saharan.
const NORTH_AFRICA = new Set(['DZA', 'EGY', 'LBY', 'MAR', 'TUN']);

function dedupe(rows, code, name, sub) {
  const m = new Map();
  for (const r of rows) {
    if (!m.has(r[code])) m.set(r[code], { iso3: r[code], name: r[name], subregion: r[sub] });
  }
  return [...m.values()].sort((a, b) => a.name.localeCompare(b.name));
}

const ORIGINS = dedupe(CORRIDORS, 'orig', 'orig_name', 'orig_subregion');
const DESTS = dedupe(CORRIDORS, 'dest', 'dest_name', 'dest_subregion');

function resolveCountry(q, list) {
  if (!q) return null;
  const s = String(q).trim().toLowerCase();
  return list.find(c => c.iso3.toLowerCase() === s)
    || list.find(c => c.name.toLowerCase() === s)
    || list.find(c => c.name.toLowerCase().startsWith(s))
    || list.find(c => c.name.toLowerCase().includes(s))
    || null;
}

function scoreOf(row, preset) { return row[PRESET_KEYS[preset] || 'score_ajf']; }

/* ----------------------------------------------------------------- tools */

const TOOLS = [
  {
    name: 'list_countries',
    description:
      'List the 54 African origin countries and 27 EU destination countries the tool covers, '
      + 'with ISO3 codes and UN subregions. Call this first if you need exact names or codes.',
    inputSchema: {
      type: 'object',
      properties: {
        side: {
          type: 'string', enum: ['origins', 'destinations', 'both'],
          description: 'Which side to list. Default both.',
        },
      },
    },
  },
  {
    name: 'search_corridors',
    description:
      'Search and rank Africa-to-EU labour mobility corridors. All 1,458 origin-destination pairs are '
      + 'scored 0-100 on four dimensions: underutilisation (how far below a PPML gravity benchmark actual '
      + 'permit flows run), access (can an application physically be lodged, and does it succeed), ease '
      + '(language, legal channel, salary floor, recognition, processing time) and potential (income gain, '
      + 'skills supply, labour supply, destination demand, diaspora momentum). Use for questions like '
      + '"best corridors for Kenya", "where should we focus in West Africa", or "which corridors are '
      + 'underused but easy to unlock".',
    inputSchema: {
      type: 'object',
      properties: {
        origin: { type: 'string', description: 'Origin country name or ISO3. Optional.' },
        destination: { type: 'string', description: 'Destination country name or ISO3. Optional.' },
        origin_subregion: {
          type: 'string',
          description: 'e.g. "Western Africa", or "Sub-Saharan Africa" for all but the five North African states.',
        },
        destination_subregion: { type: 'string', description: 'e.g. "Southern Europe".' },
        preset: {
          type: 'string', enum: ['cgd', 'ajf', 'balanced'],
          description: 'Weighting. cgd leads on underutilisation, ajf on potential, balanced is even. Default ajf.',
        },
        sort_by: {
          type: 'string',
          enum: ['score', 'underutilisation', 'access', 'ease', 'potential', 'scale', 'permits', 'shortfall'],
          description: 'Default score.',
        },
        low_hanging_fruit_only: {
          type: 'boolean',
          description: 'Only corridors materially below prediction that also have above-median access and ease and an existing legal channel.',
        },
        min_score: { type: 'number' },
        max_permits: {
          type: 'number',
          description: 'Only corridors currently below this many permits a year - useful for finding unused corridors.',
        },
        exclude_who_safeguard_origins: {
          type: 'boolean',
          description: 'Exclude origins on the WHO health workforce safeguards list. That list is never scored, only flagged.',
        },
        limit: { type: 'number', description: 'Default 20, max 100.' },
      },
    },
  },
  {
    name: 'get_corridor',
    description:
      'Full detail for one corridor: every score component, the gravity model comparison, and the '
      + 'underlying facts - consular presence, visa refusal rate, languages, agreements, air passengers, '
      + 'diaspora, income gap, skills supply and any WHO safeguards flag.',
    inputSchema: {
      type: 'object',
      properties: {
        origin: { type: 'string', description: 'Origin country name or ISO3.' },
        destination: { type: 'string', description: 'Destination country name or ISO3.' },
      },
      required: ['origin', 'destination'],
    },
  },
  {
    name: 'get_methodology',
    description:
      'How the scores are built: the weighting presets, the sub-weights inside access, ease and potential, '
      + 'the fitted gravity coefficients, and the data sources with vintage and confidence. Call this before '
      + 'relying on a number, and to see which inputs are hand-assembled rather than official.',
    inputSchema: {
      type: 'object',
      properties: {
        section: {
          type: 'string', enum: ['weights', 'gravity', 'sources', 'all'],
          description: 'Default all.',
        },
      },
    },
  },
  {
    name: 'get_sector_shortages',
    description:
      'EU-wide shortage occupations by sector family, from the European Labour Authority EURES report - '
      + 'the occupations more than half of reporting countries call a shortage, with how many countries and '
      + 'how severe. These are the designations that open shortage-list visa routes and labour-market-test '
      + 'exemptions. Absence from the list is NOT evidence of no shortage: the threshold is "more than half '
      + 'of reporting countries", so concentrated shortages such as ICT do not appear.',
    inputSchema: {
      type: 'object',
      properties: {
        sector: {
          type: 'string',
          description: 'Sector code (e.g. "O-Q" for health and care) or name. Omit for all.',
        },
      },
    },
  },
];

/* --------------------------------------------------------------- handlers */

function toolListCountries(a) {
  const side = a.side || 'both';
  const out = {};
  if (side !== 'destinations') out.origins = ORIGINS;
  if (side !== 'origins') out.destinations = DESTS;
  return out;
}

function toolSearch(a) {
  const preset = a.preset || 'ajf';
  const o = a.origin ? resolveCountry(a.origin, ORIGINS) : null;
  const d = a.destination ? resolveCountry(a.destination, DESTS) : null;
  if (a.origin && !o) {
    return { error: 'Unknown origin "' + a.origin + '". Call list_countries for valid names.' };
  }
  if (a.destination && !d) {
    return { error: 'Unknown destination "' + a.destination + '". Call list_countries for valid names.' };
  }

  const osub = (a.origin_subregion || '').toLowerCase();
  const ssa = osub.indexOf('sub-saharan') !== -1;
  const dsub = (a.destination_subregion || '').toLowerCase();

  const rows = CORRIDORS.filter(r => {
    if (o && r.orig !== o.iso3) return false;
    if (d && r.dest !== d.iso3) return false;
    if (ssa) { if (NORTH_AFRICA.has(r.orig)) return false; }
    else if (osub && (r.orig_subregion || '').toLowerCase() !== osub) return false;
    if (dsub && (r.dest_subregion || '').toLowerCase() !== dsub) return false;
    if (a.low_hanging_fruit_only && !r.low_hanging_fruit) return false;
    if (a.min_score != null && scoreOf(r, preset) < a.min_score) return false;
    if (a.max_permits != null && (r.permits_mean3 || 0) > a.max_permits) return false;
    if (a.exclude_who_safeguard_origins && r.who_safeguard_origin) return false;
    return true;
  });

  const keys = {
    score: r => scoreOf(r, preset),
    underutilisation: r => r.underutilisation_score,
    access: r => r.access_score,
    ease: r => r.ease_score,
    potential: r => r.potential_score,
    scale: r => r.scale_score,
    permits: r => r.permits_mean3 || 0,
    shortfall: r => r.unrealised_flow || 0,
  };
  const key = keys[a.sort_by || 'score'] || keys.score;
  rows.sort((x, y) => (key(y) || 0) - (key(x) || 0));

  const limit = Math.min(Math.max(a.limit || 20, 1), 100);
  return {
    matched: rows.length,
    showing: Math.min(limit, rows.length),
    preset: preset,
    corridors: rows.slice(0, limit).map(r => ({
      corridor: r.orig_name + ' -> ' + r.dest_name,
      orig: r.orig,
      dest: r.dest,
      score: scoreOf(r, preset),
      underutilisation: r.underutilisation_score,
      access: r.access_score,
      ease: r.ease_score,
      potential: r.potential_score,
      scale: r.scale_score,
      permits_per_year: r.permits_mean3 || 0,
      predicted_by_model: r.predicted,
      low_hanging_fruit: !!r.low_hanging_fruit,
      who_safeguard_origin: !!r.who_safeguard_origin,
    })),
  };
}

function toolGetCorridor(a) {
  const o = resolveCountry(a.origin, ORIGINS);
  const d = resolveCountry(a.destination, DESTS);
  if (!o) return { error: 'Unknown origin "' + a.origin + '". Call list_countries for valid names.' };
  if (!d) return { error: 'Unknown destination "' + a.destination + '". Call list_countries for valid names.' };
  const r = CORRIDORS.find(x => x.orig === o.iso3 && x.dest === d.iso3);
  if (!r) return { error: 'No corridor ' + o.name + ' -> ' + d.name + '.' };

  const pick = function () {
    const out = {};
    for (let i = 0; i < arguments.length; i++) {
      const k = arguments[i];
      if (r[k] !== undefined) out[k] = r[k];
    }
    return out;
  };

  return {
    corridor: r.orig_name + ' -> ' + r.dest_name,
    scores: {
      cgd: r.score_cgd, ajf: r.score_ajf, balanced: r.score_balanced,
      underutilisation: r.underutilisation_score, access: r.access_score,
      ease: r.ease_score, potential: r.potential_score, scale: r.scale_score,
      low_hanging_fruit: !!r.low_hanging_fruit,
    },
    flows: {
      permits_per_year: r.permits_mean3 || 0,
      permits_latest: r.permits_latest || 0,
      predicted_by_model: r.predicted,
      log_gap: r.gap_log,
      shortfall_per_year: r.unrealised_flow,
      student_permits: r.student_permits,
    },
    access: pick('f_consular', 'f_visa_success', 'f_pathways', 'dest_embassy_in_orig',
      'neighbour_embassy', 'vfs_present', 'vfs_operator', 'consular_tier',
      'visa_refusal_rate', 'visa_applications', 'africa_orientation_penalty',
      'icmpd_restricted_n'),
    ease: pick('f_language', 'f_agreement', 'f_salary_threshold', 'f_cultural',
      'f_recognition', 'f_processing', 'f_connectivity', 'f_admin',
      'shared_official_language', 'origin_official_languages',
      'dest_official_languages', 'closest_language_pair', 'language_proximity',
      'n_agreements', 'agreement_types', 'eu_instruments',
      'pt_fast_track_scheme', 'pt_talent_partnership',
      'req_language_requirement', 'req_recognition_regime', 'req_key_requirements',
      'air_passengers', 'air_year'),
    potential: pick('p_income_gain', 'p_skills_supply', 'p_labour_supply',
      'p_dest_demand', 'p_diaspora_growth', 'median_income_gap', 'income_multiple',
      'o_inc_median_annual_ppp', 'd_inc_median_annual_ppp', 'diaspora',
      'diaspora_cagr', 'o_grad_health_welfare', 'o_grad_ict',
      'o_grad_engineering_construction', 'o_nurses_midwives_per_10k',
      'd_nurses_midwives_per_10k'),
    context: pick('dist', 'f_proximity', 'majority_religion_orig',
      'majority_religion_dest', 'cultural_basis', 'who_safeguard_origin', 'who_status'),
    notes: [
      'Scores are percentile ranks within the 1,458-corridor universe; higher is always better.',
      'f_proximity is shown but NOT scored: distance is already a regressor in the gravity model behind underutilisation.',
      'The WHO safeguards flag is never scored, only surfaced.',
      'Components with no data are dropped and the remaining weights renormalise, rather than being scored as average.',
    ],
  };
}

function toolMethodology(a) {
  const sec = a.section || 'all';
  const out = {};
  if (sec === 'weights' || sec === 'all') {
    out.presets = META.presets;
    out.access_weights = META.access_weights;
    out.ease_weights = META.ease_weights;
    out.potential_weights = META.potential_weights;
    out.notes = [
      'Access components combine HARMONICALLY, so the weakest gate dominates the dimension.',
      'Missing components are dropped and the remaining weights renormalise - absent data is never scored as average.',
      'Distance is deliberately excluded from ease: it is already a regressor in the gravity model.',
    ];
  }
  if (sec === 'gravity' || sec === 'all') out.gravity = META.gravity;
  if (sec === 'sources' || sec === 'all') out.sources = META.sources;
  return out;
}

function toolShortages(a) {
  const sh = META.shortage || {};
  const sectors = META.sectors || {};
  if (a.sector) {
    const q = String(a.sector).trim().toLowerCase();
    const keys = Object.keys(sectors);
    const code = keys.find(k => k.toLowerCase() === q)
      || keys.find(k => (sectors[k] || '').toLowerCase().indexOf(q) !== -1);
    if (!code) {
      return {
        error: 'Unknown sector "' + a.sector + '". Known: '
          + keys.map(k => k + ' (' + sectors[k] + ')').join(', '),
      };
    }
    return {
      sector: code,
      label: sectors[code],
      shortage: sh[code] || null,
      note: sh[code] ? undefined
        : 'No widespread shortage occupation in this family. That is not evidence of no shortage - the '
          + 'EURES threshold is "more than half of reporting countries", so concentrated shortages such '
          + 'as ICT do not appear.',
    };
  }
  return {
    source: 'European Labour Authority, EURES report on labour shortages and surpluses 2025',
    scope: 'EU-wide. The per-country answers exist only inside a PowerBI embed with no documented query interface.',
    sectors: Object.keys(sectors).map(code => ({
      code: code, label: sectors[code], shortage: sh[code] || null,
    })),
  };
}

const DISPATCH = {
  list_countries: toolListCountries,
  search_corridors: toolSearch,
  get_corridor: toolGetCorridor,
  get_methodology: toolMethodology,
  get_sector_shortages: toolShortages,
};

/* -------------------------------------------------------------- transport */

function rpc(id, result) { return { jsonrpc: '2.0', id: id, result: result }; }
function rpcError(id, code, message) {
  return { jsonrpc: '2.0', id: id, error: { code: code, message: message } };
}

function handle(msg) {
  const id = msg && msg.id;
  const method = msg && msg.method;
  const params = (msg && msg.params) || {};

  if (method === 'initialize') {
    const asked = params.protocolVersion;
    return rpc(id, {
      protocolVersion: SUPPORTED.indexOf(asked) !== -1 ? asked : FALLBACK,
      capabilities: { tools: { listChanged: false } },
      serverInfo: SERVER,
      instructions:
        'Scores every Africa-to-EU labour mobility corridor. Start with search_corridors; use '
        + 'get_corridor for detail, and get_methodology to check how a number was built and which '
        + 'inputs are hand-assembled rather than official.',
    });
  }
  if (method === 'ping') return rpc(id, {});
  if (method === 'tools/list') return rpc(id, { tools: TOOLS });
  if (method === 'tools/call') {
    const fn = DISPATCH[params.name];
    if (!fn) return rpcError(id, -32602, 'Unknown tool: ' + params.name);
    try {
      const result = fn(params.arguments || {});
      return rpc(id, {
        content: [{ type: 'text', text: JSON.stringify(result, null, 1) }],
        isError: !!result.error,
      });
    } catch (e) {
      return rpc(id, {
        content: [{ type: 'text', text: 'Tool failed: ' + e.message }],
        isError: true,
      });
    }
  }
  return rpcError(id, -32601, 'Method not found: ' + method);
}

module.exports = function (req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, GET, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers',
    'Content-Type, Authorization, mcp-session-id, mcp-protocol-version');
  res.setHeader('Access-Control-Expose-Headers', 'mcp-session-id');

  if (req.method === 'OPTIONS') { res.status(204).end(); return; }

  // A plain GET is what a person gets pasting the URL into a browser, and it is
  // also the SSE-stream opener some clients try first. This server is stateless
  // and answers everything in the POST response, so there is no stream to open:
  // say so rather than leaving the client hanging on an open connection.
  if (req.method === 'GET') {
    res.status(200).json({
      server: SERVER,
      transport: 'streamable-http',
      usage: 'POST JSON-RPC 2.0 to this URL. Add it in Cowork as a custom connector.',
      tools: TOOLS.map(t => t.name),
      corridors: CORRIDORS.length,
      generated: DATA.generated,
    });
    return;
  }
  if (req.method !== 'POST') { res.status(405).json(rpcError(null, -32600, 'Use POST')); return; }

  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch (e) {
      res.status(400).json(rpcError(null, -32700, 'Parse error'));
      return;
    }
  }
  if (!body) { res.status(400).json(rpcError(null, -32700, 'Empty body')); return; }

  // Notifications carry no id and must not receive a response body.
  const isNotification = m => m && m.id === undefined;
  if (Array.isArray(body)) {
    const out = body.filter(m => !isNotification(m)).map(handle);
    if (out.length) res.status(200).json(out); else res.status(202).end();
    return;
  }
  if (isNotification(body)) { res.status(202).end(); return; }
  res.status(200).json(handle(body));
};
