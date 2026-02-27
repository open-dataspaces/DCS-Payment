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

## ドキュメント

| ドキュメント | 説明 |
|------------|------|
| [基本設計](docs/basic_design.md) | システムアーキテクチャ、全体構成 |
| [詳細設計](docs/detail_design.md) | 詳細シーケンス、データ設計 |
| [OpenAPI仕様 (JSON)](docs/openapi/openapi.json) | API仕様（機械可読形式） |
| [OpenAPI仕様 (HTML)](docs/openapi/openapi.html) | API仕様（ブラウザ閲覧用） |

## ライセンス

未定
