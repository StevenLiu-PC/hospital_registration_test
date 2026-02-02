from __future__ import annotations

from typing import Any, Dict, List


# steps_plan.py：只負責把「規格 steps」綁到 patient_raw 上（SSOT）
# - 測試端完全不寫規則
# - MockServer/Tests 都把規格當契約

def attach_expect(p: Dict[str, Any], steps: List[Dict[str, Any]], scenario: str) -> Dict[str, Any]:
    """
    把規格(steps)附加到測資上
    - p["_expect"]["scenario"]：情境名（給 report 統計用）
    - p["_expect"]["steps"]：每一步要打哪個 action + 預期什麼結果

    steps example:
      {"action":"register", "expect_status":200, "expect_json":{"status":"掛號成功"}, "number_parity":"even"}
      {"action":"query", "expect_status":200, "fields_check": True}
      {"action":"cancel", "expect_status":404, "expect_json":{"error":"掛號不存在"}}
    """
    p["_expect"] = {"scenario": scenario, "steps": steps}
    return p
