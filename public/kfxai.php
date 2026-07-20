<?php
// Public read-only dashboard. The trading control API remains disabled in kfxai/.env.
// config.php(kfreqaiと共通・同ディレクトリ)があればブログAPIトークン等を利用する。
if (file_exists(__DIR__ . '/config.php')) { require_once __DIR__ . '/config.php'; }
$api_base = defined('KFXAI_API_BASE') ? KFXAI_API_BASE : 'http://exbridge.ddns.net:18324';

function kfxai_h($s) { return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8'); }

// Kurageブログ(Bludit共用)からcategory=kfxaiの記事だけを新しい順に返す。
function kfxai_latest_blog_posts($limit = 5) {
    if (!defined('KFREQAI_BLOG_BASE') || !defined('KFREQAI_BLOG_API_TOKEN')) { return array(); }
    $ch = curl_init(rtrim(KFREQAI_BLOG_BASE, '/') . '/api/categories/kfxai?token=' . urlencode(KFREQAI_BLOG_API_TOKEN));
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 8);
    curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 5);
    $res = curl_exec($ch);
    $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($res === false || $code >= 400) { return array(); }
    $data = json_decode($res, true);
    $pages = isset($data['data']['pages']) ? $data['data']['pages'] : array();
    return array_slice($pages, 0, $limit);
}

function kfxai_get_json($url) {
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 4);
    curl_setopt($ch, CURLOPT_TIMEOUT, 10);
    curl_setopt($ch, CURLOPT_HTTPHEADER, array('Accept: application/json'));
    $body = curl_exec($ch);
    $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);
    if ($body === false || $code < 200 || $code >= 300) {
        return array(null, $code, $error !== '' ? $error : 'backend unavailable');
    }
    $decoded = json_decode($body, true);
    if (!is_array($decoded)) { return array(null, 502, 'invalid backend response'); }
    return array($decoded, $code, '');
}

if (isset($_GET['api']) && $_GET['api'] === 'status') {
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store, max-age=0');
    list($data, $code, $error) = kfxai_get_json(rtrim($api_base, '/') . '/api/status');
    if ($data === null) {
        http_response_code(502);
        echo json_encode(array('error' => $error, 'backend_status' => $code), JSON_UNESCAPED_UNICODE);
        exit;
    }
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}
?>
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kurage FX AI Trade | OANDA FX運用ダッシュボード</title>
<meta name="description" content="OANDAの市場データを使うKurage FX AI Tradeの稼働状況、AI判断、paper取引成績を表示します。">
<meta name="robots" content="noindex,nofollow">
<meta property="og:title" content="Kurage FX AI Trade">
<meta property="og:description" content="AI判断と固定リスク制御を分離したOANDA FX運用システム。">
<meta property="og:type" content="website">
<meta property="og:url" content="https://kurage.exbridge.jp/kfxai.php">
<link rel="stylesheet" href="assets/kurage-avatar.css">
<style>
  :root {
    --indigo: #3949ab; --cyan: #00acc1; --bg: #f6f8fb; --card: #ffffff;
    --ink: #1c2536; --muted: #66748f; --border: #e3e8f0;
    --up: #1baf7a; --down: #d6453d;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: linear-gradient(180deg, #eef2fb 0%, var(--bg) 320px);
    color: var(--ink);
  }
  header {
    padding: 28px 20px 18px; max-width: 1080px; margin: 0 auto;
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;
  }
  header .brand { display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 20px; margin: 0; }
  header h1 span { color: var(--indigo); }
  header h1 small { display: block; font-size: 11px; font-weight: 700; letter-spacing: .18em; color: var(--cyan); margin-top: 3px; }
  .badge {
    display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
    margin-left: 8px; vertical-align: middle; background: #fff3cd; color: #8a6100;
  }
  .stamp { text-align: right; }
  .stamp .st-label { font-size: 11px; font-weight: 700; letter-spacing: .1em; color: var(--muted); }
  .stamp .st-state { font-size: 22px; font-weight: 700; }
  .stamp .st-time { font-size: 11px; color: var(--muted); }
  main { max-width: 1080px; margin: 0 auto; padding: 0 20px 60px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; margin-bottom: 24px; }
  .card {
    background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 16px 18px; min-width: 0;
  }
  .card .label { font-size: 12px; color: var(--muted); margin-bottom: 6px; letter-spacing: .03em; }
  .card .value { font-size: 22px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .card .sub { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .up { color: var(--up); } .down { color: var(--down); }
  section { margin-bottom: 28px; }
  .tabs { display:flex; gap:8px; margin:4px 0 20px; border-bottom:1px solid var(--border); }
  .tabbtn { border:none; background:none; padding:10px 16px; font-size:14px; font-weight:700; color:var(--muted); cursor:pointer; border-bottom:2px solid transparent; margin-bottom:-1px; }
  .tabbtn.active { color:var(--indigo); border-bottom-color:var(--indigo); }
  section h2 { font-size: 15px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; margin: 0 0 10px; }
  .twocol { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .panel { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 18px 20px; min-width: 0; }
  .panel h3 { margin: 0 0 12px; font-size: 14px; }
  .blog-links { list-style: none; padding: 0; margin: 0; background: var(--card); border: 1px solid var(--border); border-radius: 14px; overflow: hidden; }
  .blog-links li { padding: 12px 16px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; gap: 12px; }
  .blog-links li:last-child { border-bottom: none; }
  .blog-links a { color: var(--indigo); text-decoration: none; font-size: 14px; }
  .blog-links a:hover { text-decoration: underline; }
  .blog-date { font-size: 12px; color: var(--muted); white-space: nowrap; }
  .blog-more { font-size: 13px; margin-top: 10px; }
  .blog-more a { color: var(--cyan); text-decoration: none; font-weight: 700; }
  .statusline { display: flex; gap: 11px; align-items: flex-start; }
  .dot { width: 11px; height: 11px; flex: none; margin-top: 5px; border-radius: 50%; background: var(--up); box-shadow: 0 0 0 5px rgba(27,175,122,.14); }
  .dot.bad { background: var(--down); box-shadow: 0 0 0 5px rgba(214,69,61,.14); }
  .statusline strong { display: block; font-size: 15px; }
  .statusline span { display: block; color: var(--muted); font-size: 12px; line-height: 1.6; margin-top: 3px; }
  .chips { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 12px; }
  .chip { padding: 5px 10px; border: 1px solid var(--border); border-radius: 999px; background: #f4f7fc; font-size: 11px; font-weight: 600; color: var(--muted); }
  .tscroll { overflow-x: auto; border: 1px solid var(--border); border-radius: 12px; }
  table { width: 100%; border-collapse: collapse; background: var(--card); min-width: 640px; }
  th, td { text-align: left; padding: 10px 14px; font-size: 13px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  th { color: var(--muted); font-weight: 600; background: #f9fafc; position: sticky; top: 0; }
  tr:last-child td { border-bottom: none; }
  td.buy { color: var(--up); font-weight: 700; } td.sell { color: var(--down); font-weight: 700; } td.hold { color: var(--muted); }
  .error { display: none; background: #fde2e1; color: #a4201b; padding: 12px 16px; border-radius: 10px; font-size: 13px; margin-bottom: 20px; }
  footer { text-align: center; color: var(--muted); font-size: 12px; padding: 30px 20px; line-height: 1.8; }
  footer a { color: var(--indigo); text-decoration: none; font-weight: 600; }
  @media (max-width: 720px) { .twocol { grid-template-columns: 1fr; } header h1 { font-size: 18px; } }
</style>
</head>
<body>
  <header>
    <div class="brand">
      <span class="kurage-avatar-stage kurage-avatar-mini" role="img" aria-label="Kurage avatar"><span class="kurage-avatar-motion"><span class="kurage-avatar-breath"><img class="kurage-avatar-frame kurage-avatar-frame-0" src="avatar/lipsync/kurage_mouth_0.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-1" src="avatar/lipsync/kurage_mouth_1.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-2" src="avatar/lipsync/kurage_mouth_2.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-3" src="avatar/lipsync/kurage_mouth_3.png" alt=""><img class="kurage-avatar-frame kurage-avatar-frame-4" src="avatar/lipsync/kurage_mouth_4.png" alt=""></span></span></span>
      <h1><span>Kurage FX</span> AI Trade
        <span class="badge" id="modeBadge">PAPER（紙上取引）</span>
        <small>OSS BODY / METERED BRAIN</small>
      </h1>
    </div>
    <div class="stamp">
      <div class="st-label">SYSTEM STATE</div>
      <div class="st-state" id="systemState">CONNECTING</div>
      <div class="st-time" id="updatedAt">状態取得中</div>
    </div>
  </header>
  <main>
    <div class="error" id="errorBox"></div>

    <section>
      <div class="grid">
        <div class="card"><div class="label">モード</div><div class="value" id="mode">-</div></div>
        <div class="card"><div class="label">市場</div><div class="value" id="market">-</div></div>
        <div class="card"><div class="label">地合い判定</div><div class="value" id="regime">-</div></div>
        <div class="card"><div class="label">リスク方針</div><div class="value" id="directive">-</div></div>
        <div class="card"><div class="label">ポジション枠</div><div class="value" id="slots">-</div></div>
        <div class="card"><div class="label">paper累計損益</div><div class="value" id="pnl">¥0</div></div>
        <div class="card"><div class="label">判断エンジン</div><div class="value" id="brain">-</div></div>
      </div>
    </section>

    <section>
      <div class="twocol">
        <article class="panel">
          <h3>運転状況</h3>
          <div class="statusline">
            <div class="dot" id="statusDot"></div>
            <div><strong id="cycleState">データ待機中</strong><span id="cycleDetail">バックエンドへ接続しています。</span></div>
          </div>
          <div class="chips" id="instruments"></div>
        </article>
        <article class="panel">
          <h3>検証成績</h3>
          <div class="statusline">
            <div class="dot"></div>
            <div><strong id="record">0 trades / 0.0% win</strong><span id="recordDetail">決済済みpaper取引を集計します。</span></div>
          </div>
          <div class="chips">
            <span class="chip">AIは注文量を変更不可</span>
            <span class="chip">日次損失上限</span>
            <span class="chip">スプレッド制限</span>
            <span class="chip">週末停止</span>
          </div>
        </article>
      </div>
    </section>

    <!-- レーン選択タブ(本番/投資家A/B/C)。選ぶと下の画面全体が選択レーンのデータになる(kfreqaiと同じUX)。 -->
    <div class="tabs" id="laneTabs"><button type="button" class="tabbtn active" data-lane="本番">本番</button></div>

    <div id="pane-lane" class="pane">
      <p id="laneCaption" style="font-size:12px;color:var(--muted);line-height:1.7;margin:-4px 0 12px"></p>
      <section>
        <div class="grid" id="laneCards">
          <div class="card"><div class="label">残高</div><div class="value" id="lnEquity">-</div></div>
          <div class="card"><div class="label">収益率</div><div class="value" id="lnReturn">-</div></div>
          <div class="card"><div class="label">本日損益</div><div class="value" id="lnToday">¥0</div></div>
          <div class="card"><div class="label">累計損益</div><div class="value" id="lnPnl">¥0</div></div>
          <div class="card"><div class="label">決済数 / 勝率</div><div class="value" id="lnRecord">-</div></div>
          <div class="card"><div class="label">ポジション枠</div><div class="value" id="lnSlots">-</div></div>
        </div>
      </section>
      <section>
        <h2 id="laneLedgerTitle">取引台帳</h2>
        <div class="tscroll"><table>
          <thead><tr><th>ID</th><th>通貨ペア</th><th>方向</th><th>状態</th><th>建玉日時(JST)</th><th>決済日時(JST)</th><th>建値</th><th>決済値</th><th>損益</th><th>理由</th></tr></thead>
          <tbody id="laneTrades"><tr><td colspan="10">読み込み中</td></tr></tbody>
        </table></div>
      </section>
    </div>

    <section>
      <h2>直近の判断（市場共通・全レーンで共有）</h2>
      <div class="tscroll"><table>
        <thead><tr><th>時刻</th><th>通貨ペア</th><th>判断</th><th>P(up)</th><th>スプレッド</th><th>実行</th><th>理由</th></tr></thead>
        <tbody id="decisions"><tr><td colspan="7">読み込み中</td></tr></tbody>
      </table></div>
    </section>

    <section>
      <h2>最新記事（Kurage 暗号資産/FX AI 自動取引日記）</h2>
      <?php $kfxai_blog_posts = kfxai_latest_blog_posts(5); ?>
      <?php if (empty($kfxai_blog_posts)): ?>
        <p class="blog-more">記事の取得に失敗したか、まだ記事がありません。
          <a href="https://kurage.exbridge.jp/blog/category/kfxai">ブログで見る →</a></p>
      <?php else: ?>
      <ul class="blog-links">
        <?php foreach ($kfxai_blog_posts as $p): ?>
        <li><a href="<?php echo kfxai_h(isset($p['permalink']) ? $p['permalink'] : '#'); ?>"><?php echo kfxai_h(isset($p['title']) ? $p['title'] : '(無題)'); ?></a>
          <span class="blog-date"><?php echo kfxai_h(isset($p['date']) ? $p['date'] : ''); ?></span></li>
        <?php endforeach; ?>
      </ul>
      <p class="blog-more"><a href="https://kurage.exbridge.jp/blog/category/kfxai">kfxaiの記事一覧を見る →</a></p>
      <?php endif; ?>
    </section>

    <footer>
      <a href="https://kurage.exbridge.jp/">Kurageプロジェクト</a> / Kurage FX AI Trade<br>
      投資助言ではありません。レバレッジ取引には元本を超える損失リスクがあります。
    </footer>
  </main>
<script>
const yen=new Intl.NumberFormat('ja-JP',{style:'currency',currency:'JPY',maximumFractionDigits:0});
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// 表示は閲覧環境に関わらず常に日本時間(JST)
const time=v=>v?new Date(v).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'-';
const pnlClass=n=>Number(n)>0?'up':(Number(n)<0?'down':'');
async function refresh(){
  const box=document.querySelector('#errorBox');
  try{
    const response=await fetch('kfxai.php?api=status',{cache:'no-store'});
    const d=await response.json();
    if(!response.ok)throw new Error(d.error||`HTTP ${response.status}`);
    const cycle=d.recent_cycles?.[0];
    document.querySelector('#systemState').textContent=cycle?.status==='failed'?'ATTENTION':'RUNNING';
    document.querySelector('#statusDot').classList.toggle('bad',cycle?.status==='failed');
    document.querySelector('#updatedAt').textContent=`updated ${time(d.time)}`;
    if(d.mode){const badge=document.querySelector('#modeBadge');badge.textContent=String(d.mode).toLowerCase()==='live'?'LIVE':'PAPER（紙上取引）';}
    document.querySelector('#mode').textContent=`${d.mode||'-'} / ${d.environment||'-'}`;
    document.querySelector('#market').textContent=d.market_open?'OPEN':'CLOSED';
    document.querySelector('#regime').textContent=d.regime?.regime||'-';
    document.querySelector('#directive').textContent=d.directive?.directive||'-';
    document.querySelector('#brain').textContent=d.backend||'-';
    // 上部の枠・累計損益はシステム全体(全レーン合算)。レーン別の内訳は下のタブで見る。
    const pnl=d.performance?.pnl_jpy||0;
    const pnlEl=document.querySelector('#pnl');pnlEl.textContent=yen.format(pnl);pnlEl.className='value '+pnlClass(pnl);
    const slotsEl=document.querySelector('#slots');const used=(d.open_trades||[]).length;const cap=d.max_positions;
    const laneCount=(d.strategy_performance||[]).filter(x=>x.production||x.arena===true).length||1;
    if(d.strategy_mode==='arena'){
      const totalCap=laneCount*(cap||0);
      slotsEl.textContent=`${used} / ${totalCap} 使用（${laneCount}レーン合算）`;
      slotsEl.className='value '+(totalCap&&used>=totalCap?'down':'');
    }else{
      slotsEl.textContent=cap?`${used} / ${cap} 使用`:`${used} 使用`;
      slotsEl.className='value '+(cap&&used>=cap?'down':'');
    }
    document.querySelector('#instruments').innerHTML=(d.instruments||[]).map(x=>`<span class="chip">${esc(x)}</span>`).join('');
    document.querySelector('#cycleState').textContent=cycle?`cycle #${cycle.id} / ${cycle.status}`:'まだサイクル未実行';
    document.querySelector('#cycleDetail').textContent=cycle?`${time(cycle.started_at)}開始・${cycle.detail||'エラーなし'}`:'OANDA認証情報を設定しworkerを起動してください。';
    const p=d.performance||{};
    document.querySelector('#record').textContent=`${p.closed_trades||0} trades / ${((p.win_rate||0)*100).toFixed(1)}% win`;
    document.querySelector('#recordDetail').textContent=`wins ${p.wins||0} / 累計損益 ${yen.format(p.pnl_jpy||0)}`;
    document.querySelector('#decisions').innerHTML=(d.recent_decisions||[]).slice(0,40).map(x=>`<tr><td>${time(x.created_at)}</td><td>${esc(x.instrument)}</td><td class="${esc(x.action)}">${esc(x.action)}</td><td>${Number(x.probability_up).toFixed(3)}</td><td>${x.spread_pips==null?'-':Number(x.spread_pips).toFixed(2)}</td><td>${x.executed?'YES':'NO'}</td><td title="${esc(x.reason)}">${esc(String(x.reason).slice(0,46))}</td></tr>`).join('')||'<tr><td colspan="7">判断履歴はまだありません。</td></tr>';
    // レーン(本番/投資家A/B/C)のタブ+選択レーンの画面を描画。データは全レーン分が
    // この1レスポンスに入っているので、タブ切替はクライアント側だけで完結する。
    LAST=d; renderLanes();
    const err=d.last_error?.error;box.style.display=err?'block':'none';box.textContent=err?`last error: ${err}`:'';
  }catch(error){
    document.querySelector('#systemState').textContent='OFFLINE';
    document.querySelector('#statusDot').classList.add('bad');
    box.style.display='block';box.textContent=`backend error: ${error.message}`;
  }
}
// レーン選択(本番/投資家A/B/C)。選ぶと画面全体が選択レーンのデータになる(kfreqaiと同じUX)。
let LAST=null, LANE=null;
function laneRows(d){
  // 本番→投資家(A/B/C)の順。過去の単体戦略(session等・arena===false)はタブに出さない。
  const perf=d.strategy_performance||[];
  const inv=perf.filter(x=>x.arena===true).sort((a,b)=>String(a.strategy).localeCompare(String(b.strategy)));
  return perf.filter(x=>x.production).concat(inv);
}
function renderLanes(){
  const d=LAST; if(!d) return;
  const lanes=laneRows(d);
  const names=lanes.map(x=>x.strategy);
  if(LANE===null||!names.includes(LANE)) LANE=names[0]||'本番';
  // タブ生成(本番/A/B/C)
  const tabs=document.querySelector('#laneTabs');
  tabs.innerHTML=lanes.map(x=>{const lbl=x.production?'本番':x.strategy;return `<button type="button" class="tabbtn${x.strategy===LANE?' active':''}" data-lane="${esc(x.strategy)}">${esc(lbl)}</button>`;}).join('')||'<button type="button" class="tabbtn active" data-lane="本番">本番</button>';
  tabs.querySelectorAll('.tabbtn').forEach(b=>b.addEventListener('click',()=>{LANE=b.dataset.lane;renderLanes();}));
  const row=lanes.find(x=>x.strategy===LANE)||lanes[0]||{};
  const cap=d.max_positions, budget=d.agent_budget_jpy;
  const label=row.production?'本番':(row.strategy||'-');
  // 説明文
  const subs=(row.subs&&row.subs.length)?row.subs.join(' + '):'-';
  document.querySelector('#laneCaption').innerHTML=row.production
    ? '本番レーン — 検証済みの本番戦略。アリーナで良い成果を出したロジックを昇格していく先。'
    : `投資家${esc(label)}（アリーナ）— 本番の横で並列に試行する実験レーン。予算${budget?yen.format(budget):'-'}・枠${cap??'-'}・DD10%で新規停止。名前A/B/Cに意味はなく中身は進化する。回している戦略: <b>${esc(subs)}</b>。成績は投資家単位で評価。`;
  // 上部カード(選択レーン)
  const setv=(id,v,cls)=>{const e=document.querySelector(id);if(!e)return;e.textContent=v;if(cls!=null)e.className='value '+cls;};
  const wr=row.trades?Math.round(100*row.wins/row.trades):null;
  setv('#lnEquity',row.equity_jpy==null?'-':yen.format(row.equity_jpy));
  setv('#lnReturn',(row.return_pct==null?'-':row.return_pct.toFixed(2)+'%'),pnlClass(row.return_pct));
  setv('#lnToday',yen.format(row.today_pnl||0),pnlClass(row.today_pnl));
  setv('#lnPnl',yen.format(row.pnl_jpy||0),pnlClass(row.pnl_jpy));
  setv('#lnRecord',`${row.trades||0} / ${wr==null?'-':wr+'%'}`);
  setv('#lnSlots',`${row.open_now||0} / ${row.max_positions??cap??'-'}`,((row.open_now||0)>=(row.max_positions??cap)?'down':''));
  // 状態バッジ(停止/稼働)を残高カードに反映しないが、説明で補足済み
  document.querySelector('#laneLedgerTitle').textContent=`${label}の取引台帳`;
  // 台帳: 選択レーンのstrategyで絞る
  const tradeRow=x=>`<tr><td>${x.id}</td><td>${esc(x.instrument)}</td><td class="${esc((x.side||'').toLowerCase())}">${esc(x.side)}</td><td>${esc(x.status)}</td><td>${time(x.open_time)}</td><td>${time(x.close_time)}</td><td>${Number(x.open_price).toFixed(5)}</td><td>${x.close_price==null?'-':Number(x.close_price).toFixed(5)}</td><td class="${pnlClass(x.pnl_jpy)}">${x.pnl_jpy==null?'-':yen.format(x.pnl_jpy)}</td><td>${esc(x.exit_reason||'-')}</td></tr>`;
  const mine=(d.recent_trades||[]).filter(x=>x.strategy===LANE);
  document.querySelector('#laneTrades').innerHTML=mine.slice(0,40).map(tradeRow).join('')||`<tr><td colspan="10">${esc(label)}の取引はまだありません。</td></tr>`;
}
refresh();setInterval(refresh,15000);
</script>
</body>
</html>
