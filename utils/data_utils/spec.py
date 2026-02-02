from __future__ import annotations

from typing import Any, Dict, List


# spec.py：規格/常數/欄位清單（SSOT）
# - MockServer：照這裡的規格做判斷
# - Tests：照這裡的規格做驗證（不要在 test_*.py 自己推導規則）

# 病患資料「基本欄位骨架」

BASE_PATIENT_TEMPLATE: Dict[str, Any] = {
    "id": "",
    "dob": "",
    "name": "",
    "department": "",
    "doctor": "",
    "appointment_time": "",
    "registration_date": "",
}


# 靜態資料：科別 -> 可選醫師名單（MockServer / Tests 共用）

DEPARTMENT_DOCTOR_MAP = {
    "婦產科": ["Dr. Wang", "Dr. Chen"],
    "肝膽腸胃科": ["Dr. Lin", "Dr. Huang"],
    "心臟科": ["Dr. Lee", "Dr. Wu"],
    "耳鼻喉科": ["Dr. Tsai", "Dr. Hsu"],
}

# 可選時段（MockServer / Tests 共用）
APPOINTMENT_TIMES = ["早診", "下午診", "晚診"]
# 規格：register 必填欄位（少任何一個就要判定為缺欄位）
REQUIRED_FIELDS: List[str] = ["id", "dob", "name", "department", "doctor", "appointment_time", "registration_date"]
# 規格：掛號唯一鍵（register/cancel/query 用同一套 key）
REGISTRATION_KEY_FIELDS: List[str] = ["id", "registration_date", "department"]
# 規格：派號計數鍵（同醫師/同時段/同一天 共用同一把 key）
COUNTER_KEY_FIELDS: List[str] = ["doctor", "registration_date", "appointment_time"]
# late 規則：id 只要包含 token 就視為 late

LATE_TOKEN = "LATE"
# status（成功狀態）

STATUS_OK = "掛號成功"
STATUS_LATE = "過號重新掛號"
# error（錯誤訊息）— SSOT
ERR_MISSING_REQUIRED = "缺少必填欄位"
ERR_INVALID_ID = "身分證格式錯誤"
ERR_INVALID_DATE = "掛號日期錯誤"
ERR_DUPLICATE = "已掛號"
ERR_NOT_FOUND = "掛號不存在"


#  conftest.seed_dirty_db 用：可注入的髒資料類型（MockServer 不會用到，只是讓 import 不會炸）

FAIL_TYPES: List[str] = ["invalid_id", "invalid_date", "missing_required"]


# 規格 DB：把所有規格包成一包（外部可匯出/檢視）

RULES_DB: Dict[str, Any] = {
    "required_fields": REQUIRED_FIELDS,
    "registration_key_fields": REGISTRATION_KEY_FIELDS,
    "counter_key_fields": COUNTER_KEY_FIELDS,
    "late_token": LATE_TOKEN,
    "status": {"ok": STATUS_OK, "late": STATUS_LATE},
    "errors": {
        "missing_required": ERR_MISSING_REQUIRED,
        "invalid_id": ERR_INVALID_ID,
        "invalid_date": ERR_INVALID_DATE,
        "duplicate": ERR_DUPLICATE,
        "not_found": ERR_NOT_FOUND,
    },
    #  你的 mock_server：normal 是偶數(current_even += 2)，late 是奇數(current_odd += 2)
    "success_rule": {"number_parity": "even"},  # 正常掛號：偶數
    "late_rule": {"number_parity": "odd"},      # 過號掛號：奇數
}
