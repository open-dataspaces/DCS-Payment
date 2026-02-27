"""API V1 Router"""
from fastapi import APIRouter

from app.api.v1.endpoints import fee_model
from app.api.v1.endpoints import data_exchange
from app.api.v1.endpoints import payment_billing

api_router = APIRouter()

# payment
# 利用料モデル
api_router.include_router(fee_model.router, tags=["精算決済"])
# 取引可否、データ交換状態登録、更新
api_router.include_router(data_exchange.router, tags=["精算決済"])
# 支払予定額取得、請求予定額取得
api_router.include_router(payment_billing.router, tags=["精算決済"])
