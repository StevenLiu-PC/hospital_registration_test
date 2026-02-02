# mock_server.py
# Mock Server（用 Flask 假裝一個「掛號系統後端」）
# - POST /register   → 掛號
# - POST /cancel     → 取消掛號
# - GET  /query?id=  → 查詢掛號資料
# - POST /admin/reset → 測試用重置（清 DB、派號歸零）
# - POST /admin/seed  → 測試用塞資料（給 stress/chaos 造「既有/髒資料」）
# - GET  /health      → 探活（conftest 會 ping）

from __future__ import annotations

import time
import random
from datetime import datetime
from threading import Lock

from flask import Flask, request, jsonify

# SSOT：Single Source of Truth（規格單一來源）
from utils.data_utils import (
    REQUIRED_FIELDS,                  # register 必填欄位（缺任何一個就 400）
    REGISTRATION_KEY_FIELDS,          # cancel 用來組 key 的欄位
    LATE_TOKEN,                       # 過號 token（例如 "LATE"）
    STATUS_OK, STATUS_LATE,           # 成功狀態字串（掛號成功/過號重新掛號）
    ERR_MISSING_REQUIRED,             # 缺必填欄位時的 error 字串
    ERR_INVALID_ID,                   # 身分證錯誤時的 error 字串
    ERR_INVALID_DATE,                 # 日期格式錯誤時的 error 字串
    ERR_DUPLICATE,                    # 重複掛號時的 error 字串
    ERR_NOT_FOUND,                    # 取消找不到時的 error 字串
    make_registration_key,            # 統一掛號唯一 key 的生成方式（SSOT）
)

app = Flask(__name__)


# 簡單資料庫模擬（in-memory DB）

# db：用 dict 假裝資料庫
# - key: 由 make_registration_key(patient) 統一產生（SSOT）
# - value: patient dict（整包病患資料）
# lock：thread-safe（stress/chaos 併發會同時打 API，沒 lock 會 race）
db = {}
lock = Lock()


# 派號計數器（伺服器端狀態）

# 正常掛號 → 偶數：2,4,6...
# 過號掛號 → 奇數：3,5,7...
current_even = 0
current_odd = 1



# 小工具：必填/日期檢查

def _missing_required_fields(patient: dict, fields: list[str]) -> list[str]:
    """回傳缺的欄位清單（key 不存在 / 值 None / 空字串 都算缺）"""
    return [f for f in fields if f not in patient or patient.get(f) in (None, "")]


def _is_bad_date(date_str: str) -> bool:
    """驗 YYYY-MM-DD；不存在日期（如 2026-02-30）也視為 bad date"""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return False
    except ValueError:
        return True



# Chaos / Infra 注入（延遲 / 5xx）

# 只有 request headers 有帶 chaos 參數才會注入
# smoke/stress 沒帶 → 不注入（避免 gate 被亂搞）
def _get_float_header(name: str, default: float) -> float:
    v = (request.headers.get(name) or "").strip()
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _get_int_seed() -> int | None:
    seed = (request.headers.get("X-Seed") or "").strip()
    if seed.isdigit():
        return int(seed)
    return None


def _maybe_inject_infra():
    """
    伺服器層級的「不穩定注入」
    - 5xx 注入：更像服務掛掉 → 直接回 500
    - latency 注入：更像慢響應 → sleep 後繼續正常流程
    """
    has_chaos_params = any(
        request.headers.get(k)
        for k in ("X-Latency-Prob", "X-Latency-Min", "X-Latency-Max", "X-Error5xx-Prob")
    )
    if not has_chaos_params:
        return None

    err_prob = _get_float_header("X-Error5xx-Prob", 0.0)
    lat_prob = _get_float_header("X-Latency-Prob", 0.0)
    lat_min = _get_float_header("X-Latency-Min", 0.0)
    lat_max = _get_float_header("X-Latency-Max", 0.0)

    seed = _get_int_seed()
    rng = random.Random(seed) if seed is not None else random.Random()

    # 先注入 5xx（像服務崩）
    if err_prob > 0 and rng.random() < err_prob:
        return jsonify({"error": "infra_error", "note": "mock_5xx_injected"}), 500

    # 再注入 latency（慢但仍會回）
    if lat_prob > 0 and lat_max > lat_min > 0 and rng.random() < lat_prob:
        time.sleep(rng.uniform(lat_min, lat_max))

    return None



# Health：探活（給 conftest ping）

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200



# register：掛號
# 規則順序（像真後端驗證流程）：
# 1 缺必填 → 400 ERR_MISSING_REQUIRED
# 2 日期錯 → 400 ERR_INVALID_DATE
# 3 身分證錯 → 400 ERR_INVALID_ID
# 4 duplicate → 400 ERR_DUPLICATE
# 5 成功 → 200 + (status, number)

@app.route("/register", methods=["POST"])
def register():
    global current_even, current_odd

    injected = _maybe_inject_infra()
    if injected is not None:
        return injected

    patient = request.json or {}

    # 1) 缺必填
    missing = _missing_required_fields(patient, REQUIRED_FIELDS)
    if missing:
        return jsonify({"error": ERR_MISSING_REQUIRED, "missing_fields": missing}), 400

    # 2) 日期錯
    if _is_bad_date(str(patient.get("registration_date"))):
        return jsonify({"error": ERR_INVALID_DATE}), 400

    # 3) 身分證錯（你的測資用 INVALID）
    if patient.get("id") == "INVALID":
        return jsonify({"error": ERR_INVALID_ID}), 400

    # 4) duplicate：key 用 SSOT
    pid = str(patient.get("id", ""))
    key = make_registration_key(patient)

    with lock:
        if key in db:
            return jsonify({"error": ERR_DUPLICATE}), 400

        # 寫入 DB
        db[key] = patient

        # 5) 派號（過號 odd / 正常 even）
        if LATE_TOKEN in pid:
            current_odd += 2
            assigned_number = current_odd
            status = STATUS_LATE
        else:
            current_even += 2
            assigned_number = current_even
            status = STATUS_OK

    return jsonify({
        "status": status,
        "number": assigned_number,
        "patient_name": patient.get("name", ""),
    }), 200



# cancel：取消掛號
# 1 缺 key 欄位 → 400 ERR_MISSING_REQUIRED
# 2 db 沒這筆 → 404 ERR_NOT_FOUND
# 3 找到就刪 → 200

@app.route("/cancel", methods=["POST"])
def cancel():
    injected = _maybe_inject_infra()
    if injected is not None:
        return injected

    patient = request.json or {}

    # cancel 只要求「能組 key 的欄位」
    missing = _missing_required_fields(patient, REGISTRATION_KEY_FIELDS)
    if missing:
        return jsonify({"error": ERR_MISSING_REQUIRED, "missing_fields": missing}), 400

    key = make_registration_key(patient)

    with lock:
        if key not in db:
            return jsonify({"error": ERR_NOT_FOUND}), 404
        del db[key]

    return jsonify({"status": "取消成功"}), 200



# query：查詢掛號資料
# GET /query?id=xxxx
# - db 裡找同 id 的第一筆回傳
# - 找不到 → 404

@app.route("/query", methods=["GET"])
def query():
    injected = _maybe_inject_infra()
    if injected is not None:
        return injected

    patient_id = request.args.get("id", "").strip()
    if not patient_id:
        return jsonify({"error": "missing id"}), 400

    with lock:
        # key 可能是 tuple / str（看你的 make_registration_key 實作）
        # 所以這裡用 value 去找 id 最保險
        matches = [v for v in db.values() if str(v.get("id", "")) == patient_id]

    if not matches:
        return jsonify({"error": "查無資料"}), 404

    return jsonify(matches[0]), 200


# /admin/reset：測試用重置
# - 清 DB、派號歸零
# - 不做 chaos 注入（不然 reset 都可能 500/latency）

@app.route("/admin/reset", methods=["POST"])
def admin_reset():
    global current_even, current_odd
    with lock:
        db.clear()
        current_even = 0
        current_odd = 1
    return jsonify({"status": "reset_ok"}), 200



# /admin/seed：測試用塞資料（配合 conftest.seed_dirty_db）
# payload: {"rows": [patient_dict, ...]}
# 目的：
# - 允許塞「既有資料 / 髒資料」
# - 但仍要能產 key：key 產不出來就 skip
@app.route("/admin/seed", methods=["POST"])
def admin_seed():
    payload = request.json or {}
    rows = payload.get("rows") or []
    if not isinstance(rows, list):
        return jsonify({"error": "rows must be a list"}), 400

    inserted = 0
    skipped = 0

    with lock:
        for p in rows:
            if not isinstance(p, dict):
                skipped += 1
                continue
            try:
                key = make_registration_key(p)
            except Exception:
                # 髒到連 key 都組不出來 → 直接跳過
                skipped += 1
                continue

            # 避免重複塞同 key（跟真 DB 比較像）
            if key in db:
                skipped += 1
                continue

            db[key] = p
            inserted += 1

    return jsonify({"status": "seed_ok", "inserted": inserted, "skipped": skipped, "total": len(rows)}), 200


# local run 入口
if __name__ == "__main__":
    app.run(port=5000, debug=True, threaded=True)
