# 這支 conftest 是「環境設置 + 報表記錄員」                                     #  角色定位：測試環境/記錄，不做規則判斷
# 統一提供 tests 會用到的三個 API fixture：register_api / cancel_api / query_api  #  tests 端只打 fixture，不直接碰 requests
# 統一做：每個 test 前 reset（測試隔離）、記錄每次 API call、記錄每個 pytest case 結果、session 結束輸出報表

from __future__ import annotations  #  讓型別註記（|）在舊版 python 也更穩
import os                            #  讀環境變數（MOCK_BASE_URL 等）
import time                          #  計算 latency（毫秒）
from threading import Lock           #  duplicate 情境的「第幾次 register」需要 thread-safe 計數
from requests.adapters import HTTPAdapter  #  提高 connection pool，支援壓測併發
from datetime import datetime         #  seed_dirty_db：產資料用 now

# 測資工具：產生病患資料（給 seed / tests 用） 同時匯入故障注入工具，用在 seed_dirty_db 造髒資料
from utils.data_utils import generate_patient, inject_fault, make_rng, FAIL_TYPES  #  SSOT 測資/故障注入都集中在 data_utils

import pytest                        #  pytest fixture/hook/mark
import requests                      #  requests.Session 統一發送 HTTP

# 報表記錄工具：把每次 API call、每個 test case 結果收集起來
# 目的：
# - 本機私有版：有 reporting 模組 -> 正常記錄內控報表
# - GitHub 公開版：沒有 reporting 模組 -> 不要讓 pytest / CI 直接炸掉
try:
    from reporting.report_utils import (  #  「記錄」發生在這裡，規則判斷在 report_rules / data_utils
        record_api_call,                 #  記 API call（action/status/latency/scenario）
        set_run_meta,                    #  記本次 run 的 meta（env/base_url/timeout）
        record_test_case,                #  記 pytest case 層級結果（passed/failed/duration）
    )

    # 報表輸出：pytest session 結束後輸出 md/csv/json
    from reporting.report_export import export_reports  #  sessionfinish 時統一輸出報表

    REPORTING_AVAILABLE = True  #  私有完整模式：真的產內控報表

except ModuleNotFoundError:
    REPORTING_AVAILABLE = False  #  公開展示模式：沒有 reporting 也要能跑測試
    print("[WARN] reporting module not found, skip custom reporting.")

    # no-op：讓下面 _record / session hooks 照常呼叫，不中斷 pytest
    def record_api_call(*args, **kwargs):
        pass

    def set_run_meta(*args, **kwargs):
        pass

    def record_test_case(*args, **kwargs):
        pass

    def export_reports(*args, **kwargs):
        return {}


# ------------------------------
# Mock-only 全域設定
# ------------------------------

DEFAULT_BASE_URL = "http://127.0.0.1:5000"  #  mock server 預設位置
DEFAULT_TIMEOUT = 5                         #  預設 timeout 秒數
DEFAULT_RESET_PATH = "/admin/reset"         #  reset endpoint（測試隔離）
DEFAULT_SEED_PATH = "/admin/seed"           #  seed endpoint（塞髒資料/既有資料）


# 目的：給 seed endpoint path（預設 /admin/seed），可用環境變數覆蓋
@pytest.fixture(scope="session")
def seed_path() -> str:
    return os.getenv("MOCK_SEED_PATH", DEFAULT_SEED_PATH)  #  可切換不同 server 路由

# reset endpoint path（預設 /admin/reset），可用環境變數覆蓋
@pytest.fixture(scope="session")
def reset_path() -> str:
    return os.getenv("MOCK_RESET_PATH", DEFAULT_RESET_PATH)  #  專案換路由不動測試碼

# 目的：mock server base url（預設本機 127.0.0.1:5000），可用環境變數覆蓋
@pytest.fixture(scope="session")
def mock_base_url() -> str:
    return os.getenv("MOCK_BASE_URL", DEFAULT_BASE_URL)  #  支援切環境（mock / staging）

# API timeout（預設 5 秒），可用環境變數覆蓋
@pytest.fixture(scope="session")
def api_timeout() -> int:
    return int(os.getenv("MOCK_TIMEOUT", str(DEFAULT_TIMEOUT)))  #  測慢/測穩定可調整


@pytest.fixture(scope="session")
def http():
    """
    這個 Session 會被併發共用
    - pool_maxsize 拉大，避免 MAX_WORKERS=50 時噴 connection pool is full
    """
    s = requests.Session()  #  一次建立，給所有測試共用（省成本）
    adapter = HTTPAdapter(pool_connections=200, pool_maxsize=200, pool_block=True)  #  併發不容易爆 pool
    s.mount("http://", adapter)   #  HTTP 套用大 pool
    s.mount("https://", adapter)  #  HTTPS 套用大 pool
    yield s                       #  fixture 回傳 Session 給 tests / fixtures 用
    s.close()                     #  測試結束關閉連線


# 小工具（只做「資料整理/分類」，不做「規則判斷」）

# 目的：query_api 支援 dict 或直接傳 id，統一抽出 id
def _extract_patient_id(x):
    return x.get("id") if isinstance(x, dict) else x  #  tests 可以丟 patient dict 或直接丟 id 字串

def _strip_meta(payload: dict) -> dict:
    """送 API 前自動剝掉 _xxx 欄位，避免污染 mockserver storage"""
    clean = dict(payload)  #  複製一份，不改原本 dict（保留給報表/測試用）
    for k in list(clean.keys()):  #  用 list() 避免迭代中修改 dict
        if isinstance(k, str) and k.startswith("_"):  #  _expect/_fault/_meta 都屬於本地欄位
            clean.pop(k, None)  #  去掉 meta 欄位
    return clean

def _extract_scenario_from_headers(headers) -> str | None:
    """從 headers 取 X-Scenario（tests 若有傳就優先用，避免 report 全是 unknown）"""
    if not isinstance(headers, dict):  #  防呆：不是 dict 就不解析
        return None
    s = headers.get("X-Scenario")      #  允許 tests 主動塞 scenario 讓報表分類穩
    if isinstance(s, str) and s.strip():
        return s.strip()
    return None

"""
scenario 不要再一直 unknown
tests 端可以丟 headers={"X-Scenario": "invalid_id"} 讓 report 一定吃得到
"""

# 從測資 dict 裡，把 scenario（情境標籤）挖出來，用來做報表分類
def _extract_scenario_from_patient(patient_or_id) -> str | None:
    if not isinstance(patient_or_id, dict):  #  如果只是 id 字串，拿不到 scenario
        return None

    exp = patient_or_id.get("_expect")  #  smoke/stress/chaos 測資通常會放這裡
    if isinstance(exp, dict) and isinstance(exp.get("scenario"), str) and exp["scenario"].strip():
        return exp["scenario"].strip()

    if isinstance(patient_or_id.get("_scenario"), str) and patient_or_id["_scenario"].strip():  #  兼容其他格式
        return patient_or_id["_scenario"].strip()

    meta = patient_or_id.get("_meta")  #  兼容 _meta.scenario
    if isinstance(meta, dict) and isinstance(meta.get("scenario"), str) and meta["scenario"].strip():
        return meta["scenario"].strip()

    return None

# 把 headers 清乾淨：沒有就回 None，有內容才真的傳出去避免 {} 空 dict 造成以為有傳但其實沒意義
def _normalize_headers(headers):
    if not headers:  #  None / False 直接視為沒 headers
        return None
    if isinstance(headers, dict) and len(headers) == 0:  #  空 dict 視為無效
        return None
    return headers  #  有效 headers 才回傳

"""
讓 tests 端傳進來的 headers 更安全：
- None -> None
- 空 dict -> None（避免 server 誤判）
"""

def _resolve_scenario(patient_or_id, headers) -> str | None:
    """
    1) headers 的 X-Scenario
    2) patient/_expect scenario
    3) 沒有就 None（report_utils 會變 unknown）
    """
    return _extract_scenario_from_headers(headers) or _extract_scenario_from_patient(patient_or_id)  #  優先 headers



# duplicate 情境：把「第1次 register」視為 setup（不算 DDE 分母）

# 目的：
# - duplicate 情境會出現兩次 register：
#   第一次是「先建立既有資料」= setup（不想算進 DDE 分母）
#   第二次才是「真正要被擋」= duplicate（保留給 report_rules）
_DUP_LOCK = Lock()    #  防止併發下計數器亂跳
_DUP_REG_CALL_NO = 0  #  記錄「同一個 pytest case」裡 duplicate 的 register 第幾次

# 目的：每個 pytest case 開始先清掉 duplicate/register 計數器（確保 case 內拆分準）
def _begin_case():
    global _DUP_REG_CALL_NO
    with _DUP_LOCK:
        _DUP_REG_CALL_NO = 0  #  每個 case 重置，避免跨 case 互相污染

# 目的：duplicate + register 時，把第一次改成 duplicate_setup（不算分母），第二次起保留 duplicate
def _dup_step_scenario(original: str) -> str:
    """
    duplicate 的 register：
    - 第一次 setup => duplicate_setup（不進 DDE 分母）
    - 第二次（真正要被擋）=> duplicate（照 report_rules 期待 400）
    """
    global _DUP_REG_CALL_NO
    with _DUP_LOCK:
        _DUP_REG_CALL_NO += 1
        n = _DUP_REG_CALL_NO  #  取得第幾次 register

    if n == 1:
        return "duplicate_setup"  #  第一次只是建立既有資料，不算負向攔截
    return original               #  第二次才是「真正 duplicate」



# API call 統一記錄入口（不做規則，只做「紀錄與分類」）

def _record(action, patient_or_id, url, method, resp=None, t0=None, exc=None, headers=None):
    latency_ms = int((time.perf_counter() - t0) * 1000) if t0 else None  #  latency 毫秒
    status_code = None  #  沒回應/例外時會維持 None
    note = None         #  嘗試從 response json 取 error/message/note
    error_type = None   #  例外類型（timeout/connection error 等）

    if resp is not None:
        status_code = resp.status_code  #  HTTP status
        try:
            j = resp.json()  #  盡量把後端回的 error 記下來
            if isinstance(j, dict):
                note = j.get("error") or j.get("message") or j.get("note")
        except Exception:
            pass  #  response 不是 json 也不阻斷測試

    if exc is not None:
        error_type = type(exc).__name__  #  例外類型
        note = str(exc)                  #  例外訊息

    scenario = _resolve_scenario(patient_or_id, headers)  #  用 headers/patient 提取 scenario
    sc = scenario if (isinstance(scenario, str) and scenario.strip()) else "unknown"  #  沒有就 unknown

    # 只對 duplicate + register 做第 1 次改成 setup（方便報表分類）
    if sc == "duplicate" and action == "register":
        sc = _dup_step_scenario(sc)  #  duplicate_setup / duplicate

    record_api_call(
        action=action,           #  register/cancel/query
        patient_or_id=patient_or_id,  #  原始輸入（方便報表追查）
        url=url,                 #  endpoint
        method=method,           #  GET/POST
        status_code=status_code, #  HTTP status
        latency_ms=latency_ms,   #  latency
        error_type=error_type,   #  timeout/connection error 等
        note=note,               #  後端錯誤訊息
        scenario=sc,             #  報表分類 key
    )


def _ping_mockserver(base_url: str, timeout_sec: int):
    """探活用：有 /health 最好；沒有也不擋測試"""
    try:
        requests.get(f"{base_url}/health", timeout=timeout_sec)  #  不阻斷，只是暖機/提示
    except Exception:
        pass



# fixtures：tests 永遠只用這三個 API fixture

# 目的：
# - tests 只用 register_api/cancel_api/query_api
# - 這三個 fixture 內部統一做：strip_meta、normalize_headers、_record（報表記錄）
# -  讓 tests 檔保持乾淨：不碰 requests、不自建 logging

# 送出「掛號/建立資料」的標準入口（POST /register）
@pytest.fixture
def register_api(http, mock_base_url, api_timeout):
    def _register(patient: dict, headers=None):
        url = f"{mock_base_url}/register"     #  register endpoint
        t0 = time.perf_counter()              #  計時起點
        hdr = _normalize_headers(headers)      #  header 防呆（空 dict -> None）
        try:
            payload = _strip_meta(patient) if isinstance(patient, dict) else patient  #  避免 _expect 污染 DB
            resp = http.post(url, json=payload, timeout=api_timeout, headers=hdr)     #  送 request
            _record("register", patient, url, "POST", resp=resp, t0=t0, headers=hdr) #  統一記錄
            return resp
        except Exception as e:
            _record("register", patient, url, "POST", t0=t0, exc=e, headers=hdr)     #  例外也記錄（INFRA）
            raise
    return _register


# 代表：送出「取消掛號/刪除」的標準入口（POST /cancel）
@pytest.fixture
def cancel_api(http, mock_base_url, api_timeout):
    def _cancel(patient: dict, headers=None):
        url = f"{mock_base_url}/cancel"       #  cancel endpoint
        t0 = time.perf_counter()              #  計時
        hdr = _normalize_headers(headers)      #  header 防呆
        try:
            payload = _strip_meta(patient) if isinstance(patient, dict) else patient  #  避免 meta 污染 DB
            resp = http.post(url, json=payload, timeout=api_timeout, headers=hdr)     #  送 request
            _record("cancel", patient, url, "POST", resp=resp, t0=t0, headers=hdr)    #  記錄
            return resp
        except Exception as e:
            _record("cancel", patient, url, "POST", t0=t0, exc=e, headers=hdr)        #  例外也記錄
            raise
    return _cancel


# 代表：送出「查詢狀態/查 DB」的標準入口（GET /query?id=xxx）
@pytest.fixture
def query_api(http, mock_base_url, api_timeout):
    def _query(patient_or_id, headers=None):
        pid = _extract_patient_id(patient_or_id)  #  支援傳 dict 或 id
        url = f"{mock_base_url}/query"            #  query endpoint
        t0 = time.perf_counter()                  #  計時
        hdr = _normalize_headers(headers)          #  header 防呆
        try:
            resp = http.get(url, params={"id": pid}, timeout=api_timeout, headers=hdr) #  query 用 params
            _record("query", patient_or_id, url, "GET", resp=resp, t0=t0, headers=hdr) #  記錄（scenario 也靠這裡解析）
            return resp
        except Exception as e:
            _record("query", patient_or_id, url, "GET", t0=t0, exc=e, headers=hdr)     #  例外也記錄
            raise
    return _query



# reset：每個 test 前清理（測試隔離）
@pytest.fixture
def reset_api(http, mock_base_url, api_timeout, reset_path):
    def _reset():
        return http.post(f"{mock_base_url}{reset_path}", timeout=api_timeout)  #  呼叫 /admin/reset
    return _reset


@pytest.fixture(autouse=True, scope="function")
def _reset_before_each_test(reset_api):
    _begin_case()  #  每個 pytest case 開始先清 duplicate/register 計數（避免跨 case 污染）
    try:
        reset_api()  #  reset mock server DB（測試隔離）
    except Exception:
        pass  #  reset 失敗不硬擋（避免整包測試直接死）
    yield


# seed_dirty_db：給 stress/chaos 用的「塞髒資料」
@pytest.fixture
def seed_dirty_db(http, mock_base_url, api_timeout, seed_path):
    """
    FAIL_RATE 是 tests/test_stress_flow.py
    - 這裡只用 fail_rate 算 dirty_n
    - 每筆髒資料用 inject_fault() 直接打髒（不再出現 fault_rate=1.0）
    """
    def _seed(*, total: int, fail_rate: float, prefix: str = "USER", config=None, now: datetime | None = None):
        dirty_n = int(total * fail_rate)  #  要塞幾筆髒資料（比例控制）
        if dirty_n <= 0:
            return None                   #  0 就不做事

        now = now or datetime.now()       #  給測資用的 now（可固定以利重現）

        rows = []                         #  最終要送進 /admin/seed 的 rows
        for i in range(dirty_n):
            # 先做乾淨資料（fault_rate=0.0 保證乾淨）                         #  先乾淨，再注入故障，流程清楚
            p = generate_patient(
                seed=i,                   #  讓資料可重現
                now=now,                  #  固定時間可重現日期
                prefix=prefix,            #  id 前綴（例如 USER）
                config=config,            #  測資可客製（若你有 config）
                fault_rate=0.0,           #  保證先產乾淨（髒由 inject_fault 負責）
            )

            rng = make_rng(i)             #  用 seed 做可重現 random
            inject_fault(
                p,                        #  直接把 p 打髒（改 id/date/missing…）
                rng,                      #  可重現
                fail_types=FAIL_TYPES,    #  允許注入哪些故障類型
                seed=i,                   #  記錄 seed（若你 data_utils 需要）
                prefix=prefix,            #  讓 id 邏輯一致
            )

            rows.append(_strip_meta(p))   #  送 server 前剝掉 _expect/_meta，避免污染 DB

        url = f"{mock_base_url}{seed_path}"                     #  /admin/seed
        resp = http.post(url, json={"rows": rows}, timeout=api_timeout)  #  把 rows 塞進 mock DB

        # 如果 seed 失敗，印出 body 方便你馬上 debug
        if resp is not None and getattr(resp, "status_code", None) != 200:
            try:
                print(f"[seed_dirty_db] status={resp.status_code} body={resp.text}")
            except Exception:
                print(f"[seed_dirty_db] status={resp.status_code}")

        return resp

    return _seed



# Hook：抓 pytest case 結果 → 丟給 report_utils（case-level）
def _get_scenario_from_item(item) -> str:
    """
    目的：取得 pytest case 層級 scenario
    - 優先 item.scenario_type（你在 test 動態塞）
    - 其次 @pytest.mark.scenario("xxx")
    - 最後 unknown
    """
    s = getattr(item, "scenario_type", None)  #  若 test 動態塞 item.scenario_type 就吃它
    if s:
        return str(s)
    mk = item.get_closest_marker("scenario")  #  吃 marker（最常用）
    if mk and mk.args:
        return str(mk.args[0])
    return "unknown"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call":   #  只記「測試本體」階段，不記 setup/teardown
        return
    # 目的：把每個 pytest case 的 pass/fail、duration、error 記進 report_utils
    scenario = _get_scenario_from_item(item)  #  case-level scenario
    record_test_case(
        scenario=scenario,                    #  報表分類用
        passed=rep.passed,                    #  True/False
        duration_ms=rep.duration * 1000.0,    #  秒→毫秒
        nodeid=item.nodeid,                   #  pytest case 名稱（可追查）
        error=str(rep.longrepr) if rep.failed else None,  #  失敗時存 stacktrace
    )


# 整包 pytest 開始跑之前：先把「這次測試環境資訊」記下來，順便摸一下 mock server 活著沒。
def pytest_sessionstart(session):
    base_url = os.getenv("MOCK_BASE_URL", DEFAULT_BASE_URL)   #  讀 env（沒有就 default）
    timeout = int(os.getenv("MOCK_TIMEOUT", str(DEFAULT_TIMEOUT)))  #  讀 env

    set_run_meta(env="mock", base_url=base_url, timeout=timeout)  #  報表 meta（環境資訊）
    _ping_mockserver(base_url, timeout)                            #  探活（不擋測試）

# 整包 pytest 全跑完後：把你收集到的 API call / test case 統計輸出成報表檔。
def pytest_sessionfinish(session, exitstatus):
    if not REPORTING_AVAILABLE:  #  公開版沒有 reporting 時，跳過自訂報表輸出
        print("\n[API Call Report]")  #  CLI 提示：目前是公開展示模式
        print(" - custom reporting skipped (reporting module not available)")
        return

    paths = export_reports(out_dir="reports", filename_prefix="mock", slow_threshold_ms=1000)  #  統一輸出
    print("\n[API Call Report]")          #  CLI 提示：報表在哪
    print(" - MD  :", paths.get("md"))    #  markdown
    print(" - CSV :", paths.get("csv"))   #  csv
    print(" - JSON:", paths.get("json"))  #  json