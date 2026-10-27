# 精算決済

データ交換プラットフォームにおける精算・決済機能を提供するAPIサーバです。

## 概要

本機能は、データ提供者（Provider）とデータ消費者（Consumer）間のデータ交換に伴う課金・決済処理を行います。

### 主要機能

- **利用料モデル管理**: データ交換の料金体系を定義・管理
- **取引可否確認**: 外部決済サービスとの与信確認
- **データ交換状態管理**: 取引の登録・更新・参照
- **支払/請求予定額算出**: 期間指定での金額集計

## ディレクトリ構成

```
.
├── app/                   # FastAPI アプリケーション
│   ├── main.py            # エントリーポイント、ミドルウェア設定
│   ├── api/               # APIルーター・エンドポイント
│   ├── models/            # SQLAlchemy ORMモデル
│   ├── schemas/           # Pydantic リクエスト/レスポンススキーマ
│   ├── repositories/      # データアクセス層（DBクエリ）
│   ├── services/          # ビジネスロジック層
│   ├── clients/           # 外部サービスHTTPクライアント
│   ├── core/              # 設定、セキュリティ、イベントハンドラ
│   ├── middleware/        # カスタムミドルウェア
│   ├── db/                # SQLAlchemy セッション設定
│   └── utils/             # ユーティリティ
│
├── migrations/            # Alembic データベースマイグレーション
│   ├── alembic.ini       # Alembic設定
│   ├── env.py            # マイグレーション環境設定
│   └── versions/         # マイグレーションスクリプト
│
├── docker/                # Docker関連ファイル
│   ├── README.md         # Docker環境構築・起動手順
│   ├── Dockerfile        # 本番用Dockerfile
│   ├── Dockerfile.dev    # 開発用Dockerfile
│   ├── docker-compose.yml # ローカル開発用Compose
│   ├── requirements.txt  # Python依存パッケージ
│   └── requirements-dev.txt # 開発用Python依存パッケージ
│
├── cronjob/              # CronJob関連
│   ├── docker/           # CronJob用Docker関連ファイル
│   ├── docs/             # CronJob用ドキュメント
│   └── src/              # CronJobスクリプト
│
├── scripts/
│   └── generate-openapi.sh # OpenAPI仕様生成
│
├── docs/                  # ドキュメント
│   ├── basic_design.md   # 基本設計
│   ├── detail_design.md  # 詳細設計
│   └── openapi/          # OpenAPI仕様
│       ├── openapi.json  # OpenAPI JSON
│       └── openapi.html  # OpenAPI HTML



```

### 前提条件

- Docker / Docker Compose
- AWS/ EKS / Kubernetes/ helm

## バージョン 1.2.0 での変更

変更の一覧は API ドキュメント（`/docs`）の変更履歴を参照してください。1.0.0 から利用している場合は、次の互換性に影響する変更を確認してください。

- **トラッキング ID による紐付け**: 取引可否確認 API が返す `tracking_id`（リクエストの `X-TrackingId` と同じ値）を、データ交換状態登録 API・取引金額確定 API（利用料モデルなし）の本文 `tracking_id` に指定してください。取引可否確認・データ取得・データ交換状態登録では同じ `X-TrackingId` を使用します（[詳細設計](docs/detail_design.md)の「トラッキングIDの取り扱い」を参照）。
- **取引可否確認の必須化**: 与信が必要な決済サービスの取引は、取引可否確認（与信 OK）を経ていないとデータ交換状態登録 API を 404 で拒否します。
- **支払/請求予定額の対象**: `L2_HTTP_STATUS_FILTER_ENABLED` の既定値が `true` になり、L2 ログで成功を確認できた取引だけを含めます。L2 ログ突合 CronJob を運用しない環境では `false` を設定してください。
- **認可確認の設定項目**: `AUTHZ_BASE_URL`・`AUTHZ_OPENFGA_STORE_ID`・`AUTHZ_OPENFGA_MODEL_ID` に代えて `L3_BASE_URL`・`L3_API_KEY`・`AUTHZ_STORE_ID` を使用します。
- **CORS の設定名**: `ALLOWED_HOSTS` に代えて `CORS_ALLOWED_ORIGINS` を使用します（旧名も当面は読み込みます）。
- **API キーの必須化**: `X_PAYMENT_API_KEY` が未設定の場合はすべてのリクエストを拒否します。
- **ロック待ちの 503**: 同じ取引を処理中の他のリクエストを `DB_LOCK_TIMEOUT` 秒待っても完了しない場合は 503（`Retry-After` 付き）を返します。
- **マイグレーション 002〜005 の適用**: 起動前に `alembic upgrade head` を実行してください。

## ドキュメント

| ドキュメント | 説明 |
|------------|------|
| [Docker環境構築・起動手順](docker/README.md) | ローカルDocker環境でのセットアップ・起動・テスト手順 |
| [基本設計](docs/basic_design.md) | システムアーキテクチャ、全体構成 |
| [詳細設計](docs/detail_design.md) | 詳細シーケンス、データ設計 |
| [OpenAPI仕様 (JSON)](docs/openapi/openapi.json) | API仕様（機械可読形式） |
| [OpenAPI仕様 (HTML)](docs/openapi/openapi.html) | API仕様（ブラウザ閲覧用） |

## ライセンス
- 本リポジトリはMITライセンスで提供されています。
- ソースコードおよび関連ドキュメントの著作権は、一般社団法人自動車・蓄電池トレーサビリティ推進センターに帰属します。  

## 免責事項
- 本リポジトリの内容は予告なく変更・削除する可能性があります。
- 本リポジトリの利用により生じた損失及び損害等について、いかなる責任も負わないものとします。
