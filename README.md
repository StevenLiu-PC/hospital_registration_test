# Hospital Registration Test (Smoke / Stress / Chaos)

這是一個用 **Python + Pytest + Requests** 撰寫的「醫院網路掛號系統」自動化測試專案示範。  
目標是用 **可讀、可擴充、可重複執行** 的方式，驗證掛號流程的正向/負向規則，並支援併發壓測與故障注入（Chaos）測試。

---

## 功能特色

- **Smoke Tests**：快速確認核心功能可用（可做成 gate / pre-check）
- **Rule-based Negative Tests**：缺欄位、格式錯誤、重複掛號、取消不存在等
- **Stress Tests**：多執行緒併發請求，觀察成功率與極端情境
- **Chaos Tests（可選）**：用可控方式注入延遲、timeout、5xx 等故障，驗證系統韌性
- **一致的資料與規格**：測試資料與錯誤規格集中管理（避免規格分裂）

---

## Quick Start

###  Create venv & install

```bash
python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Windows (CMD)
# .\.venv\Scripts\activate.bat

# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt

python -m api.mock_server
# default: http://127.0.0.1:5000
pytest -q -s tests/test_smoke_flow.py
pytest -q -s tests/test_stress_flow.py
pytest -q -s tests/test_chaos_flow.py
