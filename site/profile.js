// 偏好儀表板 — 讀 profile.json（PRPM v2 模型自省輸出），純靜態渲染。
// 管線尚未跑 / 檔案缺失 → 顯示待訓練訊息，不崩潰。
const $ = (s) => document.querySelector(s);
function esc(s){ return String(s||'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function pct(x){ return Math.round(Math.max(0, Math.min(1, x)) * 100); }

async function load(){
  let d = null;
  try{
    const r = await fetch('profile.json', {cache:'no-cache'});
    if(!r.ok) throw new Error('http ' + r.status);
    d = await r.json();
  }catch(e){
    $('#dash').innerHTML = `<div class="rev-done">模型尚未訓練 — 明早管線跑完後出現<br>
      <span class="dim">（每晚主機 cron 跑完 train_model + rank 才會產生 profile.json）</span></div>`;
    $('#meta').textContent = '尚未訓練';
    return;
  }
  render(d);
}

// 一列水平長條（top / avoid / 沿用 .hbar-row）
function hbar(x, {red=false} = {}){
  // 偏好強度：|mean − 0.5| × 2 → 0..1；label 左、bar 中、n 右
  const w = pct(Math.abs((x.mean ?? 0.5) - 0.5) * 2);
  const color = red ? '#6a2a2a' : '#2a5a66';
  return `<div class="hbar-row">
    <span class="hbar-l">${esc(x.label || x.f || '')}</span>
    <div class="hbar" style="width:${w}%;background:${color}"></div>
    <span class="hbar-n">${esc(String(x.n ?? 0))}</span></div>`;
}

function moveRow(x, up){
  const arrow = up ? '↗' : '↘';
  const cls = up ? 'pos' : 'neg';
  const dv = (typeof x.delta === 'number') ? x.delta : 0;
  const sign = dv > 0 ? '+' : (dv < 0 ? '−' : '');
  return `<div class="hbar-row">
    <span class="hbar-l">${esc(x.label || x.f || '')}</span>
    <span class="move ${cls}">${arrow} ${sign}${Math.abs(dv).toFixed(3)}</span>
    <span class="hbar-n">${esc(String(x.n ?? 0))}</span></div>`;
}

function render(d){
  const sig = d.signals || {};
  const tile = (n, l) => `<div class="card stat-tile"><div class="stat-n">${n ?? 0}</div><div class="stat-l">${l}</div></div>`;
  const tiles = `<div class="stat-tiles st5">
    ${tile(sig.engaged, '投入')}${tile(sig.up, '👍')}${tile(sig.down, '👎')}
    ${tile(sig.neutral, '😐')}${tile(sig.seen_only, '僅看過')}</div>`;

  const top = d.top || [], avoid = d.avoid || [], rising = d.rising || [], falling = d.falling || [];
  const topCard = `<div class="card"><div class="c-title">最偏好的特徵</div>
    ${top.length ? top.map(x => hbar(x)).join('') : '<div class="rev-note">還沒有明顯偏好。</div>'}</div>`;
  const avoidCard = `<div class="card"><div class="c-title">想避開的特徵</div>
    ${avoid.length ? avoid.map(x => hbar(x, {red:true})).join('') : '<div class="rev-note">還沒有明顯排斥。</div>'}</div>`;

  const moved = rising.map(x => moveRow(x, true)).concat(falling.map(x => moveRow(x, false)));
  const moveCard = `<div class="card"><div class="c-title">偏好變化（近 30 天 vs 全期）</div>
    ${moved.length ? moved.join('') : '<div class="rev-note">偏好還很穩定，沒有明顯漂移。</div>'}</div>`;

  const ex = d.explore || {};
  const shown = ex.shown ?? 0, engaged = ex.engaged ?? 0;
  const hit = shown ? Math.round(engaged / shown * 100) : 0;
  const exCard = `<div class="card"><div class="c-title">探索成效</div>
    <div class="stat-tiles st3">
      ${tile(shown, '探索曝光')}${tile(engaged, '有互動')}
      <div class="card stat-tile"><div class="stat-n">${hit}%</div><div class="stat-l">命中率</div></div>
    </div>
    <div class="rev-note">探索位（🧭）推非典型論文，命中率量測跳出同溫層的收穫。</div></div>`;

  $('#dash').innerHTML = tiles + topCard + avoidCard + moveCard + exCard;
  $('#meta').textContent = `更新 ${esc(d.updated || '—')}`;
  $('#foot').textContent = `更新 ${d.updated || '—'}｜特徵數 ${d.n_features ?? 0}` +
    (d.has_profile_vec ? '｜含語意向量' : '');
}

load();
