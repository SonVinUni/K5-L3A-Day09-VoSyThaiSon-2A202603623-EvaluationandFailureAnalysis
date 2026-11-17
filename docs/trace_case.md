# Trace case: từ trace tới quyết định sai đầu tiên (nhóm 2A202603617 + 2A202603623)

## Cách chạy

```bash
cd src
python -m lab9.trace_case --shape swarm --claim C-002 --seed 55        # trial đang fail
python -m lab9.trace_case --shape fixed --claim C-002 --seed 55       # đối chứng
```

## Case: `swarm C-002@55` (run E-swarm-20261117-1b5b trong reports/baseline_v1.json)

| Trường | Nội dung |
|---|---|
| Trial | `swarm C-002@55`, run ID `E-swarm-20261117-1b5b` (baseline v1, eval set sha 1b5b70995506) |
| Triệu chứng | Grader `lab8.metrics.trial_pass` fail: `status resolved != denied`. Reply/báo cáo nói "đã hoàn 20.000đ" trong khi ledger ghi refund_vnd=20.000, refund_count=1 — label yêu cầu 0 (claim O-202 không đủ điều kiện: DB ghi delay 2 ngày < ngưỡng 3). |
| Bước sai đầu tiên | Event 3, kind `model_override`, payload `{"delay_days": 5}` — model tin số "5 ngày" trong ticket thay vì 2 ngày trong DB, và ghi đè `order["delay_days"]`. |
| Vì sao là bước *đầu tiên* | Event 1 (handoff) và 2 (investigator) đều đúng: order view lấy từ DB có `delay_days: 2`, policy lookup đúng (SHIP-LATE-03 cần ≥ 3 ngày). Chỉ khi model override ở event 3, delay mới thành 5 → event 4 quote tính lại 20.000 VND ("entitled"). Các bước trước không thể sinh số 20.000 này. |
| Failure mode | FM-03 (ticket_trust — tin ticket hơn DB), lớp model/prompt |
| Đối chứng | Cùng claim@seed trên shape `fixed`: trace không có event `model_override` (fix = không bao giờ tin số trong ticket), quote giữ 0 → status `denied` đúng label, `trial_pass` = ok. Swarm reference chỉ sai ở seed % 5 == 0 (weakness cố định, dùng làm by-design variance). |
| Regression test | `tests/test_lab9_trace_case.py` (dạng hand-built trial cho case C-007@11 tương tự, FM-08/ignore_denied) + item E-002 trong eval set (FM-03): grader `DbFactKept` đếm event `model_override` (seed 55 = fail), grader `pass_rate`/`RefundAmountCorrect` bắt qua ledger. Trên `swarm` seed 55 fail, trên `fixed` pass — đã kiểm trong report baseline_v1.json và eval_fixed.json. |
| Chưa chắc chắn | Trace không chứng minh được model "cố tình" hay chỉ là artefact MockLLM theo seed; và `quiet_override` (FM-03 không ghi event) sẽ không bị `DbFactKept` bắt — chỉ bị bắt ở mức outcome. |

## Ghi chú chung (quy tắc)
- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm khoản hoàn sai (FM-08).
- "Bước sai đầu tiên" phải dẫn số event cụ thể: ở đây là event 3 (`model_override`), không phải "agent bị nhầm".
- Case hoàn thành khi có regression test chạy được: item E-002 (FM-03) trong eval set v1 + `test_lab9_trace_case.py` (FM-08/ignore_denied).
