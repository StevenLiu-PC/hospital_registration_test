from __future__ import annotations

import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict

import pytest
from reporting.report_utils import begin_case

from utils.data_utils import (
    patient_success,
    patient_late,
    patient_invalid_id,
    patient_invalid_date,
    patient_missing_required,
    patient_duplicate,
    patient_cancel_nonexist,
    run_steps,  #  同一個 executor：Chaos 也驗 expect_json/parity/fields_check
)

# Chaos Test：只做「注入不穩定」+ 統計（DDE/INFRA）
# - 驗證規則（expect_json/parity/fields_check）全部在 data_utils.verify_step_assertions


# 可調參數（CHAOS）

TOTAL_PATIENTS = 1000          #  這次 Chaos 要跑幾筆（以前你叫 TOTAL_CASES）
MAX_WORKERS = 50               #  併發數
FAIL_RATE = 0.1                #  normal/late 允許混入一定比例髒資料（走 patient_factory 的 inject_fault）

# Chaos「環境注入」參數（送到 mock_server headers）
LATENCY_PROBABILITY = 0.2
LATENCY_RANGE = (1.5, 3.0)

ERROR_5XX_PROBABILITY = 0.02

# 混情境比例（策略層）
#  只有 normal/late 允許混 fail_rate（髒資料）
#  其他情境固定走你寫死的規格，不混髒比較好讀報表
SCENARIOS = [
    ("normal", lambda *, seed: patient_success(seed=seed, fail_rate=FAIL_RATE)),
    ("late", lambda *, seed: patient_late(seed=seed, fail_rate=FAIL_RATE)),

    ("invalid_id", lambda *, seed: patient_invalid_id(seed=seed)),
    ("invalid_date", lambda *, seed: patient_invalid_date(seed=seed)),
    ("missing_required", lambda *, seed: patient_missing_required(["name"], seed=seed)),
    ("duplicate", lambda *, seed: patient_duplicate(seed=seed)),
    ("cancel_nonexist", lambda *, seed: patient_cancel_nonexist(seed=seed)),
]


def _attach_chaos_headers(patient_raw: Dict[str, Any], *, seed: int) -> None:
    """
     把 chaos 注入參數「透過 headers」送到 mock_server（server 才會注入 latency/5xx）
    - 注意：這是「server 不穩定」，不是髒資料（髒資料是 FAIL_RATE 走 patient_factory inject_fault）
    """
    patient_raw.setdefault("_expect", {})
    patient_raw["_expect"].setdefault("headers", {})
    patient_raw["_expect"]["headers"].update({
        "X-Latency-Prob": str(LATENCY_PROBABILITY),
        "X-Latency-Min": str(LATENCY_RANGE[0]),
        "X-Latency-Max": str(LATENCY_RANGE[1]),
        "X-Error5xx-Prob": str(ERROR_5XX_PROBABILITY),
        "X-Seed": str(seed),  #  可重現
    })


def run_one(
    seed: int,
    register_api: Callable[[Dict[str, Any]], Any],
    cancel_api: Callable[[Dict[str, Any]], Any],
    query_api: Callable[[Dict[str, Any]], Any],
):
    """ Chaos 單筆：只做策略（抽 scenario）+ 丟給 executor"""
    begin_case()

    rng = random.Random(seed)
    sc, build = rng.choice(SCENARIOS)
    patient_raw = build(seed=seed)

    #  scenario 標記（報表用）
    patient_raw.setdefault("_expect", {})
    patient_raw["_expect"]["scenario"] = sc

    #  送 chaos 注入 headers（server 端要吃這些 header 才會 sleep/5xx）
    _attach_chaos_headers(patient_raw, seed=seed)

    return run_steps(
        test_type="CHAOS",
        seed=seed,
        patient_raw=patient_raw,
        register_api=register_api,
        cancel_api=cancel_api,
        query_api=query_api,
        verbose_fail=True,
    )


@pytest.mark.chaos
def test_chaos_concurrent(register_api, cancel_api, query_api):
    """
     Chaos 主體：只做併發 + 統計
    """
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = [ex.submit(run_one, i, register_api, cancel_api, query_api) for i in range(TOTAL_PATIENTS)]
        for fu in as_completed(futs):
            results.append(fu.result())

    # 統計：RULE_FAIL / INFRA_FAIL（分類在 executor，不在測試檔）
    by_type = defaultdict(int)
    by_scenario = defaultdict(lambda: {"pass": 0, "fail": 0})

    for r in results:
        if r.ok:
            by_scenario[r.scenario]["pass"] += 1
        else:
            by_scenario[r.scenario]["fail"] += 1
            by_type[r.fail_type or "UNKNOWN"] += 1

    total_pass = sum(v["pass"] for v in by_scenario.values())
    total_fail = sum(v["fail"] for v in by_scenario.values())

    print(f"\n[CHAOS] total={TOTAL_PATIENTS} pass={total_pass} fail={total_fail}")
    print(f"[CHAOS] fail_type={dict(by_type)}")
    print("[CHAOS] scenario breakdown:")
    for sc in sorted(by_scenario.keys()):
        s = by_scenario[sc]
        print(f" - {sc} pass={s['pass']} fail={s['fail']}")

    #  Chaos 通常是觀測型，不一定硬 Gate（看你的面試故事）
