import pytest  # pytest 測試框架（parametrize/fixture/mark 都靠它）

from utils.data_utils import (  #  全部只從 SSOT 入口拿（不要 test 自己推導規則）
    patient_success,
    patient_late,
    patient_invalid_id,
    patient_cancel_nonexist,
    patient_invalid_date,
    patient_duplicate,
    patient_missing_required,
    REQUIRED_FIELDS,
    execute_steps_or_raise,  #  測試檔只呼叫，不做 action 路由/不做 parity/不做欄位比對
)


# Smoke Gate：小量情境快速確認 register/query/cancel 基本沒壞
# - 測試端「去邏輯化」：不寫 if-else 規則、不寫 assert 規則
# - 規格全部由 patient_* 產生的 _expect.steps（SSOT）


SCENARIOS = [
    {"type": "normal", "func": patient_success},
    {"type": "late", "func": patient_late},
    {"type": "invalid_id", "func": patient_invalid_id},
    {"type": "invalid_date", "func": patient_invalid_date},
    {"type": "missing_required", "func": patient_missing_required},
    {"type": "duplicate", "func": patient_duplicate},
    {"type": "cancel_nonexist", "func": patient_cancel_nonexist},
]

# missing_required：你要測幾個必填欄位缺失，就在這裡控制
MISSING_FIELD_CASES = [[f] for f in REQUIRED_FIELDS]

# Smoke Gate 的「快慢控制」
SEEDS = range(3)


# ----------------------------
# 產生 cases（只有「展開策略」；不寫任何規則判定）
# ----------------------------
CASES = []
for s in SCENARIOS:
    for seed in SEEDS:
        stype = s["type"]
        if stype == "missing_required":
            for mf in MISSING_FIELD_CASES:
                CASES.append(
                    pytest.param(
                        s,
                        seed,
                        mf,
                        id=f"missing_{'+'.join(mf)}_seed{seed}",
                        marks=pytest.mark.scenario(stype),
                    )
                )
        else:
            CASES.append(
                pytest.param(
                    s,
                    seed,
                    None,
                    id=f"{stype}_seed{seed}",
                    marks=pytest.mark.scenario(stype),
                )
            )


def _build_patient(scenario, seed: int, missing_fields):
    """只產生測資；規則/斷言交給 executor"""
    if scenario["type"] == "missing_required":  
        # seed：固定可回放；verbose 關掉避免污染輸出
        return scenario["func"](missing_fields=missing_fields, seed=seed, verbose=False)  
    return scenario["func"](seed=seed, verbose=False)  # 統一用 seed 產生可重現測資



@pytest.mark.parametrize("scenario, seed, missing_fields", CASES)
def test_smoke_gate_steps(register_api, cancel_api, query_api, scenario, seed, missing_fields):
    """
     Smoke Gate 測試本體（去邏輯化）：
    1) build patient（內含 _expect.steps 規格）
    2) 丟給 SSOT executor：execute_steps_or_raise
       - action 路由在 executor
       - parity/fields_check/assert 全在 verify_step_assertions
       - fail 格式全在 failfmt
    """
    patient_raw = _build_patient(scenario, seed, missing_fields)
    # 保險：確保 scenario 有寫進 _expect（方便 report 統計）
    patient_raw.setdefault("_expect", {})
    patient_raw["_expect"].setdefault("scenario", scenario.get("type", "unknown"))

    execute_steps_or_raise(
        test_type="SMOKE",
        seed=seed,
        patient_raw=patient_raw,
        register_api=register_api,
        cancel_api=cancel_api,
        query_api=query_api,
    )




@pytest.mark.parametrize("scenario, seed, missing_fields", CASES)  # 參數化：多情境覆蓋；seed 可重現
def test_smoke_gate_steps(register_api, cancel_api, query_api, scenario, seed, missing_fields):
    patient_raw = _build_patient(scenario, seed, missing_fields)  # 產生測資：含缺欄位負向注入
    patient_raw.setdefault("_expect", {})  # 防呆：確保 _expect 存在
    patient_raw["_expect"].setdefault("scenario", scenario.get("type", "unknown"))  # 追蹤：報表可按情境統計
    execute_steps_or_raise(  # Gate：測試只宣告情境，規則/斷言集中 executor
        test_type="SMOKE",
        seed=seed,  # 回放：同 seed 可重跑定位 bug
        patient_raw=patient_raw,
        register_api=register_api,  # 注入：可切 mock / real API
        cancel_api=cancel_api,
        query_api=query_api,
    )
