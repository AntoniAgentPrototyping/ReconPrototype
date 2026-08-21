# Hướng dẫn sử dụng · User guide

**Cho người dùng, không phải cho lập trình viên.** Mười sáu tài liệu còn lại trong
`docs/` viết cho người bảo trì hệ thống. Đây là tài liệu duy nhất viết cho người
thực sự dùng nó hằng tháng.

*For users, not maintainers. The other sixteen documents in `docs/` are written for
whoever maintains this system; this is the only one written for the people who use
it. Each section is Vietnamese first, then English.*

---

## 1. Hệ thống này làm gì · What this does

Mỗi kỳ đối soát, bạn tải file sàn (TikTok Shop, Shopee, Lazada) lên. Hệ thống đọc
file, áp đúng các quy tắc mà team đã dùng từ trước, và tạo ra **file Excel để xuất
hoá đơn**.

Hệ thống **không tự nghĩ ra quy tắc nào**. Mọi công thức trong đó đều được dựng lại
từ chính file Power Query và bảng tính của team, rồi đối chiếu từng dòng với kết quả
của team trước khi được đưa vào.

*Each settlement period, you upload the platform exports. The system reads them,
applies the rules the team already used, and produces the **Excel file you invoice
from**. It invents no rules: every formula was rebuilt from the team's own Power
Query and worksheets, then checked row by row against the team's own results.*

---

## 2. Một kỳ, từ đầu đến cuối · One period, start to finish

### Bước 1 — Mở kỳ cần làm

Trang đầu (**Các kỳ đối soát**) liệt kê từng kỳ. Bấm vào mã kỳ (ví dụ
`2026-05_w1`) để mở. Có thể lọc theo **Tháng** ở đầu trang. Một kỳ đã có file tải
lên nhưng chưa chạy vẫn hiện trong danh sách, với nhãn **chưa chạy** — không cần
nhớ mã kỳ để tìm lại nó.

### Bước 2 — Tải file lên

Chọn **Loại file** (đơn hàng / doanh thu, hoặc weekly / daily với Lazada) rồi chọn
file. Có thể chọn nhiều file một lúc.

Hai điều nên biết:

- **Tên cửa hàng được đọc từ tên file.** File sàn không có cột tên cửa hàng, nên tên
  file chính là danh tính cửa hàng. Nếu hệ thống không đọc được tên, nó sẽ **từ chối
  và nói rõ lý do** — đừng đổi tên file cho "qua được", hãy hỏi lại.
- **Thông tin khách hàng bị xoá ngay khi tải lên.** Tên người nhận, số điện thoại,
  địa chỉ bị loại bỏ trước khi file được lưu.

### Bước 3 — Nhập số của team

Ở mục **Số của team**, nhập tổng số từ file của team. Đây là bước quyết định: nếu
không có số này, hệ thống vẫn chạy xong nhưng kết quả sẽ là **chưa đối chiếu** —
tức là không có gì xác nhận các con số.

**Để trống khác với số 0.** Ô trống nghĩa là "team không đưa số này", và hệ thống bỏ
qua. Gõ `0` nghĩa là "team nói kỳ này bằng 0 đồng", và cả kỳ sẽ bị báo lệch.

### Bước 4 — Chạy

Về trang đầu, chọn sàn và kỳ, bấm chạy. Trang lần chạy tự cập nhật trong lúc chạy.

### Bước 5 — Đọc kết quả

| Kết quả | Nghĩa là | Làm gì |
|---|---|---|
| **ok có thể xuất HD** | Số của hệ thống khớp số của team | Tải file Excel về và dùng |
| **Cần check lại — số có vấn đề** | Lệch thật so với số của team | Xem danh sách chênh lệch. **Chưa xuất hoá đơn.** Cần người xem bên nào đúng |
| **chưa đối chiếu** | Chạy xong không lỗi, nhưng chưa có số của team | Nhập số của team rồi chạy lại |
| **đã dừng** | Hệ thống dừng và không tạo file nào | Đọc lý do trên trang lần chạy — thường là thiếu file của một cửa hàng |

Hai việc nữa trên trang lần chạy:

- **Dòng cần quyết định**: các dòng hệ thống không tự xử lý được (ví dụ phí chưa có
  trong danh mục). Có thể đánh dấu **đã xem xét** hoặc **lệch đã biết**, kèm lý do —
  quyết định sẽ đi theo dòng đó qua các lần chạy sau, hiện thành nhãn. Dòng không
  bao giờ bị ẩn: số đã đánh dấu mà tăng lên vẫn phải được nhìn thấy.
- Nếu kỳ được khai báo **chỉ một phần cửa hàng**, file Excel tải về sẽ tự ghi rõ
  điều đó ở đầu trang `PV sum` / `Summary`, kèm tên các cửa hàng thiếu — người đọc
  file không cần mở hệ thống để biết.

*Step 1 open the period (the board lists uploaded-but-not-yet-run periods too, with
a month filter) · Step 2 upload (the store comes from the filename, and
customer data is stripped on arrival) · Step 3 enter the team's figures (**blank is
not zero**) · Step 4 run · Step 5 read the result. The four results above are
"matches", "does not match", "not checked" and "stopped". Rows needing a decision
can be marked reviewed/expected with a reason — the decision follows the row across
runs and never hides it — and a partial-roster period stamps its own caveat into the
Excel file.*

---

## 3. Điều quan trọng nhất trong tài liệu này

**"đã dừng" không phải lỗi của bạn, và cũng không phải hệ thống hỏng.**

Hệ thống dừng khi nếu chạy tiếp nó sẽ tạo ra một file *trông có vẻ đầy đủ nhưng
thật ra không*. Ví dụ thật: một kỳ Shopee đến với 1 trên 17 cửa hàng. Chạy tiếp sẽ
ra một file trông bình thường và xuất thiếu hoá đơn cho 16 cửa hàng.

Dừng là **cố ý**. Một hoá đơn sai tốn kém hơn một hoá đơn muộn.

Tương tự, nếu một kỳ **thật sự** chỉ có một phần cửa hàng, hãy khai báo ở mục
**Danh sách cửa hàng** kèm lý do. Lý do đó hiện trên trang đầu để người duyệt số
sau này đọc được — đó là toàn bộ khác biệt giữa việc khai báo và việc tick một ô cho
xong.

*The most important thing here: "stopped" is not your mistake and not a malfunction.
The system stops when continuing would produce a file that looks complete and is
not — a real Shopee period arrived with 1 of 17 stores. A wrong invoice costs more
than a late one. If a period genuinely has only some stores, declare it with a
reason; the reason appears on the board for whoever reviews the numbers later, and
that is the entire difference between a declaration and a tick-box.*

---

## 4. Những gì hệ thống **không** làm

- **Không tự sửa số.** Nó báo lệch và dừng lại ở đó. Quyết định là của người.
- **Không tự chạy lại.** Chạy lại một kỳ đối soát là ghi tiền lần thứ hai, nên phải
  có người bấm.
- **Không quyết định bên nào đúng.** File của team không mặc nhiên đúng, mà file của
  hệ thống cũng vậy. Đã có năm lỗi được tìm ra trong chính bảng tính của team bằng
  cách so sánh này.

*What it does not do: change a number, re-run itself, or decide which side is right.
Five defects have already been found in the team's own workbooks by this comparison.*

---

## 5. Khi có gì đó không ổn

| Thấy gì | Nghĩa là |
|---|---|
| *"không đọc được tên cửa hàng từ tên file"* | Tên file khác quy ước. Hỏi người phụ trách, đừng tự đổi tên |
| *"file này được mã hoá bằng nhãn bảo mật"* | File có nhãn bảo mật của Microsoft. **Không có mật khẩu nào để hỏi** — cần bản không gắn nhãn, hoặc IT cấp quyền cho hệ thống |
| *"đã tải file này lên rồi"* | Đúng file cũ, trùng từng byte. Đây là bảo vệ chống xuất hoá đơn trùng |
| *"có ô ngày không đọc được"* | Sàn đổi định dạng ngày. Báo người phụ trách — những dòng đó sẽ **biến mất** khỏi bảng theo tháng |
| Một kỳ hiện **đang chạy** mãi không xong | Máy chạy nền có thể đã chết. Quản trị viên bấm *"A run appears stuck"* trên trang đầu |

*Common messages and what they mean. Note the second: a Microsoft sensitivity label
has no password to ask for.*

---

## 6. Chuyển ngôn ngữ · Language

Nút ở góc trên bên phải. Nhãn luôn là ngôn ngữ bạn sẽ chuyển **sang**, viết bằng
chính ngôn ngữ đó — nên nó dùng được kể cả khi bạn không đọc được giao diện hiện tại.

Mặc định theo ngôn ngữ trình duyệt; nếu trình duyệt không nói tiếng Anh thì mặc định
là tiếng Việt.

---

## Còn thiếu gì · What this guide does not cover

Thành thật: **chưa có ai dùng thử các màn hình này**. Tài liệu này viết từ mã nguồn
và từ hành vi hệ thống, không phải từ việc quan sát người dùng thật
([defect 2.8](08-KNOWN-DEFECTS.md)). Buổi dùng thử đầu tiên gần như chắc chắn sẽ tìm
ra chỗ tài liệu này sai hoặc thiếu — khi đó hãy sửa tài liệu, đừng sửa người dùng.

Phần quản trị (tạo tài khoản, sửa quy tắc) chưa nằm trong tài liệu này và các màn
hình đó hiện vẫn bằng tiếng Anh.

*Honestly: nobody has used these screens yet. This guide is written from the code
and the system's behaviour, not from watching anyone. The first real session will
find where it is wrong — fix the guide, not the user. Administration screens
(accounts, editing the rules) are not covered here and remain in English.*
