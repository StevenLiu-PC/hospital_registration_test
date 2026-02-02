from __future__ import annotations

from typing import Any, Dict, Optional


# failfmt.py：統一失敗輸出格式（SSOT）
# - 不要每個 test 檔自己寫 _print_fail
# - 你要改格式，只改這裡

def format_fail(
    *,
    test_type: str,
    seed: int,
    scenario: str,
    patient_id: str,
    fail_type: str,
    step: Dict[str, Any],
    reason: str,
    got: Optional[Dict[str, Any]] = None,
    fault: Any = None,
    latency_ms: Optional[float] = None,
) -> str:
    lines = []
    lines.append("\n" + "=" * 88)
    lines.append(f"[{test_type} FAIL] type={fail_type}")
    lines.append(f"- scenario   : {scenario}")
    lines.append(f"- seed       : {seed}")
    lines.append(f"- patient_id : {patient_id}")
    if latency_ms is not None:
        lines.append(f"- latency_ms : {latency_ms:.1f}")
    if fault:
        lines.append(f"- _fault     : {fault}")
    lines.append(f"- step       : {step}")
    lines.append(f"- reason     : {reason}")
    if got is not None:
        lines.append(f"- got        : {got}")
    lines.append("=" * 88 + "\n")
    return "\n".join(lines)
