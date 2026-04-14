# Rasa cho Phòng Khám (V2)

Repository này chứa mô hình chatbot sử dụng Rasa cho hệ thống Quản Lý Phòng Khám phiên bản V2, phục vụ hỗ trợ tư vấn và tương tác tự động với bệnh nhân.

## Công nghệ sử dụng
- **Rasa (Open Source)**: Framework xử lý ngôn ngữ tự nhiên (NLU) và hội thoại (Core) chính.
- **Python**: Môi trường chạy và Actions tuỳ chỉnh.

## Cài đặt môi trường

1. Tạo và kích hoạt môi trường ảo (khuyến nghị sử dụng Python 3.8 - 3.10 tuỳ phiên bản Rasa):
    ```bash
    python -m venv venv
    source venv/bin/activate  # Trên Linux/Mac
    venv\Scripts\activate     # Trên Windows
    ```

2. Cài đặt các thư viện (Rasa và phụ thuộc):
    ```bash
    pip install -r requirements.txt
    ```
    *Lưu ý:* Việc cài đặt Rasa trên Windows đôi khi đòi hỏi phải cài thêm Build Tools cho Visual Studio.

## Các lệnh cơ bản

1. Huấn luyện (Train) mô hình lại từ đầu dựa trên NLU và Domain:
    ```bash
    rasa train
    ```

2. Test thử ở console:
    ```bash
    rasa shell
    ```

3. Chạy Rasa REST API server hoặc Actions server (với Custom Actions) tùy thiết lập:
    ```bash
    rasa run actions
    rasa run --enable-api
    ```
