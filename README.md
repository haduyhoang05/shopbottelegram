# 🤖 Telegram Shop Bot - VietQR & AutoBank PC 24/7

Hệ thống Bot Telegram bán hàng tự động chuyên nghiệp cho các sản phẩm số (tài khoản, key bản quyền, giftcard, khóa học...), tích hợp giao diện điều khiển **Desktop GUI**, cổng thanh toán **VietQR động** và cơ chế quét biến động số dư **AutoBank 24/7** tự động cộng tiền và trả hàng tức thì.

---

## ✨ Tính năng nổi bật

- 🖥️ **Giao diện quản trị Desktop GUI**: Thiết kế Dark Mode hiện đại bằng `CustomTkinter`, quản lý cấu hình và theo dõi log hệ thống theo thời gian thực.
- 💳 **Thanh toán VietQR động**: Tự động tạo mã QR chứa số tiền và nội dung chuyển khoản tương ứng với từng tài khoản (`Napid <user_id>`).
- ⚡ **AutoBank 24/7**: Tự động quét API ngân hàng (hỗ trợ Vietcombank, MB Bank, BIDV, Techcombank, ACB, TPBank...), tự cộng tiền và gửi thông báo ngay khi giao dịch thành công.
- 📦 **Kho hàng tự động (Auto-delivery)**: 
  - Khách mua xong hệ thống tự động xuất kho và gửi tài khoản qua tin nhắn riêng.
  - Quản lý đa chuyên mục, danh sách sản phẩm linh hoạt.
- 🔒 **Chống Race Condition & An toàn tuyệt đối**:
  - Toàn bộ chu trình mua hàng và nạp tiền được bảo vệ bằng **SQLite Transaction (`BEGIN IMMEDIATE`)**.
  - Không bao giờ xảy ra tình trạng trùng tài khoản, âm tiền ví hoặc âm kho khi nhiều khách mua cùng lúc.
- 🎟️ **Mã giảm giá (Coupon)**: Hỗ trợ giảm theo số tiền, giảm theo %, giới hạn đơn tối thiểu và số lượt dùng.
- 🤝 **Tiếp thị liên kết (Affiliate)**: Tự động trích % hoa hồng cho người giới thiệu ngay khi đơn hàng hoàn tất.
- 📢 **Broadcast**: Gửi tin nhắn thông báo hàng loạt đến tất cả người dùng bot.
- 📑 **Telegram Log Group**: Tự động bắn thông báo đơn hàng mới và giao dịch nạp tiền về nhóm quản trị Telegram.

---

## 🛠️ Hướng dẫn cài đặt & Khởi chạy

### 1. Yêu cầu hệ thống
- Hệ điều hành: Windows, macOS hoặc Linux.
- **Python 3.9+** trở lên.

### 2. Cài đặt các thư viện cần thiết
Mở Terminal / PowerShell và chạy lệnh:
```bash
pip install python-telegram-bot customtkinter requests
```

### 3. Cấu hình hệ thống
1. Tạo một bản sao cấu hình từ file mẫu:
   ```bash
   cp tele_shop_config.example.json tele_shop_config.json
   ```
2. Khởi động giao diện điều khiển:
   ```bash
   python botbanhang1.py
   ```
3. Nhập các thông tin trên giao diện:
   - **Admin ID**: ID tài khoản Telegram của bạn (Lấy bằng cách chat `/getchatid` với bot).
   - **Bot Token**: Token bot được cấp từ [@BotFather](https://t.me/BotFather).
   - **VietQR Bank / STK / CTK**: Thông tin tài khoản ngân hàng để tạo mã QR nạp tiền.
   - **API Token Coderent**: Token API AutoBank để tự động quét lịch sử giao dịch.
4. Bấm nút **💾 LƯU CONFIG** và bấm **🚀 START BOT**.

---

## 📖 Bảng lệnh quản trị trên Telegram

Sau khi vào bot, gửi lệnh `/checkadmin` để kích hoạt quyền Quản trị viên (nút **🛠 Admin Panel** sẽ hiện trên menu bot).

| Lệnh | Cú pháp | Chức năng |
|---|---|---|
| `/start` | `/start` | Mở menu chính và kiểm tra số dư ví |
| `/getchatid` | `/getchatid` | Lấy User ID hoặc Group ID |
| `/checkadmin` | `/checkadmin` | Kích hoạt quyền Admin ẩn trên bot |
| `/addcat` | `/addcat [Tên]` | Tạo chuyên mục mới |
| `/editcat` | `/editcat [ID] [Tên mới]` | Đổi tên chuyên mục |
| `/delcat` | `/delcat [ID]` | Xóa/ẩn chuyên mục |
| `/addprod` | `/addprod [CAT_ID] \| [Tên] \| [Giá] \| [Kho] \| [Nội dung]` | Thêm sản phẩm và nạp kho (mỗi món 1 dòng) |
| `/editprod` | `/editprod [PROD_ID] \| [Tên] \| [Giá] [\| Kho \| Nội dung thêm]` | Cập nhật giá/tên hoặc nạp thêm kho |
| `/delprod` | `/delprod [PROD_ID]` | Xóa/ẩn sản phẩm |
| `/coupon` | `/coupon [CODE] \| [Tiền giảm] \| [% giảm] \| [all/ID_SP] \| [Đơn tối thiểu] \| [Lượt dùng]` | Tạo mã giảm giá |
| `/couponoff` | `/couponoff [CODE]` | Tắt mã giảm giá |
| `/setcomm` | `/setcomm [USER_ID] [PERCENT]` | Cài đặt % hoa hồng cho CTV |
| `/broadcast` | `/broadcast [Nội dung]` | Gửi thông báo đến toàn bộ khách hàng |

---

## 🔒 Lưu ý bảo mật

- Không bao giờ commit hay chia sẻ file `tele_shop_config.json` hoặc database `tele_shop.sqlite3` lên mạng.
- Luôn giữ file `.gitignore` để bảo vệ Token bot và STK cá nhân của bạn.

---

## 📜 Giấy phép & Đóng góp
Dự án được phát triển nhằm mục đích tự động hóa bán hàng số trên Telegram. Mọi đóng góp (Pull Request / Issue) đều được hoan nghênh!
