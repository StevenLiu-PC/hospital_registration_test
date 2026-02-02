from __future__ import annotations

from collections import defaultdict  #  做統計（策略層）
from concurrent.futures import ThreadPoolExecutor, as_completed  #  併發（策略層）

from reporting.report_utils import begin_case  #  每筆 case 開始前 reset 記錄（你的報表機制）
from utils.data_utils import (
    patient_success,
    patient_late,
    patient_invalid_id,
    patient_invalid_date,
    patient_missing_required,
    patient_duplicate,
    patient_cancel_nonexist,
    run_steps,  #  SSOT executor：路由/判定/報警格式全部在 data_utils
)


# Stress Test：只做「併發 + 統計」
# - 不做 action 判斷
# - 不做 parity/fields_check/assert
# - 不做 _print_fail/_execute_steps


TOTAL_PATIENTS = 1000
MAX_WORKERS = 50

# 壓測混入髒資料比例（讓 DB 不是永遠乾淨）
FAIL_RATE = 0.2

# 情境 mix：只負責「挑哪個測資工廠」來產生 patient_raw（規格在 _expect.steps）
SCENARIOS = [
    ("normal", lambda *, seed: patient_success(seed=seed, fail_rate=0.0)),
    ("late", lambda *, seed: patient_late(seed=seed, fail_rate=0.0)),
    ("invalid_id", lambda *, seed: patient_invalid_id(seed=seed)),
    ("invalid_date", lambda *, seed: patient_invalid_date(seed=seed)),
    ("missing_required", lambda *, seed: patient_missing_required(["name"], seed=seed)),
    ("duplicate", lambda *, seed: patient_duplicate(seed=seed)),
    ("cancel_nonexist", lambda *, seed: patient_cancel_nonexist(seed=seed)),
]


def run_one(seed: int, register_api, cancel_api, query_api):
    """ 單筆 worker：只做「開始記錄 + 產測資 + 丟給 executor」"""
    begin_case()

    sc, build = SCENARIOS[seed % len(SCENARIOS)]  #  固定輪轉（可重現、不靠隨機）
    patient_raw = build(seed=seed)

    # 保險：確保 scenario 寫進 _expect（避免 report 變 unknown）
    patient_raw.setdefault("_expect", {})
    patient_raw["_expect"]["scenario"] = sc

    return run_steps(
        test_type="STRESS",
        seed=seed,
        patient_raw=patient_raw,
        register_api=register_api,
        cancel_api=cancel_api,
        query_api=query_api,
        verbose_fail=True,
    )


def test_stress_register_concurrent(register_api, cancel_api, query_api, seed_dirty_db):
    """
     Stress 主體：只做併發 + 統計輸出
    """
    # 先把 mock server 的 DB 塞一批髒資料（比例由 FAIL_RATE 控制）
    seed_dirty_db(total=TOTAL_PATIENTS, fail_rate=FAIL_RATE)

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = [ex.submit(run_one, i, register_api, cancel_api, query_api) for i in range(TOTAL_PATIENTS)]
        for fu in as_completed(futs):
            results.append(fu.result())

    # 按 scenario 統計 pass/fail
    stats = defaultdict(lambda: {"pass": 0, "fail": 0})
    for r in results:
        stats[r.scenario]["pass" if r.ok else "fail"] += 1

    pass_cnt = sum(v["pass"] for v in stats.values())
    fail_cnt = sum(v["fail"] for v in stats.values())

    print(f"\n總計 {TOTAL_PATIENTS} 筆併發完成 | pass={pass_cnt} fail={fail_cnt}")
    print("情境統計：")
    for sc in sorted(stats.keys()):
        s = stats[sc]
        print(f" - {sc} pass={s['pass']} fail={s['fail']}")

    
