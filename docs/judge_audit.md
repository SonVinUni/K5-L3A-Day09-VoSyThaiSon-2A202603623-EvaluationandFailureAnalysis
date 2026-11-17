# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5`
Floors (bài giảng S6): κ ≥ 0.6, Precision ≥ 0.8, Recall ≥ 0.7. Nhãn nhị phân PASS/FAIL, lớp dương = FAIL (việc của judge là TÌM lỗi).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | có (0.757/0.857/0.800) | **dùng được** — gate được cho tiêu chí giọng điệu, vẫn cần nhãn người |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **không** (κ=0.375<0.6, R=0.300<0.7) | **loại** — không dùng làm gate |

Tone (n=100, ma trận): tp=24, fp=4, fn=6, tn=66. Fail rate observed 0.28 → Rogan-Gladen (sens 0.8, spec 66/70) = 0.30.
Helpful (n=100, ma trận): tp=9, fp=0, fn=21, tn=70. P=1.0 nhưng R=0.3: judge bỏ sót 21/30 fail thật (đa số là "reply nói số tiền khác ledger" — lỗi cần code, không phải judge).

## ≥5 case audit thủ công (chọn 8 bất đồng từ file công khai)

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric ra sao |
|---|---|---|---|---|---|
| 1 | H-008 (tone) | PASS | FAIL | Người đúng | "Em đã hoàn tiền… nha" — xưng em + từ suồng sã với khách. Rubric cần danh mục xưng hô cấm (em/nha/đoà) với kênh khiếu nại |
| 2 | H-029 (tone) | PASS | FAIL | Người đúng | "không cần nhắn thêm" là giọng ra lệnh. Sửa rubric: câu mệnh lệnh với khách = FAIL |
| 3 | H-070 (tone) | PASS | FAIL | Người đúng | Emoji 😂 trong phản hồi khiếu nại. Sửa rubric: emoji chỉ chấp nhận khi khách dùng trước |
| 4 | H-042 (tone) | FAIL | PASS | Judge đúng | Judge phạt "thiếu xin lỗi" nhưng nhãn không yêu cầu xin lỗi khi thông báo kết quả. Sửa rubric: xin lỗi chỉ bắt buộc khi nhận lỗi của hệ thống |
| 5 | H-081 (tone) | FAIL | PASS | Judge đúng (nhãn sai) | Người chấm đọc nhầm ticket nên gắn PASS; câu trả lời đúng chuẩn. Sửa nhãn nguồn, giữ judge |
| 6 | HL-015 (helpful) | PASS | FAIL | Người đúng | Reply nói 55.000đ, ledger ghi 40.000đ. Judge "helpful" không đối chiếu số — lỗi loại này thuộc grader CODE (FM-08), không phải judge |
| 7 | HL-020 (helpful) | PASS | FAIL | Người đúng | 90.000đ vs ledger 120.000đ — cùng loại sai số. Giải pháp: tách rubric helpful thành "đúng số" (code) và "đúng nội dung" (judge) |
| 8 | HL-028 (helpful) | PASS | FAIL | Người đúng | Reply xin lỗi sáo rỗng, không trả được ý (sai số, thiếu thông tin). Rubric: "helpful" phải kiểm tra reply chứa đúng ít nhất một sự kiện đúng |

## Kết luận
- **tone_ok**: đạt cả ba floors (κ=0.757, P=0.857, R=0.800) → dùng được cho gate tiêu chí giọng điệu; 8 bất đồng trên đều có sửa rubric cụ thể (xưng hô, mệnh lệnh, emoji, xin lỗi, 1 nhãn nguồn sai).
- **helpful**: κ=0.375, R=0.300 → **loại**, không làm gate. Nguyên nhân chính (bằng ma trận): judge "helpful" coi reply suông lời xin lỗi là đủ, nhưng nhãn người đếm cả "reply nói số khác ledger" (HL-015/020/028/035/041/043) — lỗi này phải giao cho grader code (`reply_ledger_match`, `refund_amount_correct`), tiền luôn chấm bằng code.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai (2 CSV); cần nhãn người trên tập held-out mới trước khi dùng judge cho tiêu chí mới.
