'use strict';

/* Paper trading dashboard. Alleen lezen: alle gegevens komen uit data/*.json,
   elke nacht gemaakt door scripts/exporteer_dashboard.py. Nep-geld. */

const LWC = window.LightweightCharts;
const st = { meta: null, weergave: 'echt', tab: 'overzicht', cache: new Map(), grafieken: {} };

/* ------------------------------------------------------------ notatie */

const nlCache = new Map();
function nf(d) {
  if (!nlCache.has(d)) nlCache.set(d, new Intl.NumberFormat('nl-NL', { minimumFractionDigits: d, maximumFractionDigits: d }));
  return nlCache.get(d);
}
function getal(x, d = 1, teken = false) {
  if (x === null || x === undefined || Number.isNaN(x)) return '-';
  const t = nf(d).format(x);
  return teken && x > 0 ? '+' + t : t;
}
function dollar(x, d = 2) {
  if (x === null || x === undefined) return '-';
  return (x < 0 ? '-$' : '$') + nf(d).format(Math.abs(x));
}
function pct(x, d = 1, teken = true) { return x === null || x === undefined ? '-' : getal(x, d, teken) + '%'; }
function pp(x) { return x === null || x === undefined ? '-' : getal(x, 1, true) + ' pp'; }
const datumFmt = new Intl.DateTimeFormat('nl-NL', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const tijdFmt = new Intl.DateTimeFormat('nl-NL', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' });
function datum(iso) { return iso ? datumFmt.format(new Date(iso.slice(0, 10) + 'T12:00:00Z')) : '-'; }
function tijd(iso) { if (!iso) return '-'; const d = new Date(iso); return Number.isNaN(d.getTime()) ? iso : tijdFmt.format(d) + ' UTC'; }
function kleurTeken(x) { return x === null || x === undefined ? '' : x > 0 ? 'pos' : x < 0 ? 'neg' : ''; }

/* ------------------------------------------------------------ DOM-hulp (altijd textContent) */

function el(tag, props, ...kinderen) {
  const e = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k === 'style') e.setAttribute('style', v);
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    }
  }
  for (const k of kinderen.flat()) {
    if (k === null || k === undefined || k === false) continue;
    e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return e;
}
function rij(naam, waarde, cls) { return el('div', { class: 'rij' }, el('span', { text: naam }), el('span', { class: cls || '', text: waarde })); }
function cijfer(naam, waarde, cls) { return el('div', { class: 'cijfer' }, el('div', { class: 'naam', text: naam }), el('div', { class: 'waarde ' + (cls || ''), text: waarde })); }
function leeg(tekst) { return el('p', { class: 'leeg', text: tekst }); }

async function laad(pad) {
  if (st.cache.has(pad)) return st.cache.get(pad);
  const r = await fetch('data/' + pad, { cache: 'no-cache' });
  if (!r.ok) throw new Error(pad + ': ' + r.status);
  const j = await r.json();
  st.cache.set(pad, j);
  return j;
}
async function laadOfNiets(pad) { try { return await laad(pad); } catch (e) { return null; } }

/* ------------------------------------------------------------ kleuren en namen */

const VAST = {
  benchmark_spy: '#9598a1', benchmark_btc: '#ffb74d', gelijk_alle: '#e0e3eb', gelijk_ai: '#b2b5be',
  ai_analist: '#2962ff', proef_ai_mix: '#2962ff', ai_controle_haiku: '#26c6da', proef_ai_haiku: '#26c6da',
  ai_enkel: '#ab47bc', proef_ai_enkel: '#ab47bc', proef_ai_opus: '#f06292',
  momentum: '#66bb6a', mean_reversion: '#fdd835', trend: '#ff7043',
  momentum_ai: '#a5d6a7', mean_reversion_ai: '#fff59d', trend_ai: '#ffab91',
};
const REST = ['#80deea', '#ce93d8', '#bcaaa4', '#90caf9', '#c5e1a5', '#ffcc80', '#f48fb1', '#b39ddb'];
const vrijeKleur = new Map();
function kleur(naam) {
  if (VAST[naam]) return VAST[naam];
  if (!vrijeKleur.has(naam)) vrijeKleur.set(naam, REST[vrijeKleur.size % REST.length]);
  return vrijeKleur.get(naam);
}
const isMeetlat = n => ['benchmark_spy', 'benchmark_btc', 'gelijk_alle', 'gelijk_ai'].includes(n);
const NAAM = { benchmark_spy: 'SPY kopen en vasthouden', benchmark_btc: 'BTC kopen en vasthouden', gelijk_alle: 'gelijk_alle (meetlat)', gelijk_ai: 'gelijk_ai (meetlat)' };
const naam = n => NAAM[n] || n;
function vraagTekst(v) { return v === 'absoluut' ? 'absoluut: SPY en BTC zelf, tegen kas' : 'relatief: tegen SPY of BTC'; }

/* ------------------------------------------------------------ grafieken */

const THEMA = {
  autoSize: true,
  layout: { background: { type: 'solid', color: '#1e222d' }, textColor: '#b2b5be', fontSize: 11, attributionLogo: true },
  grid: { vertLines: { color: '#262a35' }, horzLines: { color: '#262a35' } },
  rightPriceScale: { borderColor: '#2a2e39' },
  timeScale: { borderColor: '#2a2e39', lockVisibleTimeRangeOnResize: true },
  localization: { locale: 'nl-NL', priceFormatter: p => getal(p, 2) },
};
function maakGrafiek(tab, houder, extra) {
  const g = LWC.createChart(houder, Object.assign({}, THEMA, extra || {}));
  (st.grafieken[tab] = st.grafieken[tab] || []).push(g);
  return g;
}
function ruimOp(tab) { for (const g of st.grafieken[tab] || []) g.remove(); st.grafieken[tab] = []; }
/* Pas de tijdas in zodra de container zijn echte breedte heeft. */
function pasIn(g) { g.timeScale().fitContent(); requestAnimationFrame(() => requestAnimationFrame(() => g.timeScale().fitContent())); }
const lijnOpties = (kleur, extra) => Object.assign({ color: kleur, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, extra || {});

/* ------------------------------------------------------------ opstarten */

async function start() {
  try { st.meta = await laad('meta.json'); }
  catch (e) { document.getElementById('inhoud').replaceChildren(leeg('Geen gegevens gevonden. Het dashboard wordt elke nacht na de ronde gebouwd.')); return; }
  const m = st.meta;
  document.getElementById('stand').textContent = 'koersen tot ' + datum(m.koersen_tot);
  document.getElementById('gemaakt').textContent = 'Bijgewerkt: ' + tijd(m.gemaakt_op);
  if (m.label) { const l = document.getElementById('label'); l.textContent = m.label; l.hidden = false; }
  const uitUrl = new URLSearchParams(location.hash.slice(1));
  st.weergave = uitUrl.get('w') === 'proef' ? 'proef' : (uitUrl.get('w') === 'echt' ? 'echt' : m.standaard);
  st.tab = uitUrl.get('t') || 'overzicht';
  for (const b of document.querySelectorAll('#weergave button')) b.addEventListener('click', () => kies(b.dataset.weergave, st.tab));
  for (const b of document.querySelectorAll('#tabs button')) b.addEventListener('click', () => kies(st.weergave, b.dataset.tab));
  kies(st.weergave, st.tab);
}

function kies(weergave, tab) {
  if (!document.querySelector(`[data-paneel="${tab}"]`)) tab = 'overzicht';
  st.weergave = weergave; st.tab = tab;
  history.replaceState(null, '', '#w=' + weergave + '&t=' + tab);
  for (const b of document.querySelectorAll('#weergave button')) b.setAttribute('aria-pressed', String(b.dataset.weergave === weergave));
  for (const b of document.querySelectorAll('#tabs button')) b.setAttribute('aria-selected', String(b.dataset.tab === tab));
  document.getElementById('proefbalk').hidden = weergave !== 'proef';
  for (const s of document.querySelectorAll('[data-paneel]')) s.hidden = s.dataset.paneel !== tab;
  teken(tab).catch(e => { console.error(e); paneel(tab).replaceChildren(leeg('Er ging iets mis bij het laden: ' + e.message)); });
}
const paneel = tab => document.querySelector(`[data-paneel="${tab}"]`);
const pad = bestand => st.weergave + '/' + bestand;

async function teken(tab) {
  ruimOp(tab);
  const p = paneel(tab);
  const w = st.meta.weergaven[st.weergave];
  if (!w || !w.beschikbaar) {
    p.replaceChildren(el('div', { class: 'paneel' }, el('h2', { text: st.weergave === 'echt' ? 'Echte meting' : 'Proefweek' }),
      el('p', { text: (w && w.melding) || 'Voor deze weergave zijn er nog geen gegevens.' }),
      st.weergave === 'echt' ? el('p', { class: 'dim', text: 'Tot dan staat alles onder "Proefweek, telt niet mee".' }) : null));
    return;
  }
  p.replaceChildren(leeg('Laden...'));
  const tekenaars = { overzicht: tekenOverzicht, kansen: tekenKansen, symbolen: tekenSymbolen, vergaderzaal: tekenVergaderzaal, weekrapport: tekenWeekrapport, kosten: tekenKosten };
  await tekenaars[tab](p);
}

/* ------------------------------------------------------------ overzicht */

async function tekenOverzicht(p) {
  const [ov, ka] = await Promise.all([laad(pad('overzicht.json')), laad(pad('kansen.json'))]);
  const delen = [];
  const h = ov.hoofdvraag;
  if (h) {
    const label = h.label;
    const cls = label === 'ja' ? 'ja' : label === 'nee' ? 'nee' : 'wacht';
    const tekstLabel = { 'nog niet te zeggen': 'Nog niet te zeggen (tussenstand)', ja: 'Ja', nee: 'Nee, geen betekenisvol voordeel', 'telt niet mee': 'Proefweek: telt niet mee' }[label] || label;
    delen.push(el('div', { class: 'paneel hoofdvraag' },
      el('div', { class: 'dim klein', text: 'Hoofdvraag, vooraf vastgelegd' }),
      el('div', { class: 'vraag', text: h.vraag }),
      el('div', { class: 'cijfers' },
        cijfer(h.ai, pct(h.ai_rendement_pct), kleurTeken(h.ai_rendement_pct)),
        cijfer(h.meetlat, pct(h.meetlat_rendement_pct), kleurTeken(h.meetlat_rendement_pct)),
        cijfer('verschil', pp(h.verschil_pp), kleurTeken(h.verschil_pp)),
        cijfer('p-waarde (blok-bootstrap)', h.p_waarde === null ? 'nog te weinig dagen' : getal(h.p_waarde, 2)),
        cijfer('einddatum ' + datum(h.einde), 'nog ' + h.dagen_tot_einde + ' dagen')),
      el('p', { style: 'margin:10px 0 0' }, el('span', { class: 'pil ' + cls, text: tekstLabel })),
      el('p', { class: 'dim klein', style: 'margin:6px 0 0', text: 'Tot de einddatum blijft dit "nog niet te zeggen", ook bij een lage p-waarde: elke dag kijken en bij het eerste mooie getal stoppen maakt de toets waardeloos. Daarna "ja" bij een hoger rendement en een p-waarde van 0,05 of lager, anders "nee".' })));
  }

  if (ov.ai_vandaag && ov.ai_vandaag.length) {
    const d = ov.ai_vandaag[0].datum;
    delen.push(el('div', { class: 'paneel' },
      el('h3', { text: 'AI vandaag (' + datum(d) + ')' }),
      el('div', { class: 'cijfers' }, ov.ai_vandaag.map(a =>
        el('div', { class: 'cijfer' }, el('div', { class: 'naam', text: a.portefeuille }),
          el('div', { style: 'font-size:13px', text: a.beoordeeld + ' beoordeeld, ' + a.kopen + ' kopen, ' + a.verkopen + ' verkopen' }))))));
  }

  const hoofdAi = h ? h.ai : null;
  const groepen = (ka.groepen || []).filter(g => g.portefeuille === hoofdAi);
  if (groepen.length) {
    delen.push(el('div', { class: 'paneel' },
      el('h3', { text: 'Klopten de kansen van ' + hoofdAi + '?' }),
      el('div', { class: 'cijfers' }, groepen.map(g => cijfer(vraagTekst(g.vraag),
        g.n ? 'Brier ' + getal(g.brier, 3) + ' (' + g.n + ')' : 'eerste score ' + datum(g.eerste_score)))),
      el('p', { class: 'dim klein', style: 'margin:6px 0 0', text: 'Lager is beter; altijd 0,5 zeggen geeft 0,250. Meer op het tabblad Kansen.' })));
  }

  const pfs = ov.portefeuilles || [];
  const grafiekPaneel = el('div', { class: 'paneel' }, el('h3', { text: 'Waarde per portefeuille, in dollars' }));
  const houder = el('div', { class: 'grafiek grafiek-groot' });
  const chips = el('div', { class: 'chips' });
  grafiekPaneel.append(houder, chips,
    el('p', { class: 'dim klein', style: 'margin:6px 0 0', text: 'Gestippeld: de meetlatten. Klik op een naam om de lijn aan of uit te zetten. Gemeten op de dagen dat de Amerikaanse beurs open was.' }));
  delen.push(grafiekPaneel);

  const kaarten = el('div', { class: 'rooster' });
  for (const pf of pfs) {
    kaarten.append(el('div', { class: 'paneel' },
      el('div', { class: 'kaart-kop' }, el('span', { class: 'stip', style: 'background:' + kleur(pf.naam) }),
        el('strong', { text: naam(pf.naam) }), el('span', { class: 'dim klein', text: { meetlat: 'meetlat', ai: 'AI', regel: 'regels' }[pf.soort] })),
      rij('waarde', dollar(pf.waarde)),
      rij('rendement', pct(pf.rendement_pct, 2), kleurTeken(pf.rendement_pct)),
      pf.meetlat ? rij('tegen ' + pf.meetlat, pp(pf.vs_meetlat_pp), kleurTeken(pf.vs_meetlat_pp)) : rij('tegen meetlat', 'is zelf een meetlat'),
      rij('gemiddeld belegd', pct(pf.belegd_pct, 0, false)),
      rij('grootste daling', pct(pf.max_daling_pct, 1, false)),
      rij('trades', String(pf.trades) + (pf.afgerond ? ' (' + pf.afgerond + ' afgerond, ' + pct(pf.win_pct, 0, false) + ' winst)' : ''))));
  }
  delen.push(kaarten);
  p.replaceChildren(...delen);

  if (!pfs.length) { houder.replaceWith(leeg('Nog geen standen.')); return; }
  const g = maakGrafiek('overzicht', houder, { localization: { locale: 'nl-NL', priceFormatter: x => dollar(x, 0) } });
  for (const pf of pfs) {
    const meetlat = isMeetlat(pf.naam);
    const s = g.addSeries(LWC.LineSeries, lijnOpties(kleur(pf.naam), {
      lineWidth: pf.soort === 'ai' ? 2 : meetlat ? 2 : 1,
      lineStyle: meetlat ? LWC.LineStyle.Dashed : LWC.LineStyle.Solid,
      lastValueVisible: true, title: '',
    }));
    s.setData(pf.reeks.filter(r => r[1] !== null).map(r => ({ time: r[0], value: r[1] })));
    const standaardAan = !pf.naam.endsWith('_ai') && !/_[AB]\d+$/.test(pf.naam);
    s.applyOptions({ visible: standaardAan });
    const chip = el('button', { type: 'button', class: 'chip' + (standaardAan ? '' : ' uit'), 'aria-pressed': String(standaardAan) },
      el('span', { class: 'stip', style: 'background:' + kleur(pf.naam) + (meetlat ? ';height:2px;border-top:1px dashed ' + kleur(pf.naam) : '') }), naam(pf.naam));
    chip.addEventListener('click', () => {
      const aan = chip.getAttribute('aria-pressed') !== 'true';
      s.applyOptions({ visible: aan }); chip.setAttribute('aria-pressed', String(aan)); chip.classList.toggle('uit', !aan);
    });
    chips.append(chip);
  }
  pasIn(g);
}

/* ------------------------------------------------------------ kansen */

function kalibratieSvg(vakken) {
  const ns = 'http://www.w3.org/2000/svg';
  const W = 260, H = 200, m = 28;
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('width', '100%'); svg.setAttribute('style', 'max-width:340px');
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', 'Kalibratie: gezegde kans tegen hoe vaak het uitkwam');
  const x = v => m + v * (W - m - 8), y = v => H - m - v * (H - m - 8);
  const add = (tag, attrs, tekst) => { const e = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (tekst) e.textContent = tekst; svg.append(e); return e; };
  add('rect', { x: m, y: 8, width: W - m - 8, height: H - m - 8, fill: '#252a37' });
  add('line', { x1: x(0), y1: y(0), x2: x(1), y2: y(1), stroke: '#555a66', 'stroke-dasharray': '4 3' });
  for (const t of [0, 0.5, 1]) {
    add('text', { x: x(t), y: H - 10, fill: '#868993', 'font-size': 10, 'text-anchor': 'middle' }, getal(t, 1));
    add('text', { x: m - 4, y: y(t) + 3, fill: '#868993', 'font-size': 10, 'text-anchor': 'end' }, getal(t * 100, 0) + '%');
  }
  add('text', { x: W / 2, y: H - 0.5, fill: '#868993', 'font-size': 10, 'text-anchor': 'middle' }, 'gezegde kans');
  for (const v of vakken) {
    add('circle', { cx: x(v.gem_kans), cy: y(v.kwam_uit_pct / 100), r: Math.max(3, Math.min(9, Math.sqrt(v.n))), fill: v.weinig ? '#555a66' : '#2962ff', 'fill-opacity': 0.85 });
  }
  return svg;
}

async function tekenKansen(p) {
  const ka = await laad(pad('kansen.json'));
  const delen = [el('div', { class: 'paneel' },
    el('h2', { text: 'Klopten de kansen?' }),
    el('p', { text: 'Elke beursdag geeft de AI per symbool een kans. Na ' + ka.horizon_dagen + ' dagen kijkt de bot wat er echt gebeurde: kwam het uit, na ' + getal(ka.kosten_pct, 1) + '% kosten? De Brier-score is het gemiddelde van (kans min uitkomst) in het kwadraat. Lager is beter; altijd 0,5 zeggen geeft ' + getal(ka.brier_geen_idee, 3) + '.' }),
    el('p', { text: 'Kalibratie: zegt de AI honderd keer 0,6, dan hoort het ongeveer zestig keer uit te komen. Punten op de stippellijn zijn goed gekalibreerd. Grijze punten hebben minder dan 10 kansen en zeggen nog weinig.' }),
    el('p', { class: 'dim', text: 'De twee soorten vraag staan apart, want het zijn verschillende vragen: relatief (beter dan SPY of BTC) en absoluut (SPY en BTC zelf: stijgen ze meer dan de kosten?).' }))];
  if (!ka.groepen.length) delen.push(leeg('Nog geen kansen van de AI.'));
  const perPf = new Map();
  for (const g of ka.groepen) { if (!perPf.has(g.portefeuille)) perPf.set(g.portefeuille, []); perPf.get(g.portefeuille).push(g); }
  for (const [pf, groepen] of perPf) {
    const blok = el('div', { class: 'paneel' }, el('div', { class: 'kaart-kop' }, el('span', { class: 'stip', style: 'background:' + kleur(pf) }), el('h3', { style: 'margin:0', text: pf })));
    const rooster = el('div', { class: 'rooster' });
    for (const g of groepen) {
      const kaart = el('div', { class: 'paneel', style: 'background:#1a1e28' }, el('span', { class: 'pil ' + (g.vraag === 'absoluut' ? 'paars' : 'blauw'), text: vraagTekst(g.vraag) }));
      if (!g.n) {
        kaart.append(el('p', { style: 'margin-top:8px', text: 'Nog niets gescoord. ' + g.open + ' kansen wachten; de eerste score komt in de ronde van ' + datum(g.eerste_score) + '.' }));
      } else {
        const breedte = Math.max(0, Math.min(100, g.brier / 0.5 * 100));
        kaart.append(
          el('div', { class: 'cijfers', style: 'margin-top:8px' }, cijfer('Brier', getal(g.brier, 3), g.brier < ka.brier_geen_idee ? 'pos' : 'neg'), cijfer('gescoord', String(g.n)), cijfer('gemiddelde kans', getal(g.gem_kans, 2)), cijfer('kwam uit', pct(g.kwam_uit_pct, 0, false))),
          el('div', { class: 'balk', title: 'Brier tegen 0,25' }, el('div', { style: 'width:' + breedte + '%' })),
          el('div', { class: 'dim klein', text: 'de streep tot halverwege is 0,250 (geen idee)' }),
          kalibratieSvg(g.vakken),
          el('div', { class: 'schuif' }, el('table', { class: 'tabel' },
            el('thead', null, el('tr', null, el('th', { text: 'kans' }), el('th', { class: 'getal', text: 'aantal' }), el('th', { class: 'getal', text: 'gemiddeld' }), el('th', { class: 'getal', text: 'kwam uit' }))),
            el('tbody', null, g.vakken.map(v => el('tr', { class: v.weinig ? 'dim' : '' },
              el('td', { text: getal(v.van, 2) + ' tot ' + getal(v.tot, 2) }), el('td', { class: 'getal', text: String(v.n) }),
              el('td', { class: 'getal', text: getal(v.gem_kans, 2) }), el('td', { class: 'getal', text: pct(v.kwam_uit_pct, 0, false) })))))));
        if (g.open) kaart.append(el('p', { class: 'dim klein', style: 'margin-top:6px', text: g.open + ' kansen wachten nog op hun score.' }));
      }
      rooster.append(kaart);
    }
    blok.append(rooster);
    delen.push(blok);
  }
  p.replaceChildren(...delen);
}

/* ------------------------------------------------------------ symbolen */

function uitlegTeksten(m) {
  const mr = m.mean_reversion, tr = m.trend, br = m.beslisregel;
  return {
    kaars: 'Elke kaars is een dag. Het dikke deel loopt van de openingskoers tot de slotkoers: groen als de dag hoger sloot, rood als lager. De dunne streepjes tonen het hoogste en laagste punt van de dag.',
    volume: 'Hoeveel er die dag verhandeld werd. De regelstrategieën gebruiken het niet; de AI krijgt te zien of het volume hoger of lager is dan normaal.',
    ma50: 'Het gemiddelde van de laatste ' + tr.snelle_ma + ' slotkoersen. Staat deze lijn boven de ' + tr.langzame_ma + '-daagse, dan mag de trend-strategie dit kopen en houdt hij het vast. Zakt hij eronder, dan verkoopt hij.',
    ma200: 'Het gemiddelde van de laatste ' + tr.langzame_ma + ' slotkoersen: de lange trend. Mean reversion koopt alleen als de koers boven deze lijn staat (een dip in een stijgende markt). De trend-strategie vergelijkt er de ' + tr.snelle_ma + '-daagse mee.',
    ma5: 'Het gemiddelde van de laatste ' + mr.verkoop_boven_ma + ' slotkoersen. Mean reversion verkoopt zodra de koers hierboven sluit: de dip is dan voorbij.',
    bb: 'Bollinger-banden: het 20-daags gemiddelde met daaromheen twee keer de gewone schommeling. Ze laten zien hoe ver de koers normaal beweegt. De bot gebruikt deze lijnen niet; alleen ter informatie.',
    rsi: 'RSI over ' + mr.rsi_periode + ' dagen: hoe hard de koers de laatste dagen daalde of steeg, van 0 tot 100. Onder ' + getal(mr.koop_onder, 0) + ' is een felle dip; mean reversion koopt dan, als de koers boven het ' + mr.trendfilter_ma + '-daags gemiddelde staat.',
    ai: 'De kans die de AI die dag gaf: doet dit symbool het de komende 14 dagen beter dan zijn referentie (bij SPY en BTC: stijgt het meer dan de kosten)? Bij ' + getal(br.koop_kans, 2) + ' of meer, en een verwachting van ' + getal(br.koop_verwacht_pct, 1) + '% of meer, koopt de AI. Onder ' + getal(br.verkoop_kans_onder, 2) + ' verkoopt hij.',
    trades: 'Pijltje omhoog onder de kaars: gekocht op de opening van die dag. Pijltje omlaag erboven: verkocht. Elke portefeuille heeft een eigen kleur. Klik op een pijltje (of op een trade in de lijst eronder) voor de reden.',
  };
}

const LIJNEN = [
  { id: 'volume', naam: 'Volume', aan: true },
  { id: 'ma50', naam: 'MA50', aan: true, kleur: '#2962ff' },
  { id: 'ma200', naam: 'MA200', aan: true, kleur: '#ff9800' },
  { id: 'ma5', naam: 'MA5', aan: true, kleur: '#e040fb' },
  { id: 'bb', naam: 'Bollinger', aan: false, kleur: '#4dd0e1' },
  { id: 'rsi', naam: 'RSI(2)', aan: true, kleur: '#b39ddb' },
  { id: 'ai', naam: 'AI-kans', aan: true },
  { id: 'trades', naam: 'Koop en verkoop', aan: true },
];
const symboolStand = { symbool: null, aan: Object.fromEntries(LIJNEN.map(l => [l.id, l.aan])), pfUit: new Set() };

async function tekenSymbolen(p) {
  const lijst = await laad('symbolen.json');
  if (!lijst.length) { p.replaceChildren(leeg('Geen koersen gevonden.')); return; }
  if (!symboolStand.symbool || !lijst.find(s => s.symbool === symboolStand.symbool)) symboolStand.symbool = lijst[0].symbool;
  const uitleg = uitlegTeksten(st.meta);

  const keuze = el('select', { 'aria-label': 'Symbool' });
  for (const [soort, titel] of [['aandeel', 'Aandelen'], ['etf', 'Indexfondsen'], ['crypto', 'Crypto']]) {
    const groep = el('optgroup', { label: titel });
    for (const s of lijst.filter(x => x.soort === soort)) groep.append(el('option', { value: s.symbool, text: s.symbool, selected: s.symbool === symboolStand.symbool }));
    if (groep.children.length) keuze.append(groep);
  }
  keuze.addEventListener('change', () => { symboolStand.symbool = keuze.value; tekenGrafiek(); });

  const uitlegVak = el('div', { class: 'uitleg', hidden: true });
  const toonUitleg = (id, knop) => {
    const zelfde = !uitlegVak.hidden && uitlegVak.dataset.id === id;
    uitlegVak.hidden = zelfde; uitlegVak.dataset.id = id; uitlegVak.textContent = uitleg[id];
    for (const k of document.querySelectorAll('.vraagteken')) k.setAttribute('aria-expanded', 'false');
    if (!zelfde) knop.setAttribute('aria-expanded', 'true');
  };
  const chips = el('div', { class: 'chips' });
  const kaarsKnop = el('button', { type: 'button', class: 'vraagteken', 'aria-label': 'Uitleg bij de kaarsen', 'aria-expanded': 'false', text: '?' });
  kaarsKnop.addEventListener('click', () => toonUitleg('kaars', kaarsKnop));
  chips.append(el('span', { class: 'chip', style: 'border:1px solid #363a45' }, 'Kaarsen', kaarsKnop));
  for (const l of LIJNEN) {
    const chip = el('button', { type: 'button', class: 'chip' + (symboolStand.aan[l.id] ? '' : ' uit'), 'aria-pressed': String(symboolStand.aan[l.id]) },
      l.kleur ? el('span', { class: 'stip', style: 'background:' + l.kleur }) : null, l.naam);
    chip.addEventListener('click', () => { symboolStand.aan[l.id] = !symboolStand.aan[l.id]; chip.setAttribute('aria-pressed', String(symboolStand.aan[l.id])); chip.classList.toggle('uit', !symboolStand.aan[l.id]); tekenGrafiek(); });
    const vraag = el('button', { type: 'button', class: 'vraagteken', 'aria-label': 'Uitleg bij ' + l.naam, 'aria-expanded': 'false', text: '?' });
    vraag.addEventListener('click', () => toonUitleg(l.id, vraag));
    chips.append(el('span', { style: 'display:inline-flex;gap:3px;align-items:center' }, chip, vraag));
  }

  const lezen = el('details', { class: 'paneel' }, el('summary', { text: 'Zo lees je deze grafiek' }),
    el('p', { text: 'Bovenaan de koers per dag als kaarsen, met de lijnen die de bot echt gebruikt. Daaronder RSI(2), de dip-meter van mean reversion, en de kans die de AI elke dag gaf. Pijltjes tonen waar een portefeuille kocht of verkocht.' }),
    el('p', { text: 'Momentum heeft geen eigen lijn: die strategie kijkt naar het rendement over 90 kalenderdagen, gedeeld door hoe wild de koers beweegt, en koopt de 10 sterkste van alle symbolen.' }),
    el('p', { text: 'Klik op een "?" voor uitleg bij een lijn. Alleen dagkoersen: de bot beslist eens per dag, na de slotkoers, en handelt de volgende dag op de opening.' }));

  const houder = el('div', { class: 'grafiek grafiek-symbool' });
  const legenda = el('div', { class: 'chips' });
  const info = el('div', { class: 'trade-info' });
  const tradesLijst = el('div', { class: 'schuif' });
  p.replaceChildren(lezen,
    el('div', { class: 'paneel' }, el('div', { class: 'filters' }, el('label', null, 'Symbool', keuze)), chips, uitlegVak),
    el('div', { class: 'paneel' }, houder, legenda, info),
    el('div', { class: 'paneel' }, el('h3', { text: 'Trades in deze periode' }), tradesLijst));

  async function tekenGrafiek() {
    ruimOp('symbolen');
    info.replaceChildren(); legenda.replaceChildren(); tradesLijst.replaceChildren();
    const s = lijst.find(x => x.symbool === symboolStand.symbool);
    const [k, extra] = await Promise.all([laad('koersen/' + s.bestand), laadOfNiets(pad('symbolen/' + s.bestand))]);
    const kol = Object.fromEntries(k.kolommen.map((n, i) => [n, i]));
    const rijen = k.rijen;
    const g = maakGrafiek('symbolen', houder);
    const kaarsen = g.addSeries(LWC.CandlestickSeries, { upColor: '#26a69a', downColor: '#ef5350', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350', priceLineVisible: false }, 0);
    kaarsen.setData(rijen.map(r => ({ time: r[kol.datum], open: r[kol.open], high: r[kol.hoog], low: r[kol.laag], close: r[kol.slot] })));
    const lijn = (sleutel, kleur, extraOpties, pane = 0) => {
      const se = g.addSeries(LWC.LineSeries, lijnOpties(kleur, extraOpties), pane);
      se.setData(rijen.filter(r => r[kol[sleutel]] !== null).map(r => ({ time: r[kol.datum], value: r[kol[sleutel]] })));
      return se;
    };
    if (symboolStand.aan.volume) {
      const vol = g.addSeries(LWC.HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: 'volume', priceLineVisible: false, lastValueVisible: false }, 0);
      vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(rijen.filter(r => r[kol.volume] !== null).map(r => ({ time: r[kol.datum], value: r[kol.volume], color: r[kol.slot] >= r[kol.open] ? 'rgba(38,166,154,.35)' : 'rgba(239,83,80,.35)' })));
    }
    if (symboolStand.aan.bb) { for (const b of ['bb_boven', 'bb_midden', 'bb_onder']) lijn(b, '#4dd0e1', { lineStyle: b === 'bb_midden' ? LWC.LineStyle.Dotted : LWC.LineStyle.Dashed }); }
    if (symboolStand.aan.ma200) lijn('ma200', '#ff9800', { lineWidth: 2 });
    if (symboolStand.aan.ma50) lijn('ma50', '#2962ff', { lineWidth: 2 });
    if (symboolStand.aan.ma5) lijn('ma5', '#e040fb');

    let pane = 1;
    if (symboolStand.aan.rsi) {
      const rsi = lijn('rsi2', '#b39ddb', { autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) }, pane);
      rsi.createPriceLine({ price: st.meta.mean_reversion.koop_onder, color: '#ef5350', lineWidth: 1, lineStyle: LWC.LineStyle.Dashed, axisLabelVisible: true, title: 'koopgrens ' + getal(st.meta.mean_reversion.koop_onder, 0) });
      g.panes()[pane].setHeight(110);
      pane += 1;
    }
    const kansen = (extra && extra.kansen) || {};
    if (symboolStand.aan.ai && Object.keys(kansen).length) {
      let eerste = null;
      for (const [pf, reeks] of Object.entries(kansen)) {
        const se = g.addSeries(LWC.LineSeries, lijnOpties(kleur(pf), { lineWidth: 2, pointMarkersVisible: true, autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 1 } }) }), pane);
        se.setData(reeks.map(r => ({ time: r[0], value: r[1] })));
        eerste = eerste || se;
        legenda.append(el('span', { class: 'chip', style: 'border:1px solid #363a45' }, el('span', { class: 'stip', style: 'background:' + kleur(pf) }), 'AI-kans ' + pf));
      }
      eerste.createPriceLine({ price: st.meta.beslisregel.koop_kans, color: '#26a69a', lineWidth: 1, lineStyle: LWC.LineStyle.Dashed, axisLabelVisible: true, title: 'koop ' + getal(st.meta.beslisregel.koop_kans, 2) });
      eerste.createPriceLine({ price: st.meta.beslisregel.verkoop_kans_onder, color: '#ef5350', lineWidth: 1, lineStyle: LWC.LineStyle.Dotted, axisLabelVisible: true, title: 'verkoop onder ' + getal(st.meta.beslisregel.verkoop_kans_onder, 2) });
      g.panes()[pane].setHeight(110);
    } else if (symboolStand.aan.ai) {
      legenda.append(el('span', { class: 'dim klein', text: 'Nog geen AI-kansen voor ' + s.symbool + ' in deze weergave.' }));
    }

    const trades = (extra && extra.trades) || [];
    const datums = new Set(rijen.map(r => r[kol.datum]));
    const pfs = [...new Set(trades.map(t => t.portefeuille))];
    for (const pf of pfs) {
      const uit = symboolStand.pfUit.has(pf);
      const chip = el('button', { type: 'button', class: 'chip' + (uit ? ' uit' : ''), 'aria-pressed': String(!uit) }, el('span', { class: 'stip', style: 'background:' + kleur(pf) }), pf);
      chip.addEventListener('click', () => { if (symboolStand.pfUit.has(pf)) symboolStand.pfUit.delete(pf); else symboolStand.pfUit.add(pf); tekenGrafiek(); });
      legenda.append(chip);
    }
    const toonTrade = t => {
      info.replaceChildren(el('div', { class: 'uitleg' },
        el('div', { class: 'kaart-kop' }, el('span', { class: 'stip', style: 'background:' + kleur(t.portefeuille) }),
          el('strong', { text: (t.actie === 'KOOP' ? 'Koop' : 'Verkoop') + ' ' + s.symbool + ' door ' + t.portefeuille })),
        rij('uitgevoerd', datum(t.datum) + ' op de opening'), rij('besloten', datum(t.besluit_datum)),
        rij('prijs', dollar(t.prijs, 2)), rij('bedrag', dollar(t.bedrag)),
        t.actie === 'VERKOOP' ? rij('resultaat', dollar(t.resultaat) + ' (' + pct(t.resultaat_pct, 1) + ')', kleurTeken(t.resultaat)) : null,
        el('p', { style: 'margin:6px 0 0', text: t.reden || 'geen reden opgeslagen' })));
    };
    if (symboolStand.aan.trades) {
      const markers = [];
      trades.forEach((t, i) => {
        if (symboolStand.pfUit.has(t.portefeuille) || !datums.has(t.datum)) return;
        markers.push({ id: 't' + i, time: t.datum, position: t.actie === 'KOOP' ? 'belowBar' : 'aboveBar', shape: t.actie === 'KOOP' ? 'arrowUp' : 'arrowDown', color: kleur(t.portefeuille), size: 1.2 });
      });
      markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
      LWC.createSeriesMarkers(kaarsen, markers);
      g.subscribeClick(param => {
        const id = param.hoveredObjectId;
        if (typeof id === 'string' && id.startsWith('t')) toonTrade(trades[Number(id.slice(1))]);
      });
    }
    if (trades.length) {
      tradesLijst.append(el('table', { class: 'tabel' },
        el('thead', null, el('tr', null, el('th', { text: 'datum' }), el('th', { text: 'portefeuille' }), el('th', { text: 'actie' }), el('th', { class: 'getal', text: 'prijs' }), el('th', { class: 'getal', text: 'resultaat' }))),
        el('tbody', null, trades.slice().reverse().map(t => {
          const r = el('tr', { style: 'cursor:pointer', tabindex: '0' },
            el('td', { text: datum(t.datum) }), el('td', null, el('span', { class: 'stip', style: 'display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:' + kleur(t.portefeuille) }), t.portefeuille),
            el('td', { text: t.actie === 'KOOP' ? 'koop' : 'verkoop' }), el('td', { class: 'getal', text: dollar(t.prijs, 2) }),
            el('td', { class: 'getal ' + kleurTeken(t.resultaat), text: t.actie === 'VERKOOP' ? dollar(t.resultaat) : '' }));
          r.addEventListener('click', () => { toonTrade(t); info.scrollIntoView({ block: 'nearest' }); });
          r.addEventListener('keydown', e => { if (e.key === 'Enter') toonTrade(t); });
          return r;
        }))));
    } else {
      tradesLijst.append(leeg('Geen trades in ' + s.symbool + ' in deze periode.'));
    }
    pasIn(g);
  }
  await tekenGrafiek();
}

/* ------------------------------------------------------------ vergaderzaal */

const zaalStand = { datum: null, symbool: '', portefeuille: '', actie: '', aantal: 40 };

function stapBlok(titel, regels) {
  const vol = regels.filter(r => r && r[1] !== undefined && r[1] !== null && r[1] !== '');
  if (!vol.length) return null;
  return el('div', { class: 'stap' }, el('h4', { text: titel }), vol.map(([n, w]) => el('p', { style: 'margin:0 0 4px' }, n ? el('span', { class: 'dim', text: n + ': ' }) : null, String(w))));
}

async function tekenVergaderzaal(p) {
  const idx = await laad(pad('vergaderzaal.json'));
  if (!idx.dagen.length) { p.replaceChildren(el('div', { class: 'paneel' }, el('h2', { text: 'Vergaderzaal' }), leeg('Nog geen AI-beslissingen in deze weergave.'))); return; }
  if (!zaalStand.datum || !idx.dagen.includes(zaalStand.datum)) zaalStand.datum = idx.dagen[0];
  const select = (label, waarde, opties, bijKeuze) => {
    const s = el('select', null, opties.map(([v, t]) => el('option', { value: v, text: t, selected: v === waarde })));
    s.addEventListener('change', () => { bijKeuze(s.value); zaalStand.aantal = 40; vul(); });
    return el('label', null, label, s);
  };
  const lijst = el('div');
  p.replaceChildren(
    el('div', { class: 'paneel' }, el('h2', { text: 'Vergaderzaal' }),
      el('p', { class: 'dim', text: 'Per beslissing wat elke rol zei, de kans, de verwachting, de soort vraag en het besluit. Ook als er niets gebeurde: die kansen tellen evengoed mee in de meting.' }),
      el('div', { class: 'filters' },
        select('Datum', zaalStand.datum, idx.dagen.map(d => [d, datum(d)]), v => { zaalStand.datum = v; }),
        select('Symbool', zaalStand.symbool, [['', 'alle']].concat(idx.symbolen.map(s => [s, s])), v => { zaalStand.symbool = v; }),
        select('Portefeuille', zaalStand.portefeuille, [['', 'alle']].concat(idx.portefeuilles.map(s => [s, s])), v => { zaalStand.portefeuille = v; }),
        select('Besluit', zaalStand.actie, [['', 'alle'], ['KOOP', 'kopen'], ['VERKOOP', 'verkopen'], ['NIETS', 'niets doen']], v => { zaalStand.actie = v; }))),
    lijst);

  async function vul() {
    lijst.replaceChildren(leeg('Laden...'));
    const items = (await laad(pad('vergaderzaal/' + zaalStand.datum + '.json'))).filter(i =>
      (!zaalStand.symbool || i.symbool === zaalStand.symbool) && (!zaalStand.portefeuille || i.portefeuille === zaalStand.portefeuille) && (!zaalStand.actie || i.actie === zaalStand.actie));
    if (!items.length) { lijst.replaceChildren(leeg('Geen beslissingen met deze filters.')); return; }
    const kaarten = items.slice(0, zaalStand.aantal).map(i => {
      const s = i.stappen || {};
      const besluit = { KOOP: 'kopen', VERKOOP: 'verkopen', NIETS: 'niets doen' }[i.actie] || i.actie;
      const n = s.nieuws || {}, bu = s.bull || {}, be = s.bear || {}, ri = s.risk || s.enkel || {};
      const kenmerken = s.kenmerken ? el('details', null, el('summary', { text: 'kengetallen (uitgerekend door de code)' }),
        el('table', { class: 'tabel' }, el('tbody', null, Object.entries(s.kenmerken).filter(([k]) => k !== 'symbool').map(([k, v]) =>
          el('tr', null, el('td', { class: 'dim', text: k.replace(/_/g, ' ') }), el('td', { class: 'getal', text: typeof v === 'number' ? getal(v, Math.abs(v) < 10 ? 2 : 1) : (v === null ? '-' : String(v)) })))))) : null;
      return el('div', { class: 'paneel' },
        el('div', { class: 'kaart-kop' }, el('strong', { text: i.symbool }), el('span', { class: 'stip', style: 'background:' + kleur(i.portefeuille) }), el('span', { text: i.portefeuille }),
          el('span', { class: 'pil ' + (i.vraag === 'absoluut' ? 'paars' : 'blauw'), text: i.vraag === 'absoluut' ? 'absoluut, tegen kas' : 'relatief, tegen ' + (i.referentie || '') }),
          el('span', { class: 'pil ' + (i.actie === 'KOOP' ? 'ja' : i.actie === 'VERKOOP' ? 'wacht' : 'nee'), text: besluit })),
        el('div', { class: 'cijfers' }, cijfer('kans', getal(i.kans, 2)), cijfer('verwacht extra rendement', pct(i.verwacht_pct, 1)), cijfer('kosten', dollar(i.kosten_usd, 4)),
          i.uitkomst_pct !== null ? cijfer('wat er gebeurde', pct(i.uitkomst_pct, 1) + (i.geraakt ? ', uitgekomen' : ', niet uitgekomen'), kleurTeken(i.uitkomst_pct)) : null),
        i.onderbouwing ? el('p', { style: 'margin:8px 0 0', text: i.onderbouwing }) : null,
        i.probleem ? el('p', { class: 'neg klein', style: 'margin:6px 0 0', text: 'Geen bruikbare kans: ' + i.probleem }) : null,
        el('details', { style: 'margin-top:8px' }, el('summary', { text: 'wat elke rol zei, en ' + i.koppen.length + ' nieuwskoppen' }),
          el('div', { class: 'stappen' },
            stapBlok('Nieuwsanalist', [['', n.samenvatting], ['risico', n.belangrijkste_risico], ['gebeurtenis binnenkort', n.gebeurtenis_binnenkort === undefined ? null : (n.gebeurtenis_binnenkort ? 'ja' : 'nee')]]),
            stapBlok('Bull (voor)', [['', bu.argument], ['sterkste punt', bu.sterkste_punt], ['overtuiging', bu.overtuiging === undefined ? null : getal(bu.overtuiging, 2)]]),
            stapBlok('Bear (tegen)', [['', be.argument], ['sterkste punt', be.sterkste_punt], ['overtuiging', be.overtuiging === undefined ? null : getal(be.overtuiging, 2)]]),
            stapBlok(s.enkel ? 'Analist (alles in een keer)' : 'Risk manager', [['', ri.onderbouwing], ['belangrijkste risico', ri.belangrijkste_risico]])),
          kenmerken,
          i.koppen.length ? el('ul', { class: 'koppen' }, i.koppen.map(k => el('li', null, el('time', { text: tijd(k.gepubliceerd) }),
            k.url && /^https?:\/\//.test(k.url) ? el('a', { href: k.url, rel: 'noopener noreferrer', target: '_blank', text: k.titel || '(zonder titel)' }) : (k.titel || '(zonder titel)'),
            k.bron ? el('span', { class: 'dim', text: ' · ' + k.bron }) : null))) : el('p', { class: 'dim', text: 'Geen nieuwskoppen gezien (alleen koppen van voor het moment van uitvoeren tellen mee).' })));
    });
    lijst.replaceChildren(...kaarten);
    if (items.length > zaalStand.aantal) {
      const meer = el('button', { type: 'button', text: 'Toon meer (' + (items.length - zaalStand.aantal) + ' over)' });
      meer.addEventListener('click', () => { zaalStand.aantal += 40; vul(); });
      lijst.append(meer);
    }
  }
  await vul();
}

/* ------------------------------------------------------------ weekrapport */

async function tekenWeekrapport(p) {
  const w = await laad(pad('weekrapport.json'));
  const delen = [];
  if (!w.rapporten.length) {
    delen.push(el('div', { class: 'paneel' }, el('h2', { text: 'Weekrapport' }), leeg('Nog geen weekrapport. Het komt elke zondagnacht, in de ronde van 00:30 UTC.')));
  } else {
    const r = w.rapporten[0];
    delen.push(el('div', { class: 'paneel' }, el('h2', { text: 'Weekrapport tot en met ' + datum(r.datum) }),
      r.conclusies.length ? el('div', null, el('h3', { text: 'Drie conclusies van Claude' }), el('ol', null, r.conclusies.map(c => el('li', { text: c })))) : el('p', { class: 'dim', text: 'Geen conclusies van Claude bij dit rapport.' }),
      el('details', null, el('summary', { text: 'het hele rapport' }), el('pre', { class: 'rapport', text: r.tekst }))));
    if (w.rapporten.length > 1) {
      delen.push(el('div', { class: 'paneel' }, el('h3', { text: 'Eerdere weekrapporten' }),
        w.rapporten.slice(1).map(x => el('details', null, el('summary', { text: datum(x.datum) }), el('pre', { class: 'rapport', text: x.tekst })))));
    }
  }
  const status = { loopt: ['loopt', 'blauw'], wacht_op_akkoord: ['wacht op jouw akkoord', 'wacht'], aangenomen: ['aangenomen: eigen portefeuille', 'ja'], afgewezen: ['gestopt', 'nee'] };
  const ch = el('div', { class: 'paneel' }, el('h2', { text: 'Challengers' }),
    el('p', { class: 'dim', text: 'Een voorstel past nooit een lopende portefeuille aan. Na een backtest draait het als eerlijk paar (A huidig, B voorstel); na jouw /akkoord gaat B verder als eigen portefeuille.' }));
  if (!w.challengers.length) ch.append(leeg('Nog geen challengers.'));
  for (const c of w.challengers) {
    const [tekst, cls] = status[c.status] || [c.status, 'nee'];
    ch.append(el('div', { class: 'paneel', style: 'background:#1a1e28' },
      el('div', { class: 'kaart-kop' }, el('strong', { text: 'Challenger ' + c.id }), el('span', { class: 'pil ' + cls, text: tekst })),
      rij('voorstel', c.strategie + '.' + c.parameter + ': ' + getal(c.oude_waarde, 0) + ' naar ' + getal(c.nieuwe_waarde, 0)),
      rij('gestart', datum(c.gestart_op)), c.beoordeeld_op ? rij('beoordeeld', datum(c.beoordeeld_op)) : null,
      rij('portefeuilles', c.portefeuille_a + ' en ' + c.portefeuille),
      c.uitslag ? el('p', { class: 'klein', style: 'margin:6px 0 0', text: c.uitslag }) : null));
  }
  delen.push(ch);
  p.replaceChildren(...delen);
}

/* ------------------------------------------------------------ kosten */

async function tekenKosten(p) {
  const k = await laad(pad('kosten.json'));
  const deel = k.budget_usd ? Math.min(100, 100 * k.uitgegeven_usd / k.budget_usd) : 0;
  const houder = el('div', { class: 'grafiek grafiek-klein' });
  const maand = k.per_rol.filter(r => r.maand === k.maand);
  p.replaceChildren(
    el('div', { class: 'paneel' }, el('h2', { text: 'AI-kosten in ' + k.maand }),
      el('div', { class: 'cijfers' }, cijfer('uitgegeven', dollar(k.uitgegeven_usd, 2)), cijfer('budget', dollar(k.budget_usd, 2)), cijfer('over', dollar(Math.max(0, k.budget_usd - k.uitgegeven_usd), 2))),
      el('div', { class: 'balk' }, el('div', { style: 'width:' + deel + '%;background:' + (deel > 85 ? '#ef5350' : '#2962ff') })),
      el('p', { class: 'dim klein', style: 'margin:6px 0 0', text: 'Correcties (kosten buiten de bot om, zoals een rooktest) tellen mee in het budget. Raakt het budget op, dan koopt de AI niets nieuws meer tot de volgende maand.' })),
    el('div', { class: 'paneel' }, el('h3', { text: 'Per dag' }), houder),
    el('div', { class: 'paneel' }, el('h3', { text: 'Per rol en model, ' + k.maand }), maand.length ? el('div', { class: 'schuif' }, el('table', { class: 'tabel' },
      el('thead', null, el('tr', null, el('th', { text: 'rol' }), el('th', { text: 'model' }), el('th', { class: 'getal', text: 'aanroepen' }), el('th', { class: 'getal', text: 'kosten' }))),
      el('tbody', null, maand.map(r => el('tr', null, el('td', { text: r.rol }), el('td', { text: r.model }), el('td', { class: 'getal', text: String(r.aanroepen) }), el('td', { class: 'getal', text: dollar(r.usd, 4) })))))) : leeg('Nog geen kosten deze maand.')));
  if (k.per_dag.length) {
    const g = maakGrafiek('kosten', houder, { localization: { locale: 'nl-NL', priceFormatter: x => dollar(x, 2) } });
    const s = g.addSeries(LWC.HistogramSeries, { color: '#2962ff', priceLineVisible: false });
    s.setData(k.per_dag.map(d => ({ time: d.datum, value: d.usd })));
    pasIn(g);
  } else {
    houder.replaceWith(leeg('Nog geen kosten.'));
  }
}

if (!LWC) {
  document.getElementById('inhoud').replaceChildren(leeg('De grafiekbibliotheek kon niet worden geladen. Controleer je internetverbinding en laad de pagina opnieuw.'));
} else {
  start();
}
