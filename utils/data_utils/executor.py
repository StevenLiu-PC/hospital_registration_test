from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .failfmt import format_fail
from .patient_factory import strip_meta
from .spec import REQUIRED_FIELDS



# executor.py：唯一的「執行 + 驗證」引擎（SSOT）

# - tests 不判斷 action（register/query/cancel）
# - tests 不寫 parity（n%2）
# - tests 不寫 fields_check
# - tests 不寫 print_fail/execute_steps
# - 所有 if-else / assert 都集中在這裡
# - 支援從 patient_raw["_expect"]["headers"] 讀注入參數
# - 每個 step 也可選擇性加 step["headers"] 覆蓋/追加
# - 實際呼叫 register_api/cancel_api/query_api 時把 headers 傳下去



@dataclass
class StepRunResult:
    ok: bool                                # 這筆 case 是否成功
    fail_type: Optional[str] = None         # RULE_FAIL / INFRA_FAIL（成功就 None）
    reason: Optional[str] = None            # 失敗原因（人看得懂）
    scenario: str = "unknown"               # 情境名（用於報表）
    seed: int = -1                          # seed（可重現）
    patient_id: str = "N/A"                 # patient id（定位用）
    fault: Any = None                       # _fault（debug 用）
    step: Optional[Dict[str, Any]] = None   # 哪一步壞
    got: Optional[Dict[str, Any]] = None    # 實際拿到什麼
    latency_ms: Optional[float] = None      # 最後一步 latency（近似值，給你快速判斷）


def _safe_json(resp: Any) -> Dict[str, Any]:
    """避免 resp.json() 爆掉，至少拿到 text"""
    try:
        return resp.json()
    except Exception:
        return {"_raw": getattr(resp, "text", "")}


def _classify_fail(*, resp: Any = None, exc: Exception = None) -> str:
    """
    失敗分類（SSOT）：
    - INFRA_FAIL：timeout/exception/5xx
    - RULE_FAIL ：非 5xx，視為規格不符
    """
    if exc is not None:
        return "INFRA_FAIL"

    if resp is not None:
        try:
            sc = int(getattr(resp, "status_code", 0) or 0)
        except Exception:
            sc = 0
        if 500 <= sc <= 599:
            return "INFRA_FAIL"

    return "RULE_FAIL"



#  headers 小工具（SSOT）
# - 讓 Chaos 注入參數可以一路傳到 mock_server
# - 避免空 dict / 非 dict 造成誤判

def _normalize_headers(headers: Any) -> Optional[Dict[str, str]]:
    """
    headers 正規化：
    - None / 空 dict -> None（避免 server 誤判「有帶但其實沒意義」）
    - dict -> 轉成 dict[str,str]（值都轉字串，requests 也比較穩）
    """
    if not headers:
        return None
    if not isinstance(headers, dict):
        return None

    out: Dict[str, str] = {}
    for k, v in headers.items():
        if k is None:
            continue
        ks = str(k).strip()
        if not ks:
            continue
        #  header value 轉字串（避免 float/int 直接塞進去有時會怪）
        out[ks] = "" if v is None else str(v)

    return out or None


def _merge_headers(base: Any, extra: Any, *, scenario: str) -> Optional[Dict[str, str]]:
    """
    合併 headers：
    - base = patient_raw["_expect"]["headers"]（整筆 case 共用）
    - extra = step["headers"]（單一步覆蓋/追加）
    - extra 覆蓋 base 同名 key
    - 補上 X-Scenario（讓 report 端永遠吃得到 scenario）
    """
    b = _normalize_headers(base)
    e = _normalize_headers(extra)

    if not b and not e:
        return None

    merged: Dict[str, str] = {}
    if b:
        merged.update(b)
    if e:
        merged.update(e)

    #  保底：讓 conftest/report 一定拿得到 scenario
    merged.setdefault("X-Scenario", scenario)

    return merged or None


# ----------------------------
# 規格驗證（你要求的 verify_step_assertions）
# - 所有 if-else / assert 都在這裡
# - tests 端只要丟 resp + step_spec + patient_clean 就好
# ----------------------------
def verify_step_assertions(
    *,
    step_spec: Dict[str, Any],
    action: str,
    resp: Any,
    data: Dict[str, Any],
    patient_clean: Dict[str, Any],
) -> None:
    """
    step_spec 支援的規格：
    - expect_status：預期 http status code
    - expect_json：預期回傳 json 中指定 key/value
    - number_parity：派號奇偶（even/odd）
    - fields_check：True → query 回來要比 REQUIRED_FIELDS 是否一致
    """
    exp_status = step_spec.get("expect_status")
    exp_json = step_spec.get("expect_json")
    parity = step_spec.get("number_parity")
    fields_check = step_spec.get("fields_check", False)

    #  status_code 斷言
    if exp_status is not None and getattr(resp, "status_code", None) != exp_status:
        raise AssertionError(
            f"status_code 不符合 expect_status | expected={exp_status} got={getattr(resp, 'status_code', None)}"
        )
    #  expect_json 斷言
    if isinstance(exp_json, dict):
        if not isinstance(data, dict):
            raise AssertionError("預期 json dict 但實際拿不到 json dict")
        for k, v in exp_json.items():
            if data.get(k) != v:
                raise AssertionError(f"回傳欄位不符合 expect_json: {k} | expected={v} got={data.get(k)}")
    #  parity（奇偶）斷言：只在 register 成功且有 number 時檢查
    #     測試檔不再自己算 n % 2
    if (
         parity  
        and action == "register"  # 只針對 register 動作做 parity 規則（避免 query/cancel 誤觸發）
        and getattr(resp, "status_code", None) == 200  # API 必須成功（200）才驗證業務欄位，失敗情境交給錯誤碼斷言處理
        and isinstance(data, dict)  
        and "number" in data 
    ):
        n = int(data["number"])  # Normalize：把 number 轉成 int，避免字串型別造成判斷錯誤
        if parity == "even" and n % 2 != 0: 
            raise AssertionError("number_parity 不符合：預期偶數，但拿到奇數")  
        if parity == "odd" and n % 2 != 1:  
            raise AssertionError("number_parity 不符合：預期奇數，但拿到偶數")  
    #  fields_check 斷言：只在 query 成功時做欄位一致性比對
    if fields_check and action == "query" and getattr(resp, "status_code", None) == 200 and isinstance(data, dict):  
        for f in REQUIRED_FIELDS:  # Spec-driven：用 SSOT 的 REQUIRED_FIELDS 當比對清單
            if data.get(f) != patient_clean.get(f):  
                raise AssertionError( 
                    f"query 欄位不一致: {f} | expected={patient_clean.get(f)} got={data.get(f)}"
                )  # 錯誤訊息帶 expected/got：一看就知道差在哪，方便定位是 API 回傳問題還是資料生成問題



# 單筆 steps 執行器（SSOT）
# -  action 路由也在這裡（tests 不判斷 register/query/cancel）
# -  Chaos/Smoke/Stress 共用同一份行為

def run_steps(
    *,
    test_type: str,
    seed: int,
    patient_raw: Dict[str, Any],
    register_api: Callable[[Dict[str, Any]], Any],
    cancel_api: Callable[[Dict[str, Any]], Any],
    query_api: Callable[[Dict[str, Any]], Any],
    verbose_fail: bool = True,
) -> StepRunResult:
    """
    回傳 StepRunResult，不在 tests 端 throw 一堆 try/except。
    - ok=True：代表整套 steps 都符合規格
    - ok=False：fail_type/reason/step/got 都會帶出來
    """
    expect = patient_raw.get("_expect") or {}
    steps = expect.get("steps")
    scenario = str(expect.get("scenario") or "unknown").strip() or "unknown"
    patient_id = str(patient_raw.get("id", "N/A"))
    fault = patient_raw.get("_fault")

    #  這筆 case 的「共用 headers」（Chaos 注入參數會放這裡）
    base_headers = expect.get("headers")

    # 本地比對用：去掉 _ 開頭 meta（避免干擾欄位一致性）
    patient_clean = strip_meta(patient_raw)

    if not isinstance(steps, list) or not steps:
        msg = format_fail(
            test_type=test_type,
            seed=seed,
            scenario=scenario,
            patient_id=patient_id,
            fail_type="RULE_FAIL",
            step={"action": "plan"},
            reason="測資缺 _expect.steps（規格沒綁上去）",
            got={"_expect": expect},
            fault=fault,
        )
        if verbose_fail:
            print(msg)
        return StepRunResult(
            ok=False,
            fail_type="RULE_FAIL",
            reason="missing _expect.steps",
            scenario=scenario,
            seed=seed,
            patient_id=patient_id,
            fault=fault,
            step={"action": "plan"},
            got={"_expect": expect},
        )

    # 逐步執行 steps
    for idx, step in enumerate(steps, start=1):
        action = step.get("action")
        step_view = {**step, "_idx": idx}  # 方便你 fail trace 看到第幾步

        #  step 也可有 headers（單一步覆蓋/追加）
        step_headers = step.get("headers")

        #  合併 headers：base + step（step 覆蓋 base）
        #    這裡會自動補 X-Scenario，讓 report 端永遠有 scenario
        call_headers = _merge_headers(base_headers, step_headers, scenario=scenario)

        # 1) 路由：依 action 打不同 API（ tests 不再判斷 action）
        try:
            t0 = time.perf_counter()
            if action == "register":
                resp = register_api(patient_raw, headers=call_headers)  #  關鍵：headers 傳下去
            elif action == "query":
                resp = query_api(patient_raw, headers=call_headers)     #  關鍵：headers 傳下去
            elif action == "cancel":
                resp = cancel_api(patient_raw, headers=call_headers)    #  關鍵：headers 傳下去
            else:
                raise AssertionError(f"不支援的 step.action: {action}")
            latency_ms = (time.perf_counter() - t0) * 1000.0
        except Exception as e:
            fail_type = _classify_fail(exc=e)
            msg = format_fail(
                test_type=test_type,
                seed=seed,
                scenario=scenario,
                patient_id=patient_id,
                fail_type=fail_type,
                step=step_view,
                reason=f"呼叫 API 發生例外: {e}",
                got={"exc": repr(e), "headers": call_headers},  #  把 headers 也打印出來（方便查）
                fault=fault,
            )
            if verbose_fail:
                print(msg)
            return StepRunResult(
                ok=False,
                fail_type=fail_type,
                reason=str(e),
                scenario=scenario,
                seed=seed,
                patient_id=patient_id,
                fault=fault,
                step=step_view,
                got={"exc": repr(e), "headers": call_headers},
            )

        # 2) 取 json（保底）
        data = _safe_json(resp)

        # 3) 規格驗證（ 所有 assert 都集中 verify_step_assertions）
        try:
            verify_step_assertions(
                step_spec=step,
                action=str(action),
                resp=resp,
                data=data,
                patient_clean=patient_clean,
            )
        except Exception as e:
            fail_type = _classify_fail(resp=resp, exc=None)
            msg = format_fail(
                test_type=test_type,
                seed=seed,
                scenario=scenario,
                patient_id=patient_id,
                fail_type=fail_type,
                step=step_view,
                reason=str(e),
                got={
                    "status_code": getattr(resp, "status_code", None),
                    "json": data,
                    "headers": call_headers,  #  規格不符時也把 headers 帶出（看是不是注入影響）
                },
                fault=fault,
                latency_ms=latency_ms,
            )
            if verbose_fail:
                print(msg)
            return StepRunResult(
                ok=False,
                fail_type=fail_type,
                reason=str(e),
                scenario=scenario,
                seed=seed,
                patient_id=patient_id,
                fault=fault,
                step=step_view,
                got={
                    "status_code": getattr(resp, "status_code", None),
                    "json": data,
                    "headers": call_headers,
                },
                latency_ms=latency_ms,
            )

    # steps 全部通過
    return StepRunResult(ok=True, scenario=scenario, seed=seed, patient_id=patient_id, fault=fault)


def execute_steps_or_raise(
    *,
    test_type: str,
    seed: int,
    patient_raw: Dict[str, Any],
    register_api: Callable[[Dict[str, Any]], Any],
    cancel_api: Callable[[Dict[str, Any]], Any],
    query_api: Callable[[Dict[str, Any]], Any],
) -> None:
    """
    Smoke 用：失敗就直接讓 pytest fail（測試檔不用寫 assert/if）
    """
    r = run_steps(
        test_type=test_type,
        seed=seed,
        patient_raw=patient_raw,
        register_api=register_api,
        cancel_api=cancel_api,
        query_api=query_api,
        verbose_fail=True,
    )
    if not r.ok:
        raise AssertionError(r.reason or "step execution failed")
