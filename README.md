# Rasa cho Phòng Khám (V2)

Chatbot sử dụng Rasa cho hệ thống Quản Lý Phòng Khám V2.

## Cài đặt môi trường

1. Tạo và kích hoạt môi trường ảo:
    ```bash
    python -m venv venv
    source venv/bin/activate  # Linux/Mac
    venv\Scripts\activate  # Windows
    ```

2. Cài đặt Rasa và các phụ thuộc:
    ```bash
    pip install -r requirements.txt
    ```

## Chạy Rasa

1. Train mô hình:
    ```bash
    rasa train
    ```

2. Chạy Rasa shell để test trực tiếp trên console:
    ```bash
    rasa shell
    ```

3. Chạy Rasa server hoặc hành động tùy chỉnh:
    ```bash
    rasa run actions
    rasa run
    ```
