'use strict';

(function () {
  var video = document.getElementById('video');
  var loginWrap = document.getElementById('login');
  var loginForm = document.getElementById('login-form');
  var loginPassword = document.getElementById('login-password');
  var loginError = document.getElementById('login-error');
  var app = document.getElementById('app');
  var sidebar = document.getElementById('sidebar');
  var menuBtn = document.getElementById('menu-btn');
  var sourceInput = document.getElementById('source-url');
  var sourceLoad = document.getElementById('source-load');
  var presets = document.getElementById('presets');
  var searchInput = document.getElementById('search');
  var channelList = document.getElementById('channel-list');
  var overlay = document.getElementById('overlay');
  var overlayText = document.getElementById('overlay-text');
  var retryBtn = document.getElementById('retry-btn');
  var numberEntry = document.getElementById('number-entry');
  var channelInput = document.getElementById('channel-input');
  var goBtn = document.getElementById('go-btn');
  var prevBtn = document.getElementById('prev-btn');
  var nextBtn = document.getElementById('next-btn');
  var randomBtn = document.getElementById('random-btn');
  var modeBtn = document.getElementById('mode-btn');
  var orderBtn = document.getElementById('order-btn');
  var raceBtn = document.getElementById('race-btn');
  var prefetchBtn = document.getElementById('prefetch-btn');
  var playerArea = document.getElementById('player-area');
  var destructionBtn = document.getElementById('destruction-btn');
  var reportBtn = document.getElementById('report-btn');
  var cleanupStatus = document.getElementById('cleanup-status');
  var playBtn = document.getElementById('play-btn');
  var muteBtn = document.getElementById('mute-btn');
  var volumeInput = document.getElementById('volume');
  var fullscreenBtn = document.getElementById('fullscreen-btn');
  var nowPlaying = document.getElementById('now-playing');
  var channelBadge = document.getElementById('channel-badge');

  var state = { channels: [], current: -1, hls: null, numberBuffer: '', numberTimer: null,
                transcoding: false, noVideoNotified: false, slot: 0, setCount: 1,
                mode: localStorage.getItem('tv-mode') || 'normal',
                order: localStorage.getItem('tv-zap-order') === 'random' ? 'random' : 'sequential',
                libraryScope: null, sourceId: null, sourceSets: 1, destructive: false, destructionTimer: null,
                destructionDeadline: 0, cleaned: 0, removing: false, reporting: false, triedChannels: {}, generation: 0, sourceRequest: 0, zapAttempts: 0, watchdog: null,
                race: null, raceOn: localStorage.getItem('tv-source-race') !== '0',
                prefetchMode: (localStorage.getItem('tv-prefetch') || 'light') };

  function showLogin() {
    loginWrap.hidden = false;
    app.hidden = true;
    loginPassword.focus();
  }

  function enterApp() {
    loginWrap.hidden = true;
    app.hidden = false;
  }

  function api(path, options) {
    return fetch(path, options).then(function (response) {
      if (response.status === 401) { showLogin(); throw new Error('unauthorized'); }
      return response;
    });
  }

  var autoplayNotice = false;

  function showOverlay(text) {
    overlayText.textContent = text;
    retryBtn.hidden = true;
    overlay.hidden = false;
    autoplayNotice = false;
  }

  function showError(text, retry) {
    overlayText.textContent = text;
    retryBtn.hidden = !retry;
    overlay.hidden = false;
    autoplayNotice = false;
  }

  function hideOverlay() { overlay.hidden = true; autoplayNotice = false; }

  function showAutoplayNotice() {
    overlayText.textContent = '浏览器限制自动播放，请点"播放"按钮';
    retryBtn.hidden = true;
    overlay.hidden = false;
    autoplayNotice = true;
  }

  function channelCount() { return state.channels.length; }

  function highlightChannel(index) {
    var active = channelList.querySelector('.ch.active');
    if (active) active.classList.remove('active');
    if (index < 0) return;
    var item = channelList.querySelector('[data-index="' + index + '"]');
    if (item) {
      item.classList.add('active');
      item.scrollIntoView({ block: 'nearest' });
    }
  }

  function renderList() {
    channelList.textContent = '';
    var query = searchInput.value.trim().toLowerCase();
    state.channels.forEach(function (channel, index) {
      var haystack = (channel.name + ' ' + (channel.group || '')).toLowerCase();
      if (channel.removed) return;
      if (query && haystack.indexOf(query) === -1) return;
      var item = document.createElement('button');
      item.className = 'ch';
      item.dataset.index = index;
      var num = document.createElement('span');
      num.className = 'ch-num';
      num.textContent = index + 1;
      var name = document.createElement('span');
      name.className = 'ch-name';
      name.textContent = channel.name;
      if (channel.group) name.title = channel.group;
      item.appendChild(num);
      item.appendChild(name);
      item.addEventListener('click', function () {
        play(index);
        if (window.innerWidth <= 900) sidebar.classList.remove('open');
      });
      channelList.appendChild(item);
    });
    highlightChannel(state.current);
  }

  function clearDestruction() {
    if (state.destructionTimer) clearTimeout(state.destructionTimer);
    state.destructionTimer = null;
  }

  function nextVisible(index) {
    for (var step=1;step<=channelCount();step+=1) {
      var i=(index+step+channelCount())%channelCount();
      if (!state.channels[i].removed) return i;
    }
    return -1;
  }

  function reportCurrentLine() {
    if (!state.libraryScope || state.current < 0 || state.reporting) return;
    var channel = state.channels[state.current];
    if (!channel || channel.removed) return;
    if (!window.confirm('当前线路播放的不是该频道内容？确认后此线路将被清理，可在管理页恢复')) return;
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      showError('网络已断开，未清理', true); return;
    }
    var slot = state.slot, index = state.current, generation = state.generation, source = state.sourceId;
    state.reporting = true;
    clearWatchdog(); clearDestruction(); endRace(null);
    api('/api/library/action', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({operation:'report_line', source_id:source, channel_id:channel.id,
                            slot:slot, reason:'content_mismatch'})})
      .then(function (response) { return response.json().then(function (data) {
        if (!response.ok) throw Error(response.status === 409
          ? '总表已更新，请重新加载分组后再清理' : (data.error || '清理未保存'));
        if (state.sourceId !== source) return;
        state.cleanupRevision = data.revision;
        if (data.channel_quarantined) {
          channel.removed = true;
          cleanupStatus.textContent = '已清理 1 条线路 · 该频道已无可播线路，移入清理区 · 可在管理页恢复';
          renderList();
          if (state.generation === generation) {
            var next = nextVisible(index);
            if (next < 0) showError('当前分组已无可播放频道，可到管理页恢复', false);
            else play(next);
          }
          return;
        }
        cleanupStatus.textContent = '已清理 1 条线路 · 可在管理页恢复';
        if (state.generation === generation && state.current === index) play(index);
      }); })
      .catch(function (error) { showError(error.message || '清理失败，未删除记录', true); })
      .finally(function () { state.reporting = false; });
  }

  function removeFailed(reason) {
    if (!state.destructive || !state.libraryScope || state.removing) return false;
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      clearDestruction();showError('网络已断开，暂停清理',true);return true;
    }
    var index=state.current, generation=state.generation, source=state.sourceId;
    var channel=state.channels[index];
    if (!channel || channel.removed) return false;
    clearWatchdog();clearDestruction();endRace(null);state.removing=true;
    if (state.hls) {state.hls.destroy();state.hls=null;}
    video.pause();showOverlay('正在保存清理记录…');
    api('/api/library/action',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({operation:'cleanup',source_id:source,channel_id:channel.id,reason:reason})})
      .then(function(response){return response.json().then(function(data){
        if (!response.ok) throw Error(response.status===409?'总表已更新，请重新加载分组后再清理':data.error||'清理未保存');
        if (state.sourceId!==source) return;
        channel.removed=true;state.cleaned+=1;state.cleanupRevision=data.revision;
        cleanupStatus.textContent='已移入清理区 '+state.cleaned+' 台 · 桶同步排队中 · 可在频道管理恢复';
        renderList();
        if (state.generation===generation) {
          var next=nextVisible(index);
          if(next<0)showError('当前分组已无可播放频道，可到管理页恢复',false);
          else play(next);
        }
      });}).catch(function(error){showError(error.message||'清理失败，未删除记录',true);})
      .finally(function(){state.removing=false;});
    return true;
  }

  function armDestruction() {
    clearDestruction();
    if(!state.destructive||!state.libraryScope||state.current<0)return;
    state.destructionDeadline=Date.now()+20000;
    var generation=state.generation;
    state.destructionTimer=setTimeout(function(){
      if(generation===state.generation)removeFailed('startup_timeout');
    },20000);
  }

  function clearWatchdog() {
    if (state.watchdog) { clearTimeout(state.watchdog); state.watchdog = null; }
  }

  function armWatchdog() {
    clearWatchdog();
    if (state.destructive || state.mode !== 'enhanced') return;
    state.watchdog = setTimeout(function () {
      if (video.buffered.length > 0 || video.readyState >= 2) return;
      if (state.race && state.race.candidates.some(function (c) {
        return !c.dropped && (c.video.readyState >= 2 || c.video.buffered.length > 0);
      })) return;
      advanceAfterFailure('10 秒未加载');
    }, 10000);
  }

  function advanceAfterFailure(reason) {
    clearWatchdog();
    endRace(null);
    if (state.slot + 1 < state.setCount) {
      state.slot += 1;
      state.transcoding = false;
      if (state.hls) { state.hls.destroy(); state.hls = null; }
      startPlayback(false);
      return true;
    }
    if (state.destructive && state.destructionTimer && removeFailed('all_lines_failed')) return true;
    if (state.mode === 'enhanced' && channelCount() > 0) {
      var remaining = [];
      for (var step = 1; step <= channelCount(); step += 1) {
        var candidate = (state.current + step) % channelCount();
        if (!state.triedChannels[candidate] && !state.channels[candidate].removed) remaining.push(candidate);
      }
      if (!remaining.length) {
        showError('当前列表已全部尝试，均无法播放，可手动换台或重试', true);
        return true;
      }
      state.zapAttempts += 1;
      var target = state.order === 'random'
        ? remaining[Math.floor(Math.random() * remaining.length)] : remaining[0];
      play(target, true);
      return true;
    }
    return false;
  }

  function handleFatalError(data) {
    if (data.response && data.response.code === 401) { clearDestruction();clearWatchdog();showLogin();return; }
    var isMedia = window.Hls && data.type === Hls.ErrorTypes.MEDIA_ERROR;
    if (isMedia && !state.transcoding && !state.destructive) {
      showOverlay('画面编码浏览器不支持，启动服务器转码…');
      if (state.hls) { state.hls.destroy(); state.hls = null; }
      startPlayback(true);
      return;
    }
    if (advanceAfterFailure(data.details)) return;
    showError('频道播放失败（' + data.details + '），可换台或重试', true);
  }

  function endRace(race, winner) {
    if (!race) race = state.race;
    if (!race || race.ended) return race;
    race.ended = true;
    if (state.race === race) state.race = null;
    race.candidates.forEach(function (c) {
      if (c.loadedHandler) { c.video.removeEventListener('loadeddata', c.loadedHandler); c.loadedHandler = null; }
      if (winner && c === winner) return;
      if (c.main && !winner) return;
      if (c.hls && !c.dropped) { c.hls.destroy(); if (c.main) state.hls = null; }
      if (!c.main && c.video.parentNode) c.video.parentNode.removeChild(c.video);
    });
    return race;
  }

  function dropRaceCandidate(race, candidate, isMedia) {
    if (race.ended || state.race !== race) return;
    candidate.dropped = true;
    if (isMedia) race.mediaError = true;
    if (candidate.hls && !candidate.hls.destroyed) { candidate.hls.destroy(); if (candidate.main) state.hls = null; }
    if (!candidate.main && candidate.video.parentNode) candidate.video.parentNode.removeChild(candidate.video);
    if (race.candidates.every(function (c) { return c.dropped; })) {
      endRace(race, null);
      if (race.mediaError && !state.destructive) {
        state.slot = 0;
        startPlayback(true);
      } else {
        state.slot = race.candidates.length - 1;
        advanceAfterFailure('多线路竞速全部失败');
      }
    }
  }

  function raceWinner(race, candidate) {
    if (race.ended || state.race !== race || state.generation !== race.generation) return;
    endRace(race, candidate);
    if (candidate.main) return;
    var old = video;
    var el = candidate.video;
    state.hls = candidate.hls;
    state.slot = candidate.slot;
    if (old.parentNode) old.parentNode.replaceChild(el, old);
    old.removeAttribute('id');
    old.pause();
    old.removeAttribute('src');
    old.load();
    el.classList.remove('race-video');
    el.id = 'video';
    el.muted = old.muted;
    el.volume = old.volume;
    video = el;
    bindVideoEvents(el);
    hideOverlay();
    el.play().catch(function (err) {
      if (state.generation !== race.generation || video !== el) return;
      if (err && err.name === 'AbortError') return;
      clearWatchdog();clearDestruction();
      showAutoplayNotice();
    });
  }

  function startRace(index, generation) {
    var channel = state.channels[index];
    var lines = Math.min(5, channel.line_count || 1);
    if (state.slot !== 0 || !state.raceOn || lines < 2) return;
    var race = { generation: generation, candidates: [], mediaError: false, ended: false };
    state.race = race;
    var main = { slot: 0, video: video, hls: state.hls, dropped: false, main: true };
    main.loadedHandler = function () { raceWinner(race, main); };
    race.candidates.push(main);
    video.addEventListener('loadeddata', main.loadedHandler);
    for (var s = 1; s < lines; s += 1) {
      (function (slot) {
        var el = document.createElement('video');
        el.muted = true;
        el.setAttribute('playsinline', '');
        el.classList.add('race-video');
        playerArea.appendChild(el);
        var rhls = new Hls({
          enableWorker: false,
          maxBufferLength: 30,
          manifestLoadingTimeOut: 20000,
          manifestLoadingMaxRetry: 2,
          fragLoadingTimeOut: 30000,
          fragLoadingMaxRetry: 3
        });
        var candidate = { slot: slot, video: el, hls: rhls, dropped: false, main: false };
        candidate.loadedHandler = function () { raceWinner(race, candidate); };
        el.addEventListener('loadeddata', candidate.loadedHandler);
        race.candidates.push(candidate);
        rhls.on(Hls.Events.ERROR, function (event, data) {
          if (!data || !data.fatal) return;
          if (state.race === race && !race.ended) {
            dropRaceCandidate(race, candidate, window.Hls && data.type === Hls.ErrorTypes.MEDIA_ERROR);
            return;
          }
          if (state.hls === rhls) handleFatalError(data);
        });
        rhls.on(Hls.Events.FRAG_BUFFERED, function () {
          if (state.race === race) clearWatchdog();
        });
        var rurl = '/tv/play/' + index + '/' + slot;
        if (state.sourceId) rurl += '?source_id=' + encodeURIComponent(state.sourceId);
        rhls.loadSource(rurl);
        rhls.attachMedia(el);
      })(s);
    }
  }

  function startPlayback(transcode) {
    state.transcoding = !!transcode;
    var index = state.current;
    var generation = state.generation;
    if (transcode) showOverlay('该频道需要服务器转码，正在启动…（约 5 秒）');
    else if (state.slot > 0) showOverlay('本套源失败，自动尝试备用源（' + (state.slot + 1) + '/' + state.setCount + '）…');
    else showOverlay('正在连接…');
    var url = (transcode ? '/tv/transcode/' : '/tv/play/') + index + '/' + state.slot;
    if(state.sourceId)url+='?source_id='+encodeURIComponent(state.sourceId);
    if (window.Hls && Hls.isSupported()) {
      var hls = new Hls({
        enableWorker: false,
        maxBufferLength: 30,
        manifestLoadingTimeOut: 20000,
        manifestLoadingMaxRetry: 2,
        fragLoadingTimeOut: 30000,
        fragLoadingMaxRetry: 3
      });
      state.hls = hls;
      hls.on(Hls.Events.ERROR, function (event, data) {
        if (state.hls !== hls || state.generation !== generation) return;
        if (!data.fatal) return;
        if (state.race && state.race.candidates[0] && state.race.candidates[0].hls === hls) {
          dropRaceCandidate(state.race, state.race.candidates[0], window.Hls && data.type === Hls.ErrorTypes.MEDIA_ERROR);
          return;
        }
        handleFatalError(data);
      });
      hls.on(Hls.Events.FRAG_BUFFERED, function () {
        if (state.hls === hls && state.generation === generation) clearWatchdog();
      });
      hls.on(Hls.Events.MANIFEST_PARSED, function () {
        if (state.hls !== hls || state.generation !== generation) return;
        hideOverlay();
        var el = video;
        video.play().catch(function (err) {
          if (state.generation !== generation || video !== el) return;
          if (err && err.name === 'AbortError') return;
          clearWatchdog();clearDestruction();
          showAutoplayNotice();
        });
      });
      hls.loadSource(url);
      hls.attachMedia(video);
      if (!transcode) startRace(index, generation);
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url;
      video.addEventListener('loadedmetadata', function () {
        if (state.generation !== generation) return;
        hideOverlay();
        var el = video;
        video.play().catch(function (err) {
          if (state.generation !== generation || video !== el) return;
          if (err && err.name === 'AbortError') return;
          clearWatchdog();clearDestruction();
          showAutoplayNotice();
        });
      }, { once: true });
      video.addEventListener('error', function () {
        if (state.generation !== generation) return;
        if (advanceAfterFailure('播放出错')) return;
        showError('频道播放失败，可换台或重试', true);
      }, { once: true });
    } else {
      showError('当前浏览器不支持 HLS 播放');
    }
    armWatchdog();
  }

  function play(index, viaZap) {
    var n = channelCount();
    if (!n) return;
    var i = ((index % n) + n) % n;
    if(state.channels[i].removed){i=nextVisible(i);if(i<0)return;}
    clearWatchdog();
    endRace(null);
    state.generation += 1;
    state.current = i;
    state.transcoding = false;
    state.noVideoNotified = false;
    state.slot = 0;
    if (!viaZap) { state.zapAttempts = 0; state.triedChannels = {}; }
    state.triedChannels[i] = true;
    var channel = state.channels[i];
    state.setCount=channel.line_count || state.setCount;
    armDestruction();
    nowPlaying.textContent = (i + 1) + ' · ' + channel.name;
    channelBadge.textContent = (i + 1) + ' ' + channel.name;
    highlightChannel(i);
    if (state.hls) { state.hls.destroy(); state.hls = null; }
    video.pause();
    video.removeAttribute('src');
    video.load();
    startPlayback(false);
  }

  function next() { var i=nextVisible(state.current);if(i>=0)play(i); }
  function prev() { for(var j=1;j<=channelCount();j+=1){var i=(state.current-j+channelCount())%channelCount();if(!state.channels[i].removed)return play(i);} }

  function randomPlay() {
    var available=[];
    state.channels.forEach(function(c,i){if(!c.removed&&i!==state.current)available.push(i);});
    if(!available.length)return;
    play(available[Math.floor(Math.random()*available.length)]);
  }

  function togglePlay() {
    if (video.paused) { video.play().catch(function () {}); } else { video.pause(); }
  }

  function updatePlayBtn() {
    playBtn.textContent = video.paused ? '播放' : '暂停';
  }

  function toggleMute() {
    video.muted = !video.muted;
    muteBtn.textContent = video.muted ? '已静音' : '音量';
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(function () {});
    } else if (document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen().catch(function () {});
    }
  }

  function showNumberEntry() {
    numberEntry.textContent = state.numberBuffer;
    numberEntry.hidden = false;
  }

  function commitNumber() {
    var target = parseInt(state.numberBuffer, 10);
    state.numberBuffer = '';
    clearTimeout(state.numberTimer);
    numberEntry.hidden = true;
    if (target >= 1 && target <= channelCount()) {
      play(target - 1);
    } else if (channelCount()) {
      showError('没有 ' + target + ' 号频道', true);
    }
  }

  function digitInput(digit) {
    state.numberBuffer = (state.numberBuffer + digit).slice(-4);
    showNumberEntry();
    clearTimeout(state.numberTimer);
    state.numberTimer = setTimeout(commitNumber, 1300);
  }

  function loadSource(url) {
    if (!url) return;
    clearWatchdog();clearDestruction();endRace(null);
    state.generation += 1;
    if (state.hls) { state.hls.destroy(); state.hls = null; }
    video.pause();
    video.removeAttribute('src');
    video.load();
    var request = ++state.sourceRequest;
    showOverlay('正在加载频道列表…');
    api('/api/tv/source', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url })
    }).then(function (response) {
      return response.json().then(function (data) {
        if (request !== state.sourceRequest) return;
        if (!response.ok) {
          showError(data.detail || ('加载失败：' + (data.error || '未知错误')), true);
          return;
        }
        state.channels = data.channels;
        state.sourceId=data.source_id||null;state.libraryScope=data.library_scope||null;
        state.sourceSets=data.sets||1;state.cleaned=0;
        destructionBtn.disabled=!state.libraryScope;
        reportBtn.disabled=!state.libraryScope;
        state.setCount = data.sets || 1;
        localStorage.setItem('tv-source', url);
        renderList();
        play(0);
      });
    }).catch(function (error) {
      if (request !== state.sourceRequest) return;
      if (error.message !== 'unauthorized') showError('加载失败，请检查网络或链接', true);
    });
  }

  function onLogin(event) {
    event.preventDefault();
    loginError.hidden = true;
    api('/api/tv/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: loginPassword.value })
    }).then(function (response) {
      if (!response.ok) { loginError.hidden = false; return Promise.reject(new Error('bad')); }
      loginPassword.value = '';
      return fetch('/api/tv/session').then(function (sessionResponse) {
        return sessionResponse.json();
      }).then(function (data) {
        if (data.ok && data.count > 0) {
          state.channels = data.channels;
          state.sourceId=data.source_id||null;state.libraryScope=data.library_scope||null;state.sourceSets=data.sets||1;
          destructionBtn.disabled=!state.libraryScope;
        reportBtn.disabled=!state.libraryScope;
          state.setCount = data.sets || 1;
          sourceInput.value = data.source || sourceInput.value;
          renderList();
        }
        enterApp();
        if (!channelCount()) showOverlay('填入订阅链接，点"加载"开始');
      });
    }).catch(function (error) {
      if (error.message === 'unauthorized') showLogin();
    });
  }

  function onKey(event) {
    var tag = event.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    if (event.key >= '0' && event.key <= '9') { digitInput(event.key); return; }
    switch (event.key) {
      case 'ArrowUp': event.preventDefault(); prev(); break;
      case 'ArrowDown': event.preventDefault(); next(); break;
      case 'r': case 'R': randomPlay(); break;
      case 'f': case 'F': toggleFullscreen(); break;
      case 'm': case 'M': toggleMute(); break;
      case ' ': event.preventDefault(); togglePlay(); break;
      case 'Enter': if (!numberEntry.hidden) commitNumber(); break;
    }
  }

  loginForm.addEventListener('submit', onLogin);
  sourceLoad.addEventListener('click', function () { loadSource(sourceInput.value.trim()); });
  sourceInput.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') loadSource(sourceInput.value.trim());
  });
  function renderPresets() {
    fetch('/api/tv/playlists').then(function (response) {
      return response.json();
    }).then(function (data) {
      presets.textContent = '';
      (data.playlists || []).forEach(function (item) {
        var button = document.createElement('button');
        button.dataset.url = item.url;
        button.textContent = item.label;
        button.title = item.url;
        presets.appendChild(button);
      });
    }).catch(function () {});
  }

  presets.addEventListener('click', function (event) {
    var button = event.target.closest('button[data-url]');
    if (!button) return;
    sourceInput.value = button.dataset.url;
    loadSource(button.dataset.url);
  });
  searchInput.addEventListener('input', renderList);
  menuBtn.addEventListener('click', function () { sidebar.classList.toggle('open'); });
  prevBtn.addEventListener('click', prev);
  nextBtn.addEventListener('click', next);
  function updateModeBtn() {
    modeBtn.textContent = state.mode === 'enhanced' ? '增强模式' : '普通模式';
    modeBtn.classList.toggle('enhanced', state.mode === 'enhanced');
    orderBtn.textContent = state.order === 'random' ? '随机换台' : '顺序换台';
    orderBtn.setAttribute('aria-pressed', state.order === 'random' ? 'true' : 'false');
  }

  modeBtn.addEventListener('click', function () {
    state.mode = state.mode === 'enhanced' ? 'normal' : 'enhanced';
    localStorage.setItem('tv-mode', state.mode);
    state.zapAttempts = 0;
    state.triedChannels = {};
    if (state.current >= 0) state.triedChannels[state.current] = true;
    updateModeBtn();
    armWatchdog();
  });
  orderBtn.addEventListener('click', function () {
    state.order = state.order === 'sequential' ? 'random' : 'sequential';
    localStorage.setItem('tv-zap-order', state.order);
    updateModeBtn();
    if (state.order === 'sequential') maybePrefetch();
  });
  function updateRaceBtn() {
    raceBtn.textContent = '多源竞速：' + (state.raceOn ? '开' : '关');
    raceBtn.setAttribute('aria-pressed', String(state.raceOn));
  }
  raceBtn.addEventListener('click', function () {
    state.raceOn = !state.raceOn;
    localStorage.setItem('tv-source-race', state.raceOn ? '1' : '0');
    updateRaceBtn();
  });
  var PREFETCH_MODES = ['off', 'light', 'full'];
  function updatePrefetchBtn() {
    var label = { off: '关', light: '轻量', full: '完整' }[state.prefetchMode] || '轻量';
    prefetchBtn.textContent = '下一台预热：' + label;
    prefetchBtn.setAttribute('aria-pressed', String(state.prefetchMode !== 'off'));
  }
  function maybePrefetch() {
    if (state.prefetchMode === 'off' || state.order !== 'sequential' || state.current < 0 || !state.libraryScope) return;
    var path = '/api/tv/prefetch' + (state.sourceId ? '?source_id=' + encodeURIComponent(state.sourceId) : '');
    api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: state.prefetchMode, index: state.current, source_id: state.sourceId })
    }).catch(function () {});
  }
  function cyclePrefetch() {
    var i = PREFETCH_MODES.indexOf(state.prefetchMode);
    state.prefetchMode = PREFETCH_MODES[(i + 1) % PREFETCH_MODES.length];
    localStorage.setItem('tv-prefetch', state.prefetchMode);
    updatePrefetchBtn();
    maybePrefetch();
  }
  prefetchBtn.addEventListener('click', cyclePrefetch);
  updatePrefetchBtn();
  reportBtn.addEventListener('click', reportCurrentLine);
  destructionBtn.addEventListener('click',function(){
    if(!state.libraryScope){showError('自定义订阅不受总表管理，不能清理',false);return;}
    state.destructive=!state.destructive;
    destructionBtn.textContent='失效清理：'+(state.destructive?'开':'关');
    destructionBtn.setAttribute('aria-pressed',String(state.destructive));
    destructionBtn.classList.toggle('destructive',state.destructive);
    clearWatchdog();
    if(state.destructive&&video.readyState<2)armDestruction();else clearDestruction();
  });
  randomBtn.addEventListener('click', randomPlay);
  playBtn.addEventListener('click', togglePlay);
  muteBtn.addEventListener('click', toggleMute);
  fullscreenBtn.addEventListener('click', toggleFullscreen);
  goBtn.addEventListener('click', function () {
    var target = parseInt(channelInput.value, 10);
    channelInput.value = '';
    if (target >= 1 && target <= channelCount()) play(target - 1);
  });
  channelInput.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') goBtn.click();
  });
  volumeInput.addEventListener('input', function () {
    video.volume = parseFloat(volumeInput.value);
    video.muted = false;
    muteBtn.textContent = '音量';
  });
  retryBtn.addEventListener('click', function () {
    if (state.current >= 0) play(state.current);
  });
  function bindVideoEvents(el) {
    el.addEventListener('play', updatePlayBtn);
    el.addEventListener('playing', function () {
      clearDestruction();
      if (autoplayNotice) hideOverlay();
      maybePrefetch();
    });
    el.addEventListener('pause', updatePlayBtn);
    el.addEventListener('progress', function () {
      if (el.buffered.length > 0 || el.readyState >= 2) clearWatchdog();
    });
  }
  bindVideoEvents(video);
  document.addEventListener('keydown', onKey);

  setInterval(function () {
    if (state.current < 0 || video.paused || video.videoWidth > 0) return;
    if (video.currentTime < 2) return;
    if (!state.transcoding) {
      state.transcoding = true;
      showOverlay('未检测到画面，启动服务器转码…');
      if (state.hls) { state.hls.destroy(); state.hls = null; }
      startPlayback(true);
    } else if (!state.noVideoNotified) {
      state.noVideoNotified = true;
      showOverlay('该频道只有声音，没有画面（纯音频源）');
    }
  }, 3000);

  setInterval(function(){
    if(!state.cleaned||!state.libraryScope)return;
    api('/api/library/status').then(function(r){return r.json();}).then(function(x){
      var status=x.publication&&x.publication.state;
      var note=status==='sync_failed'?'桶同步失败，后台重试中':'桶同步排队中';
      if(x.published_revision>=state.cleanupRevision)note=status==='synced'?'已同步到桶':'已更新本地';
      cleanupStatus.textContent='已移入清理区 '+state.cleaned+' 台 · '+note+' · 可在管理页恢复';
    }).catch(function(){});
  },10000);

  renderPresets();

  if (!document.documentElement.requestFullscreen) fullscreenBtn.hidden = true;
  updateModeBtn();
  updateRaceBtn();

  var saved = localStorage.getItem('tv-source') || '';
  if (saved) sourceInput.value = saved;

  fetch('/api/tv/session').then(function (response) {
    if (response.status === 401) { showLogin(); return; }
    return response.json().then(function (data) {
      enterApp();
      if (data.ok && data.count > 0) {
        state.channels = data.channels;
        state.sourceId=data.source_id||null;state.libraryScope=data.library_scope||null;state.sourceSets=data.sets||1;
        destructionBtn.disabled=!state.libraryScope;
        reportBtn.disabled=!state.libraryScope;
        state.setCount = data.sets || 1;
        sourceInput.value = data.source || saved;
        renderList();
        showOverlay('选择频道开始观看');
      } else {
        showOverlay(saved ? '点"加载"恢复上次的订阅' : '填入订阅链接，点"加载"开始');
      }
    });
  }).catch(function () {
    enterApp();
    showOverlay('服务暂时不可用，请刷新重试', true);
  });
})();
