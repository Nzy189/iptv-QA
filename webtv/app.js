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
  var playBtn = document.getElementById('play-btn');
  var muteBtn = document.getElementById('mute-btn');
  var volumeInput = document.getElementById('volume');
  var fullscreenBtn = document.getElementById('fullscreen-btn');
  var nowPlaying = document.getElementById('now-playing');
  var channelBadge = document.getElementById('channel-badge');

  var state = { channels: [], current: -1, hls: null, numberBuffer: '', numberTimer: null,
                transcoding: false, noVideoNotified: false };

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

  function showOverlay(text) {
    overlayText.textContent = text;
    retryBtn.hidden = true;
    overlay.hidden = false;
  }

  function showError(text, retry) {
    overlayText.textContent = text;
    retryBtn.hidden = !retry;
    overlay.hidden = false;
  }

  function hideOverlay() { overlay.hidden = true; }

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

  function startPlayback(transcode) {
    state.transcoding = !!transcode;
    var index = state.current;
    if (transcode) showOverlay('该频道需要服务器转码，正在启动…（约 5 秒）');
    else showOverlay('正在连接…');
    var url = (transcode ? '/tv/transcode/' : '/tv/play/') + index;
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
        if (!data.fatal) return;
        if (window.Hls && data.type === Hls.ErrorTypes.MEDIA_ERROR && !state.transcoding) {
          showOverlay('画面编码浏览器不支持，启动服务器转码…');
          if (state.hls) { state.hls.destroy(); state.hls = null; }
          startPlayback(true);
          return;
        }
        showError('频道播放失败（' + data.details + '），可换台或重试', true);
      });
      hls.on(Hls.Events.MANIFEST_PARSED, function () {
        hideOverlay();
        video.play().catch(function () { showOverlay('浏览器限制自动播放，请点"播放"按钮'); });
      });
      hls.loadSource(url);
      hls.attachMedia(video);
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url;
      video.addEventListener('loadedmetadata', function () {
        hideOverlay();
        video.play().catch(function () { showOverlay('浏览器限制自动播放，请点"播放"按钮'); });
      }, { once: true });
      video.addEventListener('error', function () {
        showError('频道播放失败，可换台或重试', true);
      }, { once: true });
    } else {
      showError('当前浏览器不支持 HLS 播放');
    }
  }

  function play(index) {
    var n = channelCount();
    if (!n) return;
    var i = ((index % n) + n) % n;
    state.current = i;
    state.transcoding = false;
    state.noVideoNotified = false;
    var channel = state.channels[i];
    nowPlaying.textContent = (i + 1) + ' · ' + channel.name;
    channelBadge.textContent = (i + 1) + ' ' + channel.name;
    highlightChannel(i);
    if (state.hls) { state.hls.destroy(); state.hls = null; }
    video.pause();
    startPlayback(false);
  }

  function next() { play(state.current + 1); }
  function prev() { play(state.current - 1); }

  function randomPlay() {
    var n = channelCount();
    if (n < 2) return play(0);
    var i = state.current;
    while (i === state.current) i = Math.floor(Math.random() * n);
    play(i);
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
    showOverlay('正在加载频道列表…');
    api('/api/tv/source', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url })
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          showError(data.detail || ('加载失败：' + (data.error || '未知错误')), true);
          return;
        }
        state.channels = data.channels;
        localStorage.setItem('tv-source', url);
        renderList();
        play(0);
      });
    }).catch(function (error) {
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
  video.addEventListener('play', updatePlayBtn);
  video.addEventListener('pause', updatePlayBtn);
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

  renderPresets();

  if (!document.documentElement.requestFullscreen) fullscreenBtn.hidden = true;

  var saved = localStorage.getItem('tv-source') || '';
  if (saved) sourceInput.value = saved;

  fetch('/api/tv/session').then(function (response) {
    if (response.status === 401) { showLogin(); return; }
    return response.json().then(function (data) {
      enterApp();
      if (data.ok && data.count > 0) {
        state.channels = data.channels;
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
