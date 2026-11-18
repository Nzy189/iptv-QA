// Browser control regression without a network service or third-party runtime.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function player(count = 40, sets = 3) {
  const elements = {}, timers = new Map(), storage = new Map(), instances = [], created = [];
  let counter = 0;
  class Element {
    constructor() {
      this.listeners = {}; this.children = []; this.value = ''; this.hidden = false;
      this.classList = { add() {}, remove() {}, toggle() {} }; this.dataset = {};
      this.paused = true; this.readyState = 0; this.buffered = { length: 0 };
      this.muted = false; this.volume = 1; this.id = ''; this.parentNode = null;
    }
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
    removeEventListener(name, callback) { this.listeners[name] = (this.listeners[name] || []).filter(f => f !== callback); }
    click() { for (const f of this.listeners.click || []) f({ target: this }); }
    appendChild(child) { child.parentNode = this; this.children.push(child); }
    removeChild(child) { const i = this.children.indexOf(child); if (i >= 0) this.children.splice(i, 1); child.parentNode = null; }
    replaceChild(next, prev) { const i = this.children.indexOf(prev); if (i >= 0) this.children.splice(i, 1, next); next.parentNode = this; prev.parentNode = null; }
    querySelector() { return null; }
    scrollIntoView() {}
    setAttribute(key, value) { this[key] = value; }
    removeAttribute() {}
    pause() { this.paused = true; }
    play() { this.paused = false; return Promise.resolve(); }
    load() { this.buffered = { length: 0 }; this.readyState = 0; }
    canPlayType() { return ''; }
  }
  class Hls {
    static isSupported() { return true; }
    static Events = { ERROR: 'error', FRAG_BUFFERED: 'buffer', MANIFEST_PARSED: 'manifest' };
    static ErrorTypes = { MEDIA_ERROR: 'media' };
    constructor() { this.events = {}; instances.push(this); }
    on(event, callback) { this.events[event] = callback; }
    loadSource(url) { this.url = url; }
    attachMedia() {}
    destroy() { this.destroyed = true; }
    error() { this.events.error('error', { fatal: true, type: 'network', details: 'manifestLoadError' }); }
  }
  const document = {
    getElementById(id) { return elements[id] ||= new Element(); },
    createElement() { const el = new Element(); created.push(el); return el; }, addEventListener() {},
    documentElement: { requestFullscreen() { return Promise.resolve(); } }
  };
  const sandbox = {
    document, Hls, window: { Hls, innerWidth: 1200, confirm: () => true },
    localStorage: { getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v) },
    setTimeout(callback, delay) { const id = ++counter; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); }, setInterval() {},
    Math: Object.assign(Object.create(Math), { random: () => .99 }),
    fetch: () => Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve({ ok: false, playlists: [] }) })
  };
  let code = fs.readFileSync(path.join(__dirname, '../iptv_qa/playback/assets/app.js'), 'utf8');
  const end = code.lastIndexOf('})();');
  code = code.slice(0, end) + 'globalThis.playerTest = {state, play, advanceAfterFailure, armDestruction, removeFailed, reportCurrentLine, currentVideo: function(){return video;}};\n})();';
  vm.runInNewContext(code, sandbox);
  const api = sandbox.playerTest;
  api.state.channels = Array.from({ length: count }, (_, i) => ({ name: 'Channel ' + (i + 1) }));
  api.state.setCount = sets;
  return { ...api, sandbox, elements, instances, created, storage, timers,
    fail: () => instances.filter(h => !h.destroyed).at(-1).error(),
    tick() { const item = timers.entries().next().value; assert.ok(item); timers.delete(item[0]); item[1].callback(); }
  };
}

{
  const p = player(); p.state.mode = 'enhanced'; p.play(32);
  p.fail(); p.fail(); p.fail();
  assert.deepEqual(p.instances.map(h => h.url), [
    '/tv/play/32/0', '/tv/play/32/1', '/tv/play/32/2', '/tv/play/33/0']);
  assert.equal(p.state.current, 33, 'channel 33 advances to 34 after all three lines fail');
}
{
  const p = player(18, 1); p.state.mode = 'enhanced'; p.play(0);
  for (let i = 0; i < 18; i++) p.fail();
  assert.equal(p.instances.length, 18, 'sequential scan continues past the old 15-channel cap');
  assert.ok(p.elements['overlay-text'].textContent.includes('全部尝试'));
}
{
  const p = player(4, 1); p.state.mode = 'enhanced'; p.play(0);
  p.elements['order-btn'].click();
  assert.equal(p.storage.get('tv-zap-order'), 'random');
  p.fail(); p.fail(); p.fail(); p.fail();
  assert.equal(new Set(p.instances.map(h => h.url)).size, 4, 'random scan does not retry failed channels');
  assert.equal(p.instances.length, 4);
}
{
  const p = player(); p.play(0); const old = p.instances[0]; p.play(5); old.error();
  assert.equal(p.state.current, 5, 'late errors from an old channel cannot change the new selection');
  assert.equal(p.instances.length, 2);
}
{
  const p = player(3, 3); p.state.mode = 'enhanced'; p.play(0);
  p.tick(); p.tick(); p.tick();
  assert.equal(p.state.current, 1, '10-second watchdog exhausts backups before sequential advancement');
}
{
  const p = player(3, 3); p.play(1); p.fail(); p.fail(); p.fail();
  assert.equal(p.state.current, 1, 'normal mode does not auto-switch channels');
  assert.equal(p.instances.length, 3);
}
{
  const p=player(3,5);p.state.mode='enhanced';p.state.channels[0].line_count=5;p.play(0);
  for(let i=0;i<5;i++)p.fail();
  assert.deepEqual(p.instances.slice(0,5).map(h=>h.url),Array.from({length:5},(_,i)=>'/tv/play/0/'+i));
  assert.equal(p.state.current,1,'all five lines must fail before advancing');
}
{
  const p=player(3,5);p.state.libraryScope='main';p.state.sourceId='snapshot';p.state.channels[0].id='fixed-channel';
  p.play(0);p.elements['destruction-btn'].click();
  assert.equal(p.state.destructive,true);assert.ok(p.state.destructionTimer);
  p.fail();assert.equal(p.state.slot,1);
  assert.ok(p.state.destructionTimer,'backup switches share the original 20-second budget');
  p.elements.video.listeners.playing[0]();
  assert.equal(p.state.destructionTimer,null,'actual playback cancels startup cleanup');
}
{
  const p=player();p.play(0);p.elements['destruction-btn'].click();
  assert.ok(!p.state.destructive,'custom playlists cannot enable destructive cleanup');
}
{
  const p = player(3, 5); p.state.mode = 'enhanced'; p.state.channels[0].line_count = 5; p.play(0);
  assert.equal(p.instances.length, 5, 'a five-line channel races all five lines at once');
  p.fail(); p.fail(); p.fail(); p.fail(); p.fail();
  assert.equal(p.state.current, 1, 'an exhausted five-line race advances to the next channel');
}
{
  const p = player(3, 7); p.state.channels[0].line_count = 7; p.play(0);
  assert.equal(p.instances.length, 5, 'racing is capped at five concurrent lines');
  p.fail(); p.fail(); p.fail(); p.fail(); p.fail();
  assert.equal(p.state.slot, 5, 'an exhausted race continues at the first unraced line');
  assert.equal(p.instances[5].url, '/tv/play/0/5');
}
{
  const p = player(3, 3); p.state.channels[0].line_count = 3; p.play(0);
  assert.deepEqual(p.instances.map(h => h.url), ['/tv/play/0/0', '/tv/play/0/1', '/tv/play/0/2'],
    'hedged racing loads every line at once');
  const winner = p.created[0];
  winner.listeners.loadeddata[0]();
  assert.equal(p.state.slot, 1, 'the first line to decode a frame wins the race');
  assert.equal(p.currentVideo(), winner, 'the winning line is promoted into the player');
  assert.equal(p.state.hls, p.instances[1], 'the winning hls instance becomes the active player');
  assert.ok(p.instances[0].destroyed && p.instances[2].destroyed, 'losing lines are destroyed');
  assert.ok(!p.instances[1].destroyed, 'the winner keeps playing');
}
{
  const p = player(3, 3); p.state.channels[0].line_count = 2; p.play(0);
  p.instances[1].events.error('error', { fatal: true, type: 'media', details: 'bufferIncompatible' });
  p.instances[0].events.error('error', { fatal: true, type: 'media', details: 'bufferIncompatible' });
  assert.equal(p.instances[2].url, '/tv/transcode/0/0', 'codec-only failures fall back to server transcoding');
}
{
  const p = player(3, 3); p.state.channels[0].line_count = 3;
  p.elements['race-btn'].click();
  assert.equal(p.storage.get('tv-source-race'), '0');
  p.play(0);
  assert.equal(p.instances.length, 1, 'racing can be disabled from the toggle');
}
{
  const p = player(3, 3); p.state.mode = 'enhanced'; p.state.channels[0].line_count = 2; p.play(0);
  p.created[0].readyState = 2;
  p.tick();
  assert.equal(p.state.current, 0, 'the watchdog waits while any racing line has data');
  assert.ok(p.state.race, 'the race stays alive while a line is still loading');
}
{
  const p = player(3, 3); p.state.channels[0].line_count = 2; p.play(0);
  const hidden = p.created[0];
  p.play(1);
  assert.ok(p.instances[0].destroyed && p.instances[1].destroyed, 'switching channels tears down the race');
  assert.ok(!p.elements['player-area'].children.includes(hidden), 'hidden race elements are removed');
}
{
  const p = player(3, 3); p.state.channels[0].line_count = 3; p.play(0);
  let staleCatch; const main = p.elements.video;
  main.play = function () { return { catch(cb) { staleCatch = cb; } }; };
  p.instances[0].events.manifest();
  p.created[0].listeners.loadeddata[0]();
  staleCatch({ name: 'NotAllowedError' });
  assert.ok(p.elements.overlay.hidden, 'a stale autoplay rejection cannot shadow the promoted winner');
}
{
  const p = player(3, 3); p.play(0);
  let pendingCatch; const main = p.elements.video;
  main.play = function () { return { catch(cb) { pendingCatch = cb; } }; };
  p.instances[0].events.manifest();
  pendingCatch({ name: 'AbortError' });
  assert.ok(p.elements.overlay.hidden, 'a pause-interrupted play() is not an autoplay block');
}
{
  const p = player(3, 3); p.play(0);
  let pendingCatch; const main = p.elements.video;
  main.play = function () { return { catch(cb) { pendingCatch = cb; } }; };
  p.instances[0].events.manifest();
  pendingCatch({ name: 'NotAllowedError' });
  assert.ok(!p.elements.overlay.hidden && p.elements['overlay-text'].textContent.includes('自动播放'),
    'a real autoplay block still shows the notice');
  main.paused = true;
  main.play = function () { this.paused = false; return Promise.resolve(); };
  p.elements['play-btn'].click();
  main.listeners.playing[0]();
  assert.ok(p.elements.overlay.hidden, 'actual playback dismisses the autoplay notice');
}
(async () => {
  function settle() { return new Promise(resolve => setImmediate(resolve)); }
  function reportPlayer() {
    const p = player(3, 5);
    p.state.libraryScope = 'main'; p.state.sourceId = 'snap';
    p.state.channels[0].id = 'ch0'; p.state.channels[0].line_count = 5;
    p.calls = []; p.reply = { revision: 7, channel_quarantined: false };
    p.sandbox.fetch = (path, options) => {
      p.calls.push([path, options.body ? JSON.parse(options.body) : null]);
      return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve(p.reply) });
    };
    return p;
  }
  {
    const p = reportPlayer(); p.play(0);
    p.elements['report-btn'].click();
    await settle();
    assert.equal(p.calls.length, 1, 'accepted confirm sends exactly one report');
    assert.deepEqual(p.calls[0], ['/api/library/action',
      { operation: 'report_line', source_id: 'snap', channel_id: 'ch0', slot: 0, reason: 'content_mismatch' }]);
    assert.deepEqual(Array.from(p.state.channels[0].reportedSlots), [0]);
    assert.equal(p.state.slot, 1, 'playback resumes at the next free line');
    assert.ok(p.elements['cleanup-status'].textContent.indexOf('已清理 1 条线路') === 0);
  }
  {
    const p = reportPlayer(); p.sandbox.window.confirm = () => false; p.play(0);
    p.elements['report-btn'].click();
    await settle();
    assert.equal(p.calls.length, 0, 'cancelling the confirm sends nothing');
  }
  {
    const p = reportPlayer(); p.reply = { revision: 8, channel_quarantined: true }; p.play(0);
    p.elements['report-btn'].click();
    await settle();
    assert.ok(p.state.channels[0].removed, 'an emptied channel is marked removed');
    assert.equal(p.state.current, 1, 'playback moves to the next channel');
  }
  {
    const p = reportPlayer();
    p.sandbox.fetch = () => Promise.resolve({ status: 409, ok: false, json: () => Promise.resolve({ error: 'stale_revision' }) });
    p.play(0);
    p.elements['report-btn'].click();
    await settle();
    assert.ok(p.elements['overlay-text'].textContent.indexOf('总表已更新') !== -1,
      'a stale revision explains the conflict');
    assert.ok(!p.state.channels[0].reportedSlots, 'a failed report marks nothing locally');
  }
  {
    const p = player(3, 3); p.calls = []; p.play(0);
    p.sandbox.fetch = (path) => { p.calls.push(path); return Promise.resolve({ status: 200, ok: true, json: () => Promise.resolve({}) }); };
    p.elements['report-btn'].click();
    await settle();
    assert.equal(p.calls.length, 0, 'report does nothing outside managed sources');
  }
  {
    const p = reportPlayer(); p.play(0);
    p.elements['report-btn'].click();
    await settle();
    p.elements['race-btn'].click();
    p.play(0);
    p.fail();
    assert.equal(p.state.slot, 2, 'line advance skips the reported line');
  }
  {
    const p = reportPlayer(); p.play(0);
    p.elements['report-btn'].click();
    await settle();
    p.play(1); p.play(0);
    assert.deepEqual(p.instances.slice(-4).map(h => h.url),
      ['/tv/play/0/1?source_id=snap', '/tv/play/0/2?source_id=snap', '/tv/play/0/3?source_id=snap', '/tv/play/0/4?source_id=snap'],
      'returning to the channel races the remaining lines, never the reported one');
  }
})().then(() => console.log('PLAYER_NAVIGATION_OK: backups, sequential scan, random non-repeat, stale events, watchdog, normal mode, hedged racing, autoplay notices, report cleanup'))
  .catch(error => { console.error(error); process.exit(1); });
