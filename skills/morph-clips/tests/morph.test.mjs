// node --test tests/morph.test.mjs — engine math and transcript parsing.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ctx = {window: {}};
vm.runInNewContext(fs.readFileSync(path.join(HERE, '..', 'engine', 'motion.js'), 'utf8'), ctx);
const M = ctx.window.M;

test('spring starts at 0 and settles at 1', () => {
  assert.equal(M.spring(0, 15, 0.84), 0);
  assert.ok(Math.abs(M.spring(3, 15, 0.84) - 1) < 1e-4);
  assert.equal(M.spring(0.1, Infinity, 1), 1);
});

test('track settles on every target and is pure', () => {
  const f = M.track(0, [[1, 100], [2, 40]]);
  assert.equal(f(0.5), 0);
  assert.ok(Math.abs(f(1.99) - 100) < 2);
  assert.ok(Math.abs(f(5) - 40) < 1e-3);
  assert.equal(f(1.3), f(1.3));
});

test('show: entry and exit do not overlap', () => {
  assert.equal(M.show(0, 1, 2).o, 0);
  assert.ok(M.show(1.6, 1, 2).o > 0.99);
  assert.ok(M.show(2.2, 1, 2).o < 0.01);
});

test('path equals its keys at the ends', () => {
  const y = M.path([[0, 10, 20], [1, 110, 20]]);
  assert.deepEqual({...y(-1)}, {x: 10, y: 20});
  assert.deepEqual({...y(2)}, {x: 110, y: 20});
});

test('words: SRT and JSON', () => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'morph-test-'));
  fs.writeFileSync(path.join(d, 'a.srt'), '1\n00:00:01,000 --> 00:00:02,000\nskill yüklenir\n');
  fs.writeFileSync(path.join(d, 'a.json'), JSON.stringify([{word: 'skill', start: 1.2, end: 1.5}, {word: 'yüklenir', start: 1.6, end: 2}]));
  const runWords = f => spawnSync(process.execPath, [path.join(HERE, '..', 'cli', 'morph.mjs'), 'words', path.join(d, f)], {encoding: 'utf8'}).stdout.trim();
  assert.equal(runWords('a.srt'), '1.00:skill 1.40:yüklenir');
  assert.equal(runWords('a.json'), '1.20:skill 1.60:yüklenir');
});

test('colorTrack reaches its target colour', () => {
  const c = M.colorTrack('#000000', [[0, '#ff8000']]);
  assert.equal(c(5), 'rgb(255,128,0)');
});

test('safeArea picks the largest free strip beside protected boxes', () => {
  assert.deepEqual({...M.safeArea(1920, 1080, [])}, {x: 0, y: 0, w: 1920, h: 1080, cx: 960, cy: 540});
  const a = M.safeArea(1920, 1080, [[160, 90, 700, 900]]);
  assert.equal(a.x, 908); assert.equal(a.w, 1012); assert.equal(a.cx, 1414);
  const b = M.safeArea(1920, 1080, [[1500, 700, 380, 340]]);
  assert.equal(b.x, 0); assert.equal(b.w, 1452);
});

test('words: YouTube auto VTT (inline times, rolling repeats)', () => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'morph-vtt-'));
  fs.writeFileSync(path.join(d, 'a.vtt'), 'WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello<00:00:01.500><c> world</c>\n\n00:00:03.000 --> 00:00:05.000\nhello world\nnext<00:00:03.600><c> line</c>\n');
  const out = spawnSync(process.execPath, [path.join(HERE, '..', 'cli', 'morph.mjs'), 'words', path.join(d, 'a.vtt')], {encoding: 'utf8'}).stdout.trim();
  assert.equal(out, '1.00:hello 1.50:world\n3.00:next 3.60:line');
});

test('rgb reads short and long hex', () => {
  assert.deepEqual([...M.rgb('#fff')], [255, 255, 255]);
  assert.deepEqual([...M.rgb('#ff8000cc')], [255, 128, 0]);
  assert.deepEqual([...M.rgb('#f80c')], [255, 136, 0]);
});
