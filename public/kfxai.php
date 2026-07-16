<?php
// Public read-only dashboard. The trading control API remains disabled in kfxai/.env.
$api_base = defined('KFXAI_API_BASE') ? KFXAI_API_BASE : 'http://exbridge.ddns.net:18324';

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
<style>
:root{--ink:#123640;--muted:#61767b;--paper:#fffdf8;--aqua:#14a8b6;--pale:#dff6f4;--coral:#f36f59;--line:#cee2df;--good:#24886f;--bad:#c85046}
*{box-sizing:border-box}html{background:#edf8f6}body{margin:0;min-height:100vh;color:var(--ink);font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif;background:radial-gradient(circle at 7% 4%,#c9f1ec 0,transparent 32rem),radial-gradient(circle at 96% 0,#fff0d5 0,transparent 28rem),linear-gradient(145deg,#fff 0,#f5fbf9 55%,#e7f5f3 100%)}
main{width:min(1460px,100%);margin:auto;padding:22px}.hero,.metric,.panel{border:1px solid rgba(18,54,64,.1);background:rgba(255,255,255,.86);box-shadow:0 16px 42px rgba(18,54,64,.075)}.hero{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:22px;align-items:end;padding:24px 27px;border-radius:28px}.kicker{font-size:12px;font-weight:900;letter-spacing:.2em;color:var(--aqua)}h1{font-size:clamp(34px,5vw,64px);line-height:.92;letter-spacing:-.055em;margin:9px 0 14px}h1 span{color:var(--coral)}.lead{max-width:810px;margin:0;color:var(--muted);font-size:14px;line-height:1.8;font-weight:700}.stamp{min-width:205px;padding:15px 18px;border:1px solid var(--line);border-radius:18px;background:var(--paper)}.stamp small,.metric span{display:block;color:var(--muted);font-size:11px;font-weight:900;letter-spacing:.08em}.stamp strong{display:block;font-size:27px;margin:3px 0}.strip{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin:14px 0}.metric{padding:15px 16px;border-radius:18px;min-width:0}.metric strong{display:block;margin-top:6px;font-size:20px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.panel{padding:18px;border-radius:22px;min-width:0}.panel h2{margin:0 0 14px;font-size:17px}.status{display:flex;gap:11px;align-items:flex-start;padding:14px;border-radius:16px;background:linear-gradient(130deg,var(--pale),#fff)}.dot{width:12px;height:12px;flex:none;margin-top:5px;border-radius:50%;background:var(--good);box-shadow:0 0 0 5px rgba(36,136,111,.13)}.dot.bad{background:var(--bad);box-shadow:0 0 0 5px rgba(200,80,70,.12)}.status strong,.status span{display:block}.status span{color:var(--muted);font-size:12px;line-height:1.6;margin-top:3px}.chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}.chip{padding:6px 9px;border:1px solid var(--line);border-radius:999px;background:#f0f8f6;font-size:11px;font-weight:900}.table-wrap{overflow:auto;max-height:400px;border:1px solid #e1ecea;border-radius:14px}table{width:100%;min-width:700px;border-collapse:collapse;background:#fff}th,td{padding:10px 11px;border-bottom:1px solid #edf2f1;text-align:left;font-size:11px;white-space:nowrap}th{position:sticky;top:0;background:#f5faf9;color:var(--muted);z-index:1}.buy{color:var(--good);font-weight:900}.sell{color:var(--bad);font-weight:900}.hold{color:var(--muted)}.error{display:none;margin-top:12px;padding:11px;border-radius:12px;background:#fff1ed;color:var(--bad);font-size:12px}.footer{display:flex;justify-content:space-between;gap:18px;padding:15px 4px;color:var(--muted);font-size:11px}.footer a{color:var(--aqua);font-weight:900;text-decoration:none}
@media(max-width:1050px){.strip{grid-template-columns:repeat(3,1fr)}.grid{grid-template-columns:1fr}}@media(max-width:650px){main{padding:10px}.hero{grid-template-columns:1fr;padding:19px}.stamp{min-width:0}.strip{grid-template-columns:repeat(2,1fr)}h1{font-size:39px}.footer{display:block}.footer span{display:block;margin-top:6px}}
</style>
</head>
<body><main>
<header class="hero"><div><div class="kicker">OSS BODY / METERED BRAIN</div><h1>Kurage FX<br><span>AI Trade</span></h1><p class="lead">OANDAの市場データを使い、方向予測、地合い判断、固定リスク制御、取引後レビュー、再検証を継続するFX運用システムです。</p></div><div class="stamp"><small>SYSTEM STATE</small><strong id="systemState">CONNECTING</strong><small id="updatedAt">状態取得中</small></div></header>
<section class="strip"><div class="metric"><span>MODE</span><strong id="mode">-</strong></div><div class="metric"><span>MARKET</span><strong id="market">-</strong></div><div class="metric"><span>REGIME</span><strong id="regime">-</strong></div><div class="metric"><span>DIRECTIVE</span><strong id="directive">-</strong></div><div class="metric"><span>PAPER P&amp;L</span><strong id="pnl">¥0</strong></div><div class="metric"><span>BRAIN</span><strong id="brain">-</strong></div></section>
<section class="grid">
<article class="panel"><h2>運転状況</h2><div class="status"><div class="dot" id="statusDot"></div><div><strong id="cycleState">データ待機中</strong><span id="cycleDetail">バックエンドへ接続しています。</span></div></div><div class="chips" id="instruments"></div><div class="error" id="errorBox"></div></article>
<article class="panel"><h2>検証成績</h2><div class="status"><div class="dot"></div><div><strong id="record">0 trades / 0.0% win</strong><span id="recordDetail">決済済みpaper取引を集計します。</span></div></div><div class="chips"><span class="chip">AIは注文量を変更不可</span><span class="chip">日次損失上限</span><span class="chip">スプレッド制限</span><span class="chip">週末停止</span></div></article>
<article class="panel"><h2>直近の判断</h2><div class="table-wrap"><table><thead><tr><th>時刻</th><th>通貨</th><th>判断</th><th>P(up)</th><th>spread</th><th>実行</th><th>理由</th></tr></thead><tbody id="decisions"><tr><td colspan="7">読み込み中</td></tr></tbody></table></div></article>
<article class="panel"><h2>paper取引台帳</h2><div class="table-wrap"><table><thead><tr><th>ID</th><th>通貨</th><th>方向</th><th>状態</th><th>建値</th><th>決済</th><th>損益</th><th>理由</th></tr></thead><tbody id="trades"><tr><td colspan="8">読み込み中</td></tr></tbody></table></div></article>
</section>
<footer class="footer"><span><a href="https://kurage.exbridge.jp/">Kurageプロジェクト</a> / Kurage FX AI Trade</span><span>投資助言ではありません。レバレッジ取引には元本を超える損失リスクがあります。</span></footer>
</main><script>
const yen=new Intl.NumberFormat('ja-JP',{style:'currency',currency:'JPY',maximumFractionDigits:0});
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time=v=>v?new Date(v).toLocaleString('ja-JP',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'-';
async function refresh(){const box=document.querySelector('#errorBox');try{const response=await fetch('kfxai.php?api=status',{cache:'no-store'});const d=await response.json();if(!response.ok)throw new Error(d.error||`HTTP ${response.status}`);const cycle=d.recent_cycles?.[0];document.querySelector('#systemState').textContent=cycle?.status==='failed'?'ATTENTION':'RUNNING';document.querySelector('#updatedAt').textContent=`updated ${time(d.time)}`;document.querySelector('#mode').textContent=`${d.mode} / ${d.environment}`;document.querySelector('#market').textContent=d.market_open?'OPEN':'CLOSED';document.querySelector('#regime').textContent=d.regime?.regime||'-';document.querySelector('#directive').textContent=d.directive?.directive||'-';document.querySelector('#brain').textContent=d.backend||'-';document.querySelector('#pnl').textContent=yen.format(d.performance?.pnl_jpy||0);document.querySelector('#instruments').innerHTML=(d.instruments||[]).map(x=>`<span class="chip">${esc(x)}</span>`).join('');document.querySelector('#cycleState').textContent=cycle?`cycle #${cycle.id} / ${cycle.status}`:'まだサイクル未実行';document.querySelector('#cycleDetail').textContent=cycle?`${time(cycle.started_at)}開始・${cycle.detail||'エラーなし'}`:'OANDA認証情報を設定しworkerを起動してください。';const p=d.performance||{};document.querySelector('#record').textContent=`${p.closed_trades||0} trades / ${((p.win_rate||0)*100).toFixed(1)}% win`;document.querySelector('#recordDetail').textContent=`wins ${p.wins||0} / total P&L ${yen.format(p.pnl_jpy||0)}`;document.querySelector('#decisions').innerHTML=(d.recent_decisions||[]).slice(0,40).map(x=>`<tr><td>${time(x.created_at)}</td><td>${esc(x.instrument)}</td><td class="${esc(x.action)}">${esc(x.action)}</td><td>${Number(x.probability_up).toFixed(3)}</td><td>${x.spread_pips==null?'-':Number(x.spread_pips).toFixed(2)}</td><td>${x.executed?'YES':'NO'}</td><td title="${esc(x.reason)}">${esc(String(x.reason).slice(0,46))}</td></tr>`).join('')||'<tr><td colspan="7">判断履歴はまだありません。</td></tr>';document.querySelector('#trades').innerHTML=(d.recent_trades||[]).slice(0,40).map(x=>`<tr><td>${x.id}</td><td>${esc(x.instrument)}</td><td>${esc(x.side)}</td><td>${esc(x.status)}</td><td>${Number(x.open_price).toFixed(5)}</td><td>${x.close_price==null?'-':Number(x.close_price).toFixed(5)}</td><td>${x.pnl_jpy==null?'-':yen.format(x.pnl_jpy)}</td><td>${esc(x.exit_reason||'-')}</td></tr>`).join('')||'<tr><td colspan="8">paper取引はまだありません。</td></tr>';const err=d.last_error?.error;box.style.display=err?'block':'none';box.textContent=err?`last error: ${err}`:'';}catch(error){document.querySelector('#systemState').textContent='OFFLINE';document.querySelector('#statusDot').classList.add('bad');box.style.display='block';box.textContent=`backend error: ${error.message}`;}}
refresh();setInterval(refresh,15000);
</script></body></html>
