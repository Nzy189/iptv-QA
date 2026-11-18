'use strict';
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var data, selected = null, page = 0, pending = null, timer;
  var labels = {pending:'待处理',done:'完成',failed:'失败，将重试',superseded:'已有新版本',
    synced:'已同步',sync_failed:'桶同步失败',local_only:'仅本地',verified:'已验证',
    stable:'稳定优选',basic:'基础可播',pending_review:'待复查',imported_unverified:'导入，未重新验证'};
  function el(tag,text) { var n=document.createElement(tag); if(text!==undefined)n.textContent=text; return n; }
  function button(text,action) { var n=el('button',text);n.addEventListener('click',action);return n; }
  function notice(text,error) { $('notice').textContent=text;$('notice').classList.toggle('error',!!error); }
  function request(path,body) {
    return fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined)
      .then(function(r){if(r.status===401){$('manager-login').hidden=false;$('workspace').hidden=true;throw Error('请先登录');}
        return r.json().then(function(x){if(!r.ok)throw Error(r.status===409?'数据已更新，请刷新后再操作':x.error||'操作失败');return x;});});
  }
  function action(body) {
    body.revision=data.revision;
    return request('/api/library/action',body).then(function(){notice('已保存。订阅修改将在发布后同步。');$('preview').hidden=true;return refresh();})
      .catch(function(e){notice(e.message,true);});
  }
  function confirm(text,callback) { $('preview-text').textContent=text;pending=callback;$('preview').hidden=false;$('preview').scrollIntoView({block:'nearest'}); }
  function channelAction(operation,row,value,sid) { return action({operation:operation,scope:$('scope').value,channel_id:row.id,value:value,stream_id:sid}); }
  function detail(row) {
    selected=row.id;var box=$('detail');box.textContent='';box.appendChild(el('h2',row.name));
    box.appendChild(el('small','固定 ID '+row.id+' · '+(row.enabled?'当前分组启用':'已在清理区')));
    [['name','频道名称','rename'],['group_name','显示分组','group']].forEach(function(f){
      var wrap=el('div');wrap.className='field';var input=el('input');input.value=row[f[0]];input.setAttribute('aria-label',f[1]);
      wrap.appendChild(input);wrap.appendChild(button('保存'+f[1],function(){channelAction(f[2],row,input.value);}));box.appendChild(wrap);
    });
    var clean=button(row.enabled?'移入当前分组清理区':'恢复到当前分组',function(){
      request('/api/library/action',{operation:'preview',scope:$('scope').value,channel_id:row.id}).then(function(p){
        confirm((row.enabled?'清理':'恢复')+'「'+p.name+'」，影响当前分组的 '+p.outputs.join(' / ')+'；其他分组不变。',
          function(){channelAction(row.enabled?'quarantine':'restore',row);});
      }).catch(function(e){notice(e.message,true);});
    });clean.className=row.enabled?'danger':'';box.appendChild(clean);
    box.appendChild(el('h2','候选线路（最多选用前五条）'));
    row.lines.forEach(function(line,index){
      var item=el('div');item.className='line';item.appendChild(el('div',(index+1)+'. '+line.host));
      item.appendChild(el('small',(labels[line.evidence]||line.evidence)+' · '+(line.enabled?'启用':'已停用')+
        (line.eligible?'':' · 未入本轮可播')+' · '+line.last_seen+' · 来源：'+(line.sources||[]).join(' / ')));
      var controls=el('div');controls.className='line-controls';var priority=el('input');priority.type='number';priority.min='0';priority.max='999';
      priority.value=line.priority===null?line.rank:line.priority;priority.setAttribute('aria-label','优先级，数字越小越优先');
      controls.appendChild(priority);controls.appendChild(button('设置优先级',function(){channelAction('priority',row,Number(priority.value),line.stream_id);}));
      controls.appendChild(button(line.enabled?'停用线路':'恢复线路',function(){channelAction(line.enabled?'disable_line':'enable_line',row,null,line.stream_id);}));
      item.appendChild(controls);box.appendChild(item);
    });
    box.appendChild(el('h2','最近检测与播放反馈'));
    (row.feedback||[]).forEach(function(f){box.appendChild(el('small',(f.kind==='browser'?'浏览器反馈':'服务端检测')+' · '+(f.ok?'通过':f.reason||'未通过')+' · '+f.created_at));});
  }
  function render() {
    $('workspace').hidden=false;$('manager-login').hidden=true;
    $('counter').textContent=data.count+' 个频道 · 总表版本 '+data.revision;
    $('page').textContent='第 '+(page+1)+' 页';$('previous').disabled=page===0;$('next').disabled=(page+1)*50>=data.count;
    $('channels').textContent='';data.channels.forEach(function(row){
      var n=button(row.name,function(){detail(row);});n.className='channel'+(selected===row.id?' active':'');
      n.appendChild(el('small',row.lines.filter(function(l){return l.enabled&&l.eligible;}).length+' 条线路'+(row.enabled?'':' · 清理区')));$('channels').appendChild(n);
    });
    var current=data.channels.find(function(r){return r.id===selected;});if(current)detail(current);else{$('detail').textContent='选择频道，查看线路、优先级与恢复操作。';}
    $('jobs').textContent='';data.jobs.slice(0,5).forEach(function(j){$('jobs').appendChild(el('p',(j.kind==='publish'?'发布':'备份')+' · '+(labels[j.state]||j.state)+' · '+j.updated_at+(j.error?' · '+j.error:'')));});
    $('versions').textContent='';data.versions.forEach(function(v){var row=el('div');row.className='row';
      row.appendChild(el('span','总表版本 '+v.revision+' · '+(labels[v.state]||v.state)+' · '+v.created_at));
      row.appendChild(button('回退至此版本',function(){confirm('将总表控制状态回退至版本 '+v.revision+' 并生成新发布。操作记录保留，可再次恢复。',function(){action({operation:'rollback',version:v.id});});}));$('versions').appendChild(row);
    });
    $('backups').textContent='';data.backups.forEach(function(b){$('backups').appendChild(el('p',(labels[b.state]||b.state)+' · '+b.created_at+(b.error?' · '+b.error:'')));});
    $('audit').textContent='';data.audit.forEach(function(a){$('audit').appendChild(el('p',a.created_at+' · '+a.scope+' · '+a.action));});
  }
  function refresh() {
    return request('/api/library/state?scope='+encodeURIComponent($('scope').value)+'&q='+encodeURIComponent($('query').value)+'&removed='+($('removed').checked?'1':'0')+'&page='+page)
      .then(function(x){data=x;render();}).catch(function(e){notice(e.message,true);});
  }
  $('manager-login').addEventListener('submit',function(e){e.preventDefault();request('/api/tv/login',{password:$('password').value}).then(function(){$('password').value='';refresh();}).catch(function(e){notice(e.message,true);});});
  $('scope').addEventListener('change',function(){page=0;selected=null;refresh();});$('removed').addEventListener('change',function(){page=0;refresh();});
  $('query').addEventListener('input',function(){clearTimeout(timer);timer=setTimeout(function(){page=0;refresh();},300);});
  $('refresh').addEventListener('click',refresh);$('previous').addEventListener('click',function(){page-=1;refresh();});$('next').addEventListener('click',function(){page+=1;refresh();});
  $('publish').addEventListener('click',function(){if(data)confirm('发布总表版本 '+data.revision+'：统一生成 Top5、兼容套表、今日补充和英语订阅，并同步桶。',function(){action({operation:'publish'});});});
  $('backup').addEventListener('click',function(){if(data)action({operation:'backup'});});
  $('cancel').addEventListener('click',function(){$('preview').hidden=true;pending=null;});$('apply').addEventListener('click',function(){if(pending){var fn=pending;pending=null;fn();}});
  setInterval(function(){if(!$('workspace').hidden&&$('preview').hidden&&document.activeElement.tagName!=='INPUT')refresh();},10000);
  refresh();
})();
