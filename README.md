# Kurage FX AI Trade

OANDAの価格・注文APIを使うFX自動運用ボディと、差し替え可能なAI判断レイヤーを分離したOSSです。予測、リスク制御、paper取引、取引後レビュー、改善仮説の検証を継続します。

初期設定は`paper`です。実口座へ注文しません。

## 設計

```text
OANDA candles/pricing
        |
        v
local direction model ---> rule / Ollama / x402 judgment brain
        |                           |
        +---------- fixed risk gates
                                  |
                     paper / OANDA Practice / live
                                  |
                       journal and research loop
```

- **OSS body**: OANDA接続、方向予測、注文、損切り・利確、スプレッド制限、日次損失制限、SQLite履歴、ダッシュボード
- **Metered brain**: 地合い分類、リスク指示、取引反省、改善仮説。`x402`対応ゲートウェイの背後に配置可能
- **Trust boundary**: AI判断レイヤーは注文API、認証情報、注文数量を持ちません。最終リスク判定はOSS bodyだけが行います

## モード

| モード | 価格 | 注文 |
|---|---|---|
| `paper` | OANDA Practice | SQLite内の仮想取引 |
| `practice` | OANDA Practice | OANDA Practice口座 |
| `live` | OANDA Live | 実口座。環境と明示的な確認文字列が必須 |

`live`は`OANDA_ENVIRONMENT=live`と`KFXAI_LIVE_ACK=I_UNDERSTAND_THIS_USES_REAL_MONEY`が同時に設定されない限り起動しません。

## セットアップ

Python 3.10以上とOANDA Practice口座のAPIトークンを用意します。

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.sample .env
```

`.env`の次の2項目を設定します。

```dotenv
OANDA_ACCOUNT_ID=
OANDA_ACCESS_TOKEN=
```

最初は変更せず`KFXAI_TRADING_MODE=paper`で動かします。

```bash
set -a; source .env; set +a
kfxai-cycle
uvicorn kfxai.api:app --host 127.0.0.1 --port 18324
```

ダッシュボードは`http://127.0.0.1:18324/`です。常駐workerは次で起動します。

```bash
kfxai-worker
```

## 判断バックエンド

`KFXAI_JUDGMENT_BACKEND`で選択します。

- `rule_based`: 外部LLMなし。完全ローカルの基準実装
- `local_llm`: Ollamaで判断し、障害時はルールへ安全にフォールバック
- `x402`: `KFXAI_BRAIN_URL`の有料判断APIを利用

知能APIを自分で提供する場合は次を起動します。

```bash
KFXAI_BRAIN_ENGINE=local_llm kfxai-brain
```

APIは`127.0.0.1:18325`で待ち受けます。公開時は認証、レート制限、x402決済を担当するゲートウェイを前段に置いてください。

```text
GET  /v1/health
GET  /v1/meta
POST /v1/judge/regime
POST /v1/judge/directive
POST /v1/judge/postmortem
POST /v1/research/hypotheses
```

## 常駐運用

`systemd/`には次のunitがあります。

- `kfxai-api.service`: 状態APIとダッシュボード
- `kfxai-worker.service`: 5分間隔の取引サイクル
- `kfxai-brain.service`: 判断API
- `kfxai-research.timer`: 8時間ごとの改善仮説検証

インストール後はまずAPIと`paper` workerだけを起動し、`/api/status`の履歴と損益を確認してください。

## Docker

```bash
cp .env.sample .env
docker compose up --build
```

APIとbrainはlocalhostだけに公開されます。

## テスト

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q
```

テストはOANDAへ接続せず、疑似市場データと疑似API応答で価格解析、注文payload、リスクゲート、APIを確認します。

## 重要事項

- 本ソフトウェアは投資助言ではありません。
- FXはレバレッジにより元本を超える損失が発生する可能性があります。
- OANDA Japan株式会社またはOANDAグループによる承認・提携を示すものではありません。
- API仕様と取引条件は必ずOANDA公式資料と契約口座で確認してください。
- Practiceで十分な期間検証し、スリッページ、通信障害、金利調整、税務を含めて評価してください。

## License

GPL-3.0-only
