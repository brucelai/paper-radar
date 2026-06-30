/* 論文學習雷達 — 前端 */
// 注：前端 fetch 的 papers.json 由 run.sh 從產生的 papers.json 複製覆蓋。
//     repo 內附 papers.sample.json 為合成範例（clone 後想直接看畫面可改名/複製成 papers.json）。
const LS_ACT = 'pr_actions_v1';     // 各篇動作 {item_id:{vote,star,zotero,deepread}}
const LS_TOPIC = 'pr_topics_v1';    // 主題開關 {group:bool}
const LS_FILT = 'pr_filters_v1';    // {badge,sort,search,showSeen}
const LS_VISIT = 'pr_visit_v1';     // {prev,last}
const API = '/api/action';          // Worker(step 4)；失敗則純本地

let DATA = null;
let actions = load(LS_ACT, {});
let topics = load(LS_TOPIC, null);
let filt = load(LS_FILT, {badge:'all', sort:'rec', search:'', showSeen:false});
// 一次性遷移：PRPM v2 伺服器排序上線，舊的預設 'score' → 'rec'（只跑一次，之後尊重使用者選擇）
if(!localStorage.getItem('pr_sortmig_v2')){
  if(filt.sort === 'score'){ filt.sort = 'rec'; save(LS_FILT, filt); }
  localStorage.setItem('pr_sortmig_v2','1');
}
let visit = resolveVisit();
const pendingIds = new Set();        // synced=0 且有實際動作的 item_id（activity-based：點擊當下即時增減，不等重載）
const seenAtLoad = new Set();        // 開頁當下已 seen 的 item_id → 只有「載入前就已看」的才隱藏；本 session 新點的留著（可再點第二顆鈕）
let headCollapsed = load('pr_headcollapse_v1', window.innerWidth < 600);

function load(k,d){ try{return JSON.parse(localStorage.getItem(k))??d}catch{return d} }

// 從 D1 載入動作狀態（跨瀏覽器真實來源；失敗則沿用 localStorage）
async function loadActionsFromServer(){
  try{
    const r = await fetch('/api/state?_=' + Date.now());
    if(!r.ok) return;
    const j = await r.json();
    const map = {};
    pendingIds.clear();
    for(const a of (j.actions||[])){
      const e = {};
      if(a.vote) e.vote = a.vote;
      if(a.seen){ e.seen = true; seenAtLoad.add(a.item_id); }
      if(a.deepread) e.deepread = true;   // 🔬 品質（沿用 deepread 欄）
      if(a.content) e.content = true;      // 📚 內容
      if(a.pdf_key) e.pdf = true;          // 📎 已上傳全文（給 pending 判定用）
      if(Object.keys(e).length) map[a.item_id] = e;
      // 等待同步＝未 synced 且有實際動作（純 seen 不算）
      const actionable = a.vote || a.deepread || a.content || a.star || a.zotero || a.pdf_key;
      if(!a.synced && actionable) pendingIds.add(a.item_id);
    }
    actions = map;
    save(LS_ACT, actions);
  }catch(e){ /* keep localStorage */ }
}
function save(k,v){ localStorage.setItem(k, JSON.stringify(v)); }

// 回訪：跨「日」才滾動
function resolveVisit(){
  const today = new Date().toISOString().slice(0,10);
  let v = load(LS_VISIT, null);
  if(!v){ v={prev:null, last:today}; }
  else if(v.last !== today){ v = {prev:v.last, last:today}; }
  save(LS_VISIT, v);
  return v;
}

async function init(){
  const r = await fetch('papers.json', {cache:'no-cache'});   // ETag/304 生效；不再強制重載全檔
  DATA = await r.json();
  if(topics===null){ // 首次：用 config default_on
    topics = {};
    for(const [k,v] of Object.entries(DATA.topic_groups)) topics[k] = v.default_on;
    save(LS_TOPIC, topics);
  }
  document.getElementById('meta').textContent =
    `更新 ${DATA.updated}｜${DATA.counts.exported} 篇`;
  await loadActionsFromServer();   // D1 = 跨瀏覽器真實來源
  // seenAtLoad 補種：離線 / D1 失敗時也能用 localStorage 的 seen 來隱藏
  for(const [id,a] of Object.entries(actions)) if(a && a.seen) seenAtLoad.add(id);
  buildTopics();
  updateSyncBanner();
  buildVisitBanner();
  bindFilters();
  bindUpload();
  bindHeadToggle();
  applyHeadCollapse();
  render();
}

// 等待同步 banner：activity-based 即時計數（pendingIds 隨點擊增減）。
// 點一下 → 只看等待同步的論文；再點一下 → 恢復全部。
function updateSyncBanner(){
  const el = document.getElementById('syncBanner');
  if(!el) return;
  const n = pendingIds.size;
  if(n === 0){
    el.classList.add('hidden');
    if(filt.badge === 'pending'){ filt.badge = 'all'; save(LS_FILT, filt); syncBadgeUI(); render(); }
    return;
  }
  el.textContent = filt.badge === 'pending'
    ? `⏳ 只看 ${n} 篇等待同步中 — 點此恢復全部`
    : `⏳ 目前 ${n} 篇等待同步 — 點此只看這些｜回 PC 對 Claude 說「論文同步」`;
  el.classList.remove('hidden');
  el.onclick = () => {
    filt.badge = (filt.badge === 'pending') ? 'all' : 'pending';
    save(LS_FILT, filt); syncBadgeUI(); updateSyncBanner(); render();
  };
}

function bindHeadToggle(){
  const btn = document.getElementById('headToggle');
  if(!btn) return;
  btn.onclick = () => {
    headCollapsed = !headCollapsed;
    save('pr_headcollapse_v1', headCollapsed);
    applyHeadCollapse();
  };
}
function applyHeadCollapse(){
  const ex = document.getElementById('headExtra');
  const btn = document.getElementById('headToggle');
  if(!ex) return;
  ex.classList.toggle('hidden', headCollapsed);
  if(btn){
    btn.classList.toggle('on', !headCollapsed);
    btn.textContent = headCollapsed ? '▶' : '▼';   // ▶ 收合 / ▼ 展開
  }
}

function bindUpload(){
  const btn = document.getElementById('upBtn');
  if(!btn) return;
  btn.onclick = async () => {
    const f = document.getElementById('upFile').files[0];
    const title = document.getElementById('upTitle').value.trim();
    const doi = document.getElementById('upDoi').value.trim();
    const content = document.getElementById('upContent').checked ? '1' : '0';
    const quality = document.getElementById('upQuality').checked ? '1' : '0';
    const msg = document.getElementById('upMsg');
    if(!f){ msg.textContent = '請先選 PDF'; return; }
    if(f.type !== 'application/pdf'){ msg.textContent = '只接受 PDF'; return; }
    msg.textContent = '上傳中…';
    const qs = new URLSearchParams({ title, doi, content, deepread: quality });
    if(doi) qs.set('item_id', 'doi:' + doi.toLowerCase());
    try{
      const r = await fetch('/api/upload?' + qs.toString(), {
        method:'POST', headers:{'Content-Type':'application/pdf'}, body: f });
      const j = await r.json();
      msg.textContent = r.ok ? `✅ 已上傳，說「論文同步」我就處理` : `✗ ${j.error||'失敗'}`;
      if(r.ok){ document.getElementById('upFile').value=''; document.getElementById('upTitle').value=''; document.getElementById('upDoi').value=''; }
    }catch(e){ msg.textContent = '✗ 上傳失敗'; }
  };
}

function buildTopics(){
  const box = document.getElementById('topics');
  box.innerHTML = '';
  for(const [k,v] of Object.entries(DATA.topic_groups)){
    const b = document.createElement('div');
    b.className = 'topic' + (topics[k] ? ' on' : '');
    b.textContent = v.label;
    b.onclick = () => { topics[k]=!topics[k]; save(LS_TOPIC,topics); b.classList.toggle('on'); render(); };
    box.appendChild(b);
  }
}

function buildVisitBanner(){
  if(!visit.prev) return;
  const n = DATA.papers.filter(p => p.first_seen >= visit.prev && passTopic(p)).length;
  if(!n) return;
  const el = document.getElementById('visitBanner');
  const labelOff = `✨ 自上次造訪(${visit.prev})以來新增 ${n} 篇 — 點此只看這些`;
  const labelOn  = `✨ 只看新增 ${n} 篇中 — 點此恢復全部`;
  el.textContent = filt.badge==='visit' ? labelOn : labelOff;
  el.classList.remove('hidden');
  el.onclick = () => {
    // toggle：已在 visit 篩選 → 恢復 all；否則進 visit
    filt.badge = (filt.badge==='visit') ? 'all' : 'visit';
    save(LS_FILT,filt); syncBadgeUI();
    el.textContent = filt.badge==='visit' ? labelOn : labelOff;
    render();
  };
}

function bindFilters(){
  const s = document.getElementById('search');
  s.value = filt.search || '';
  s.oninput = () => { filt.search=s.value; save(LS_FILT,filt); render(); };
  const sort = document.getElementById('sort');
  sort.value = filt.sort;
  sort.onchange = () => { filt.sort=sort.value; save(LS_FILT,filt); render(); };
  const ss = document.getElementById('showSeen');
  if(ss){ ss.checked = !!filt.showSeen;
    ss.onchange = () => { filt.showSeen=ss.checked; save(LS_FILT,filt); render(); }; }
  document.querySelectorAll('#badgeFilter .chip').forEach(c => {
    c.onclick = () => { filt.badge=c.dataset.badge; save(LS_FILT,filt); syncBadgeUI(); render(); };
  });
  syncBadgeUI();
}
function syncBadgeUI(){
  document.querySelectorAll('#badgeFilter .chip').forEach(c =>
    c.dataset.on = (c.dataset.badge===filt.badge) ? '1' : '0');
}

function passTopic(p){ return topics[p.group]; }

function passBadge(p){
  const a = actions[p.item_id] || {};
  switch(filt.badge){
    case 'oa': return !!p.oa_pdf_url;   // 只算「真的抓得到 OA PDF」
    case 'oanew': return !!p.oaNew;
    case 'inst': return p.inst_subscribed===1;
    case 'new': return p.isNew;
    case 'unseen': return !a.seen;
    case 'pending': return pendingIds.has(p.item_id);
    case 'visit': return visit.prev && p.first_seen >= visit.prev;
    default: return true;
  }
}

function passSearch(p){
  const q = (filt.search||'').trim().toLowerCase();
  if(!q) return true;
  return (p.title+' '+p.authors+' '+p.source_name).toLowerCase().includes(q);
}

// 已看過預設隱藏；勾「顯示已看過」才顯示。
// 只隱藏「載入前就已 seen」的；本 session 剛點的留著（讓你能在同一張卡先按品質再按內容）。
function passSeen(p){
  if(filt.badge === 'pending') return true;   // 等待同步的幾乎都已 seen，此模式不隱藏
  if(filt.showSeen) return true;
  const a = actions[p.item_id] || {};
  if(!a.seen) return true;
  return !seenAtLoad.has(p.item_id);
}

function render(){
  const list = document.getElementById('list');
  const base = DATA.papers.filter(p => passTopic(p) && passBadge(p) && passSearch(p));
  const ps = base.filter(passSeen);
  ps.sort(cmpFor(filt.sort));
  list.innerHTML = '';
  for(const p of ps) list.appendChild(card(p));
  const hidden = base.length - ps.length;
  document.getElementById('count').textContent =
    `顯示 ${ps.length} 篇` + (hidden>0 ? `（隱藏 ${hidden} 已看）` : '');
}

// 排序比較器：rec=伺服器推薦順序(p.rank)、date=最新、score=關鍵字分數。
// fallback payload 可能沒有 rank/kw_score → 全部有 ?? 守衛，退回 score 或 0。
function cmpFor(sort){
  if(sort==='date')
    return (a,b)=> (b.pub_date||b.first_seen).localeCompare(a.pub_date||a.first_seen);
  if(sort==='score')
    return (a,b)=> (b.kw_score ?? b.score ?? 0) - (a.kw_score ?? a.score ?? 0);
  // 'rec'（預設）：有 rank 用 rank 升冪；rank 缺失（fallback）退回 score 降冪
  return (a,b)=>{
    const ar = a.rank ?? null, br = b.rank ?? null;
    if(ar!=null && br!=null) return ar - br;
    if(ar!=null) return -1;
    if(br!=null) return 1;
    return (b.score ?? 0) - (a.score ?? 0);
  };
}

function card(p){
  const a = actions[p.item_id] || {};
  const el = document.createElement('div');
  el.className = 'card' + (a.vote==='down'?' down':'') + (a.seen?' seen':'');

  const sc = document.createElement('div');
  sc.className = 'score' + (p.score>=5?' hi':p.score>=3?' mid':'');
  sc.textContent = p.score;

  const body = document.createElement('div'); body.style.flex='1';
  const title = `<div class="c-title">${esc(p.title)}</div>
    <div class="c-src">${esc(p.source_name)}${p.pub_date?' · '+p.pub_date:''}${p.authors?' · '+esc(p.authors.split(',').slice(0,3).join(','))+(p.authors.split(',').length>3?' et al.':''):''}</div>`;

  // 徽章
  let badges = '';
  if(p.explore) badges += `<span class="badge b-explore">🧭 探索</span>`;   // PRPM 探索位（缺欄位=fallback，不掛）
  if(p.isNew) badges += `<span class="badge b-new">✨ NEW</span>`;
  if(p.oa_pdf_url)   // 只在真的抓得到 OA PDF 時掛 🟢；標成 OA 卻下載不到的不掛假標
    badges += `<a class="badge ${p.oaNew?'b-oanew':'b-oa'}" href="${p.oa_pdf_url}" target="_blank">${p.oaNew?'🆕🟢 新開放':'🟢 OA PDF'}</a>`;
  if(p.inst_subscribed===1)
    badges += `<a class="badge b-tz" href="${p.sfx_url}" target="_blank">🏥 機構訂閱${p.inst_platforms?' '+esc(p.inst_platforms):''}</a>`;
  else if(p.sfx_url)
    badges += `<a class="badge b-tag" href="${p.sfx_url}" target="_blank">🔎 SFX</a>`;
  for(const t of (p.tags||[]).filter(t=>!/^(neg|penalty|design|author):/.test(t)).slice(0,4))
    badges += `<span class="badge b-tag">${esc(t)}</span>`;

  body.innerHTML = title + `<div class="badges">${badges}</div>` +
    (p.abstract?`<div class="abs" id="abs-${p.item_id}">${esc(p.abstract)}</div>`:'');

  // 動作鈕：✅已看(左) | 🔬品質 | 📚內容 | 👍😐👎 | 📎PDF
  // 🔬/📚 任一顆 = /paper-sync 跑共用前置(DOI核對+Zotero+抓全文)後分流；按任一鈕都隱含標 seen
  const acts = document.createElement('div'); acts.className='acts';
  acts.appendChild(actBtn('✅ 已看','seen',!!a.seen,()=>toggle(p,'seen')));
  acts.appendChild(actBtn('🔬 品質','deepread',!!a.deepread,()=>toggleAct(p,'deepread')));
  acts.appendChild(actBtn('📚 內容','content',!!a.content,()=>toggleAct(p,'content')));
  acts.appendChild(actBtn('👍','vote up',a.vote==='up',()=>setVote(p,'up')));
  acts.appendChild(actBtn('😐','vote neutral',a.vote==='neutral',()=>setVote(p,'neutral')));
  acts.appendChild(actBtn('👎','vote down',a.vote==='down',()=>setVote(p,'down')));
  const upBtn = document.createElement('button');
  upBtn.className = 'act upload'; upBtn.textContent = '📎 上傳PDF';
  upBtn.onclick = () => uploadForPaper(p, upBtn);
  acts.appendChild(upBtn);
  body.appendChild(acts);

  body.querySelector('.c-title').onclick = () => {
    const ab = document.getElementById('abs-'+p.item_id);
    if(ab) ab.classList.toggle('show');
  };

  // why 分數分解：僅在有 p.why 時讓分數 chip 可點，懶建立面板（fallback payload 無 why → 不可點）
  if(Array.isArray(p.why) && p.why.length){
    sc.classList.add('clickable');
    sc.onclick = () => {
      let panel = body.querySelector('.why');
      if(!panel){
        panel = document.createElement('div');
        panel.className = 'why';
        const rows = p.why.map(([label, c]) => {
          const v = (typeof c === 'number') ? c : 0;
          const sign = v > 0 ? '+' : (v < 0 ? '−' : '');
          const num = v === 0 ? '' : `<span class="why-n ${v>0?'pos':'neg'}">${sign}${Math.abs(v).toFixed(1)}</span>`;
          return `<div class="why-row"><span class="why-l">${esc(label)}</span>${num}</div>`;
        }).join('');
        const kw = (p.kw_score != null)
          ? `<div class="why-row why-dim"><span class="why-l">關鍵字分數</span><span class="why-n">${esc(String(p.kw_score))}</span></div>`
          : '';
        panel.innerHTML = rows + kw;
        body.appendChild(panel);
      } else {
        panel.classList.toggle('hidden');
      }
    };
  }

  el.appendChild(sc); el.appendChild(body);
  return el;
}

function actBtn(label, cls, on, fn){
  const b = document.createElement('button');
  b.className = 'act ' + cls + (on?' on':'');
  b.textContent = label;
  b.onclick = () => { fn(); render(); };
  return b;
}

function setVote(p, v){
  const a = actions[p.item_id] || (actions[p.item_id]={});
  a.vote = (a.vote===v) ? null : v;
  persist(p, 'vote', a.vote);
  markSeen(p);   // 投票也算看過
}
function toggle(p, key){
  const a = actions[p.item_id] || (actions[p.item_id]={});
  a[key] = !a[key];
  persist(p, key, a[key]);
}
// 標 seen（不重複送）。本 session 內 passSeen 不隱藏，下次重整才消失
function markSeen(p){
  const a = actions[p.item_id] || (actions[p.item_id]={});
  if(!a.seen){ a.seen = true; persist(p, 'seen', true); }
}
// 🔬品質 / 📚內容：toggle 後一律標 seen
function toggleAct(p, key){ toggle(p, key); markSeen(p); }
function persist(p, key, val){
  save(LS_ACT, actions);
  // activity-based 待同步計數：任何動作寫入都會讓 server synced=0，
  // 本地立即依「是否還有實際動作」增減 pendingIds（純 seen 不計）
  const a = actions[p.item_id] || {};
  const actionable = a.vote || a.deepread || a.content || a.pdf;
  if(actionable) pendingIds.add(p.item_id); else pendingIds.delete(p.item_id);
  updateSyncBanner();
  // best-effort 推 Worker(step 4)；失敗純本地保留，/paper-sync 之後可補
  fetch(API, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({item_id:p.item_id, doi:p.doi, title:p.title, key, val,
      ctx:{explore: !!p.explore, rank: p.rank ?? null}})}).catch(()=>{});
}
// per-card 全文上傳（綁該篇 item_id；給機構沒訂、非 OA、你手上有 PDF 的論文）
function uploadForPaper(p, btn){
  const inp = document.createElement('input');
  inp.type = 'file'; inp.accept = 'application/pdf';
  inp.onchange = async () => {
    const f = inp.files[0]; if(!f) return;
    if(f.type !== 'application/pdf'){ btn.textContent = '只接受PDF'; return; }
    btn.textContent = '⏳ 上傳中';
    const qs = new URLSearchParams({ item_id: p.item_id, doi: p.doi || '', title: p.title || '' });
    try{
      const r = await fetch('/api/upload?' + qs.toString(),
        { method:'POST', headers:{'Content-Type':'application/pdf'}, body: f });
      const j = await r.json();
      btn.textContent = r.ok ? '✅ 已上傳' : `✗ ${j.error||'失敗'}`;
      if(r.ok){
        const a = actions[p.item_id] || (actions[p.item_id]={});
        a.pdf = true; save(LS_ACT, actions);
        pendingIds.add(p.item_id); updateSyncBanner();
      }
    }catch(e){ btn.textContent = '✗ 失敗'; }
  };
  inp.click();
}

function esc(s){ return (s||'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

init();
