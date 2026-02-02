from __future__ import annotations
# utils/data_utils/__init__.py

# utils.data_utils：對外唯一入口（Facade）                            #  外部永遠只 import utils.data_utils
# - 外部（conftest / mock_server / tests）永遠只 import 這裡           #  避免規則散落
# - 內部再拆成 spec / patient_factory / executor                       #  模組可維護、可擴充

# ---- conftest 需要：產生測資 + 故障注入 ----
from .patient_factory import generate_patient, make_rng, inject_fault  #  conftest 用得到的 3 個核心工具

# ---- conftest 需要：可注入的髒資料類型清單 ----
from .spec import FAIL_TYPES  #  conftest seed_dirty_db 用到（mock_server 不用，但 import 不能炸）


# 規格（SSOT）

from .spec import (  # noqa: F401
    BASE_PATIENT_TEMPLATE,
    DEPARTMENT_DOCTOR_MAP,
    APPOINTMENT_TIMES,
    REQUIRED_FIELDS,
    REGISTRATION_KEY_FIELDS,
    COUNTER_KEY_FIELDS,
    LATE_TOKEN,
    STATUS_OK,
    STATUS_LATE,
    ERR_MISSING_REQUIRED,
    ERR_INVALID_ID,
    ERR_INVALID_DATE,
    ERR_DUPLICATE,
    ERR_NOT_FOUND,
    RULES_DB,
)


# 測資工廠（SSOT：附上 _expect.steps）

from .patient_factory import (  # noqa: F401
    export_rules_db,
    make_registration_key,
    make_counter_key,
    is_late_patient,
    strip_meta,
    generate_dirty_rows_for_db,
    patient_success,
    patient_late,
    patient_invalid_id,
    patient_invalid_date,
    patient_missing_required,
    patient_duplicate,
    patient_cancel_nonexist,
)


# steps executor（唯一判定引擎）

from .executor import (  # noqa: F401
    StepRunResult,
    verify_step_assertions,
    run_steps,
    execute_steps_or_raise,
)

__all__ = [
    # conftest 必需
    "generate_patient", "inject_fault", "make_rng", "FAIL_TYPES",

    # mock_server / tests 常用規格
    "REQUIRED_FIELDS", "REGISTRATION_KEY_FIELDS", "COUNTER_KEY_FIELDS",
    "LATE_TOKEN", "STATUS_OK", "STATUS_LATE",
    "ERR_MISSING_REQUIRED", "ERR_INVALID_ID", "ERR_INVALID_DATE", "ERR_DUPLICATE", "ERR_NOT_FOUND",
    "make_registration_key", "make_counter_key",

    # 情境測資
    "patient_success", "patient_late", "patient_invalid_id", "patient_invalid_date",
    "patient_missing_required", "patient_duplicate", "patient_cancel_nonexist",

    # 斷言引擎/steps
    "StepRunResult", "verify_step_assertions", "run_steps", "execute_steps_or_raise",
]
