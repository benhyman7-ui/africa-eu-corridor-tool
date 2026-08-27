/* Africa Jobs Fund — Labour Mobility Corridor Tool
   Scoring is recomputed in the browser so the weight sliders re-rank instantly.
   The Python pipeline ships normalised 0–100 components; composites are built
   here. Weights are shown 0–10 and normalised by their own sum, so only the
   ratios between them matter. */

const S = {
  corridors: [], meta: null, series: null, sectors: null, geo: null,
  view: 'map', preset: 'cgd', geoMean: false,
  top: {}, ease: {}, pot: {},
  filters: { origRegion: '', orig: '', destRegion: '', dest: '', flow: '0',
             consular: false, agreement: false, lhf: false, hideWho: false },
  sort: { key: 'score', dir: -1 },
  detail: null, sector: 'O-Q', matrixMetric: 'score',
  mapSel: null, mapLines: 18,
};

const TOP_LABELS = {
  underutilisation: 'Underutilisation', ease: 'Ease of unlocking', potential: 'Potential',
};
const EASE_LABELS = {
  f_consular: 'Consular access', f_visa_success: 'Visa success rate',
  f_language: 'Language access', f_agreement: 'Legal channel',
  f_connectivity: 'Air connectivity', f_proximity: 'Proximity',
  f_admin: 'Origin admin capacity', f_cultural: 'Cultural proximity',
};
const POT_LABELS = {
  p_income_gain: 'Income gain', p_labour_supply: 'Labour supply at origin',
  p_dest_demand: 'Destination demand',
};
const PRESET_NOTES = {
  cgd: 'Corridors where flows are unexpectedly low and the barriers to fixing that are unexpectedly small — the framing in the CGD/TMO concept note.',
  ajf: 'Leads on income gain: the log of the annual gap in median income between the two countries.',
  balanced: 'Equal weight on all three dimensions.',
};

const $ = s => document.querySelector(s);
const el = (t, a = {}, ...kids) => {
  const n = document.createElement(t);
  for (const [k, v] of Object.entries(a)) {
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined)
    n.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
  return n;
};
const fmt = (v, d = 0) => v === null || v === undefined || Number.isNaN(v)
  ? '–' : Number(v).toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d });
const pctf = v => v === null || v === undefined ? '–' : (v * 100).toFixed(1) + '%';
const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* ---------------------------------------------------------------- scoring */
function weighted(row, weights, fallback) {
  let num = 0, den = 0;
  for (const [k, w] of Object.entries(weights)) {
    const v = row[k];
    if (w > 0 && v !== null && v !== undefined) { num += v * w; den += w; }
  }
  return den > 0 ? num / den : (row[fallback] ?? 50);
}
function rescore() {
  const t = S.top, sum = (t.underutilisation + t.ease + t.potential) || 1;
  for (const r of S.corridors) {
    r.ease = weighted(r, S.ease, 'ease_score');
    r.potential = weighted(r, S.pot, 'potential_score');
    r.under = r.underutilisation_score ?? 50;
    if (S.geoMean) {
      r.score = Math.exp((Math.log(Math.max(r.under, 1)) * t.underutilisation
        + Math.log(Math.max(r.ease, 1)) * t.ease
        + Math.log(Math.max(r.potential, 1)) * t.potential) / sum);
    } else {
      r.score = (r.under * t.underutilisation + r.ease * t.ease
        + r.potential * t.potential) / sum;
    }
  }
}
function filtered() {
  const f = S.filters;
  return S.corridors.filter(r => {
    if (f.origRegion && r.orig_subregion !== f.origRegion) return false;
    if (f.orig && r.orig !== f.orig) return false;
    if (f.destRegion && r.dest_subregion !== f.destRegion) return false;
    if (f.dest && r.dest !== f.dest) return false;
    if (f.flow === 'zero') { if (r.permits_latest > 0) return false; }
    else if (+f.flow > 0 && (r.permits_mean3 ?? 0) < +f.flow) return false;
    if (f.consular && !(r.visa_consulate_present || r.dest_embassy_in_orig)) return false;
    if (f.agreement && !r.has_any_agreement) return false;
    if (f.lhf && !r.low_hanging_fruit) return false;
    if (f.hideWho && r.who_safeguard_origin) return false;
    return true;
  });
}
function sorted(rows) {
  const { key, dir } = S.sort;
  return [...rows].sort((a, b) => {
    const x = a[key], y = b[key];
    if (typeof x === 'string' || typeof y === 'string')
      return dir * String(x ?? '').localeCompare(String(y ?? ''));
    return dir * ((x ?? -Infinity) - (y ?? -Infinity));
  });
}

/* ------------------------------------------------------------ colour ramps */
const lerp = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
const rgb = c => `rgb(${c.join(',')})`;
const ramp = stops => t => {
  t = Math.max(0, Math.min(1, t));
  const i = Math.min(stops.length - 2, Math.floor(t * (stops.length - 1)));
  return rgb(lerp(stops[i], stops[i + 1], t * (stops.length - 1) - i));
};

// ColorBrewer RdYlGn, 11 class, reversed so green is the promising end. A full
// heat scale rather than a three-stop blend: with eleven stops the eye can
// actually separate adjacent bands.
const promise = ramp([
  [165, 0, 38], [215, 48, 39], [244, 109, 67], [253, 174, 97], [254, 224, 139],
  [255, 255, 191], [217, 239, 139], [166, 217, 106], [102, 189, 99],
  [26, 152, 80], [0, 104, 55],
]);

// The map stays on the AJF palette rather than the green heat scale. Salience
// climbs monotonically even though lightness does not: pale blue reads as
// background, deep navy as substantial, and the AJF gold at the top end makes
// the strongest corridors jump off the page.
const brand = ramp([
  [238, 242, 248], [211, 223, 238], [176, 196, 222], [138, 165, 202],
  [102, 133, 180], [70, 104, 158], [43, 76, 126], [22, 48, 92],
  [11, 30, 61], [138, 86, 12], [214, 145, 20],
]);

/* Composite scores cluster hard — the top 18 corridors sit inside a handful of
   points — so colouring on raw value leaves almost the whole ramp unused and
   everything looks the same shade. These map a value to its RANK position
   within whatever is currently displayed, which spends the full scale every
   time. The trade-off is that colour is relative to the current view, not an
   absolute grade, and the legends say so. */
function rankScale(values) {
  const clean = values.filter(v => v !== null && v !== undefined && !Number.isNaN(v))
    .sort((a, b) => a - b);
  if (!clean.length) return () => 0.5;
  const n = clean.length;
  return v => {
    if (v === null || v === undefined || Number.isNaN(v)) return null;
    let lo = 0, hi = n;                       // lower bound
    while (lo < hi) { const m = (lo + hi) >> 1; if (clean[m] < v) lo = m + 1; else hi = m; }
    let lo2 = 0, hi2 = n;                     // upper bound, so ties share a rank
    while (lo2 < hi2) { const m = (lo2 + hi2) >> 1; if (clean[m] <= v) lo2 = m + 1; else hi2 = m; }
    return n === 1 ? 0.5 : ((lo + lo2 - 1) / 2) / (n - 1);
  };
}
// Push contrast towards the top end so the strongest corridors stand out rather
// than the middle of the pack soaking up the vivid colours.
const punch = (t, g = 1.35) => Math.pow(Math.max(0, Math.min(1, t)), g);

/* ------------------------------------------------------------- side panel */
function sliderBlock(host, obj, labels, onchange) {
  host.innerHTML = '';
  for (const k of Object.keys(obj)) {
    const out = el('span', { class: 'val' }, obj[k].toFixed(1));
    const inp = el('input', {
      type: 'range', min: 0, max: 10, step: 0.1, value: obj[k],
      oninput: e => { obj[k] = +e.target.value; out.textContent = obj[k].toFixed(1); onchange(); },
    });
    host.appendChild(el('div', { class: 'slider' },
      el('div', { class: 'row' }, el('span', { class: 'lbl' }, labels[k] ?? k), out), inp));
  }
}
function syncSliders(host, obj) {
  [...host.querySelectorAll('.slider')].forEach((s, i) => {
    const k = Object.keys(obj)[i];
    s.querySelector('input').value = obj[k];
    s.querySelector('.val').textContent = obj[k].toFixed(1);
  });
}

// Stored weights are fractions summing to 1; the UI works in 0–10.
const toTen = o => Object.fromEntries(Object.entries(o).map(([k, v]) =>
  [k, Math.round(v * 10 / Math.max(...Object.values(o)) * 10) / 10]));

function applyPreset(name) {
  S.preset = name;
  const p = S.meta.presets[name];
  const mx = Math.max(...Object.values(p));
  for (const k of Object.keys(TOP_LABELS)) S.top[k] = Math.round(p[k] / mx * 100) / 10;
  Object.assign(S.ease, toTen(S.meta.ease_weights));
  Object.assign(S.pot, toTen(S.meta.potential_weights));
  syncSliders($('#topWeights'), S.top);
  syncSliders($('#easeWeights'), S.ease);
  syncSliders($('#potWeights'), S.pot);
  [...$('#presets').children].forEach((b, i) =>
    b.classList.toggle('on', Object.keys(S.meta.presets)[i] === name));
  $('#presetNote').textContent = PRESET_NOTES[name] ?? '';
  render();
}
function markCustom() {
  S.preset = 'custom';
  [...$('#presets').children].forEach(b => b.classList.remove('on'));
  $('#presetNote').textContent = 'Custom weighting.';
}

function buildSidebar() {
  const m = S.meta;
  const pres = $('#presets');
  for (const name of Object.keys(m.presets))
    pres.appendChild(el('button', { class: 'preset', onclick: () => applyPreset(name) },
      name === 'cgd' ? 'CGD' : name === 'ajf' ? 'AJF' : 'Balanced'));

  for (const k of Object.keys(TOP_LABELS)) S.top[k] = 5;
  Object.assign(S.ease, toTen(m.ease_weights));
  Object.assign(S.pot, toTen(m.potential_weights));
  sliderBlock($('#topWeights'), S.top, TOP_LABELS, () => { markCustom(); render(); });
  sliderBlock($('#easeWeights'), S.ease, EASE_LABELS, () => { markCustom(); render(); });
  sliderBlock($('#potWeights'), S.pot, POT_LABELS, () => { markCustom(); render(); });

  // ---- filters: region and country, both sides -------------------------- //
  const origs = [...new Map(S.corridors.map(r => [r.orig, r])).values()]
    .sort((a, b) => a.orig_name.localeCompare(b.orig_name));
  const dests = [...new Map(S.corridors.map(r => [r.dest, r])).values()]
    .sort((a, b) => a.dest_name.localeCompare(b.dest_name));
  for (const r of [...new Set(origs.map(o => o.orig_subregion))].filter(Boolean).sort())
    $('#fOrigRegion').appendChild(el('option', { value: r }, r));
  for (const r of [...new Set(dests.map(d => d.dest_subregion))].filter(Boolean).sort())
    $('#fDestRegion').appendChild(el('option', { value: r }, r));

  const fillCountries = () => {
    const oR = S.filters.origRegion, dR = S.filters.destRegion;
    const rebuild = (sel, list, key, nameKey, subKey, region, label) => {
      const cur = S.filters[key];
      sel.innerHTML = '';
      sel.appendChild(el('option', { value: '' }, label));
      for (const r of list) {
        if (region && r[subKey] !== region) continue;
        sel.appendChild(el('option', { value: r[key], selected: cur === r[key] ? '' : null },
          r[nameKey]));
      }
      if (cur && !list.some(r => r[key] === cur && (!region || r[subKey] === region))) {
        S.filters[key] = ''; sel.value = '';
      }
    };
    rebuild($('#fOrig'), origs, 'orig', 'orig_name', 'orig_subregion', oR, 'All origin countries');
    rebuild($('#fDest'), dests, 'dest', 'dest_name', 'dest_subregion', dR, 'All destinations');
  };
  fillCountries();

  const bind = (sel, key, checkbox = false) => $(sel).addEventListener(
    checkbox ? 'change' : 'input',
    e => {
      S.filters[key] = checkbox ? e.target.checked : e.target.value;
      if (key === 'origRegion' || key === 'destRegion') fillCountries();
      S.detail = null; render();
    });
  bind('#fOrigRegion', 'origRegion'); bind('#fOrig', 'orig');
  bind('#fDestRegion', 'destRegion'); bind('#fDest', 'dest');
  bind('#fFlow', 'flow');
  bind('#fConsular', 'consular', true); bind('#fAgreement', 'agreement', true);
  bind('#fLHF', 'lhf', true); bind('#fWho', 'hideWho', true);

  const goHome = () => {
    Object.assign(S.filters, { origRegion: '', orig: '', destRegion: '', dest: '',
      flow: '0', consular: false, agreement: false, lhf: false, hideWho: false });
    for (const id of ['#fOrigRegion', '#fOrig', '#fDestRegion', '#fDest']) $(id).value = '';
    for (const id of ['#fConsular', '#fAgreement', '#fLHF', '#fWho']) $(id).checked = false;
    $('#fFlow').value = '0';
    $('#geoMean').checked = false; S.geoMean = false;
    S.view = 'map'; S.detail = null; S.mapSel = null; S.mapLines = 18;
    S.sort = { key: 'score', dir: -1 }; S.matrixMetric = 'score'; S.sector = 'O-Q';
    fillCountries(); syncTabs(); applyPreset('cgd');   // applyPreset renders
    scrollTo({ top: 0, behavior: 'smooth' });
  };
  $('#homeBtn').addEventListener('click', goHome);

  $('#resetBtn').addEventListener('click', () => {
    Object.assign(S.filters, { origRegion: '', orig: '', destRegion: '', dest: '',
      flow: '0', consular: false, agreement: false, lhf: false, hideWho: false });
    for (const id of ['#fOrigRegion', '#fOrig', '#fDestRegion', '#fDest', '#fFlow']) $(id).value = '';
    for (const id of ['#fConsular', '#fAgreement', '#fLHF', '#fWho']) $(id).checked = false;
    $('#fFlow').value = '0';
    fillCountries(); S.mapSel = null; S.detail = null; render();
  });
  $('#geoMean').addEventListener('change', e => { S.geoMean = e.target.checked; render(); });

  const tabs = $('#tabs');
  for (const [k, label] of [['map', 'Map'], ['ranking', 'Ranking'], ['matrix', 'Matrix'],
    ['sectors', 'Sectors'], ['method', 'Method & sources']])
    tabs.appendChild(el('button', {
      class: 'tab' + (S.view === k ? ' on' : ''),
      onclick: () => { S.view = k; S.detail = null; syncTabs(); render(); },
    }, label));

  $('#csvBtn').addEventListener('click', downloadCSV);
  $('#themeBtn').addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme');
    const dark = matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.setAttribute('data-theme',
      cur === 'dark' ? 'light' : cur === 'light' ? 'dark' : (dark ? 'light' : 'dark'));
    render();
  });
  applyPreset('cgd');
}
function syncTabs() {
  const order = ['map', 'ranking', 'matrix', 'sectors', 'method'];
  [...$('#tabs').children].forEach((b, i) => b.classList.toggle('on', order[i] === S.view));
}

/* ------------------------------------------------------------------ stats */
function renderStats(rows) {
  const zero = rows.filter(r => r.permits_latest === 0).length;
  const lhf = rows.filter(r => r.low_hanging_fruit).length;
  const inCountry = rows.filter(r => r.visa_consulate_present || r.dest_embassy_in_orig).length;
  const ref = rows.filter(r => r.visa_refusal_rate !== null && r.visa_refusal_rate !== undefined);
  const medRef = ref.length
    ? ref.map(r => r.visa_refusal_rate).sort((a, b) => a - b)[Math.floor(ref.length / 2)] : null;
  const cards = [
    [fmt(rows.length), 'corridors shown'],
    [fmt(rows.reduce((a, r) => a + (r.permits_mean3 ?? 0), 0)), 'permits/yr (3-yr mean)'],
    [fmt(rows.reduce((a, r) => a + (r.predicted ?? 0), 0)), 'predicted by the model'],
    [fmt(zero), `zero permits in ${S.meta.latest_year}`],
    [`${fmt(inCountry)} / ${fmt(rows.length)}`, 'can apply in country'],
    [medRef === null ? '–' : pctf(medRef), 'median visa refusal rate'],
    [fmt(lhf), 'low-hanging fruit'],
  ];
  $('#stats').innerHTML = '';
  for (const [v, k] of cards)
    $('#stats').appendChild(el('div', { class: 'stat' },
      el('div', { class: 'v' }, v), el('div', { class: 'k' }, k)));
}

/* -------------------------------------------------------------------- map */
function viewMap(rows) {
  const feats = S.geo.features;
  // Bounds are derived from the geometry, not hardcoded: a fixed box clipped
  // northern Finland. Africa plus Europe is genuinely taller than it is wide
  // (about 108 degrees of latitude against 86 of longitude), so the viewBox
  // takes its aspect from the data rather than forcing a landscape frame.
  const B = S.mapBounds || (S.mapBounds = (() => {
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    const eat = (x, y) => {
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
    };
    for (const f of feats) {
      const polys = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
      for (const p of polys) for (const ring of p) for (const [x, y] of ring) eat(x, y);
    }
    for (const c of S.meta.countries) if (c.lon != null) eat(c.lon, c.lat);
    const pad = 2;
    return { x0: x0 - pad, x1: x1 + pad, y0: y0 - pad, y1: y1 + pad };
  })());
  const xr = B.x1 - B.x0, yr = B.y1 - B.y0;
  const W = 880, H = Math.round(W * yr / xr);
  const k = W / xr;
  const PX = lon => (lon - B.x0) * k;
  const PY = lat => (B.y1 - lat) * k;

  const pathOf = geom => {
    const ring = r => 'M' + r.map(([x, y]) => `${PX(x).toFixed(1)},${PY(y).toFixed(1)}`).join('L') + 'Z';
    return geom.type === 'Polygon'
      ? geom.coordinates.map(ring).join('')
      : geom.coordinates.map(p => p.map(ring).join('')).join('');
  };

  const pts = new Map(S.meta.countries.map(c => [c.iso3, c]));
  const centroid = iso => {
    const c = pts.get(iso);
    return c ? [PX(c.lon), PY(c.lat)] : null;
  };

  // Which corridors to draw. Showing 1,458 arcs is unreadable, so: with a
  // country selected, its own best links; otherwise the strongest overall.
  const sel = S.mapSel;
  let pool = rows;
  if (sel) pool = rows.filter(r => r.orig === sel || r.dest === sel);
  const shown = sorted(pool).slice(0, S.mapLines);

  // `shown` is already sorted best-first, so rank position is just the index —
  // which spreads the ramp evenly no matter how tightly the scores cluster.
  const n = shown.length;
  const rankOf = new Map(shown.map((r, i) => [r, n > 1 ? 1 - i / (n - 1) : 1]));
  const norm = r => rankOf.get(r) ?? 0.5;

  const touched = new Set(shown.flatMap(r => [r.orig, r.dest]));

  // land
  const land = feats.map(f => {
    const iso = f.id;
    const cls = ['land', f.properties.side === 'destination' ? 'dest' : '',
      iso === sel ? 'sel' : '', sel && !touched.has(iso) && iso !== sel ? 'dim' : ''].join(' ');
    return `<path class="${cls}" data-iso="${iso}" d="${pathOf(f.geometry)}"><title>${esc(f.properties.name)}</title></path>`;
  }).join('');

  // island states with no polygon in the source get a marker instead
  const haveGeom = new Set(feats.map(f => f.id));
  const dots = S.meta.countries.filter(c => !haveGeom.has(c.iso3)).map(c =>
    `<circle class="dot${c.iso3 === sel ? ' sel' : ''}" data-iso="${c.iso3}"
      cx="${PX(c.lon).toFixed(1)}" cy="${PY(c.lat).toFixed(1)}" r="3.4"><title>${esc(c.name)}</title></circle>`).join('');

  // Weakest first so the strong corridors paint on top of the pale ones.
  const arcs = shown.map((r, i) => ({ r, i })).reverse().map(({ r, i }) => {
    const a = centroid(r.orig), b = centroid(r.dest);
    if (!a || !b) return '';
    const t = norm(r);
    const p = punch(t, 1.45);
    const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2 - Math.hypot(b[0] - a[0], b[1] - a[1]) * 0.24;
    // 0.6 -> 7.5 px and 8% -> 100% opacity: the top corridors read as bold
    // strokes, the tail as faint hairlines.
    const w = (0.6 + 6.9 * punch(t, 1.9)).toFixed(2);
    const op = (0.08 + 0.92 * p).toFixed(3);
    const tip = `${r.orig_name} → ${r.dest_name}\n#${i + 1} of ${n} shown · score ${fmt(r.score, 0)}\nactual ${fmt(r.permits_mean3, 0)}/yr vs predicted ${fmt(r.predicted, 0)}`;
    return `<path class="arc" data-i="${i}" d="M${a[0].toFixed(1)},${a[1].toFixed(1)} Q${mx.toFixed(1)},${my.toFixed(1)} ${b[0].toFixed(1)},${b[1].toFixed(1)}"
      stroke="${brand(p)}" stroke-width="${w}" opacity="${op}"><title>${esc(tip)}</title></path>`;
  }).join('');

  const labels = S.meta.countries.filter(c => touched.has(c.iso3) || c.iso3 === sel)
    .map(c => `<text class="lbl" x="${(PX(c.lon) + 5).toFixed(1)}" y="${(PY(c.lat) + 3).toFixed(1)}">${esc(c.name)}</text>`).join('');

  const stage = el('div', { class: 'mapstage' });
  stage.innerHTML = `<svg class="map" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    <g>${land}</g><g>${dots}</g><g>${arcs}</g><g>${labels}</g></svg>`;

  const pick = iso => { S.mapSel = (S.mapSel === iso ? null : iso); render(); };
  stage.querySelectorAll('[data-iso]').forEach(p =>
    p.addEventListener('click', () => pick(p.getAttribute('data-iso'))));
  stage.querySelectorAll('.arc').forEach(p =>
    p.addEventListener('click', () => { S.detail = shown[+p.getAttribute('data-i')]; render(); }));

  // ---- side list -------------------------------------------------------- //
  const selName = sel ? (pts.get(sel) || {}).name : null;
  const side = el('div', { class: 'mapside' },
    el('h4', {}, selName || 'Strongest corridors'),
    el('div', { class: 'sub' }, sel
      ? `${shown.length} best links ${pts.get(sel).side === 'origin' ? 'from' : 'to'} ${selName} — click the map background to clear`
      : `top ${shown.length} of ${fmt(rows.length)} — click any country to focus it`),
    el('ul', { class: 'arclist' }, shown.map((r, i) => el('li',
      { onclick: () => { S.detail = r; render(); } },
      el('span', { class: 'sw', style: `background:${brand(punch(norm(r), 1.4))}` }),
      el('span', { class: 'nm' }, sel === r.orig ? r.dest_name
        : sel === r.dest ? r.orig_name : `${r.orig_name} → ${r.dest_name}`),
      el('span', { class: 'sc' }, fmt(r.score, 0))))),
    sel ? el('button', {
      class: 'ghost', style: 'width:100%;margin-top:12px',
      onclick: () => { S.mapSel = null; render(); },
    }, 'Clear selection') : null);

  const linesSel = el('select', {
    style: 'width:auto', onchange: e => { S.mapLines = +e.target.value; render(); },
  }, [10, 18, 30, 50, 80].map(n =>
    el('option', { value: n, selected: S.mapLines === n ? '' : null }, `${n} lines`)));

  return el('div', { class: 'card' },
    el('header', {},
      el('h2', {}, 'Corridor map'),
      linesSel,
      el('span', { class: 'hint' }, 'colour and weight = rank among the lines shown'),
      el('span', { class: 'spacer' }),
      el('div', { class: 'maplegend' }, 'least',
        el('div', { class: 'ramp' }, Array.from({ length: 11 }, (_, i) =>
          el('i', { style: `background:${brand(i / 10)}` }))), 'most promising')),
    el('div', { class: 'maplayout' }, stage, side));
}

/* ---------------------------------------------------------------- ranking */
const COLS = [
  { k: '_rank', l: '#' },
  { k: 'orig_name', l: 'Corridor', render: r => el('span', { class: 'corridor' },
      r.orig_name, el('span', { class: 'arrow' }, '→'), r.dest_name) },
  { k: 'score', l: 'Score', bar: true },
  { k: 'under', l: 'Under', bar: true },
  { k: 'ease', l: 'Ease', bar: true },
  { k: 'potential', l: 'Potential', bar: true },
  { k: 'permits_mean3', l: 'Actual/yr', num: 0 },
  { k: 'predicted', l: 'Predicted', num: 0 },
  { k: 'median_income_gap', l: 'Income gap', num: 0, prefix: '$' },
  { k: 'visa_refusal_rate', l: 'Visa refusal', rate: true },
  { k: 'consular_tier', l: 'Can apply?', pill: r => r.visa_consulate_present || r.dest_embassy_in_orig },
  { k: 'low_hanging_fruit', l: 'Flags', render: r => el('span', {},
      r.low_hanging_fruit ? el('span', { class: 'pill lhf' }, 'LHF') : null,
      r.who_safeguard_origin ? el('span', { class: 'pill ssl', style: 'margin-left:4px',
        title: r.who_status }, 'SSL') : null) },
];
function viewRanking(rows) {
  const head = el('tr', {}, COLS.map(c => el('th', {
    class: (S.sort.key === c.k ? 'sorted ' : '') + (c.num !== undefined || c.bar || c.rate ? 'num' : ''),
    onclick: () => {
      if (S.sort.key === c.k) S.sort.dir *= -1;
      else { S.sort.key = c.k; S.sort.dir = (c.k === 'orig_name') ? 1 : -1; }
      render();
    },
  }, c.l + (S.sort.key === c.k ? (S.sort.dir < 0 ? ' ↓' : ' ↑') : ''))));

  const body = el('tbody', {});
  rows.slice(0, 400).forEach((r, i) => {
    const tr = el('tr', { onclick: () => { S.detail = r; render(); } });
    for (const c of COLS) {
      if (c.k === '_rank') tr.appendChild(el('td', { class: 'rank' }, i + 1));
      else if (c.render) tr.appendChild(el('td', {}, c.render(r)));
      else if (c.bar) {
        const v = r[c.k] ?? 0;
        // Coloured by VALUE, not rank: the bar's width already encodes the
        // value, and colouring by rank made the two disagree - a 62 near the top
        // of the table looked identical to a 96.
        const t = Math.max(0, Math.min(1, v / 100));
        tr.appendChild(el('td', {}, el('div', { class: 'bar' },
          el('i', { style: `width:${Math.max(0, Math.min(100, v))}%;background:${brand(t)}` }),
          el('b', {}, v.toFixed(0)))));
      } else if (c.rate) tr.appendChild(el('td', { class: 'num' }, pctf(r[c.k])));
      else if (c.pill) tr.appendChild(el('td', {}, el('span',
        { class: 'pill ' + (c.pill(r) ? 'yes' : 'no'), title: r.consular_tier || '' },
        c.pill(r) ? 'in country' : 'no')));
      else tr.appendChild(el('td', { class: 'num' },
        (c.prefix && r[c.k] != null ? c.prefix : '') + fmt(r[c.k], c.num)));
    }
    body.appendChild(tr);
  });

  return el('div', { class: 'card' },
    el('header', {}, el('h2', {}, 'Ranked corridors'),
      el('span', { class: 'hint' },
        `showing ${Math.min(400, rows.length)} of ${fmt(rows.length)} — click a row for the corridor card`)),
    el('div', { class: 'tablewrap' }, el('table', {}, el('thead', {}, head), body)));
}

/* ----------------------------------------------------------------- matrix */
function viewMatrix(rows) {
  const metric = S.matrixMetric;
  const metricSel = el('select', {
    style: 'width:auto', onchange: e => { S.matrixMetric = e.target.value; render(); },
  }, [['score', 'Composite score'], ['under', 'Underutilisation'], ['ease', 'Ease'],
      ['potential', 'Potential'], ['gap_log', 'Log gap vs prediction'],
      ['visa_refusal_rate', 'Visa refusal rate'], ['permits_mean3', 'Permits per year']]
    .map(([v, l]) => el('option', { value: v, selected: metric === v ? '' : null }, l)));

  const origins = [...new Map(rows.map(r => [r.orig, r])).values()]
    .sort((a, b) => a.orig_subregion.localeCompare(b.orig_subregion) || a.orig_name.localeCompare(b.orig_name));
  const dests = [...new Map(rows.map(r => [r.dest, r.dest_name]))].sort((a, b) => a[1].localeCompare(b[1]));
  const lookup = new Map(rows.map(r => [r.orig + '|' + r.dest, r]));

  // Lower is better for these two, so the ramp runs the other way.
  const INVERTED = ['gap_log', 'visa_refusal_rate'];
  // Rank across the cells actually on screen, so the full heat scale is used
  // even when the underlying values sit within a few points of each other.
  const rank = rankScale(rows.map(r => r[metric]));
  // A gentle S-curve on the rank spreads the middle of the distribution, where
  // most cells sit, instead of letting them collapse into one muddy band.
  const scurve = t => t < 0.5 ? 0.5 * Math.pow(2 * t, 1.45)
                              : 1 - 0.5 * Math.pow(2 * (1 - t), 1.45);
  const colour = v => {
    const t = rank(v);
    if (t === null) return 'var(--panel-2)';
    return promise(scurve(INVERTED.includes(metric) ? 1 - t : t));
  };

  const head = el('tr', {}, el('th', { class: 'rowh' }, ''),
    dests.map(([, n]) => el('th', {}, el('div', {}, n))));
  const body = el('tbody', {});
  let region = null;
  for (const o of origins) {
    if (o.orig_subregion !== region) {
      region = o.orig_subregion;
      body.appendChild(el('tr', {}, el('th', {
        class: 'rowh', colspan: dests.length + 1,
        style: 'font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-3);padding-top:10px;font-weight:700',
      }, region)));
    }
    const tr = el('tr', {}, el('th', { class: 'rowh' }, o.orig_name));
    for (const [d] of dests) {
      const r = lookup.get(o.orig + '|' + d);
      const v = r ? r[metric] : null;
      tr.appendChild(el('td', {
        style: `background:${colour(v)}`,
        title: r ? `${r.orig_name} → ${r.dest_name}\n${metric}: ${metric === 'visa_refusal_rate' ? pctf(v) : fmt(v, 1)}\nactual ${fmt(r.permits_mean3, 0)} vs predicted ${fmt(r.predicted, 0)}` : '—',
        onclick: () => { if (r) { S.detail = r; render(); } },
      }));
    }
    body.appendChild(tr);
  }
  const legend = el('div', { class: 'maplegend' },
    INVERTED.includes(metric) ? 'worst' : 'lowest',
    el('div', { class: 'ramp' }, Array.from({ length: 11 }, (_, i) =>
      el('i', { style: `background:${promise(i / 10)}` }))),
    INVERTED.includes(metric) ? 'best' : 'highest');

  return el('div', { class: 'card' },
    el('header', {}, el('h2', {}, 'Corridor matrix'), metricSel,
      el('span', { class: 'hint' }, 'colour = rank among the corridors shown'),
      el('span', { class: 'spacer' }), legend),
    el('div', { class: 'matrixwrap' }, el('table', { class: 'matrix' }, el('thead', {}, head), body)));
}

/* ---------------------------------------------------------------- sectors */
function viewSectors(rows) {
  const sel = el('select', {
    style: 'width:auto', onchange: e => { S.sector = e.target.value; render(); },
  }, Object.entries(S.meta.sectors).map(([k, l]) =>
    el('option', { value: k, selected: S.sector === k ? '' : null }, l)));

  const isHealth = S.sector === 'O-Q';
  const scored = rows.map(r => {
    const s = S.sectors[r.dest]?.[S.sector];
    if (!s) return null;
    const vac = s.vacancy ?? 0, wage = s.wage_eur ?? null;
    const gain = wage && r.o_inc_median_annual_ppp ? Math.log(wage / r.o_inc_median_annual_ppp) : null;
    return { ...r, sec_vac: vac, sec_wage: wage, sec_demand: Math.min(100, vac * 25), sec_gain: gain };
  }).filter(Boolean);
  const gains = scored.map(r => r.sec_gain).filter(v => v !== null);
  const gLo = Math.min(...gains), gHi = Math.max(...gains);
  for (const r of scored) {
    const g = r.sec_gain === null ? 50 : 100 * (r.sec_gain - gLo) / ((gHi - gLo) || 1);
    r.sec_score = 0.4 * r.score + 0.35 * r.sec_demand + 0.25 * g;
  }
  scored.sort((a, b) => b.sec_score - a.sec_score);

  const body = el('tbody', {}, scored.slice(0, 60).map((r, i) => el('tr',
    { onclick: () => { S.detail = r; render(); } },
    el('td', { class: 'rank' }, i + 1),
    el('td', {}, el('span', { class: 'corridor' }, r.orig_name,
      el('span', { class: 'arrow' }, '→'), r.dest_name),
      r.who_safeguard_origin && isHealth
        ? el('span', { class: 'pill ssl', style: 'margin-left:6px', title: r.who_status }, 'SSL') : null),
    el('td', {}, el('div', { class: 'bar' }, el('i', { style: `width:${r.sec_score}%` }),
      el('b', {}, r.sec_score.toFixed(0)))),
    el('td', { class: 'num' }, fmt(r.sec_vac, 1) + '%'),
    el('td', { class: 'num' }, r.sec_wage ? '€' + fmt(r.sec_wage) : '–'),
    el('td', { class: 'num' }, '$' + fmt(r.o_inc_median_annual_ppp)),
    el('td', { class: 'num' }, fmt(r.permits_mean3, 0)),
    el('td', {}, el('div', { class: 'bar' }, el('i', { style: `width:${r.under}%` }),
      el('b', {}, r.under.toFixed(0)))))));

  return el('div', {},
    isHealth ? el('div', { class: 'note', style: 'margin:0 0 12px;padding:6px 0 6px 10px' },
      `SSL marks an origin on the WHO health workforce safeguards list `
      + `(${S.meta.totals.who_origins} of ${S.meta.n_origins} origins), where active `
      + `international recruitment of health personnel is discouraged. Never affects the ranking.`) : null,
    el('div', { class: 'callout' },
      el('strong', {}, 'This overlay is modelled, not observed. '),
      'Eurostat reports bilateral permits by permit category, never by occupation, so no dataset states how many Ghanaian nurses entered Germany. Sector scores combine destination-side vacancy and pay by NACE sector with the corridor score and the origin income gap. Treat them as a prioritisation aid, not a measurement.'),
    el('div', { class: 'card' },
      el('header', {}, el('h2', {}, 'Sector overlay'), sel),
      el('div', { class: 'tablewrap' }, el('table', {},
        el('thead', {}, el('tr', {},
          ['#', 'Corridor', 'Sector score', 'Vacancy rate', 'Sector pay', 'Origin median income', 'Actual/yr', 'Under']
            .map(h => el('th', { class: ['#', 'Corridor'].includes(h) ? '' : 'num' }, h)))),
        body))));
}

/* ----------------------------------------------------------------- detail */
function viewDetail(r) {
  const ser = S.series.series[r.orig + '-' + r.dest] ?? [];
  const years = S.series.years;
  const maxV = Math.max(...ser.filter(v => v !== null), 1);
  const bars = el('div', { style: 'display:flex;align-items:flex-end;gap:6px;height:92px' },
    years.map((y, i) => {
      const v = ser[i];
      return el('div', { style: 'flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:4px' },
        el('div', { style: 'font-size:10px;font-family:var(--mono);color:var(--ink-3)' }, v === null ? '–' : fmt(v)),
        el('div', { style: `width:100%;background:var(--navy);border-radius:2px 2px 0 0;height:${v === null ? 1 : Math.max(2, 60 * v / maxV)}px;opacity:${v === null ? .2 : .85}` }),
        el('div', { style: 'font-size:10px;color:var(--ink-3)' }, String(y).slice(2)));
    }));

  const nameOf = i => (S.meta.countries.find(c => c.iso3 === i) || {}).name || i;
  const consularDetail = (r.visa_consulate_present || r.dest_embassy_in_orig)
    ? (r.visa_consulates ? `${r.visa_consulates} visa post(s)` : 'embassy in country')
    : r.neighbour_embassy
      ? `via ${(r.neighbour_embassy_countries || '').split(';').map(nameOf).join(', ')}`
      : r.nearest_embassy_km != null
        ? `nearest ${nameOf(r.nearest_embassy_country)}, ${fmt(r.nearest_embassy_km)} km`
        : 'none reachable in Africa';
  // 'info' is a fourth state: neither pass nor fail, just context. A cross next
  // to the WHO listing would read as a failed barrier, which it is not.
  const mark = (ok, part) => ok === 'info'
    ? el('span', { class: 'mark i' }, '·')
    : el('span', { class: 'mark ' + (ok ? 'y' : part ? 'p' : 'n') }, ok ? '✓' : part ? '~' : '✗');

  const checks = [
    [r.visa_consulate_present || r.dest_embassy_in_orig,
      r.neighbour_embassy || (r.nearest_embassy_km ?? 1e9) < 1500,
      `Consular access — ${r.consular_tier || '–'}`, consularDetail],
    [r.visa_refusal_rate != null && r.visa_refusal_rate < 0.15,
      r.visa_refusal_rate != null && r.visa_refusal_rate < 0.35,
      'Schengen visa refusal rate',
      r.visa_refusal_rate == null ? 'no visa operation' :
        `${pctf(r.visa_refusal_rate)} of ${fmt(r.visa_applications)} applications`],
    [r.shared_official_language, r.dest_language_present_at_origin, 'Shared working language',
      (r.dest_official_languages || '–') + ' / ' + (r.origin_bridge_languages || 'none')],
    [r.has_binding, r.has_any_agreement, 'Legal channel in place',
      r.agreement_types || r.eu_instruments || 'none on file'],
    [r.routes > 0, false, 'Direct flights', r.routes ? `${r.routes} routes` : 'none'],
    [r.diaspora > 1000, r.diaspora > 0, 'Existing diaspora', fmt(r.diaspora) + ' residents'],
    [false, (r.forced_total ?? 0) > 500, 'Forced displacement on this corridor',
      (r.forced_total ?? 0) > 0
        ? `${fmt(r.forced_total)} refugees + asylum seekers — demand the legal channel is not carrying`
        : 'none recorded'],
    // One row, not three. Religion and Hofstede are the two inputs to a single
    // scored factor, so they belong on its line as detail rather than standing
    // alongside it as if they were separate barriers.
    [(r.f_cultural ?? 0) > 60, (r.f_cultural ?? 0) > 40, 'Cultural proximity',
      `${fmt(r.f_cultural, 0)}/100 · religion ${fmt(r.religion_similarity, 2)}`
      + ` (${r.majority_religion_orig || '?'}/${r.majority_religion_dest || '?'})`
      + (r.hofstede_distance == null
        ? ' · no Hofstede basis'
        : ` · Hofstede ${fmt(r.hofstede_distance, 2)} (${r.hofstede_dims_used}/6 dims)`)],
  ];
  // Reported quietly, as one row among the others: it is context on how to
  // recruit, not a barrier to the corridor, and it never touches a score.
  if (r.who_ssl_2023) checks.push([
    'info', false, 'WHO health workforce safeguards list',
    r.who_expected_removal ? 'listed 2023, expected off in 2026' : 'listed 2023 and 2026']);
  const comps = [...Object.keys(EASE_LABELS).map(k => [EASE_LABELS[k], r[k]]),
    ...Object.keys(POT_LABELS).map(k => [POT_LABELS[k], r[k]])];

  return el('div', {},
    el('button', { class: 'backlink', onclick: () => { S.detail = null; render(); } },
      '← back to ' + S.view),
    el('h2', { style: 'margin:9px 0 3px;font-size:22px' }, r.orig_name + ' → ' + r.dest_name),
    el('div', { style: 'color:var(--ink-3);font-size:12px;margin-bottom:8px' },
      `${r.orig_subregion} → ${r.dest_subregion} · ${fmt(r.dist)} km`
      + (r.colony ? ' · former colonial relationship' : '')),
    el('div', { style: 'margin-bottom:14px' },
      r.low_hanging_fruit ? el('span', { class: 'pill lhf' }, 'Low-hanging fruit') : null),

    el('div', { class: 'detail' },
      el('div', { class: 'card' },
        el('header', {}, el('h2', {}, 'Flows and the model')),
        el('div', { class: 'pad' }, bars,
          el('dl', { class: 'kv', style: 'margin-top:16px' },
            el('dt', {}, 'Actual (3-yr mean)'), el('dd', {}, fmt(r.permits_mean3, 1) + ' /yr'),
            el('dt', {}, 'Predicted'), el('dd', {}, fmt(r.predicted, 1) + ' /yr'),
            el('dt', {}, 'Log gap'), el('dd', {}, fmt(r.gap_log, 2) +
              (r.gap_log < 0 ? ` (${(100 * (1 - Math.exp(r.gap_log))).toFixed(0)}% below)` : '')),
            el('dt', {}, 'Blue Card / high-skilled'), el('dd', {},
              fmt(r.permits_blue_card, 1) + ' / ' + fmt(r.permits_high_skilled, 1)),
            el('dt', {}, 'Seasonal / researcher'), el('dd', {},
              fmt(r.permits_seasonal, 1) + ' / ' + fmt(r.permits_researcher, 1))))),

      el('div', { class: 'card' },
        el('header', {}, el('h2', {}, 'Income')),
        el('div', { class: 'pad' },
          el('dl', { class: 'kv' },
            el('dt', {}, `${r.orig_name} median`), el('dd', {},
              '$' + fmt(r.o_inc_median_annual_ppp) + '/yr'),
            el('dt', {}, `${r.dest_name} median`), el('dd', {},
              '$' + fmt(r.d_inc_median_annual_ppp) + '/yr'),
            el('dt', {}, 'Annual gap'), el('dd', {},
              el('strong', {}, '$' + fmt(r.median_income_gap) + '/yr')),
            el('dt', {}, 'Ratio'), el('dd', {}, fmt(r.median_income_ratio, 1) + '×'),
            el('dt', {}, 'Basis'), el('dd', {},
              `${r.o_inc_welfare_type || '?'} / ${r.d_inc_welfare_type || '?'}`)),
          el('div', { class: 'note', style: 'margin-top:12px' },
            'Median annual welfare, 2021 PPP, from the World Bank Poverty and Inequality '
            + 'Platform. The AJF preset ranks on the log of the gap. Note the basis line: '
            + 'PIP measures consumption for most African countries and income for most '
            + 'European ones, because that is what the underlying surveys collect.'))),

      el('div', { class: 'card' },
        el('header', {}, el('h2', {}, 'Barriers')),
        el('div', { class: 'pad' },
          el('ul', { class: 'checklist' }, checks.map(([ok, part, what, val]) =>
            el('li', {}, mark(ok, part), el('span', { class: 'what' }, what),
              el('span', { class: 'val' }, val)))))),

      el('div', { class: 'card' },
        el('header', {}, el('h2', {}, 'Score decomposition')),
        el('div', { class: 'pad' },
          el('div', { class: 'decomp' },
            [['Underutilisation', r.under], ['Ease of unlocking', r.ease], ['Potential', r.potential]]
              .map(([l, v]) => el('div', { class: 'row' },
                el('div', { class: 'lbl', style: 'font-weight:700' }, l),
                el('div', { class: 'bar' }, el('i', { style: `width:${v ?? 0}%` })),
                el('div', { class: 'v' }, fmt(v, 0)))),
            el('div', { style: 'height:6px' }),
            comps.map(([l, v]) => el('div', { class: 'row' },
              el('div', { class: 'lbl' }, l),
              el('div', { class: 'bar', style: 'height:11px' },
                el('i', { style: `width:${v ?? 0}%;opacity:.6` })),
              el('div', { class: 'v' }, fmt(v, 0)))))))));
}

/* ----------------------------------------------------------------- method */
function viewMethod() {
  const t = S.meta.totals, m = S.meta;
  return el('div', { class: 'card' },
    el('header', {}, el('h2', {}, 'Method, data and limits')),
    el('div', { class: 'pad prose', html: `
<h3>What this tool does</h3>
<p>It scores all <strong>${fmt(m.n_corridors)}</strong> corridors from ${m.n_origins} African
countries to the ${m.n_destinations} EU member states on three dimensions and lets you
re-weight them. It is a prioritisation instrument, not a causal estimate.</p>

<h3>1. Underutilisation</h3>
<p>A PPML gravity model is fitted to the <em>global</em> panel of first work permits — every
reported citizenship into every EU state, 2018–${m.latest_year} — then used to predict each
Africa–EU corridor. Fitting on Africa alone would have folded the anomaly we are hunting into
the coefficients. PPML because roughly 40% of corridors record zero permits; log-OLS cannot use
a zero and would discard exactly the observations that matter.</p>
<p>The headline specification carries <strong>origin×year and destination×year fixed
effects</strong>, so everything multilateral is absorbed and the residual is the
corridor-specific anomaly. The estimator is implemented directly (IRLS with alternating
weighted within-transformations) and verified against a dense-dummy statsmodels Poisson GLM to
within 1e-13. Standard errors are clustered by corridor.</p>

<h3>2. Ease of unlocking</h3>
<ul>
<li><strong>Consular access</strong>, tiered. ${Math.round(100 * t.can_apply_in_country / m.n_corridors)}%
of corridors let an applicant lodge an application in their own country. Most EU work visas
require in-person biometrics, so elsewhere the applicant must travel abroad first — often the
binding constraint, and absent from every standard migration dataset. Presence comes primarily
from the DG HOME consulate statistics (a post that actually processed applications last year),
with Wikidata as fallback for Ireland and Cyprus, which sit outside Schengen visa reporting.
Where there is no post in country, a corridor scores 40 if a land neighbour has one and 5–35
scaled by distance otherwise. The fallback search is restricted to African countries: an
applicant who would have to reach Europe to apply for a European visa is not a fallback.</li>
<li><strong>Visa success rate</strong>, from DG HOME Schengen short-stay statistics
(${m.visa_year}). Africa→EU applications were refused at <strong>${(100 * t.africa_refusal_rate).toFixed(1)}%</strong>
against roughly 15% worldwide. This is short-stay, not work-permit, data — a proxy, because no
comparable work-visa series is published — but it is the most direct public measure of whether
a consulate actually says yes.</li>
<li><strong>Legal channel</strong> — bilateral labour agreements, EU Talent and Mobility
Partnerships, CPLP mobility, and Italy's Decreto Flussi preferential quota list.</li>
<li><strong>Language access</strong>, <strong>air connectivity</strong> (only
${fmt(t.direct_flight)} corridors have any direct flight), <strong>proximity</strong>,
<strong>origin administrative capacity</strong> and <strong>cultural proximity</strong>.</li>
</ul>

<h3>On cultural proximity</h3>
<p>The concept note specifies <strong>Hofstede cultural distance</strong> — six dimensions
(power distance, individualism, motivation towards achievement, uncertainty avoidance,
long-term orientation, indulgence) compared by the standard Kogut–Singh index, which is the
mean squared difference across the six, each divided by that dimension's variance across
countries so no dimension dominates through its spread alone.</p>
<p>That is now half of this component. The other half is <strong>religious composition</strong>,
parsed from the CIA World Factbook into a coarse eight-group vector and compared by cosine
similarity, with a Catholic-share term (the meaningful tie on Lusophone and Francophone
corridors) and a shared-majority indicator. The two halves are weighted evenly.</p>
<p><strong>Hofstede's Africa coverage is the weak point.</strong> Fourteen African countries were
surveyed individually. For the rest we fall back to Hofstede's own regional clusters — Africa
East, Africa West, Arab countries — where they plausibly apply, and to nothing at all for Middle
Africa and most of Southern Africa, which have no cluster. That leaves
${fmt(t.cultural_with_hofstede)} of ${fmt(m.n_corridors)} corridors with a Hofstede basis; the
remainder fall back to religion alone rather than being marked down for a hole in someone else's
survey. The corridor card names which basis applies.</p>
<p>Religion is a weak predictor of labour-market outcomes and a poor basis for selection. It sits
here because ease of settlement affects whether a corridor sustains itself, it is exposed as a
visible component, and the whole thing can be set to zero. It is <strong>not</strong> a
recommendation to select workers on religion.</p>

<h3>3. Potential</h3>
<p>Income gain is the <strong>log of the annual increase in median income</strong> from origin to
destination, using the World Bank Poverty and Inequality Platform (2021 PPP). Median rather than
GNI per capita: GNI pc is a mean over the whole economy, dragged upward by the top of the
distribution, and overstates what a typical mover would earn on either side. The median gap
across corridors is <strong>$${fmt(t.median_income_gap)}/yr</strong>.</p>
<p>Unlike every other component here, this one is <strong>min–max scaled, not percentile
ranked</strong>, and that is deliberate. Percentile ranking is a monotone transform, so ranking
the log would have produced results identical to ranking the raw gap and the log would have been
purely decorative. Scaling preserves the compression the log exists to provide: the step from a
$2k gap to $12k counts for far more than the step from $22k to $32k. Alongside it sit labour
supply at origin and destination demand.</p>

<h3>The WHO safeguards flag</h3>
<p>${t.who_origins} of ${m.n_origins} origins are expected on the WHO health workforce support and
safeguards list for 2026, down from ${t.who_origins_2023} on the 2023 list.
<strong>${t.who_expected_removal} African origins are expected to come off it</strong> — largely
because their UHC service coverage index improved. The flag never enters any score; it is context
on how to recruit, shown as one line on the corridor card and a small marker in the tables.</p>
<p><strong>Status of the 2026 edition.</strong> WHO set out the 2026 list at a Member States
information session on 4 March 2026: 36 countries against 55 in 2023, with the African region
falling from 37 to 27. Those countries <em>meet the technical criteria</em>; WHO is still
consulting governments, which may request exceptions or extensions, and the final list is pending
publication. Separately, the Global Code of Practice was amended on 29 May 2026 to cover care
workers as well as clinical staff. <code>ref/who_safeguards.csv</code> carries both the 2023 and
expected-2026 status per country.</p>

<h3>Data sources</h3>
<table>
<tr><th>Source</th><th>Provides</th><th>Confidence</th></tr>
<tr><td>Eurostat <code>migr_resocc</code></td><td>Bilateral first permits by category, 2018–${m.latest_year}</td><td><span class="badge high">high</span></td></tr>
<tr><td>Eurostat <code>migr_pop1ctz</code></td><td>Resident population by citizenship (diaspora)</td><td><span class="badge high">high</span></td></tr>
<tr><td>DG HOME Schengen visa statistics ${m.visa_year}</td><td>Refusal rates and consulate presence by country of application</td><td><span class="badge high">high</span></td></tr>
<tr><td>World Bank PIP</td><td>Median annual welfare, 2021 PPP</td><td><span class="badge high">high</span></td></tr>
<tr><td>World Bank WDI + WGI</td><td>Demography, education, connectivity, governance</td><td><span class="badge high">high</span></td></tr>
<tr><td>CEPII GeoDist</td><td>Distance, contiguity, language, colonial ties</td><td><span class="badge high">high</span></td></tr>
<tr><td>WHO safeguards list ${m.who_list_version}</td><td>Health workforce flag</td><td><span class="badge high">high</span></td></tr>
<tr><td>OpenFlights</td><td>Direct air routes</td><td><span class="badge medium">medium</span></td></tr>
<tr><td>Wikidata</td><td>Diplomatic missions (closed posts filtered out)</td><td><span class="badge medium">medium</span></td></tr>
<tr><td>CIA World Factbook</td><td>Religious composition, official languages</td><td><span class="badge medium">medium</span></td></tr>
<tr><td>UNHCR Refugee Data Finder</td><td>Bilateral refugee and asylum-seeker stocks</td><td><span class="badge high">high</span></td></tr>
<tr><td>UCDP battle deaths (via World Bank)</td><td>Conflict intensity at origin</td><td><span class="badge high">high</span></td></tr>
<tr><td>Hofstede dimension matrix</td><td>Cultural distance (Kogut–Singh)</td><td><span class="badge medium">medium</span></td></tr>
<tr><td>ICMPD/ECDPM/MPF Labour Pathways</td><td>Roster of 295 EU legal labour pathways</td><td><span class="badge medium">partial</span></td></tr>
<tr><td><code>ref/agreements.csv</code></td><td>Bilateral agreements and EU instruments</td><td><span class="badge low">curated</span></td></tr>
</table>

<h3>The ICMPD/ECDPM pathway mapping — what we could and could not get</h3>
<p>The dataset the concept note builds its Step 2 cost index on. Its public tool exposes an
unauthenticated WordPress REST API, so the <strong>roster of 295 pathways</strong> — title,
description, link, last-modified — is now ingested and browsable.</p>
<p><strong>The structured attributes are not public.</strong> Country, sector, duration, education
level, quota, labour market test, shortage list, income threshold and language requirement are
Pods custom fields not registered to the REST API, and <code>/pods/v1</code> returns 401. Nor is
the country-to-pathway join exposed. We tested the "records are grouped by country in ID order"
hypothesis in both directions against language fingerprints in the slugs — <em>passeport-talent</em>
for France, <em>decreto/flussi</em> for Italy — and it fails: following-order scores 19% and leaves
Italy and Belgium with zero pathways, preceding-order scores 0%. <strong>So no country is
assigned.</strong> Inventing one would have put a fabricated policy index straight into the ease
score. The ease index therefore still rests on public substitutes, and this remains the largest
fidelity gap against the brief.</p>
<p><strong>On the count:</strong> the API returns 295. ICMPD's release says "close to 300", the MPF
practitioner note says 294, and the concept note says 354 — a figure that appears in no public
source. We record what the API actually returned.</p>
<p>What we do extract is a set of policy signals read off the free-text descriptions — contract
requirement (93 of 295), salary mention (56), highly-skilled framing (43), seasonal (31), labour
market test (23), language (11), quota (8). These are <strong>derived</strong> and are never
presented as the ICMPD attribute fields of the same name.</p>

<h3>Known limits — read before citing</h3>
<ul>
<li><strong>The ICMPD/ECDPM 354-pathway file is not public.</strong> The concept note's
pathway-level cost index cannot be reproduced; ease of unlocking uses public substitutes. This
is the largest fidelity gap against the brief.</li>
<li><strong>No occupation detail exists in the flow data.</strong> Eurostat reports permit
category, never ISCO. The sector overlay is modelled and labelled as such.</li>
<li><strong>Visa refusal data is short-stay.</strong> It measures Schengen C visas, not work
permits, and it is missing entirely for Ireland and Cyprus.</li>
<li><strong>Median income mixes bases.</strong> PIP reports consumption for most African
countries and income for most European ones. The corridor card shows which applies.</li>
<li><strong>The agreements register is hand-assembled</strong> and needs expert review. It ships
as an editable CSV with a source URL and confidence flag per row.</li>
<li><strong>Zero is not missing.</strong> Eurostat suppresses small cells; unreported cells are
dropped, never zero-filled.</li>
<li><strong>Ease and underutilisation are not independent.</strong> Corridors are easy partly
because they already have flows — embassies follow diaspora, flights follow demand — so the two
dimensions partly double-count.</li>
<li><strong>Many high-ranked corridors have very small flows</strong> (0–20/yr), where
percentage gaps on tiny bases are noisy. Use the flow filter before citing anything.</li>
<li><strong>Cross-sectional ranking, not causal.</strong> It cannot tell you what an
intervention would unlock.</li>
</ul>` }));
}

/* -------------------------------------------------------------------- CSV */
function downloadCSV() {
  const rows = sorted(filtered());
  const cols = ['orig', 'dest', 'orig_name', 'dest_name', 'orig_subregion', 'dest_subregion',
    'score', 'under', 'ease', 'potential', 'permits_latest', 'permits_mean3', 'predicted',
    'gap_log', 'median_income_gap', 'median_income_ratio', 'o_inc_median_annual_ppp',
    'd_inc_median_annual_ppp', 'visa_applications', 'visa_refusal_rate', 'visa_consulate_present',
    'consular_tier', 'dest_embassy_in_orig', 'neighbour_embassy', 'nearest_embassy_km',
    'shared_official_language', 'routes', 'diaspora', 'dist', 'n_agreements', 'agreement_types',
    'religion_similarity', 'low_hanging_fruit', 'who_safeguard_origin',
    'f_consular', 'f_visa_success', 'f_language', 'f_agreement', 'f_connectivity',
    'f_proximity', 'f_admin', 'f_cultural', 'p_income_gain', 'p_labour_supply', 'p_dest_demand'];
  const q = v => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const body = [cols.join(','), ...rows.map(r => cols.map(c =>
    q(typeof r[c] === 'number' ? Math.round(r[c] * 1000) / 1000 : r[c])).join(','))].join('\n');
  const a = el('a', {
    href: URL.createObjectURL(new Blob([body], { type: 'text/csv;charset=utf-8' })),
    download: 'africa-eu-corridors.csv',
  });
  document.body.appendChild(a); a.click(); a.remove();
}

/* ----------------------------------------------------------------- render */
function render() {
  rescore();
  const rows = filtered();
  $('#filterCount').textContent = `${fmt(rows.length)} of ${fmt(S.corridors.length)} corridors match`;
  renderStats(rows);
  const host = $('#view');
  host.innerHTML = '';
  if (S.detail) { host.appendChild(viewDetail(S.detail)); return; }
  const srt = sorted(rows);
  host.appendChild(
    S.view === 'map' ? viewMap(rows) :
      S.view === 'ranking' ? viewRanking(srt) :
        S.view === 'matrix' ? viewMatrix(rows) :
          S.view === 'sectors' ? viewSectors(srt) : viewMethod());
}

/* ------------------------------------------------------------------- boot */
async function boot() {
  const names = ['corridors', 'meta', 'series', 'sectors', 'geo'];
  const bundled = window.__CORRIDOR_DATA__;
  const [corr, meta, series, sectors, geo] = bundled
    ? names.map(n => bundled[n])
    : await Promise.all(names.map(f => fetch(`data/${f}.json`).then(r => {
        if (!r.ok) throw new Error(`${f}.json: HTTP ${r.status}`);
        return r.json();
      })));

  // Columnar payload, with string fields dictionary-encoded to integer indices.
  const n = corr.columns[corr.fields[0]].length;
  const dicts = corr.dicts || {};
  S.corridors = Array.from({ length: n }, (_, i) => {
    const o = {};
    for (const f of corr.fields) {
      const v = corr.columns[f][i];
      o[f] = dicts[f] ? (v === null || v === undefined ? null : dicts[f][v]) : v;
    }
    return o;
  });
  S.meta = meta; S.series = series; S.sectors = sectors; S.geo = geo;
  buildSidebar();
}

boot().catch(e => {
  $('#view').innerHTML = `<div class="card"><div class="pad">
    <h2>Could not load the data</h2>
    <p style="color:var(--ink-2)">${esc(e.message)}</p>
    <p style="color:var(--ink-3);font-size:13px">This page reads JSON from <code>data/</code>,
    so it must be served over HTTP rather than opened from the filesystem.
    Run <code>python -m http.server -d web 8000</code>.</p></div></div>`;
});
