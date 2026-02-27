# Payment  — Docker 環境構築ガイド

## 概要

payment をローカルの Docker 環境で起動・開発・テストするための手順書です。

### サービス構成

| サービス | イメージ | ポート | 説明 |
|---|---|---|---|
| `app` | python:3.13-slim | 8000 → 8000 | Payment API (FastAPI + Uvicorn) |
| `payment-db` | postgres:18-alpine | 内部のみ | PostgreSQL データベース |


---

## 前提条件

- Docker Engine
- Docker Compose
- （任意）`jq`, `uuidgen` — curl テストスクリプト実行時に必要

---

## クイックスタート

```bash
# プロジェクトルートから実行
cd docker

# ビルド＆起動（初回はイメージビルドが走ります）
docker compose up --build

# バックグラウンド起動
docker compose up --build -d

# ログ確認
docker compose logs -f app
```

起動後、以下の URL でアクセスできます:

| URL | 説明 |
|---|---|
| http://localhost:8000/docs | OpenAPI ドキュメント (Swagger UI) |
| http://localhost:8000/redoc | OpenAPI ドキュメント (ReDoc) |

---

## ディレクトリ構成

```
docker/
├── Dockerfile           # 本番用マルチステージビルド
├── Dockerfile.dev       # 開発用（ホットリロード対応）
├── docker-compose.yml   # 開発環境定義
├── requirements.txt     # Python 依存パッケージ
├── requirements-dev.txt # 開発用追加パッケージ
└── README.md            # 本ドキュメント
```

---

## Dockerfile の使い分け

### `Dockerfile` — 本番用

- マルチステージビルド（builder → final）で軽量イメージを生成
- 非 root ユーザー（`appuser:1000`）で実行

```bash
# 本番用ビルド
docker build -f docker/Dockerfile -t payment-api:latest .
```

### `Dockerfile.dev` — 開発用

- `requirements-dev.txt`（pytest, faker 等）もインストール
- `--reload` によるホットリロード対応

```bash
# 開発用ビルド
docker build -f docker/Dockerfile.dev -t payment-api:dev .
```

---

## データベース

### 初回起動時

`docker compose up` で PostgreSQL が自動的に起動し、ヘルスチェック通過後にアプリケーションが開始されます。

### DB接続情報（開発環境デフォルト）

| 項目 | 値 |
|---|---|
| ホスト | `payment-db`（コンテナ間）|
| ポート | `5432` |
| データベース名 | `fastapi_db` |
| ユーザー | `postgres` |
| パスワード | `postgres` |

### マイグレーション

Alembic を使用してデータベーススキーマを管理しています。

```bash
# アプリコンテナに入る
docker compose exec app bash

# マイグレーション実行
cd /app
alembic -c migrations/alembic.ini upgrade head

# マイグレーション履歴確認
alembic -c migrations/alembic.ini history

# 新しいマイグレーション作成
alembic -c migrations/alembic.ini revision --autogenerate -m "add_new_table"
```

### データベースのリセット

```bash
# ボリュームごと削除して再作成
docker compose down -v
docker compose up --build
```

---

## 環境変数

`docker-compose.yml` で設定されている主要な環境変数です。
`${変数名:-デフォルト値}` の形式で、シェル環境変数での上書きが可能です。

### アプリケーション設定

| 変数名 | デフォルト値 | 説明 |
|---|---|---|
| `ENVIRONMENT` | `DEVELOP` | 実行環境（`DEVELOP` / `PRODUCTION`） |
| `DEBUG` | `False` | デバッグモード |
| `LOG_LEVEL` | `INFO` | ログレベル |

### セキュリティ設定

| 変数名 | デフォルト値 | 説明 |
|---|---|---|
| `X_PAYMENT_API_KEY` | `dev-payment-api-key` | API キー |

### L3 認証設定

| 変数名 | デフォルト値 | 説明 |
|---|---|---|
| `L3_BASE_URL` | `https://dev-auth.example.com` | L3 認証サーバー URL |
| `L3_INTROSPECT_ENDPOINT` | `/auth/token/introspect` | トークン検証エンドポイント |
| `L3_API_KEY` | `dev-l3-api-key` | L3 API キー |
| `L3_CLIENT_ID` | `dev-client-id` | L3 クライアント ID |
| `L3_CLIENT_SECRET` | `dev-client-secret` | L3 クライアントシークレット |

### 認可サービス設定（OpenFGA）(独自で構築する場合のみ)

| 変数名 | デフォルト値 | 説明 |
|---|---|---|
| `AUTHZ_ENABLED` | `false` | 認可チェック有効/無効 |
| `AUTHZ_BASE_URL` | `http://openfga:8080` | OpenFGA サーバー URL |
| `AUTHZ_OPENFGA_STORE_ID` | `01KEVMH7G43T...` | ストア ID |
| `AUTHZ_OPENFGA_MODEL_ID` | `01KEVMH7GK0B...` | モデル ID |

### 外部決済サービス設定（NP掛け払いと連携する場合のみ使用)

| 変数名 | デフォルト値 | 説明 |
|---|---|---|
| `EXTERNAL_PAYMENT_ENABLED` | `false` | 外部決済API呼出しの有効/無効 |
| `NP_KAKEBARAI_BASE_URL` | `http://np-kakebarai-mock-api:8000` | NP掛け払いAPI URL |
| `NP_KAKEBARAI_SHOP_CODE` | `cbs0138430` | ショップコード |
| `NP_KAKEBARAI_SP_CODE` | `sp00138430` | SP コード |
| `NP_KAKEBARAI_TERMINAL_ID` | `9010000001` | 端末 ID |
| `NP_KAKEBARAI_SERVICE_ID` | `550e8400-...` | 決済サービスID（UUID） |

### データベース設定

| 変数名 | デフォルト値 | 説明 |
|---|---|---|
| `POSTGRES_HOST` | `payment-db` | DB ホスト |
| `POSTGRES_PORT` | `5432` | DB ポート |
| `POSTGRES_DB` | `fastapi_db` | DB 名 |
| `POSTGRES_USER` | `postgres` | DB ユーザー |
| `POSTGRES_PASSWORD` | `postgres` | DB パスワード |
| `DB_CONNECT_TIMEOUT` | `10` | 接続タイムアウト（秒） |
| `DB_POOL_TIMEOUT` | `10` | プールタイムアウト（秒） |

### ログ設定

| 変数名 | デフォルト値 | 説明 |
|---|---|---|
| `LOG_JSON_FORMAT` | `false` | JSON 形式ログ（本番向け） |
| `LOG_FILE_ENABLED` | `false` | ファイル出力 |
| `APP_NAME` | `payment-api` | アプリケーション名 |
| `SQLALCHEMY_LOG_LEVEL` | `ERROR` | SQLAlchemy ログレベル |
| `UVICORN_LOG_LEVEL` | `debug` | Uvicorn ログレベル |

---

## ボリュームマウント（開発時）

`docker-compose.yml` では以下のディレクトリがマウントされ、ホットリロードが有効です:

| ホスト側 | コンテナ側 | 用途 |
|---|---|---|
| `../app` | `/app/app` | アプリケーションコード |
| `../migrations` | `/app/migrations` | Alembic マイグレーション |

ソースコードを編集すると、Uvicorn が自動的にリロードします。

---

## テスト実行

テスト用の Docker Compose は `test/docker-compose.test.yml` に定義されています。

```bash
# プロジェクトルートから実行

# 全テスト実行（カバレッジ付き）
docker compose -f test/docker-compose.test.yml run --rm test

# unit テストのみ（DB不要）
docker compose -f test/docker-compose.test.yml run --rm test-unit

# integration テストのみ（DB必要）
docker compose -f test/docker-compose.test.yml run --rm test-integration

# E2E テスト
docker compose -f test/docker-compose.test.yml run --rm test-e2e

```

### テスト環境のクリーンアップ

```bash
docker compose -f test/docker-compose.test.yml down -v
```

---

### イメージを完全にクリーンビルドしたい

```bash
docker compose down -v
docker compose build --no-cache
docker compose up
```
