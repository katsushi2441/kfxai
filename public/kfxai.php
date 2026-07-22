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

// タブ/投資家選択はURLで持つ(kfreqaiと同じ・実リンクで遷移するのでJSに依存せず必ず選べる)。
// ?view=arena=一覧、?agent=A=投資家Aのメイン画面。指定なし=本番。
$kfxai_view  = (isset($_GET['view']) && $_GET['view'] === 'arena') ? 'arena' : 'honban';
$kfxai_agent = isset($_GET['agent']) ? preg_replace('/[^A-Za-z0-9_\-]/', '', (string) $_GET['agent']) : '';
if ($kfxai_agent !== '') { $kfxai_view = 'agent'; }
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
  .tabbtn { display:inline-block; border:none; background:none; padding:10px 16px; font-size:14px; font-weight:700; color:var(--muted); cursor:pointer; border-bottom:2px solid transparent; margin-bottom:-1px; text-decoration:none; }
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
        <div class="card"><div class="label" id="pnlLabel">paper累計損益</div><div class="value" id="pnl">¥0</div></div>
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

    <!-- タブは本番/アリーナの2つ(kfreqaiと同じ)。実リンクでURL遷移するのでJSに依存せず必ず選べる。
         アリーナで投資家名を押すと、本番と同じ画面がその投資家のデータで開く(ドリルイン)。 -->
    <div class="tabs">
      <a class="tabbtn <?php echo $kfxai_view === 'honban' ? 'active' : ''; ?>" href="?">本番（メイン戦略）</a>
      <a class="tabbtn <?php echo ($kfxai_view === 'arena' || $kfxai_view === 'agent') ? 'active' : ''; ?>" href="?view=arena">アリーナ（戦略エージェント）</a>
    </div>

    <!-- 投資家を選択中のバナー(kfreqaiと同じ) -->
    <div id="agentBanner" style="<?php echo $kfxai_view === 'agent' ? '' : 'display:none;'; ?>background:rgba(0,172,193,.10);border:1px solid rgba(0,172,193,.45);border-radius:10px;padding:10px 16px;margin-bottom:16px;font-size:14px"></div>

    <!-- メイン画面: 本番と、選択した投資家で共用(kfreqaiと同じ・データだけ差し替わる) -->
    <div id="pane-main" class="pane" style="<?php echo $kfxai_view === 'arena' ? 'display:none;' : ''; ?>">
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
      <section>
        <h2>直近の判断（市場共通・全レーンで共有）</h2>
        <div class="tscroll"><table>
          <thead><tr><th>時刻</th><th>通貨ペア</th><th>判断</th><th>P(up)</th><th>スプレッド</th><th>実行</th><th>理由</th></tr></thead>
          <tbody id="decisions"><tr><td colspan="7">読み込み中</td></tr></tbody>
        </table></div>
      </section>
    </div>

    <!-- アリーナ一覧: 投資家リーダーボード。名前クリックで上のメイン画面をその投資家に切替 -->
    <div id="pane-arena" class="pane" style="<?php echo $kfxai_view === 'arena' ? '' : 'display:none;'; ?>">
      <section>
        <h2>投資家レーン（本番の横で並列に試行 — 各: 枠3・予算30万円・DD10%で新規停止）</h2>
        <p style="font-size:12px;color:var(--muted);line-height:1.7;margin:-4px 0 10px">別々の投資家が複数戦略を回す実験レーン(名前に意味はなく中身は進化する)。<b>投資家名をクリックすると、本番と同じ画面でその投資家のデータを表示</b>します。良いロジックは本番へ昇格し、そのレーンは「停止(手動)」= 新規取引せず過去成績のみ残す会計用レーンになります。<b>上部の累計損益 = 本番 + 全レーン(停止含む)の累計損益の合計</b>です。</p>
        <div class="tscroll"><table>
          <thead><tr><th>投資家</th><th>状態</th><th>残高</th><th>収益率</th><th>本日</th><th>決済数</th><th>勝率</th><th>累計損益</th><th>枠(使用/上限)</th></tr></thead>
          <tbody id="leaderboard"><tr><td colspan="9">読み込み中</td></tr></tbody>
        </table></div>
      </section>
    </div>

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
    // 上部カードはアクティブなレーン(本番+アリーナA/B/C)のみを合算する。paper_tradesには
    // 過去の単体戦略(session/ma_cross等=legacy)の履歴も残るが、それらはproduction/arenaの
    // どちらでもないので除外する。全体合算だと死んだ戦略の損失を引きずり、稼働中レーンの成績と
    // 実態が食い違って見えるため(下のレーン別内訳と符合させる)。単独モードはフラグ未設定なので
    // その場合のみ従来どおりperformance全体にフォールバックする。
    const activeLanes=(d.strategy_performance||[]).filter(x=>x.production||x.arena===true);
    const useAgg=activeLanes.length>0;
    const laneCount=activeLanes.length||1;
    const agg=activeLanes.reduce((a,x)=>{a.pnl+=Number(x.pnl_jpy)||0;a.trades+=Number(x.trades)||0;a.wins+=Number(x.wins)||0;return a;},{pnl:0,trades:0,wins:0});
    const perf=d.performance||{};
    const pnl=useAgg?agg.pnl:(perf.pnl_jpy||0);
    const pnlEl=document.querySelector('#pnl');pnlEl.textContent=yen.format(pnl);pnlEl.className='value '+pnlClass(pnl);
    document.querySelector('#pnlLabel').textContent=useAgg?`paper累計損益（本番+アリーナ ${laneCount}レーン合算）`:'paper累計損益';
    // ポジション枠は「実際に取引する稼働レーン」だけで合算する。停止レーンは新規取引しない=枠ゼロ
    // なので枠にも使用にも含めない(停止3レーンまで足して7レーン合算は無意味だった。2026-07-22修正)。
    const slotsEl=document.querySelector('#slots');const cap=d.max_positions;
    if(d.strategy_mode==='arena'){
      const tradingLanes=(d.strategy_performance||[]).filter(x=>x.production||(x.arena===true&&!x.stopped));
      const tradeLaneCount=tradingLanes.length||1;
      const usedActive=tradingLanes.reduce((s,x)=>s+(Number(x.open_now)||0),0);
      const totalCap=tradeLaneCount*(cap||0);
      slotsEl.textContent=`${usedActive} / ${totalCap} 使用（稼働${tradeLaneCount}レーン）`;
      slotsEl.className='value '+(totalCap&&usedActive>=totalCap?'down':'');
    }else{
      const used=(d.open_trades||[]).length;
      slotsEl.textContent=cap?`${used} / ${cap} 使用`:`${used} 使用`;
      slotsEl.className='value '+(cap&&used>=cap?'down':'');
    }
    document.querySelector('#instruments').innerHTML=(d.instruments||[]).map(x=>`<span class="chip">${esc(x)}</span>`).join('');
    document.querySelector('#cycleState').textContent=cycle?`cycle #${cycle.id} / ${cycle.status}`:'まだサイクル未実行';
    document.querySelector('#cycleDetail').textContent=cycle?`${time(cycle.started_at)}開始・${cycle.detail||'エラーなし'}`:'OANDA認証情報を設定しworkerを起動してください。';
    // 検証成績も上部カードと同じアクティブレーン合算に揃える(legacy戦略を含めない)。
    const recTrades=useAgg?agg.trades:(perf.closed_trades||0);
    const recWins=useAgg?agg.wins:(perf.wins||0);
    const recWinRate=recTrades?(recWins/recTrades*100):0;
    document.querySelector('#record').textContent=`${recTrades} trades / ${recWinRate.toFixed(1)}% win`;
    document.querySelector('#recordDetail').textContent=`wins ${recWins} / 累計損益 ${yen.format(useAgg?agg.pnl:(perf.pnl_jpy||0))}`;
    document.querySelector('#decisions').innerHTML=(d.recent_decisions||[]).slice(0,40).map(x=>`<tr><td>${time(x.created_at)}</td><td>${esc(x.instrument)}</td><td class="${esc(x.action)}">${esc(x.action)}</td><td>${Number(x.probability_up).toFixed(3)}</td><td>${x.spread_pips==null?'-':Number(x.spread_pips).toFixed(2)}</td><td>${x.executed?'YES':'NO'}</td><td title="${esc(x.reason)}">${esc(String(x.reason).slice(0,46))}</td></tr>`).join('')||'<tr><td colspan="7">判断履歴はまだありません。</td></tr>';
    // レーン(本番/投資家A/B/C)のタブ+選択レーンの画面を描画。データは全レーン分が
    // この1レスポンスに入っているので、タブ切替はクライアント側だけで完結する。
    LAST=d; applyView();
    const err=d.last_error?.error;box.style.display=err?'block':'none';box.textContent=err?`last error: ${err}`:'';
  }catch(error){
    document.querySelector('#systemState').textContent='OFFLINE';
    document.querySelector('#statusDot').classList.add('bad');
    box.style.display='block';box.textContent=`backend error: ${error.message}`;
  }
}
// タブ=本番/アリーナ(kfreqaiと同じ)。選択はURL(?view=arena / ?agent=A)で持ち、実リンクで
// 遷移するのでJSに依存せず必ず選べる。VIEW/AGENTはPHPが現在のURLから初期化する。
let LAST=null, VIEW=<?php echo json_encode($kfxai_view); ?>, AGENT=<?php echo json_encode($kfxai_agent); ?>;
function arenaInvestors(d){
  return (d.strategy_performance||[]).filter(x=>x.arena===true)
    .sort((a,b)=>String(a.strategy).localeCompare(String(b.strategy)));
}
function prodName(d){const p=(d.strategy_performance||[]).find(x=>x.production);return p?p.strategy:'本番';}
function laneRow(d,name){return (d.strategy_performance||[]).find(x=>x.strategy===name)||{};}

// 本番と選択投資家で共用するメイン画面(データだけ差し替え)
function renderMain(d){
  const laneName=(VIEW==='agent'&&AGENT)?AGENT:prodName(d);
  const row=laneRow(d,laneName);
  const cap=d.max_positions, budget=d.agent_budget_jpy;
  const isProd=!!row.production;
  const label=isProd?'本番':(row.strategy||laneName||'-');
  const subs=(row.subs&&row.subs.length)?row.subs.join(' + '):'-';
  document.querySelector('#laneCaption').innerHTML=isProd
    ? '本番レーン — 検証済みの本番戦略。アリーナで良い成果を出したロジックを昇格していく先。'
    : `投資家${esc(label)}（アリーナ）— 本番の横で並列に試行する実験レーン。予算${budget?yen.format(budget):'-'}・枠${cap??'-'}・DD10%で新規停止。名前A/B/Cに意味はなく中身は進化する。回している戦略: <b>${esc(subs)}</b>。`;
  const setv=(id,v,cls)=>{const e=document.querySelector(id);if(!e)return;e.textContent=v;if(cls!=null)e.className='value '+cls;};
  const wr=row.trades?Math.round(100*row.wins/row.trades):null;
  setv('#lnEquity',row.equity_jpy==null?'-':yen.format(row.equity_jpy));
  setv('#lnReturn',(row.return_pct==null?'-':row.return_pct.toFixed(2)+'%'),pnlClass(row.return_pct));
  setv('#lnToday',yen.format(row.today_pnl||0),pnlClass(row.today_pnl));
  setv('#lnPnl',yen.format(row.pnl_jpy||0),pnlClass(row.pnl_jpy));
  setv('#lnRecord',`${row.trades||0} / ${wr==null?'-':wr+'%'}`);
  setv('#lnSlots',`${row.open_now||0} / ${row.max_positions??cap??'-'}`,((row.open_now||0)>=(row.max_positions??cap)?'down':''));
  document.querySelector('#laneLedgerTitle').textContent=`${label}の取引台帳`;
  const tradeRow=x=>`<tr><td>${x.id}</td><td>${esc(x.instrument)}</td><td class="${esc((x.side||'').toLowerCase())}">${esc(x.side)}</td><td>${esc(x.status)}</td><td>${time(x.open_time)}</td><td>${time(x.close_time)}</td><td>${Number(x.open_price).toFixed(5)}</td><td>${x.close_price==null?'-':Number(x.close_price).toFixed(5)}</td><td class="${pnlClass(x.pnl_jpy)}">${x.pnl_jpy==null?'-':yen.format(x.pnl_jpy)}</td><td>${esc(x.exit_reason||'-')}</td></tr>`;
  const mine=(d.recent_trades||[]).filter(x=>x.strategy===laneName);
  document.querySelector('#laneTrades').innerHTML=mine.slice(0,40).map(tradeRow).join('')||`<tr><td colspan="10">${esc(label)}の取引はまだありません。</td></tr>`;
}

// アリーナ一覧(投資家リーダーボード)。名前クリックでメイン画面をその投資家に切替。
function renderArena(d){
  const cap=d.max_positions;
  const invs=arenaInvestors(d);
  document.querySelector('#leaderboard').innerHTML=invs.map(x=>{
    const wr=x.trades?Math.round(100*x.wins/x.trades):null;const st=x.status||'active';
    const stopped=(st==='stopped');
    const book=(x.subs&&x.subs.length)?x.subs.join('+'):(stopped?'(停止・会計のみ)':'');
    const stTxt=stopped?'停止(手動)':(st==='suspended'?'停止(DD超過)':'稼働中');
    const stCls=(st==='active')?'up':'down';
    const slots=stopped?'-':`${x.open_now||0} / ${x.max_positions??cap??'-'}`;
    return `<tr${stopped?' style="opacity:.6"':''}><td><a href="?agent=${encodeURIComponent(x.strategy)}" style="font-weight:700;color:var(--indigo);text-decoration:none">▶ ${esc(x.strategy)}</a>${book?` <span style="opacity:.65;font-size:11px">${esc(book)}</span>`:''}</td><td class="${stCls}">${stTxt}</td><td>${x.equity_jpy==null?'-':yen.format(x.equity_jpy)}</td><td class="${pnlClass(x.return_pct)}">${x.return_pct==null?'-':x.return_pct.toFixed(2)+'%'}</td><td class="${pnlClass(x.today_pnl)}">${yen.format(x.today_pnl||0)}</td><td>${x.trades}</td><td>${wr==null?'-':wr+'%'}</td><td class="${pnlClass(x.pnl_jpy)}">${yen.format(x.pnl_jpy||0)}</td><td>${slots}</td></tr>`;
  }).join('')||'<tr><td colspan="9">まだ取引がありません。</td></tr>';
}

function applyView(){
  const d=LAST; if(!d) return;
  const showMain=(VIEW==='honban'||VIEW==='agent');
  document.querySelector('#pane-main').style.display=showMain?'':'none';
  document.querySelector('#pane-arena').style.display=(VIEW==='arena')?'':'none';
  document.querySelectorAll('.tabbtn').forEach(b=>{const t=b.dataset.tab;
    b.classList.toggle('active',(t==='honban'&&VIEW==='honban')||(t==='arena'&&(VIEW==='arena'||VIEW==='agent')));});
  const banner=document.querySelector('#agentBanner');
  if(VIEW==='agent'&&AGENT){
    const row=laneRow(d,AGENT);const book=(row.subs&&row.subs.length)?row.subs.join('+'):'-';
    banner.style.display='block';
    banner.innerHTML=`🏟 アリーナの投資家 <b>${esc(AGENT)}</b>（戦略: ${esc(book)}・予算${yen.format(d.agent_budget_jpy||0)}・枠${d.max_positions}）を表示中 — <a href="?view=arena" style="color:var(--indigo);font-weight:700">アリーナ一覧へ</a> / <a href="?" style="color:var(--indigo);font-weight:700">本番に戻る</a>`;
  }else{ banner.style.display='none'; }
  if(showMain) renderMain(d);
  if(VIEW==='arena') renderArena(d);
}
refresh();setInterval(refresh,15000);
</script>
</body>
</html>
