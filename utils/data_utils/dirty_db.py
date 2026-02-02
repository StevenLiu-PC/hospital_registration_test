from __future__ import annotations

from typing import Any, Callable, Dict, List

from .patient_factory import generate_dirty_rows_for_db



# dirty_db.py：壓測前塞資料（策略模組）
# - 這不是規則判定，只是「讓 DB 有資料」更像真實環境


def seed_dirty_db(
    *,
    seed_fn: Callable[[List[Dict[str, Any]]], None],
    total: int,
    fail_rate: float,
    seed: int = 0,
) -> None:
    """
    封裝成一個小 helper：
    - 讓 tests 只要呼叫 seed_dirty_db(...)，不用自己寫 for loop
    - 真正塞 DB 的方式交給外部 seed_fn（例如 fixture 提供）
    """
    rows = generate_dirty_rows_for_db(total=total, fail_rate=fail_rate, seed=seed)
    seed_fn(rows)
