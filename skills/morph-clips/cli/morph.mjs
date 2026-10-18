#!/usr/bin/env node
// morph-clips — narration-timed, one-shape motion-graphics clips.
// Usage: node <skill>/cli/morph.mjs <command> ...   (node <skill>/cli/morph.mjs help)
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {spawn, spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';

const SKILL = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PLACEMENTS = ['full', 'region', 'panel'];
const WIN = process.platform === 'win32';

const fail = m => { throw Object.assign(new Error(m), {user: true}); };
const readJson = p => JSON.parse(fs.readFileSync(p, 'utf8').replace(/^﻿/, ''));
// Round to 1/100 s once, so 12.996 becomes 0m13s00 (not 0m12s100) and 59.996 becomes 1:00.00.
const cs = t => Math.round(t * 100);
const tc = t => { const c = cs(t); return `${Math.floor(c / 6000)}m${String(Math.floor(c / 100) % 60).padStart(2, '0')}s${String(c % 100).padStart(2, '0')}`; };
const clock = t => { const c = cs(t); return `${Math.floor(c / 6000)}:${String(Math.floor(c / 100) % 60).padStart(2, '0')}.${String(c % 100).padStart(2, '0')}`; };
// Safe text for generated pages: literal replacement (no $& patterns), HTML escaping, JSON that cannot close a <script>.
const esc = v => String(v).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const js = v => JSON.stringify(v).replace(/</g, '\\u003c');
const put = (s, key, val) => s.split(key).join(val);
const even = n => Math.floor(n / 2) * 2;
const rel = p => path.relative(process.cwd(), p) || '.';
const box = s => { const v = String(s).split(',').map(Number); if (v.length !== 4 || v.some(x => !Number.isFinite(x))) fail(`Box must be x,y,w,h: ${s}`); return v; };

function run(exe, args, {quiet = false} = {}) {
  const r = spawnSync(exe, args, {encoding: 'utf8', windowsHide: true, stdio: quiet ? 'pipe' : ['ignore', 'inherit', 'inherit'], maxBuffer: 1 << 28});
  if (r.error) fail(`${exe} did not run: ${r.error.message}`);
  if (r.status !== 0) fail(`${exe} failed (${r.status}). ${quiet ? (r.stderr || '').slice(-600) : ''}`);
  return r;
}
const has = exe => !spawnSync(exe, ['-version'], {windowsHide: true}).error;

// Resolution, fps, duration, audio. Uses ffprobe when present, otherwise parses `ffmpeg -i`.
function probe(video) {
  if (!fs.existsSync(video)) fail('Video not found: ' + video);
  if (has('ffprobe')) {
    const j = JSON.parse(run('ffprobe', ['-v', 'error', '-show_streams', '-show_format', '-of', 'json', video], {quiet: true}).stdout);
    const v = j.streams.find(s => s.codec_type === 'video'); if (!v) fail('No video stream.');
    return {width: v.width, height: v.height, fps: v.avg_frame_rate, duration: Number(j.format.duration), audio: j.streams.some(s => s.codec_type === 'audio')};
  }
  const e = spawnSync('ffmpeg', ['-hide_banner', '-i', video], {encoding: 'utf8', windowsHide: true}).stderr || '';
  const b = e.match(/Video:.*?(\d{2,5})x(\d{2,5})/), f = e.match(/([\d.]+) fps/), d = e.match(/Duration: (\d+):(\d+):([\d.]+)/);
  if (!b || !d) fail('Could not read video: ' + video);
  const ntsc = {'23.98': '24000/1001', '29.97': '30000/1001', '47.95': '48000/1001', '59.94': '60000/1001', '119.88': '120000/1001'};
  return {width: +b[1], height: +b[2], fps: f ? ntsc[f[1]] || f[1] : '30', duration: +d[1] * 3600 + +d[2] * 60 + +d[3], audio: /Audio:/.test(e)};
}
const fpsNum = f => String(f).includes('/') ? f.split('/')[0] / f.split('/')[1] : +f;

// Playwright: the skill's own node_modules (setup), then a global install.
let _pw;
function playwright() {
  if (_pw) return _pw;
  const globalRoot = spawnSync(WIN ? 'npm.cmd' : 'npm', ['root', '-g'], {encoding: 'utf8', shell: WIN}).stdout?.trim();
  for (const from of [path.join(SKILL, 'package.json'), globalRoot && path.join(globalRoot, 'x.js')].filter(Boolean)) {
    try { return (_pw = createRequire(from)('playwright')); } catch { /* next */ }
  }
  fail('Playwright not found. Run: node <skill>/cli/morph.mjs setup');
}
async function browser() {
  const {chromium} = playwright();
  return chromium.launch(process.env.MORPH_CHROMIUM ? {executablePath: process.env.MORPH_CHROMIUM} : {});
}
async function openClip(b, html) {
  const p = await b.newPage({viewport: {width: 1920, height: 1080}});
  const errors = []; p.on('pageerror', e => errors.push(e.message));
  await p.goto(pathToFileURL(path.resolve(html)).href + '?render');
  await p.evaluate(() => document.fonts.ready);
  const info = await p.evaluate(() => ({T: window.DURATION, W: document.getElementById('stage').offsetWidth, H: document.getElementById('stage').offsetHeight, alpha: document.documentElement.classList.contains('alpha')}));
  if (!info.T) { await p.close(); fail(`${html}: M.scene did not run. ${errors[0] || ''}`); }
  await p.setViewportSize({width: info.W, height: info.H});
  return {p, info, errors};
}

// ---------- project and plan ----------
function project(d) {
  if (!d) fail('Give a project folder (e.g. motion).');
  const r = path.resolve(d);
  return {root: r, plan: path.join(r, 'plan.json'), clips: path.join(r, 'clips'), dist: path.join(r, 'dist'), out: path.join(r, 'out'), work: path.join(r, 'work')};
}
function loadPlan(P) {
  if (!fs.existsSync(P.plan)) fail('plan.json not found: ' + P.plan);
  const x = readJson(P.plan);
  x.layout = {region: null, protect: [], ...(x.layout || {})};
  x.clips = x.clips || [];
  return x;
}
const source = (P, x) => path.resolve(P.root, x.video);
const clipFile = (P, k) => path.join(P.out, `${k.clip}_${tc(k.in)}.${k.placement === 'panel' ? 'mov' : 'mp4'}`);
function expectedSize(x, k) {
  if (k.placement === 'region' && x.layout.region) return [even(x.layout.region[2]), even(x.layout.region[3])];
  return [x.width || 1920, x.height || 1080];
}

// Brand: "dark" | "light" | path to a brand JSON (relative to plan.json). Font paths are relative to the brand file.
function loadBrand(P, name = 'dark') {
  const file = /[\\/]|\.json$/.test(name) ? path.resolve(P ? P.root : process.cwd(), name) : path.join(SKILL, 'brands', name + '.json');
  if (!fs.existsSync(file)) fail(`Brand not found: ${name} (built-in: dark, light, or a path to your brand.json)`);
  const b = readJson(file), base = name === 'dark' || name === 'light' ? {} : readJson(path.join(SKILL, 'brands', 'dark.json'));
  const colors = {...(base.colors || {}), ...b.colors}, fonts = {...(base.fonts || {}), ...(b.fonts || {})};
  const baseDir = f => (b.fonts && Object.values(b.fonts).includes(f)) ? path.dirname(file) : path.join(SKILL, 'brands');
  const face = (key, fallback) => {
    const f = fonts[key]; if (!f) return {family: fallback, css: ''};
    const css = (f.files || []).map(ff => {
      const src = path.resolve(baseDir(f), ff.file); if (!fs.existsSync(src)) fail('Font file not found: ' + src);
      const fmt = {'.woff2': 'woff2', '.woff': 'woff', '.ttf': 'truetype', '.otf': 'opentype'}[path.extname(src).toLowerCase()] || 'woff2';
      return `@font-face{font-family:'${f.family}';src:url(data:font/${fmt};base64,${fs.readFileSync(src).toString('base64')}) format('${fmt}');${ff.weight ? `font-weight:${ff.weight};` : ''}${ff.style ? `font-style:${ff.style};` : ''}${ff.range ? `unicode-range:${ff.range};` : ''}}`;
    }).join('');
    return {family: `'${f.family}',${fallback}`, css};
  };
  // The engine springs colours channel by channel, so it needs hex or rgb(); names and hsl() would turn black.
  for (const [k, v] of Object.entries(colors)) if (!/^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(v) && !/^rgba?\(/i.test(v)) fail(`Brand colour ${k}: "${v}" — use #rgb, #rrggbb, #rrggbbaa or rgb().`);
  const ui = face('ui', "'Segoe UI',system-ui,sans-serif"), mono = face('mono', 'Consolas,ui-monospace,monospace');
  const vars = Object.entries(colors).map(([k, v]) => `--${k.replace(/[A-Z]/g, c => '-' + c.toLowerCase())}:${v}`).join(';');
  return {file, colors, css: `${ui.css}${mono.css}:root{${vars};--shadow:${b.shadow || base.shadow || 'none'};--font-ui:${ui.family};--font-mono:${mono.family}}`};
}

function check(P, {quiet = false} = {}) {
  const x = loadPlan(P), E = [], W = [];
  if (!x.video) E.push('plan.video is missing (path to the source video, relative to plan.json).');
  else if (!fs.existsSync(source(P, x))) E.push('Source video not found: ' + source(P, x));
  if (!x.fps) E.push('plan.fps is missing (the source frame rate, e.g. "30" or "30000/1001").');
  try { loadBrand(P, x.brand); } catch (e) { E.push(e.message); }
  const ids = new Set(), sorted = [...x.clips].sort((a, b) => a.in - b.in);
  for (const k of x.clips) {
    const n = `#${k.id ?? '?'}`;
    if (!k.id || ids.has(k.id)) E.push(`${n}: id is empty or repeated.`); ids.add(k.id);
    if (!k.clip || !fs.existsSync(path.join(P.clips, k.clip + '.html'))) E.push(`${n}: clips/${k.clip}.html not found.`);
    if (!(k.out > k.in && k.in >= 0)) E.push(`${n}: needs 0 <= in < out.`);
    if (!PLACEMENTS.includes(k.placement)) E.push(`${n}: placement must be ${PLACEMENTS.join('|')}.`);
    if (k.placement === 'region' && !x.layout.region) E.push(`${n}: placement "region" needs layout.region [x,y,w,h].`);
    if (!k.line) W.push(`${n}: no "line" (the sentence it covers); TIMING.md will be empty for it.`);
    if (k.placement === 'full' && k.in < 1) W.push(`${n}: full-frame cutaway inside the first second of the hook.`);
    const d = k.out - k.in;
    if (k.placement !== 'panel' && (d < 2 || d > 12)) W.push(`${n}: ${d.toFixed(1)} s; cutaways read best at 2-12 s.`);
  }
  for (let i = 1; i < sorted.length; i++) {
    const a = sorted[i - 1], b = sorted[i];
    if (b.in < a.out - 1e-6) E.push(`#${a.id} and #${b.id} overlap.`);
    else if (b.in - a.out < 2 && (a.placement !== 'panel' || b.placement !== 'panel')) W.push(`#${a.id} -> #${b.id}: only ${(b.in - a.out).toFixed(1)} s of the original between them; leave ~2 s.`);
  }
  if (x.video && fs.existsSync(source(P, x))) {
    const o = probe(source(P, x));
    if (x.fps && Math.abs(fpsNum(x.fps) - fpsNum(o.fps)) > 0.01) E.push(`plan.fps ${x.fps} != source ${o.fps}.`);
    const last = Math.max(0, ...x.clips.map(k => k.out)); if (last > o.duration + 0.05) E.push(`Last clip ends at ${last}s, the source is ${o.duration.toFixed(2)}s.`);
    for (const r of [x.layout.region, ...x.layout.protect].filter(Boolean)) if (r[0] + r[2] > o.width || r[1] + r[3] > o.height) E.push(`Layout box ${r.join(',')} is outside the ${o.width}x${o.height} frame.`);
  }
  if (!quiet) {
    for (const w of W) console.log('WARN   ' + w);
    for (const e of E) console.log('ERROR  ' + e);
    console.log(E.length ? `\n${E.length} error(s).` : `Plan OK: ${x.clips.length} clip(s)${W.length ? `, ${W.length} warning(s)` : ''}.`);
  }
  if (E.length) process.exitCode = 1;
  return {x, E, W};
}

// ---------- build: fragment -> one self-contained HTML ----------
function buildOne(src, dst, brand, protect = []) {
  const f = fs.readFileSync(src, 'utf8');
  const title = (f.match(/<title>(.*?)<\/title>/) || [])[1] || path.basename(src, '.html');
  const css = [...f.matchAll(/<style>([\s\S]*?)<\/style>/g)].map(m => m[1]).join('');
  const slot = n => (f.match(new RegExp(`<div data-slot="${n}">([\\s\\S]*?)</div><!--/${n}-->`)) || [])[1] || '';
  const js = [...f.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('');
  if (!/M\.scene\(/.test(js)) fail(`${src}: no M.scene({...}) call.`);
  const cursor = `<svg id="cursor" viewBox="0 0 40 56"><path d="M3 3 L3 41 L12.5 32 L19 47 L25.5 44.2 L19.2 29.8 L32 29.8 Z" fill="${brand.colors.cursor || '#fff'}" stroke="${brand.colors.cursorStroke || '#000'}" stroke-width="2.6" stroke-linejoin="round"/></svg>`;
  fs.writeFileSync(dst, `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title><style>${brand.css}${fs.readFileSync(path.join(SKILL, 'engine', 'base.css'), 'utf8')}${css}</style></head><body>\n` +
    `<div id="wrap"><div id="stage"><div id="world">${slot('world')}<div id="shape">${slot('shape')}</div>${slot('over')}</div>${cursor}</div></div>\n` +
    `<script>window.BRAND=${JSON.stringify(brand.colors)};window.PROTECT=${JSON.stringify(protect)};</script><script>${fs.readFileSync(path.join(SKILL, 'engine', 'motion.js'), 'utf8')}</script><script>${js}</script></body></html>`);
}
// Boxes a clip must keep clear of: layout.protect for full-frame clips (they are restored on top in composite).
function protectFor(x, name) {
  const k = x && x.clips.find(c => c.clip === name);
  return k && k.placement === 'full' && k.protect !== false ? x.layout.protect.filter(Boolean) : [];
}
function build(P, names) {
  const x = fs.existsSync(P.plan) ? loadPlan(P) : null, brand = loadBrand(P, x ? x.brand : undefined);
  fs.mkdirSync(P.dist, {recursive: true});
  const list = names.length ? names.map(a => path.join(P.clips, a.replace(/\.html$/, '') + '.html')) : fs.readdirSync(P.clips).filter(n => n.endsWith('.html')).sort().map(n => path.join(P.clips, n));
  for (const k of list) { const d = path.join(P.dist, path.basename(k)); buildOne(k, d, brand, protectFor(x, path.basename(k, '.html'))); console.log('built', rel(d)); }
}

// ---------- stills: contact sheet at key times + overflow check ----------
async function stills(P, name, times) {
  if (!name || !times.length) fail('usage: stills <project> <clip> 0.4 1.2 2.1 ...');
  name = name.replace(/\.html$/, '');
  build(P, [name]);
  fs.mkdirSync(P.work, {recursive: true});
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'morph-'));
  const b = await browser();
  let info, errors; const found = [];
  try {
  const opened = await openClip(b, path.join(P.dist, name + '.html')), p = opened.p; info = opened.info; errors = opened.errors;
  if (info.alpha) await p.evaluate(() => { document.documentElement.style.background = '#2a2f31'; document.body.style.background = '#2a2f31'; });
  for (let i = 0; i < times.length; i++) {
    const t = +times[i];
    await p.evaluate(t => draw(t), t);
    found.push(...(await p.evaluate(t => {
      const S = document.getElementById('stage').getBoundingClientRect(), K = document.getElementById('shape').getBoundingClientRect(), o = [];
      const visible = el => el.checkVisibility({opacityProperty: true, visibilityProperty: true}) && getComputedStyle(el).opacity > 0.05;
      const out = (r, B) => r.left < B.left - 2 || r.right > B.right + 2 || r.top < B.top - 2 || r.bottom > B.bottom + 2;
      for (const el of document.querySelectorAll('#stage *')) {
        if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) || !visible(el)) continue;
        const r = el.getBoundingClientRect(), txt = el.textContent.trim().slice(0, 28);
        if (out(r, S)) o.push(`${t}s text leaves the frame: "${txt}"`);
        else if (el.closest('#shape') && out(r, K)) o.push(`${t}s text is clipped by the shape: "${txt}"`);
        for (const [bx, by, bw, bh] of window.PROTECT || []) if (r.right > S.left + bx && r.left < S.left + bx + bw && r.bottom > S.top + by && r.top < S.top + by + bh) o.push(`${t}s text is under a protected box (it will be covered): "${txt}"`);
        if (el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflow === 'hidden') o.push(`${t}s text does not fit its box: "${txt}"`);
      }
      const c = document.getElementById('cursor');
      if (c.style.display !== 'none') { const r = c.getBoundingClientRect(); if (out(r, S)) o.push(`${t}s cursor leaves the frame`); }
      return o;
    }, t)));
    await p.evaluate(t => { const e = document.createElement('div'); e.id = 'morph-label'; e.textContent = t.toFixed(2) + ' s'; e.style.cssText = 'position:fixed;left:14px;top:10px;z-index:99;font:600 30px system-ui,sans-serif;color:#ffd23f;background:#000000b0;padding:4px 10px;border-radius:8px'; document.body.appendChild(e); }, t);
    await p.screenshot({path: path.join(tmp, String(i).padStart(3, '0') + '.png')});
    await p.evaluate(() => document.getElementById('morph-label').remove());
  }
  } finally { await b.close(); }
  const cols = Math.min(4, times.length), rows = Math.ceil(times.length / cols), sheet = path.join(P.work, name + '-stills.png');
  run('ffmpeg', ['-loglevel', 'error', '-y', '-i', path.join(tmp, '%03d.png'), '-vf', `scale=640:-1,tile=${cols}x${rows}:padding=6:color=0x444444`, '-frames:v', '1', sheet]);
  fs.rmSync(tmp, {recursive: true, force: true});
  console.log('sheet', rel(sheet), `(${info.W}x${info.H}, ${info.T}s)`);
  for (const e of errors) console.log('JS ERROR  ' + e);
  for (const f of [...new Set(found)]) console.log('OVERFLOW  ' + f);
  if (!found.length && !errors.length) console.log('No overflow. Still look at the sheet: readability, word timing, crowding.');
}

// ---------- render: 4 sub-frames across a 180° shutter = motion blur ----------
async function renderOne(html, dst, fps, expected) {
  const FPS = fpsNum(fps), b = await browser();
  try {
    const {p, info, errors} = await openClip(b, html);
    if (expected && (info.W !== expected[0] || info.H !== expected[1])) console.log(`WARN   ${path.basename(html)} is ${info.W}x${info.H}, its placement expects ${expected.join('x')} (it will be scaled).`);
    if (info.alpha !== dst.endsWith('.mov')) fail(`${path.basename(html)}: background:${info.alpha ? 'null' : 'colour'} does not match the placement (panel = transparent .mov, full/region = .mp4 with a background).`);
    if (!info.alpha && (info.W % 2 || info.H % 2)) fail(`${path.basename(html)}: ${info.W}x${info.H} — H.264 needs even width and height.`);
    const N = Math.round(info.T * FPS), K = 4, sub = 1 / (FPS * 2 * K);
    const rate = String(fps).includes('/') ? [String(fps), `${fps.split('/')[0] * 4}/${fps.split('/')[1]}`] : [String(FPS), String(FPS * 4)];
    const blur = `tmix=frames=4:weights='1 1 1 1',select='eq(mod(n\\,4)\\,3)',setpts=N/(${rate[0]})/TB`;
    // Alpha: average premultiplied colour, or the transparent (black) pixels darken moving edges.
    const vf = info.alpha ? `format=rgba,premultiply=inplace=1,${blur},unpremultiply=inplace=1`
      : `format=gbrp,${blur},scale=out_color_matrix=bt709:out_range=tv`;
    const codec = info.alpha ? ['-c:v', 'prores_ks', '-profile:v', '4', '-pix_fmt', 'yuva444p10le', '-vendor', 'apl0']
      : ['-c:v', 'libx264', '-crf', '14', '-preset', 'medium', '-pix_fmt', 'yuv420p', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv', '-movflags', '+faststart'];
    fs.mkdirSync(path.dirname(dst), {recursive: true});
    const ff = spawn('ffmpeg', ['-loglevel', 'error', '-y', '-f', 'image2pipe', '-framerate', rate[1], '-c:v', 'png', '-i', '-', '-vf', vf, '-r', rate[0], ...codec, dst], {windowsHide: true});
    ff.stderr.on('data', d => process.stderr.write(d));
    // ffmpeg can die at any time (file locked by an editor, disk full, missing encoder): never wait on it forever.
    const done = new Promise(res => { ff.on('close', c => res(c ?? 1)); ff.on('error', e => res(e.code === 'ENOENT' ? 'ffmpeg not found' : e.message)); });
    ff.stdin.on('error', () => {});
    let exited = false; done.then(() => { exited = true; });
    for (let f = 0; f < N && !exited; f++) {
      for (let k = 0; k < K && !exited; k++) {
        await p.evaluate(t => draw(t), Math.max(0, f / FPS + (k - 1.5) * sub));
        const buf = await p.screenshot({type: 'png', omitBackground: info.alpha});
        if (!ff.stdin.write(buf)) await Promise.race([new Promise(r => ff.stdin.once('drain', r)), done]);
      }
    }
    ff.stdin.end(); const code = await done;
    if (code !== 0) fail(`ffmpeg render failed (${code}): ${dst}`);
    console.log('rendered', rel(dst), N, 'frames', errors.length ? 'JS ERROR ' + errors[0] : '');
  } finally { await b.close(); }
}
async function render(P, ids, parallel = 2) {
  const {x, E} = check(P, {quiet: true});
  if (E.length) { check(P); fail('Fix the plan first.'); }
  const picked = x.clips.filter(k => !ids.length || ids.includes(String(k.id)));
  if (!picked.length) fail('No clips selected.');
  build(P, picked.map(k => k.clip));
  const queue = [...picked];
  await Promise.all(Array.from({length: Math.min(parallel, queue.length)}, async () => {
    for (let k; (k = queue.shift());) await renderOne(path.join(P.dist, k.clip + '.html'), clipFile(P, k), x.fps, expectedSize(x, k));
  }));
}

// ---------- composite: review preview (hard cuts, original audio, protected boxes kept) ----------
function composite(P) {
  const {x, E} = check(P, {quiet: true});
  if (E.length) { check(P); fail('Fix the plan first.'); }
  const o = probe(source(P, x)), args = ['-loglevel', 'error', '-y', '-i', source(P, x)], fc = [];
  const guarded = x.clips.filter(k => k.placement !== 'panel' && k.protect !== false);
  const boxes = x.layout.protect.filter(Boolean);
  const keep = guarded.length && boxes.length;
  fc.push(keep ? `[0:v]split=${boxes.length + 1}[t0]${boxes.map((_, i) => `[src${i}]`).join('')}` : '[0:v]null[t0]');
  let last = 't0';
  x.clips.forEach((k, i) => {
    const f = clipFile(P, k); if (!fs.existsSync(f)) fail(`#${k.id} has not been rendered: ${f}`);
    args.push('-i', f); const n = i + 1, pad = (k.out - k.in + 2).toFixed(3);
    const [rx, ry, rw, rh] = k.placement === 'region' ? x.layout.region : [0, 0, o.width, o.height];
    fc.push(`[${n}:v]format=yuva444p,scale=${rw}:${rh},tpad=stop_mode=clone:stop_duration=${pad},setpts=PTS-STARTPTS+${k.in}/TB[c${n}]`);
    fc.push(`[${last}][c${n}]overlay=${rx}:${ry}:enable='gte(t,${k.in})*lt(t,${k.out})':eof_action=pass[v${n}]`); last = `v${n}`;
  });
  if (keep) {
    const on = guarded.map(k => `gte(t,${k.in})*lt(t,${k.out})`).join('+');
    boxes.forEach(([bx, by, bw, bh], i) => {
      fc.push(`[src${i}]crop=${bw}:${bh}:${bx}:${by}[p${i}]`, `[${last}][p${i}]overlay=${bx}:${by}:enable='${on}'[k${i}]`); last = `k${i}`;
    });
  }
  fc.push(`[${last}]format=yuv420p[v]`);
  const dst = path.join(P.out, 'preview.mp4');
  fs.mkdirSync(P.out, {recursive: true});
  run('ffmpeg', [...args, '-filter_complex', fc.join(';'), '-map', '[v]', '-map', '0:a?', '-c:v', 'libx264', '-crf', '18', '-preset', 'medium', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-c:a', 'copy', '-t', o.duration.toFixed(3), '-movflags', '+faststart', dst]);
  const s = probe(dst); if (Math.abs(s.duration - o.duration) > 0.15) fail(`Preview is ${s.duration.toFixed(2)}s, source is ${o.duration.toFixed(2)}s.`);
  console.log('preview', rel(dst), `${s.duration.toFixed(2)}s (same as the source)`);
}

// ---------- pages: viewer.html, compare.html, TIMING.md ----------
function pages(P) {
  const {x} = check(P, {quiet: true}), preview = path.join(P.out, 'preview.mp4');
  if (!fs.existsSync(preview)) fail('Run composite first.');
  // Page-relative URL; another drive (Windows) or odd characters (#, %) must not break the link.
  const web = f => { const r = path.relative(P.out, f); return path.isAbsolute(r) ? pathToFileURL(f).href : r.split(path.sep).map(encodeURIComponent).join('/'); };
  const where = {full: 'full-frame cutaway', region: 'fills the screen region', panel: 'transparent panel'};
  const clips = x.clips.map(k => {
    let f = clipFile(P, k);
    if (f.endsWith('.mov')) { // Browsers cannot play ProRes: make a copy on a dark background for the page.
      const pv = f.replace(/\.mov$/, '_preview.mp4');
      run('ffmpeg', ['-loglevel', 'error', '-y', '-f', 'lavfi', '-i', `color=0x2a2f31:s=${x.width || 1920}x${x.height || 1080}`, '-i', f, '-filter_complex', '[0][1]overlay=shortest=1', '-c:v', 'libx264', '-crf', '20', '-pix_fmt', 'yuv420p', pv]); f = pv;
    }
    return {n: k.id, t: k.title || k.clip, tc: `${clock(k.in)} – ${clock(k.out)}`, q: k.line || '', y: where[k.placement], src: web(f), f: path.basename(clipFile(P, k))};
  });
  clips.unshift({n: '▶', t: 'Full preview · your video with every clip cut in', tc: 'whole video', q: 'For review. In the final cut, place the clips in your own editor.', y: '', src: 'preview.mp4', f: 'preview.mp4'});
  const tpl = n => put(fs.readFileSync(path.join(SKILL, 'templates', n), 'utf8'), '/*TITLE*/', esc(x.title || 'Video'));
  fs.writeFileSync(path.join(P.out, 'viewer.html'), put(tpl('viewer.html'), '/*CLIPS*/[]', js(clips)));
  let cmp = put(tpl('compare.html'), '/*MARKS*/[]', js(x.clips.map(k => [k.in, k.out, k.id, k.title || k.clip])));
  cmp = put(put(put(cmp, '/*ORIGINAL*/', esc(web(source(P, x)))), '/*EDITED*/', 'preview.mp4'), '/*DURATION*/0', probe(preview).duration.toFixed(2));
  fs.writeFileSync(path.join(P.out, 'compare.html'), cmp);
  const cell = v => String(v).replace(/\|/g, '\\|').replace(/\r?\n/g, ' ');
  const rows = ['| File | In | Out | Placement | Covers the line |', '|---|---|---|---|---|', ...x.clips.map(k => `| ${path.basename(clipFile(P, k))} | ${clock(k.in)} | ${clock(k.out)} | ${where[k.placement]} | ${cell(k.line || '')} |`)];
  const notes = [...(x.notes || []), ...x.clips.filter(k => k.illustrative).map(k => `#${k.id}: ${k.illustrative}`)];
  fs.writeFileSync(path.join(P.out, 'TIMING.md'), `# ${x.title || 'Video'} — motion clips\n\nEach file name ends with its timeline in-point (\`0m32s40\` = 0:32.40). Region clips go into the box ${x.layout.region ? x.layout.region.join(', ') : '(none)'}; transparent panels are ProRes 4444 .mov files for a track above the video.\n\n${rows.join('\n')}\n${notes.length ? '\n## Illustrative, not real data\n' + notes.map(n => '- ' + n).join('\n') + '\n' : ''}`);
  console.log('wrote', ['viewer.html', 'compare.html', 'TIMING.md'].map(n => rel(path.join(P.out, n))).join(', '));
}

// ---------- inspect: measure the source, contact sheet, layout check ----------
function inspect(video, dir, region, protect) {
  if (!video || !dir) fail('usage: inspect <video> <folder> [--region x,y,w,h] [--protect x,y,w,h]...');
  fs.mkdirSync(dir, {recursive: true});
  const o = probe(video);
  run('ffmpeg', ['-loglevel', 'error', '-y', '-i', video, '-vf', `fps=1/${Math.max(1, o.duration / 16).toFixed(2)},scale=480:-1,tile=4x4:padding=4`, '-frames:v', '1', path.join(dir, 'contact.png')]);
  const boxes = [...(region ? [[region, '0x2f6bff']] : []), ...protect.map(p => [p, '0x1e9e6a'])];
  if (boxes.length) run('ffmpeg', ['-loglevel', 'error', '-y', '-ss', (o.duration / 2).toFixed(2), '-i', video, '-vf', boxes.map(([b, c]) => `drawbox=${b.join(':')}:color=${c}:t=6`).join(','), '-frames:v', '1', path.join(dir, 'layout-check.png')]);
  const info = {...o, layout: {region: region || null, protect}};
  fs.writeFileSync(path.join(dir, 'video.json'), JSON.stringify(info, null, 1) + '\n');
  console.log(JSON.stringify(info, null, 1));
  console.log(`\nLook at ${rel(path.join(dir, 'contact.png'))}${boxes.length ? ` and ${rel(path.join(dir, 'layout-check.png'))} (blue = region, green = protected)` : ''}.`);
}

// ---------- words: SRT/VTT estimate or JSON word timestamps ----------
function words(file) {
  if (!file) fail('usage: words <transcript.srt|.vtt|words.json>');
  const text = fs.readFileSync(file, 'utf8').replace(/^﻿/, '');
  if (file.toLowerCase().endsWith('.json')) {
    const j = JSON.parse(text), list = Array.isArray(j) ? j : (j.words || j.segments?.flatMap(s => s.words || []) || []);
    const line = []; let prevEnd = -1;
    for (const w of list) {
      const t = +(w.start ?? w.begin), s = String(w.word ?? w.text ?? '').trim();
      if (!Number.isFinite(t) || !s) continue;
      if (prevEnd >= 0 && t - prevEnd > 0.8 && line.length) { console.log(line.join(' ')); line.length = 0; }
      line.push(`${t.toFixed(2)}:${s}`); prevEnd = +(w.end ?? t);
    }
    if (line.length) console.log(line.join(' '));
    return;
  }
  const ts = s => { const p = s.trim().replace(',', '.').split(':').map(Number); return p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p[0] * 60 + p[1]; };
  let prev = [];
  for (const block of text.replace(/\r/g, '').split(/\n\n+/)) {
    const L = block.trim().split('\n'), i = L.findIndex(l => l.includes('-->')); if (i < 0) continue;
    const [a, b] = L[i].split('-->').map(s => ts(s.trim().split(' ')[0]));
    // YouTube auto-captions repeat the previous line at the top of each cue ("rolling" captions): drop repeats.
    const lines = L.slice(i + 1).filter(l => l.trim() && !prev.includes(l.replace(/<[^>]+>/g, '').trim()));
    prev = L.slice(i + 1).map(l => l.replace(/<[^>]+>/g, '').trim());
    const raw = lines.join(' ');
    if (/<\d{2}:\d{2}:\d{2}\.\d{3}>/.test(raw)) { // inline word timestamps: <00:00:01.230><c> word</c>
      const out = []; let t = a;
      for (const part of raw.split(/(<\d{2}:\d{2}:\d{2}\.\d{3}>)/)) {
        const m = part.match(/^<(\d{2}:\d{2}:\d{2}\.\d{3})>$/);
        if (m) { t = ts(m[1]); continue; }
        const ws = part.replace(/<[^>]+>/g, '').split(/\s+/).filter(Boolean);
        ws.forEach((w, j) => out.push(`${(j ? t + j * 0.01 : t).toFixed(2)}:${w}`));
      }
      if (out.length) console.log(out.join(' '));
      continue;
    }
    const ws = raw.replace(/<[^>]+>/g, '').split(/\s+/).filter(Boolean), n = ws.reduce((s, w) => s + w.length + 1, 0);
    if (!ws.length) continue;
    let c = 0; console.log(ws.map(w => { const r = `${(a + (b - a) * c / n).toFixed(2)}:${w}`; c += w.length + 1; return r; }).join(' '));
  }
}

// ---------- new / setup ----------
function create(P, video, brand) {
  if (fs.existsSync(P.plan)) fail('plan.json already exists here.');
  for (const d of [P.clips, P.out, P.work]) fs.mkdirSync(d, {recursive: true});
  let v = {fps: '30', width: 1920, height: 1080};
  if (video) { const o = probe(path.resolve(video)); v = {fps: o.fps, width: o.width, height: o.height}; }
  fs.writeFileSync(P.plan, JSON.stringify({title: 'My video', video: video ? path.relative(P.root, path.resolve(video)).split(path.sep).join('/') : 'source.mp4', ...v, brand: !brand ? 'dark' : /[\\/]|\.json$/.test(brand) ? path.relative(P.root, path.resolve(brand)).split(path.sep).join('/') : brand, layout: {region: null, protect: []}, clips: [], notes: []}, null, 2) + '\n');
  console.log('Created', rel(P.root), `\nExamples to copy into clips/: ${rel(path.join(SKILL, 'examples'))}`);
}
async function setup(install) {
  const R = []; const add = (name, ok, note = '', required = true) => R.push(`${ok ? 'OK     ' : required ? 'MISSING' : 'INFO   '}  ${name}${note ? ' — ' + note : ''}`);
  add('Node >= 18', Number(process.versions.node.split('.')[0]) >= 18, process.versions.node);
  add('ffmpeg', has('ffmpeg'), 'https://ffmpeg.org/download.html');
  add('ffmpeg prores_ks (transparent panels)', has('ffmpeg') && /prores_ks/.test(spawnSync('ffmpeg', ['-hide_banner', '-encoders'], {encoding: 'utf8'}).stdout || ''), '', false);
  add('ffprobe', has('ffprobe'), 'optional; ffmpeg output is parsed without it', false);
  const opt = {cwd: SKILL, stdio: 'inherit', shell: WIN};
  const step = (exe, a, how) => { const r = spawnSync(WIN ? exe + '.cmd' : exe, a, opt); if (r.status !== 0) console.log(`FAILED  ${how}  (run it by hand in ${SKILL})`); return r.status === 0; };
  let pw = true; try { playwright(); } catch { pw = false; }
  if (!pw && install) { step('npm', ['install', '--no-audit', '--no-fund'], 'npm install'); _pw = undefined; try { playwright(); pw = true; } catch { pw = false; } }
  add('Playwright', pw, pw ? '' : 'run: setup');
  const chromium = async () => { try { const b = await browser(); await b.close(); return ''; } catch (e) { return e.message.split('\n')[0]; } };
  let err = pw ? await chromium() : 'needs Playwright';
  if (pw && err && install) { step('npx', ['playwright', 'install', 'chromium'], 'npx playwright install chromium'); err = await chromium(); }
  if (pw) add('Chromium', !err, err || (install ? '' : ''));
  console.log(R.join('\n'));
  if (R.some(r => r.startsWith('MISSING'))) process.exitCode = 1;
}

const HELP = `morph-clips — narration-timed, one-shape motion-graphics clips
  setup | doctor                    check tools (setup installs Playwright + its Chromium)
  inspect <video> <folder> [--region x,y,w,h] [--protect x,y,w,h]
                                    measure, contact sheet, layout check picture
  words <srt|vtt|json>              word times as "12.34:word" (SRT is an estimate, ±0.2 s)
  new <project> [--video <file>] [--brand dark|light|brand.json]
  build <project> [clip ...]        clips/*.html -> dist/ (self-contained, fonts embedded)
  stills <project> <clip> t1 t2 ... contact sheet at those times + overflow check
  check <project>                   plan check (overlaps, breathing room, length, fps, layout)
  render <project> [id ...]         render with motion blur (panel -> ProRes 4444 .mov)
  composite <project>               out/preview.mp4 (hard cuts, original audio, protected boxes kept)
  pages <project>                   out/viewer.html, out/compare.html, out/TIMING.md`;

const [cmd, ...args] = process.argv.slice(2);
const flag = n => { const i = args.indexOf(n); if (i < 0) return undefined; const v = args[i + 1]; args.splice(i, 2); return v; };
const flags = n => { const r = []; for (let v; (v = flag(n)) !== undefined;) r.push(v); return r; };
try {
  switch (cmd) {
    case 'setup': await setup(true); break;
    case 'doctor': await setup(false); break;
    case 'inspect': { const r = flag('--region'), pr = flags('--protect'); inspect(args[0], args[1], r ? box(r) : null, pr.map(box)); break; }
    case 'words': words(args[0]); break;
    case 'new': { const v = flag('--video'), b = flag('--brand'); create(project(args[0]), v, b); break; }
    case 'build': build(project(args[0]), args.slice(1)); break;
    case 'stills': await stills(project(args[0]), args[1], args.slice(2)); break;
    case 'check': check(project(args[0])); break;
    case 'render': { const n = flag('--parallel'); if (n !== undefined && !(Number.isInteger(+n) && +n >= 1)) fail('--parallel must be a whole number >= 1.'); await render(project(args[0]), args.slice(1), n ? +n : 2); break; }
    case 'composite': composite(project(args[0])); break;
    case 'pages': pages(project(args[0])); break;
    default: console.log(HELP);
  }
} catch (e) {
  console.error(e.user ? 'ERROR  ' + e.message : e);
  process.exitCode = 1;
}
// A browser or ffmpeg left open by an error must not keep the process alive.
process.exit(process.exitCode ?? 0);
