from __future__ import annotations

import random
import string
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .spec import (
    BASE_PATIENT_TEMPLATE,
    DEPARTMENT_DOCTOR_MAP,
    APPOINTMENT_TIMES,
    REQUIRED_FIELDS,
    FAIL_TYPES,                 # ✅ conftest / inject_fault 會用到
    RULES_DB,                   # ✅ make_registration_key / parity 規格會用到
    LATE_TOKEN,
    STATUS_OK,
    STATUS_LATE,
    ERR_MISSING_REQUIRED,       # ✅ patient_* steps 會用到
    ERR_INVALID_ID,
    ERR_INVALID_DATE,
    ERR_DUPLICATE,
    ERR_NOT_FOUND,
)
from .steps_plan import attach_expect



# patient_factory.py：測資工廠（只負責「產資料 + 附上 _expect.steps」）
# - 這裡不做 API 呼叫、不做 assert（避免規則散落）
# - 規格（steps）在這裡附上：p["_expect"]["steps"]



def export_rules_db() -> Dict[str, Any]:
    """給 debug/文件用"""
    return dict(RULES_DB)
def make_registration_key(payload: Dict[str, Any]) -> Tuple[Any, ...]:
    """掛號唯一鍵：用固定欄位組出 tuple"""
    return tuple(payload.get(k) for k in RULES_DB["registration_key_fields"])

def make_counter_key(payload: Dict[str, Any]) -> Tuple[Any, ...]:
    """派號/名額計數鍵：同醫師/同時段/同一天，共用同一把計數 key"""
    return tuple(payload.get(k) for k in RULES_DB["counter_key_fields"])
def is_late_patient(payload: Dict[str, Any]) -> bool:
    """late 規則:id 只要含 LATE_TOKEN 就算過號"""
    return LATE_TOKEN in str(payload.get("id", ""))


# RNG：用 seed 讓資料「可重現」
def make_rng(seed: Optional[int] = None) -> random.Random:
    """用 seed 產出獨立 RNG（同 seed → 同資料，方便回放）"""
    return random.Random(seed)



# 基礎資料產生器

def generate_id_number(rng: random.Random, prefix: str = "USER") -> str:
    """產生一個看起來像 ID 的字串（非真實身分證，只做測試用）"""
    suffix = "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    return f"{prefix}_{suffix}"
def generate_name(rng: random.Random) -> str:
    """產生測試姓名（避免真實個資）"""
    suffix = "".join(rng.choice(string.ascii_uppercase) for _ in range(3))
    return f"TestUser{suffix}"
def generate_dob(rng: random.Random, now: Optional[datetime] = None) -> str:
    """產生出生日期(YYYY-MM-DD)，讓資料看起來合理"""
    now = now or datetime.now()
    years = rng.randint(18, 75)  # 年齡 18~75
    days = rng.randint(0, 365)
    dob = now - timedelta(days=years * 365 + days)
    return dob.strftime("%Y-%m-%d")
def _pick_department_doctor(rng: random.Random) -> Tuple[str, str]:
    """隨機挑科別 + 該科醫師"""
    dep = rng.choice(list(DEPARTMENT_DOCTOR_MAP.keys()))
    doc = rng.choice(DEPARTMENT_DOCTOR_MAP[dep])
    return dep, doc


def _pick_appointment_time(rng: random.Random) -> str:
    """隨機挑時段"""
    return rng.choice(APPOINTMENT_TIMES)


def _pick_registration_date(rng: random.Random, now: Optional[datetime] = None) -> str:
    """隨機挑掛號日期（以今天為中心，前後幾天）"""
    now = now or datetime.now()
    delta = rng.randint(-2, 2)
    dt = now + timedelta(days=delta)
    return dt.strftime("%Y-%m-%d")


def _default_missing_fields(seed: Optional[int]) -> List[str]:
    """
    missing_required：如果沒指定缺哪些欄位
    → 用 seed 決定缺哪個（可重現）
    """
    rng = make_rng(seed)
    return [rng.choice(REQUIRED_FIELDS)]


def inject_fault(
    payload: Dict[str, Any],                     # 目標：這筆測資 dict（會被「打髒」）
    rng: random.Random,                          # 目標：可重現的 RNG（同 seed 行為一致）
    *,
    fail_types: Optional[List[str]] = None,      # 可選：限定髒資料類型（不傳就用 spec.FAIL_TYPES）
    fault_rate: float = 1.0,                     # 注入機率：1.0=必定打髒；0.1=約 10% 打髒
    **_ignored,                                  # 相容舊版參數：seed/prefix/...（收到就忽略）
) -> Dict[str, Any]:
    """
    fault 注入（壓測/髒資料用）
    -  這裡只負責「把輸入資料弄壞」
    -  MockServer 只做「後端規則判斷」；測試端不應自己推導規則
    -  兼容兩種呼叫：
        1) generate_patient(...) 內部：inject_fault(payload=..., rng=..., fault_rate=0.1)
        2) conftest.seed_dirty_db：inject_fault(p, rng, fail_types=FAIL_TYPES, seed=i, prefix=...)
    """
    if fault_rate <= 0:
        return payload

    if rng.random() > fault_rate:
        return payload

    # 可選：讓 conftest 指定「想打哪幾種髒資料」
    choices = [t for t in (fail_types or FAIL_TYPES) if isinstance(t, str) and t.strip()]
    if not choices:
        choices = list(FAIL_TYPES)

    fault_type = rng.choice(choices)
    payload["_fault"] = fault_type  #  只做標記（方便 report/debug）

    # --------- 真的把資料「打髒」的地方（輸入層）---------
    if fault_type == "invalid_id":
        payload["id"] = "INVALID"  #  命中：ERR_INVALID_ID
    elif fault_type == "invalid_date":
        payload["registration_date"] = "2099-99-99"  #  命中：ERR_INVALID_DATE
    elif fault_type == "missing_required":
        #  注意：有可能拔掉 id，後續 patient_late 不能再硬碰 p["id"]
        miss = rng.choice(REQUIRED_FIELDS)
        payload.pop(miss, None)
        payload["_fault_detail"] = f"missing:{miss}"
    else:
        payload["_fault_detail"] = "unknown_fault_type"

    return payload


def generate_patient(
    *,
    seed: Optional[int] = None,
    prefix: str = "USER",
    config: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    fault_rate: float = 0.0,
) -> Dict[str, Any]:
    """
    產生一筆 patient 資料
    - 只負責資料長相，不做 assert、不呼叫 API
    - fault_rate > 0 時可能注入 _fault（壓測混髒資料）
    """
    _ = config  # ✅ 先留著（之後你要接 yaml/mock_data 再用）
    rng = make_rng(seed)
    p: Dict[str, Any] = dict(BASE_PATIENT_TEMPLATE)

    p["id"] = generate_id_number(rng, prefix=prefix)
    p["name"] = generate_name(rng)
    p["dob"] = generate_dob(rng, now=now)

    dep, doc = _pick_department_doctor(rng)
    p["department"] = dep
    p["doctor"] = doc
    p["appointment_time"] = _pick_appointment_time(rng)
    p["registration_date"] = _pick_registration_date(rng, now=now)

    # 壓測用：注入髒資料（可能加 _fault）
    p = inject_fault(payload=p, rng=rng, fault_rate=fault_rate)

    return p



# meta 處理：測試端/報表端常用

def strip_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    把 _expect/_fault 等 meta 拿掉（避免干擾欄位一致性比對）
    - 注意：你送 server 的 payload 可以保留 meta（conftest 會 strip）
    - 但你本地比對 query 欄位時，一律用 clean 版本
    """
    clean = dict(payload)
    for k in list(clean.keys()):
        if isinstance(k, str) and k.startswith("_"):
            clean.pop(k, None)
    return clean


def generate_dirty_rows_for_db(*, total: int, fail_rate: float, seed: int = 0) -> List[Dict[str, Any]]:
    """
    壓測前：先塞一批資料進 mock DB（更像真實環境）
    - fail_rate 控制髒資料比例
    """
    rows: List[Dict[str, Any]] = []
    rng = make_rng(seed)
    for i in range(total):
        p = generate_patient(seed=seed + i, fault_rate=fail_rate)
        # 這裡故意不 attach steps（因為只是塞 DB）
        rows.append(strip_meta(p))
    rng.shuffle(rows)
    return rows



# 情境工廠（對外入口）
# - 回傳：patient_raw（含 _expect.steps）
# - 規格（steps）是 SSOT：只改這裡，不改 test 檔


def patient_success(seed=None, prefix="USER", config=None, now=None, verbose=False, fail_rate: float = 0.0) -> Dict[str, Any]:
    """
    normal 情境入口（可選 fail_rate 壓測用）
    fail_rate 讓壓測可以混入一定比例的 fault 例如 0.1 就會有 10% 被打壞
    """
    p = generate_patient(seed=seed, prefix=prefix, config=config, now=now, fault_rate=fail_rate)
    fault = p.get("_fault")

    if fault is None:
        steps = [
            {"action": "register", "expect_status": 200, "expect_json": {"status": STATUS_OK}, "number_parity": RULES_DB["success_rule"]["number_parity"]},
            {"action": "query", "expect_status": 200, "fields_check": True},
        ]
    else:
        if fault == "invalid_id":
            steps = [{"action": "register", "expect_status": 400, "expect_json": {"error": ERR_INVALID_ID}}]
        elif fault == "invalid_date":
            steps = [{"action": "register", "expect_status": 400, "expect_json": {"error": ERR_INVALID_DATE}}]
        else:
            steps = [{"action": "register", "expect_status": 400, "expect_json": {"error": ERR_MISSING_REQUIRED}}]

    if verbose:
        print(f"[patient_success] p={p}")

    return attach_expect(p, steps, scenario="normal")


def patient_late(seed=None, prefix="USER", config=None, now=None, verbose=False, fail_rate: float = 0.0) -> Dict[str, Any]:
    """
    late 情境入口（可選 fail_rate 壓測用）
    fail_rate 同樣可用在壓測混入一定比例 fault
    """
    p = generate_patient(seed=seed, prefix=prefix, config=config, now=now, fault_rate=fail_rate)

    #  先抓 fault（可能是 missing_required / invalid_id / invalid_date）
    fault = p.get("_fault")

    #  只有在「id 還存在」且「不是 invalid_id」時，才補 LATE token
    if "id" in p and fault != "invalid_id":
        if LATE_TOKEN not in str(p.get("id", "")):
            p["id"] = f"{p['id']}_{LATE_TOKEN}"

    if fault is None:
        #  late 成功：你規則是「正常偶數 → 過號 +1 → 奇數」
        late_parity = (RULES_DB.get("late_rule") or {}).get("number_parity", "odd")

        steps = [
            {
                "action": "register",
                "expect_status": 200,
                "expect_json": {"status": STATUS_LATE},
                "number_parity": late_parity,   #  這裡改成 odd（或 RULES_DB late_rule）
            },
            {"action": "query", "expect_status": 200, "fields_check": True},
        ]
    else:
        #  late + fault：一律應該被擋（不做 query，避免 id 缺失時亂掉）
        if fault == "invalid_id":
            steps = [{"action": "register", "expect_status": 400, "expect_json": {"error": ERR_INVALID_ID}}]
        elif fault == "invalid_date":
            steps = [{"action": "register", "expect_status": 400, "expect_json": {"error": ERR_INVALID_DATE}}]
        else:
            steps = [{"action": "register", "expect_status": 400, "expect_json": {"error": ERR_MISSING_REQUIRED}}]

    if verbose:
        print(f"[patient_late] p={p}")

    return attach_expect(p, steps, scenario="late")


def patient_invalid_id(seed=None, prefix="USER", config=None, now=None, verbose=False) -> Dict[str, Any]:
    """固定做一筆 invalid_id（不靠隨機）"""
    p = generate_patient(seed=seed, prefix=prefix, config=config, now=now, fault_rate=0.0)  # 先產乾淨資料
    p["id"] = "INVALID"           #  直接把 id 改成後端會判定不合法的值
    p["_fault"] = "invalid_id"    #  標記給報表/除錯用

    steps = [
        {"action": "register", "expect_status": 400, "expect_json": {"error": ERR_INVALID_ID}},  #  invalid_id → 400 + ERR_INVALID_ID
        {"action": "query", "expect_status": 404},  #  register 沒成功寫入 DB → 查不到
    ]

    if verbose:
        print(f"[patient_invalid_id] p={p}")

    return attach_expect(p, steps, scenario="invalid_id")


def patient_invalid_date(seed=None, prefix="USER", config=None, now=None, verbose=False) -> Dict[str, Any]:
    """固定做一筆 invalid_date"""
    p = generate_patient(seed=seed, prefix=prefix, config=config, now=now, fault_rate=0.0)
    p["registration_date"] = "2099-99-99"
    p["_fault"] = "invalid_date"

    steps = [
        {"action": "register", "expect_status": 400, "expect_json": {"error": ERR_INVALID_DATE}},
        {"action": "query", "expect_status": 404},
    ]

    if verbose:
        print(f"[patient_invalid_date] p={p}")

    return attach_expect(p, steps, scenario="invalid_date")


def patient_missing_required(missing_fields=None, seed=None, prefix="USER", config=None, now=None, verbose=False) -> Dict[str, Any]:
    if missing_fields is None:
        missing_fields = _default_missing_fields(seed)

    p = generate_patient(seed=seed, prefix=prefix, config=config, now=now, fault_rate=0.0)

    for f in missing_fields:
        p.pop(f, None)

    p["_fault"] = "missing_required"
    p["_fault_detail"] = f"missing={missing_fields}"

    steps = [
        {"action": "register", "expect_status": 400, "expect_json": {"error": ERR_MISSING_REQUIRED}},
    ]

    #  缺 id 就不要 query（沒有 id 查什麼）
    if "id" not in (missing_fields or []):
        steps.append({"action": "query", "expect_status": 404})

    if verbose:
        print(f"[patient_missing_required] p={p}")

    return attach_expect(p, steps, scenario="missing_required")


def patient_duplicate(seed=None, prefix="USER", config=None, now=None, verbose=False) -> Dict[str, Any]:
    """duplicate：同一筆資料連打兩次 register，第二次要被擋"""
    p = generate_patient(seed=seed, prefix=prefix, config=config, now=now, fault_rate=0.0)
    p["_fault"] = "duplicate"  # 不改欄位，靠 steps 連打兩次觸發 duplicate

    steps = [
        {"action": "register", "expect_status": 200},
        {"action": "register", "expect_status": 400, "expect_json": {"error": ERR_DUPLICATE}},
    ]

    if verbose:
        print(f"[patient_duplicate] p={p}")

    return attach_expect(p, steps, scenario="duplicate")


def patient_cancel_nonexist(seed=None, prefix="USER", config=None, now=None, verbose=False) -> Dict[str, Any]:
    """cancel_nonexist：固定做「一定不存在」的 key"""
    p = generate_patient(seed=seed, prefix=prefix, config=config, now=now, fault_rate=0.0)
    p["id"] = f"NOT_EXIST_{seed or 0}"
    p["_fault"] = "cancel_nonexist"

    steps = [
        {"action": "cancel", "expect_status": 404, "expect_json": {"error": ERR_NOT_FOUND}},
    ]

    if verbose:
        print(f"[patient_cancel_nonexist] p={p}")

    return attach_expect(p, steps, scenario="cancel_nonexist")
