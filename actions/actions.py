from typing import Any, Text, Dict, List
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, FollowupAction, ActiveLoop
from rasa_sdk.forms import FormValidationAction
import mysql.connector
from mysql.connector import Error
import os
from dotenv import load_dotenv
import re  # Thêm để parse payload fallback
from rasa_sdk.types import DomainDict
from datetime import datetime, timedelta, time
import google.generativeai as genai
import json # ⚠️ QUAN TRỌNG: Nhớ import json ở đầu file actions.py

# Load file .env
load_dotenv()

genai.configure(api_key=os.getenv('GEMINI_API_KEY'))

# Kết nối DB từ .env
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME')
}

if None in DB_CONFIG.values():
    raise ValueError("Thiếu thông tin kết nối DB trong file .env.")

# Keywords để detect wrong input (mở rộng theo data)
WRONG_INPUT_KEYWORDS = {
    'date': ['đau', 'bệnh', 'tiêu chảy', 'sốt', 'ho', 'mô tả', 'triệu chứng'],
    'specialty': ['đau', 'bệnh', 'tiêu chảy', 'ngày', 'thời gian'],
    'doctor_name': ['đau', 'bệnh', 'ngày', 'thời gian', 'nội khoa'],
    'appointment_time': ['đau', 'bệnh', 'ngày', 'đau bụng', 'sáng'],
    'decription': ['ngày', 'thời gian', 'bác sĩ', 'chuyên khoa']
}

# Global variable cho mã bệnh nhân (có thể set động từ slot hoặc config sau)
# MA_BN_GLOBAL = "BN0001"  # Ví dụ: "BN001", thay bằng giá trị thực tế hoặc từ tracker.get_slot("patient_id")
def get_patient_id(tracker: Tracker) -> Text | None:
    """
    Lấy maBN (patientId) từ metadata được gửi từ server.js
    """
    metadata = tracker.latest_message.get("metadata")
    
    if metadata:
        # Tên "patientId" này phải khớp với key trong server.js
        patient_id = metadata.get("patientId") 
        
        if patient_id:
            print(f"[DEBUG] Lấy được patientId từ metadata: {patient_id}")
            return patient_id
            
    # Fallback nếu không tìm thấy (ví dụ: guest, hoặc lỗi cấu hình)
    print("[WARN] Không tìm thấy 'patientId' trong metadata. Người dùng có thể chưa đăng nhập.")
    return None


# === THÊM MỚI ACTION Ở CUỐI FILE HOẶC GẦN CÁC ACTION TRA CỨU KHÁC ===
class ActionShowDoctorSchedule(Action):
    """
    Action tra cứu và hiển thị lịch làm việc TUẦN HIỆN TẠI của một bác sĩ.
    """
    def name(self) -> Text:
        return "action_show_doctor_schedule"

    def _get_vietnamese_day_name(self, weekday_index):
        """Helper để chuyển 0-6 sang Thứ 2 - Chủ Nhật"""
        days_vn = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ Nhật"]
        return days_vn[weekday_index]

    def _format_time(self, time_obj):
        """Helper để xử lý time_obj (có thể là timedelta)"""
        if isinstance(time_obj, timedelta):
            return (datetime.min + time_obj).time().strftime('%H:%M')
        elif isinstance(time_obj, time):
            return time_obj.strftime('%H:%M')
        return str(time_obj)

    def run(self, dispatcher, tracker, domain):
        # 1. Lấy tên bác sĩ từ entity
        entities = tracker.latest_message.get('entities', [])
        doctor_name_input = next((e['value'] for e in entities if e['entity'] == 'doctor_name'), None)
        
        if not doctor_name_input:
            dispatcher.utter_message(text="Bạn muốn xem lịch làm việc của bác sĩ nào? Vui lòng nhập tên.")
            return []

        print(f"[DEBUG] Running ActionShowDoctorSchedule for: {doctor_name_input}")

        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            
            # 2. Xác thực tên bác sĩ (tránh trùng lặp)
            query_find_bs = "SELECT maBS, tenBS FROM bacsi WHERE tenBS LIKE %s"
            cursor.execute(query_find_bs, (f"%{doctor_name_input}%",))
            doctors_found = cursor.fetchall()
            
            unique_names = set(doc['tenBS'] for doc in doctors_found)
            
            if not doctors_found:
                dispatcher.utter_message(text=f"Không tìm thấy bác sĩ nào có tên '{doctor_name_input}'.")
                cursor.close()
                conn.close()
                return []
            
            if len(unique_names) > 1:
                dispatcher.utter_message(
                    text=f"Tên '{doctor_name_input}' không rõ ràng (tìm thấy: {', '.join(unique_names)}). Vui lòng nhập họ tên đầy đủ."
                )
                cursor.close()
                conn.close()
                return []
            
            # Đã tìm thấy 1 bác sĩ duy nhất
            doctor = doctors_found[0]
            maBS = doctor['maBS']
            tenBS = doctor['tenBS']

            # 3. Tính toán tuần hiện tại (Thứ 2 -> Chủ Nhật)
            today = datetime.now().date()
            start_of_week = today - timedelta(days=today.weekday())
            end_of_week = start_of_week + timedelta(days=6)

            # 4. Query lịch làm việc trong tuần (SỬA ĐỔI: Thêm AND trangthai != 'Nghỉ')
            query_schedule = """
            SELECT ngaythangnam, giobatdau, gioketthuc, trangthai
            FROM thoigiankham
            WHERE maBS = %s 
              AND DATE(ngaythangnam) BETWEEN %s AND %s
              AND (trangthai != 'Nghỉ' OR trangthai IS NULL)
            ORDER BY ngaythangnam, giobatdau
            """
            cursor.execute(query_schedule, (maBS, start_of_week, end_of_week))
            schedule_rows = cursor.fetchall()
            cursor.close()
            conn.close()

            if not schedule_rows:
                dispatcher.utter_message(
                    text=f"Bác sĩ {tenBS} không có lịch làm việc nào (không tính ngày nghỉ) trong tuần này (từ {start_of_week.strftime('%d/%m')} đến {end_of_week.strftime('%d/%m')})."
                )
                return []

            # 5. Xử lý và nhóm dữ liệu theo ngày
            schedule_by_date = {}
            for row in schedule_rows:
                date_obj = row['ngaythangnam']
                if date_obj not in schedule_by_date:
                    schedule_by_date[date_obj] = []
                schedule_by_date[date_obj].append(row)

            # 6. Tạo bảng HTML
            html_table = f"""
            <style>
                .schedule-table {{
                    width: 100%; max-width: 450px; border-collapse: collapse;
                    font-family: Arial, sans-serif; background: white;
                    border-radius: 8px; overflow: hidden; box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                }}
                .schedule-table th, .schedule-table td {{
                    padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee;
                }}
                .schedule-table th {{
                    background-color: #f8faff; color: #007bff; font-size: 14px;
                }}
                .schedule-table .date-cell {{
                    font-weight: bold; color: #333; font-size: 14px;
                }}
                .schedule-table .shift-cell div {{
                    margin-bottom: 4px;
                }}
                /* CSS trạng thái không còn cần thiết nhưng để lại cũng không sao */
                .status-work {{ color: green; font-weight: bold; }}
                .status-off {{ color: red; font-weight: bold; }}
            </style>
            <div style="font-family: Arial, sans-serif; font-size: 15px; margin-bottom: 8px;">
                📅 <strong>Lịch làm việc tuần này của Bác sĩ {tenBS}</strong><br>
                (Từ {start_of_week.strftime('%d/%m')} đến {end_of_week.strftime('%d/%m')})
            </div>
            <table class="schedule-table">
                <thead>
                    <tr>
                        <th>Ngày</th>
                        <th>Ca làm việc</th>
                    </tr>
                </thead>
                <tbody>
            """
            
            # Điền dữ liệu vào bảng
            for date_obj, shifts in sorted(schedule_by_date.items()):
                day_name_vn = self._get_vietnamese_day_name(date_obj.weekday())
                date_str = date_obj.strftime('%d/%m')
                
                shifts_html = ""
                for shift in shifts:
                    start_time = self._format_time(shift['giobatdau'])
                    end_time = self._format_time(shift['gioketthuc'])
                    
                    # SỬA ĐỔI: Bỏ hiển thị trạng thái
                    shifts_html += f"<div>{start_time} - {end_time}</div>"
                
                html_table += f"""
                    <tr>
                        <td class="date-cell" style="padding-right: 20px;">{day_name_vn} ({date_str})</td>
                        <td class="shift-cell" style="padding-left: 20px;">{shifts_html}</td>
                    </tr>
                """
            
            html_table += "</tbody></table>"
            dispatcher.utter_message(text=html_table, html=True)

        except Error as e:
            print(f"[ERROR] DB Error in ActionShowDoctorSchedule: {e}")
            dispatcher.utter_message(text=f"Lỗi khi tra cứu cơ sở dữ liệu: {e}")
        
        return []


class ActionListAllDoctors(Action):
    """
    Action tra cứu và hiển thị TẤT CẢ bác sĩ trong hệ thống.
    Có thể được gọi từ interruption.
    """
    def name(self) -> Text:
        return "action_list_all_doctors"

    def run(self, dispatcher, tracker, domain):
        print(f"[DEBUG] Running ActionListAllDoctors")
        
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            # Query để lấy TẤT CẢ bác sĩ và GOM NHÓM chuyên khoa
            query = """
            SELECT 
                bs.tenBS,
                GROUP_CONCAT(DISTINCT ck.tenCK SEPARATOR ', ') as chuyenkhoa
            FROM bacsi bs
            LEFT JOIN chuyenmon cm ON bs.maBS = cm.maBS
            LEFT JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
            WHERE bs.vaiTro = "DOCTOR" AND bs.xoa = 0
            GROUP BY bs.maBS, bs.tenBS
            ORDER BY bs.tenBS
            """
            cursor.execute(query)
            doctors = cursor.fetchall()
            cursor.close()
            conn.close()
            
            if doctors:
                html_list = f"""
                <div style="font-family: Arial, sans-serif; font-size: 15px; color: #333; background: #f8faff; border-radius: 10px; padding: 10px; border: 1px solid #cce0ff;">
                    <div style="color: #007bff; font-weight: bold; margin-bottom: 8px;">
                        📋 Danh sách bác sĩ trong hệ thống (Tổng: {len(doctors)}):
                    </div>
                """
                
                for doc in doctors:
                    specialties = doc['chuyenkhoa'] if doc['chuyenkhoa'] else 'Chưa có'
                    html_list += f"""
                    <div style="background: #ffffff; border-left: 3px solid #007bff; border-radius: 6px; padding: 6px 10px; margin-bottom: 6px;">
                        <div style="font-weight: bold; color: #007bff;">🩺 Bác sĩ {doc['tenBS']}</div>
                        <div><strong>Chuyên khoa:</strong> {specialties}</div>
                    </div>
                    """
                
                html_list += """
                    <div style="margin-top: 6px; font-style: italic;">👉 Vui lòng tiếp tục yêu cầu của bạn...</div>
                </div>
                """
                dispatcher.utter_message(text=html_list, html=True)
            else:
                dispatcher.utter_message(
                    text="Không tìm thấy bác sĩ nào trong hệ thống."
                )
                
        except Error as e:
            print(f"[ERROR] DB Error in ActionListAllDoctors: {e}")
            dispatcher.utter_message(text=f"Lỗi khi tra cứu cơ sở dữ liệu: {e}")
        
        # Action này chỉ hiển thị thông tin, không set slot
        # Form sẽ tự động hỏi lại slot đang yêu cầu
        return []


class ActionShowExaminingDoctorInForm(Action):
    """
    Action tra cứu và hiển thị bác sĩ đã khám gần nhất cho bệnh nhân.
    """
    def name(self) -> Text:
        return "action_show_examining_doctor_in_form"

    def run(self, dispatcher, tracker, domain):
        # Lấy maBN động
        patient_id = get_patient_id(tracker)

        # Kiểm tra nếu user đã đăng nhập
        if not patient_id:
            dispatcher.utter_message(text="Lỗi: Bạn cần đăng nhập để xem thông tin bác sĩ khám gần nhất.")
            return [] # Dừng action
        
        print(f"[DEBUG] Running ActionShowExaminingDoctorInForm cho bệnh nhân: {patient_id}")
        
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            # Query để lấy bác sĩ khám gần nhất dựa trên maBN
            query = """
            SELECT bs.tenBS, lk.ngaythangnamkham 
            FROM lankham lk
            JOIN bacsi bs ON lk.maBS = bs.maBS
            JOIN hosobenhnhan hs ON lk.maHS = hs.maHS
            WHERE hs.maBN = %s
            ORDER BY lk.ngaythangnamkham DESC
            LIMIT 1
            """
            cursor.execute(query, (patient_id,))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if result:
                doctor_name = result['tenBS']
                last_visit_date = result['ngaythangnamkham'].strftime('%d/%m/%Y')
                
                message = f"""
                <div style="font-family: Arial, sans-serif; font-size: 15px; color: #333;
                            background: #e7f3ff; border-left: 4px solid #007bff; border-radius: 8px;
                            padding: 12px 14px; margin: 4px 0;">
                    <div style="font-weight: bold; color: #007bff; margin-bottom: 6px;">🩺 Thông tin bác sĩ khám gần nhất:</div>
                    <div><strong>Bác sĩ:</strong> {doctor_name}</div>
                    <div><strong>Ngày khám:</strong> {last_visit_date}</div>
                    <div style="margin-top: 6px; font-style: italic;">👉 Vui lòng tiếp tục yêu cầu của bạn...</div>
                </div>
                """
                dispatcher.utter_message(text=message, metadata={"html": True})
            else:
                dispatcher.utter_message(
                    text="Không tìm thấy lịch sử khám bệnh nào cho bạn trong hệ thống."
                )
                
        except Error as e:
            print(f"[ERROR] DB Error in ActionShowExaminingDoctorInForm: {e}")
            dispatcher.utter_message(text=f"Lỗi khi tra cứu cơ sở dữ liệu: {e}")
        
        # Action này chỉ hiển thị thông tin, không set slot
        # Form sẽ tự động hỏi lại slot đang yêu cầu
        return []

# Thay thế phần ValidateCancelAppointmentForm và các action liên quan
class ActionHandleOutOfScope(Action):
    """
    Action xử lý các intent không được hỗ trợ (out-of-scope).
    Có thể được trigger trong bất kỳ context nào, kể cả khi đang trong form.
    
    QUAN TRỌNG: Trong form, sau khi xử lý sẽ QUAY LẠI FORM bằng FollowupAction
    """
    def name(self) -> Text:
        return "action_handle_out_of_scope"

    def run(
        self, 
        dispatcher: CollectingDispatcher, 
        tracker: Tracker, 
        domain: Dict[Text, Any]
    ) -> List[Dict]:
        
        # Kiểm tra xem có đang trong form không
        active_loop = tracker.active_loop.get('name') if tracker.active_loop else None
        current_task = tracker.get_slot("current_task")
        
        # Thông báo phù hợp với context
        if active_loop:
            # Đang trong form
            message = (
                "Xin lỗi, tôi không thể trả lời câu hỏi này lúc này. "
                "Tôi đang giúp bạn hoàn tất yêu cầu hiện tại. "
                "Bạn có thể hỏi lại sau khi hoàn tất, hoặc nói 'hủy' để dừng lại."
            )
            dispatcher.utter_message(text=message)
            
            # ← QUAN TRỌNG: Quay lại form bằng FollowupAction
            return [FollowupAction(active_loop)]
        
        else:
            # Không trong form - utter response chi tiết hơn
            dispatcher.utter_message(response="utter_out_of_scope")
            
            # Reset current_task nếu có
            if current_task:
                return [SlotSet("current_task", None)]
            
            return []


class ActionDefaultFallback(Action):
    """
    Action xử lý khi NLU không thể phân loại intent (fallback).
    Khác với out_of_scope: đây là khi bot "không hiểu", 
    còn out_of_scope là khi bot hiểu nhưng không hỗ trợ.
    
    QUAN TRỌNG: Trong form, action này sẽ:
    1. Thông báo không hiểu
    2. Hỏi lại requested_slot hiện tại
    3. QUAY LẠI FORM bằng FollowupAction
    """
    def name(self) -> Text:
        return "action_default_fallback"

    def run(
        self, 
        dispatcher: CollectingDispatcher, 
        tracker: Tracker, 
        domain: Dict[Text, Any]
    ) -> List[Dict]:
        
        active_loop = tracker.active_loop.get('name') if tracker.active_loop else None
        requested_slot = tracker.get_slot('requested_slot')
        
        if active_loop:
            # TRONG FORM - xử lý fallback và tiếp tục form
            
            # 1. Thông báo không hiểu
            dispatcher.utter_message(
                text="Xin lỗi, tôi không hiểu rõ câu nói của bạn. 🤔"
            )
            
            # 2. Hỏi lại slot hiện tại với gợi ý cụ thể
            if requested_slot:
                if requested_slot == "specialty":
                    dispatcher.utter_message(
                        text="Vui lòng cho biết bạn muốn khám chuyên khoa nào? "
                             "Ví dụ: nội khoa, ngoại khoa, nhi khoa, thần kinh, phụ sản, răng hàm mặt."
                    )
                elif requested_slot == "doctor_name":
                    dispatcher.utter_message(
                        text="Vui lòng nhập tên bác sĩ bạn muốn khám. "
                             "Ví dụ: bác sĩ Nguyễn Văn A, hoặc chỉ cần nhập 'Nguyễn Văn A'."
                    )
                elif requested_slot == "date":
                    dispatcher.utter_message(
                        text="Vui lòng nhập ngày hẹn theo định dạng DD/MM/YYYY. "
                             "Ví dụ: 25/10/2025"
                    )
                elif requested_slot == "appointment_time":
                    dispatcher.utter_message(
                        text="Vui lòng nhập giờ hẹn theo định dạng HH:MM (từ 8:00 đến 17:00). "
                             "Ví dụ: 14:30"
                    )
                elif requested_slot == "decription":
                    dispatcher.utter_message(
                        text="Vui lòng mô tả chi tiết tình trạng sức khỏe của bạn. "
                             "Ví dụ: 'Con tôi bị sốt 3 ngày, ho nhiều vào ban đêm'."
                    )
                elif requested_slot == "appointment_date":
                    dispatcher.utter_message(
                        text="Vui lòng nhập ngày bạn muốn hủy lịch theo định dạng DD/MM/YYYY. "
                             "Ví dụ: 25/10/2025"
                    )
                elif requested_slot == "selected_appointment_id":
                    dispatcher.utter_message(
                        text="Vui lòng chọn một lịch hẹn từ danh sách bằng cách click vào nút 'Chọn lịch này'."
                    )
                elif requested_slot == "symptoms":
                    dispatcher.utter_message(
                        text="Vui lòng mô tả các triệu chứng bạn đang gặp phải. "
                             "Ví dụ: đau đầu, sốt, ho, khó thở."
                    )
                else:
                    # Generic fallback cho các slot khác
                    dispatcher.utter_message(
                        text=f"Vui lòng cung cấp thông tin cho: {requested_slot}"
                    )
            else:
                # Không có requested_slot (trường hợp hiếm)
                dispatcher.utter_message(
                    text="Vui lòng trả lời câu hỏi phía trên hoặc nói 'hủy' để dừng lại."
                )
            
            # 3. ← QUAN TRỌNG: QUAY LẠI FORM bằng FollowupAction
            return [FollowupAction(active_loop)]
        
        else:
            # ⚠️ SỬA ĐỔI: NGOÀI FORM - Gợi ý chức năng VÀ THÊM NÚT HANDOFF
            message = (
                "Xin lỗi, tôi không hiểu yêu cầu của bạn. 😕\n\n"
                "Tôi có thể giúp bạn:\n"
                "🩺 Đề xuất bác sĩ\n"
                "📅 Đặt lịch hẹn khám bệnh\n"
                "❌ Hủy lịch hẹn\n\n"
                "Nếu các chức năng này không đúng ý bạn, bạn có muốn kết nối với hỗ trợ viên không?"
            )
            
            dispatcher.utter_message(
                text=message,
                buttons=[
                    {"title": "Đề xuất bác sĩ", "payload": "/request_doctor"},
                    {"title": "Đặt lịch hẹn", "payload": "/book_appointment"},
                    # ⚠️ MỚI: Button Handoff - payload này sẽ được xử lý đặc biệt ở frontend
                    {"title": "🧑‍💼 Kết nối hỗ trợ viên", "payload": "HANDOFF_TO_HUMAN"} 
                ]
            )
            return [SlotSet("current_task", None)]


class ValidateCancelAppointmentForm(FormValidationAction):
    """Validation cho cancel_appointment_form với hỗ trợ interruption"""
    
    def name(self) -> Text:
        return "validate_cancel_appointment_form"

    def _handle_form_interruption(self, dispatcher, tracker):
        """Xử lý interruption trong cancel form"""
        latest_message = tracker.latest_message
        
        if hasattr(latest_message, 'intent'):
            latest_intent = latest_message.intent.get('name')
        else:
            latest_intent = latest_message.get('intent', {}).get('name')

        # === THÊM MỚI: Xử lý list_all_specialties ===
        if latest_intent == "list_all_specialties":
            list_action = ActionListAllSpecialties()
            list_action.run(dispatcher, tracker, {})
            # Trả về slot dummy để form tiếp tục mà không bị gãy flow
            return {"just_listed_all_specialties_dummy": False}

        # === Xử lý explain_specialty ===
        if latest_intent == "explain_specialty":
            explain_action = ActionExplainSpecialtyInForm()
            explain_action.run(dispatcher, tracker, {})
            return {
                "specialty": tracker.get_slot("specialty"),
                "just_explained": False,
            }
        
        # === Xử lý ask_doctor_info ===
        if latest_intent == "ask_doctor_info":
            info_action = ActionShowDoctorInfoInForm()
            info_action.run(dispatcher, tracker, {})
            return {
                "doctor_name": tracker.get_slot("doctor_name"),
                "just_asked_doctor_info": False,
            }
        
        # === Xử lý list_doctors_by_specialty ===
        if latest_intent == "list_doctors_by_specialty":
            list_action = ActionListDoctorsInForm()
            list_action.run(dispatcher, tracker, {})
            return {
                "specialty": tracker.get_slot("specialty"),
                "just_listed_doctors": False,
            }

        # === THÊM MỚI: Xử lý ask_who_examined_me ===
        if latest_intent == "ask_who_examined_me":
            info_action = ActionShowExaminingDoctorInForm()
            info_action.run(dispatcher, tracker, {})
            # Trả về slot dummy để form tiếp tục
            return {"just_asked_examining_doctor": False}
        
        if latest_intent == "list_all_doctors":
            list_action = ActionListAllDoctors()
            list_action.run(dispatcher, tracker, {}) # Dùng {} cho domain
            return {"just_listed_all_doctors_dummy": False} # Trả về slot dummy để form tiếp tục
        
        if latest_intent == "ask_doctor_schedule":
            schedule_action = ActionShowDoctorSchedule()
            schedule_action.run(dispatcher, tracker, {})
            return {"just_asked_doctor_schedule_dummy": False}
        
        return {}

    def validate_appointment_date(
        self, 
        slot_value: Any, 
        dispatcher: CollectingDispatcher, 
        tracker: Tracker, 
        domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        """Validate ngày hủy lịch"""
        
        # === CHECK INTERRUPTION TRƯỚC ===
        interruption_result = self._handle_form_interruption(dispatcher, tracker)
        if interruption_result:
            return interruption_result
        
        # === VALIDATION BÌNH THƯỜNG ===
        if not slot_value:
            # dispatcher.utter_message(text="Vui lòng cung cấp ngày bạn muốn hủy lịch hẹn (DD/MM/YYYY).")
            return {"appointment_date": None}

        # Lấy maBN động
        patient_id = get_patient_id(tracker)
        if not patient_id:
            dispatcher.utter_message(text="Lỗi: Bạn cần đăng nhập để hủy lịch hẹn.")
            return {"appointment_date": None}

        date_input = str(slot_value).strip()
        
        # Validate format
        try:
            parsed_date = datetime.strptime(date_input, '%d/%m/%Y').date()
        except ValueError:
            dispatcher.utter_message(text="Ngày không hợp lệ. Vui lòng nhập theo định dạng DD/MM/YYYY.")
            return {"appointment_date": None}

        # Query DB để lấy danh sách lịch hẹn trong ngày
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT lh.mahen, lh.ngaythangnam, lh.khunggio, bs.tenBS, ck.tenCK, lh.mota
            FROM lichhen lh
            JOIN bacsi bs ON lh.maBS = bs.maBS
            JOIN chuyenkhoa ck ON lh.maCK = ck.maCK
            WHERE lh.maBN = %s AND DATE(lh.ngaythangnam) = %s AND lh.trangthai != 'Huy'
            ORDER BY lh.khunggio
            """
            cursor.execute(query, (patient_id, parsed_date))
            appointments = cursor.fetchall()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
            return {"appointment_date": None}

        if not appointments:
            dispatcher.utter_message(text=f"Không có lịch hẹn nào trong ngày {date_input}. Vui lòng chọn ngày khác.")
            buttons = [
                {"title": "Chọn ngày khác", "payload": "/cancel_appointment"},
                {"title": "Quay lại menu", "payload": "/greet"}
            ]
            dispatcher.utter_message(text="Bạn có muốn thử ngày khác không?", buttons=buttons)
            return {"appointment_date": None}

        # Hiển thị danh sách lịch hẹn
        dispatcher.utter_message(text=f"<b>📋 Danh sách lịch hẹn ngày </b> {date_input}:", metadata={"parse_mode": "HTML"})

        
        for idx, appt in enumerate(appointments, 1):
            appt_text = f"{idx}. 🩺 <b>Bác sĩ {appt['tenBS']}</b> ({appt['tenCK']})<br>Giờ: {appt['khunggio']}<br>Mã lịch: {appt['mahen']}<br>Mô tả: {appt['mota']}"
            dispatcher.utter_message(
                text=appt_text,
                buttons=[
                    {
                        "title": f"Chọn lịch này",
                        "payload": f"/select_appointment{{\"appointment_id\":\"{appt['mahen']}\"}}"
                    }
                ]
            )
        
        dispatcher.utter_message(text=f"\nTổng cộng: {len(appointments)} lịch hẹn. Vui lòng chọn lịch cần hủy.")
        
        # Trả về với appointment_date đã validate
        return {"appointment_date": date_input}

    def validate_selected_appointment_id(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        """Validate mã lịch hẹn được chọn"""
        
        # === CHECK INTERRUPTION TRƯỚC ===
        interruption_result = self._handle_form_interruption(dispatcher, tracker)
        if interruption_result:
            return interruption_result
        
        # Lấy maBN động
        patient_id = get_patient_id(tracker)
        if not patient_id:
            dispatcher.utter_message(text="Lỗi: Bạn cần đăng nhập để hủy lịch hẹn.")
            return {"appointment_date": None}


        if not slot_value:
            # dispatcher.utter_message(text="Vui lòng chọn một lịch hẹn để hủy.")
            return {"selected_appointment_id": None}
        
        # Validate appointment_id tồn tại trong DB
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT lh.mahen, lh.ngaythangnam, lh.khunggio, bs.tenBS, ck.tenCK, lh.mota
            FROM lichhen lh
            JOIN bacsi bs ON lh.maBS = bs.maBS
            JOIN chuyenkhoa ck ON lh.maCK = ck.maCK
            WHERE lh.mahen = %s AND lh.maBN = %s AND lh.trangthai != 'Huy'
            """
            cursor.execute(query, (slot_value, patient_id))
            appointment = cursor.fetchone()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
            return {"selected_appointment_id": None}

        if not appointment:
            dispatcher.utter_message(text="Không tìm thấy lịch hẹn này hoặc lịch đã bị hủy. Vui lòng chọn lại.")
            return {"selected_appointment_id": None}

        # Hiển thị thông tin lịch hẹn đã chọn
        confirm_text = f"""
        ✅ <b>Đã chọn lịch hẹn:</b><br>
        - Mã lịch: {appointment['mahen']}<br>
        - Bác sĩ: {appointment['tenBS']}<br>
        - Chuyên khoa: {appointment['tenCK']}<br>
        - Ngày: {appointment['ngaythangnam']}<br>
        - Giờ: {appointment['khunggio']}
        - Mô tả: {appointment['mota']}
        """

        dispatcher.utter_message(text=confirm_text, metadata={"parse_mode": "HTML"})


        return {"selected_appointment_id": slot_value}
    

class ActionCancelAppointmentUpdated(Action):
    """Action khởi tạo cancel form - CHỈ set context, KHÔNG hiển thị gì"""
    
    def name(self) -> Text:
        return "action_cancel_appointment"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        # CHỈ set context, KHÔNG utter message
        return [
            SlotSet("current_task", "cancel_appointment"),
            SlotSet("appointment_date", None),
            SlotSet("selected_appointment_id", None)
        ]


class ActionConfirmCancelUpdated(Action):
    """Action hiển thị xác nhận hủy lịch (sau khi form hoàn tất)"""
    
    def name(self) -> Text:
        return "action_confirm_cancel"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        selected_id = tracker.get_slot("selected_appointment_id")
        
        # Lấy maBN động
        patient_id = get_patient_id(tracker)
        if not patient_id:
            dispatcher.utter_message(text="Lỗi: Bạn cần đăng nhập để hủy lịch hẹn.")
            return {"appointment_date": None}

        if not selected_id:
            dispatcher.utter_message(text="Không có lịch hẹn được chọn.")
            return []

        # Query thông tin lịch hẹn để hiển thị confirm
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT lh.mahen, lh.ngaythangnam, lh.khunggio, bs.tenBS, ck.tenCK, lh.mota
            FROM lichhen lh
            JOIN bacsi bs ON lh.maBS = bs.maBS
            JOIN chuyenkhoa ck ON lh.maCK = ck.maCK
            WHERE lh.mahen = %s AND lh.maBN = %s AND lh.trangthai != 'Huy'
            """
            cursor.execute(query, (selected_id, patient_id))
            appointment = cursor.fetchone()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
            return []

        if not appointment:
            dispatcher.utter_message(text="Không tìm thấy lịch hẹn này hoặc lịch đã bị hủy.")
            return []

        # Hiển thị confirm message
        confirm_text = f"""
        📋 <b>Xác nhận hủy lịch hẹn</b><br><br>
        - Mã lịch: {appointment['mahen']}<br>
        - Bác sĩ: {appointment['tenBS']}<br>
        - Chuyên khoa: {appointment['tenCK']}<br>
        - Ngày: {appointment['ngaythangnam']}<br>
        - Giờ: {appointment['khunggio']}<br>
        - Mô tả: {appointment['mota']}<br><br>
        Bạn có chắc chắn muốn hủy lịch hẹn này không?
        """

        dispatcher.utter_message(
            text=confirm_text,
            buttons=[
                {"title": "✅ Xác nhận hủy", "payload": "/affirm"},
                {"title": "❌ Không hủy", "payload": "/deny"}
            ],
            metadata={"parse_mode": "HTML"}  # cần cho Telegram hoặc kênh hỗ trợ HTML
        )

        
        return []


class ActionPerformCancelUpdated(Action):
    """Action thực hiện hủy lịch sau khi affirm"""
    
    def name(self) -> Text:
        return "action_perform_cancel"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        selected_id = tracker.get_slot("selected_appointment_id")
        
        # Lấy maBN động
        patient_id = get_patient_id(tracker)
        if not patient_id:
            dispatcher.utter_message(text="Lỗi: Bạn cần đăng nhập để hủy lịch hẹn.")
            return {"appointment_date": None}
        
        if not selected_id:
            dispatcher.utter_message(text="Không có lịch hẹn được chọn.")
            return []

        # Update DB: Set trangthai = 'hủy'
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor()
            query = "UPDATE lichhen SET trangthai = 'Huy' WHERE mahen = %s AND maBN = %s"
            cursor.execute(query, (selected_id, patient_id))
            conn.commit()
            rows_affected = cursor.rowcount
            cursor.close()
            conn.close()
            
            if rows_affected > 0:
                dispatcher.utter_message(text=f"✅ Đã hủy thành công lịch hẹn **{selected_id}**.")
            else:
                dispatcher.utter_message(text="Không tìm thấy lịch hẹn để hủy hoặc lịch đã bị hủy trước đó.")
        except Error as e:
            dispatcher.utter_message(text=f"❌ Lỗi cập nhật DB: {e}")

        # Offer next action
        buttons = [
            {"title": "Hủy lịch khác", "payload": "/cancel_appointment"},
            {"title": "Quay lại menu", "payload": "/greet"}
        ]
        dispatcher.utter_message(text="Bạn có muốn làm gì tiếp theo?", buttons=buttons)
        
        # Reset slots
        return [
            SlotSet("selected_appointment_id", None),
            SlotSet("appointment_date", None),
            SlotSet("current_task", None)
        ]


class ActionListDoctorsInForm(Action):
    def name(self) -> Text:
        return "action_list_doctors_in_form"

    def run(self, dispatcher, tracker, domain):
        # Lấy chuyên khoa từ entities hoặc slot
        entities = tracker.latest_message.get('entities', [])
        specialty_entity = next((e['value'] for e in entities if e['entity'] == 'specialty'), None)
        
        # Ưu tiên entity, sau đó slot
        specialty = specialty_entity or tracker.get_slot("specialty")
        
        if not specialty:
            dispatcher.utter_message(text="Vui lòng cung cấp tên chuyên khoa bạn muốn xem danh sách bác sĩ.")
            return []
        
        print(f"[DEBUG] Listing doctors for specialty: {specialty}")
        
        # Query DB để lấy danh sách bác sĩ theo chuyên khoa
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS, bs.emailBS, bs.diachiBS
            FROM bacsi bs
            JOIN chuyenmon cm ON bs.maBS = cm.maBS
            JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
            WHERE ck.tenCK LIKE %s
            ORDER BY bs.tenBS
            """
            cursor.execute(query, (f"%{specialty}%",))
            doctors = cursor.fetchall()
            cursor.close()
            conn.close()
            
            if not doctors:
                dispatcher.utter_message(text=f"Không tìm thấy bác sĩ nào trong chuyên khoa '{specialty}'. Vui lòng kiểm tra lại tên chuyên khoa.")
                return [SlotSet("specialty", None)]
            
            # Hiển thị danh sách bác sĩ bằng HTML
            html_list = f"""
            <div style="font-family: Arial, sans-serif; font-size: 15px; color: #333; background: #f8faff; border-radius: 10px; padding: 10px; border: 1px solid #cce0ff;">
                <div style="color: #007bff; font-weight: bold; margin-bottom: 8px;">
                    📋 Danh sách bác sĩ chuyên khoa {doctors[0]['tenCK']}:
                </div>
            """

            for idx, doc in enumerate(doctors, 1):
                html_list += f"""
                <div style="background: #ffffff; border-left: 3px solid #007bff; border-radius: 6px; padding: 6px 10px; margin-bottom: 6px;">
                    <div style="font-weight: bold; color: #007bff;">🩺 Bác sĩ {doc['tenBS']}</div>
                    <div>📞 <strong>SĐT:</strong> {doc['sdtBS']}</div>
                    <div>✉️ <strong>Email:</strong> {doc.get('emailBS', 'Chưa có')}</div>
                    # <div>✉️ <strong>Địa chỉ:</strong> {doc.get('diachiBS')}</div>
                </div>
                """

            html_list += f"""
                <div style="margin-top: 8px; font-size: 15px; color: #555;">
                    Tổng cộng: <strong>{len(doctors)}</strong> bác sĩ<br>
                    👉 Tiếp tục đặt lịch...
                </div>
            </div>
            """

            dispatcher.utter_message(text=html_list, html=True)

            
            # Set lại specialty nếu khác với specialty hiện tại
            current_specialty = tracker.get_slot("specialty")
            if not current_specialty or current_specialty.lower() != doctors[0]['tenCK'].lower():
                return [SlotSet("specialty", doctors[0]['tenCK'])]
            
            return []
            
        except Exception as e:
            print(f"[ERROR] {e}")
            dispatcher.utter_message(text="Có lỗi khi tra cứu danh sách bác sĩ. Vui lòng thử lại.")
            return []


class ActionShowDoctorInfoInForm(Action):
    def name(self) -> Text:
        return "action_show_doctor_info_in_form"

    def run(self, dispatcher, tracker, domain):
        # 1. Lấy thông tin (Ưu tiên ID trước, sau đó đến tên)
        entities = tracker.latest_message.get('entities', [])
        doctor_id_input = next((e['value'] for e in entities if e['entity'] == 'doctor_id'), None)
        doctor_name_input = next((e['value'] for e in entities if e['entity'] == 'doctor_name'), None)

        # Fallback lấy từ slot nếu không có entity (trường hợp user gõ tên lần đầu)
        if not doctor_id_input and not doctor_name_input:
            doctor_name_input = tracker.get_slot("doctor_name")

        # 2. Xử lý query
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            
            query_base = """
            SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS, bs.emailBS, bs.gioithieu
            FROM bacsi bs
            LEFT JOIN chuyenmon cm ON bs.maBS = cm.maBS
            LEFT JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
            WHERE bs.vaiTro = 'DOCTOR' AND bs.xoa = 0 
            """
            
            params = ()

            if doctor_id_input:
                # ===== KỊCH BẢN 1: TÌM THEO ID (Sau khi user chọn từ nút bấm) =====
                print(f"[DEBUG] Showing doctor info for ID: {doctor_id_input}")
                query_full = query_base + " AND bs.maBS = %s"
                params = (doctor_id_input,)
            
            elif doctor_name_input:
                # ===== KỊCH BẢN 2: TÌM THEO TÊN (Lần đầu user hỏi) =====
                print(f"[DEBUG] Showing doctor info for Name: {doctor_name_input}")
                query_full = query_base + " AND bs.tenBS LIKE %s"
                params = (f"%{doctor_name_input}%",)
            
            else:
                # Không có input
                dispatcher.utter_message(text="Vui lòng cung cấp tên bác sĩ bạn muốn tra cứu.")
                if 'conn' in locals() and conn.is_connected():
                    conn.close()
                return []
            
            cursor.execute(query_full, params)
            doctors_found = cursor.fetchall()
            cursor.close()
            conn.close()

            # 3. Phân tích kết quả
            if not doctors_found:
                dispatcher.utter_message(text=f"Không tìm thấy bác sĩ nào. Vui lòng kiểm tra lại.")
                return []

            # Gom nhóm theo maBS (vì 1 bác sĩ có thể có nhiều hàng chuyên khoa)
            unique_doctors = {}
            for doc in doctors_found:
                maBS = doc['maBS']
                if maBS not in unique_doctors:
                    doc_info = doc.copy()
                    doc_info.pop('tenCK', None)
                    doc_info['specialties'] = []
                    unique_doctors[maBS] = doc_info
                if doc['tenCK'] and doc['tenCK'] not in unique_doctors[maBS]['specialties']:
                    unique_doctors[maBS]['specialties'].append(doc['tenCK'])
            
            # 4. Xử lý các trường hợp
            
            # Case A: TÌM THẤY 1 BÁC SĨ (Happy path, hoặc user vừa click chọn ID)
            if len(unique_doctors) == 1:
                doctor_info = list(unique_doctors.values())[0]
                specialties_str = ", ".join(doctor_info['specialties']) if doctor_info['specialties'] else "Chưa cập nhật"
                
                info_html = f"""
                <div style="border-left: 4px solid #007bff; background: #eef6ff; border-radius: 8px; padding: 10px 14px; font-family: Arial, sans-serif; font-size: 15px; line-height: 1.4; color: #333;">
                    <div style="font-weight: bold; color: #007bff; margin-bottom: 6px;">👨‍⚕️ Bác sĩ {doctor_info['tenBS']}</div>
                    <div><strong>Mã BS:</strong> {doctor_info['maBS']}</div>
                    <div><strong>Chuyên khoa:</strong> {specialties_str}</div>
                    <div><strong>SĐT:</strong> {doctor_info['sdtBS']}</div>
                    <div><strong>Email:</strong> {doctor_info.get('emailBS', 'Chưa có')}</div>
                    <div><strong>Giới thiệu:</strong> {doctor_info.get('gioithieu', 'Chưa có phần giới thiệu')}</div>
                </div>
                <div style="margin-top: 6px; font-size: 15px;">Tiếp tục...</div> 
                """
                dispatcher.utter_message(text=info_html)
                
                # Nếu đang trong form, set slot
                current_doctor = tracker.get_slot("doctor_name")
                if tracker.active_loop and (not current_doctor or current_doctor.lower() != doctor_info['tenBS'].lower()):
                    return [SlotSet("doctor_name", doctor_info['tenBS'])]
                return []

            # Case B: TÌM THẤY NHIỀU BÁC SĨ (do tìm theo TÊN)
            if len(unique_doctors) > 1:
                found_names_set = set(doc['tenBS'] for doc in unique_doctors.values())
                
                # Sub-case B1: Nhiều tên khác nhau (e.g., "Hùng" -> "Lê Hùng", "Trần Hùng")
                if len(found_names_set) > 1:
                    dispatcher.utter_message(
                        text=f"Tên '{doctor_name_input}' không rõ ràng (tìm thấy: {', '.join(found_names_set)}). Vui lòng nhập họ tên đầy đủ."
                    )
                    return []
                
                # Sub-case B2: Nhiều bác sĩ CÙNG TÊN (e.g., "Nguyễn Văn A" (Nội), "Nguyễn Văn A" (Ngoại))
                if len(found_names_set) == 1:
                    buttons = []
                    message = f"Tìm thấy nhiều bác sĩ trùng tên **'{list(found_names_set)[0]}'**. Vui lòng chọn bác sĩ bạn muốn xem thông tin:"
                    
                    for doc in unique_doctors.values():
                        specialties_str = ", ".join(doc['specialties']) or "Chưa có khoa"
                        buttons.append({
                            "title": f"BS {doc['tenBS']} (Khoa: {specialties_str})",
                            # Payload này sẽ trigger lại RASA, NLU sẽ trích xuất doctor_id
                            # và rule "Show doctor info" sẽ chạy lại action này
                            "payload": f"/ask_doctor_info{{\"doctor_id\":\"{doc['maBS']}\"}}"
                        })
                    
                    dispatcher.utter_message(text=message, buttons=buttons)
                    return []

            # Case C: (Dự phòng) Lỗi không xác định
            dispatcher.utter_message(text="Không tìm thấy thông tin bác sĩ hợp lệ.")
            return []
                
        except Exception as e:
            print(f"[ERROR] Lỗi trong ActionShowDoctorInfoInForm: {e}")
            dispatcher.utter_message(text="Có lỗi khi tra cứu thông tin bác sĩ. Vui lòng thử lại.")
            return []


class ActionExplainSpecialtyInForm(Action):
    def name(self) -> Text:
        return "action_explain_specialty_in_form"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        specialty = tracker.get_slot("specialty")
        
        if not specialty:
            return []
        
        print(f"[DEBUG] Explaining specialty: {specialty}")
        
        # Query DB
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = "SELECT tenCK, maCK, mota FROM chuyenkhoa WHERE tenCK LIKE %s"
            cursor.execute(query, (f"%{specialty}%",))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if result:
                ten_ck = result['tenCK']
                explanation = result.get('mota')
                
                if not explanation:  # If mota is None or empty
                    # Use Gemini API to generate explanation
                    model = genai.GenerativeModel('models/gemini-flash-latest')  # Or your preferred model
                    prompt = f"Giải thích ngắn gọn về chuyên khoa y tế '{specialty}' bằng tiếng Việt."
                    response = model.generate_content(prompt)
                    explanation = response.text.strip() if response else f"Chuyên khoa {specialty}..."
                
                dispatcher.utter_message(
                    text=f"""
                    <div style="background-color: #f0f0f0; padding: 15px; border-radius: 10px; border: 1px solid #ddd; max-width: 400px; margin: 10px auto; font-family: Arial, sans-serif;">
                        <p style="font-size: 16px; margin: 0;">📋 <strong>{ten_ck}</strong>: {explanation}</p>
                        <br>
                        <p style="font-size: 14px; color: #666; margin: 0;">Tiếp tục đặt lịch...</p>
                    </div>
                    """
                )
                return [SlotSet("specialty", ten_ck)]
            else:
                dispatcher.utter_message(text=f"Không tìm thấy '{specialty}'.")
                return [SlotSet("specialty", None)]
        except Exception as e:
            print(f"[ERROR] {e}")
            dispatcher.utter_message(text="Đã xảy ra lỗi khi truy vấn cơ sở dữ liệu.")
            return []


class ValidateMyForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_my_form"

    async def extract_my_slot(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: DomainDict
    ) -> Dict[Text, Any]:
        # Logic để trích xuất slot 'my_slot'
        # ...
        return []

    async def validate_my_slot(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: DomainDict,
    ) -> Dict[Text, Any]:
        # Lấy intent của tin nhắn gần nhất từ người dùng
        latest_intent = tracker.latest_message['intent'].get('name')

        # Kiểm tra nếu người dùng muốn dừng form bằng cách nói "tạm dừng"
        if value and value == "tạm dừng":
            dispatcher.utter_message(text="OK, tôi sẽ tạm dừng form này. Bạn muốn làm gì tiếp theo?")
            return {"requested_slot": None} # Đặt requested_slot về None để dừng form

        # Kiểm tra nếu người dùng kích hoạt các intent đặc biệt để dừng form
        if latest_intent in ["explain_specialty", "ask_info_doctor"]:
            dispatcher.utter_message(text="Đã dừng form hiện tại để trả lời yêu cầu của bạn.")
            # Đặt tất cả các slot của form về None nếu cần
            # Ví dụ: form_slots_to_clear = ["slot_1", "slot_2"]
            # events = {slot: None for slot in form_slots_to_clear}
            # events["requested_slot"] = None
            # return events
            return {"requested_slot": None} # Dừng form

        if value:
            # Logic validation thông thường cho my_slot nếu không có yêu cầu dừng form
            return {"my_slot": value}
        else:
            dispatcher.utter_message(text="Tôi không hiểu. Bạn có thể nói rõ hơn không?")
            return {"my_slot": None} # Yêu cầu người dùng nhập lại

    async def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:
        # Logic của form validation action
        return await super().run(dispatcher, tracker, domain)


# actions.py

class ValidateRecommendDoctorForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_recommend_doctor_form"

    def validate_symptoms(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        text_value = str(slot_value).strip()

        # 1. Bỏ qua nếu là lệnh command (bắt đầu bằng /)
        if text_value.startswith("/"):
            return {"symptoms": None}

        # 2. Kiểm tra độ dài (tránh người dùng gõ bừa 1-2 ký tự)
        if len(text_value) < 4:
            dispatcher.utter_message(text="Mô tả quá ngắn. Vui lòng kể rõ hơn về triệu chứng của bạn (hoặc của người thân).")
            return {"symptoms": None}

        # 3. OK -> Lưu nguyên câu văn đó vào slot
        return {"symptoms": text_value}


import json # ⚠️ QUAN TRỌNG: Nhớ import json ở đầu file actions.py
import re   # Thêm re để xử lý chuỗi regex

class ActionRecommendDoctor(Action):
    def name(self) -> Text:
        return "action_recommend_doctor"

    def _get_all_specialties(self):
        """Lấy danh sách tất cả tên chuyên khoa từ DB"""
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor()
            cursor.execute("SELECT tenCK FROM chuyenkhoa")
            rows = [row[0] for row in cursor.fetchall()]
            cursor.close()
            conn.close()
            return rows
        except Error as e:
            print(f"[ERROR] Cannot fetch specialties: {e}")
            return []

    def _consult_gemini_for_specialty(self, symptom_text, valid_specialties):
        """Hỏi Gemini để map triệu chứng vào danh sách chuyên khoa (Trả về LIST)"""
        try:
            specialties_str = ", ".join([f'"{s}"' for s in valid_specialties])
            
            # --- SỬA ĐỔI 1: Prompt yêu cầu trả về JSON List ---
            prompt = f"""
            Bạn là hệ thống điều phối bệnh nhân.
            
            DỮ LIỆU:
            1. Danh sách chuyên khoa hiện có: [{specialties_str}]
            2. Triệu chứng người dùng: "{symptom_text}"
            
            YÊU CẦU:
            - Phân tích triệu chứng và chọn ra các chuyên khoa phù hợp từ danh sách trên.
            - Nếu người dùng có nhiều triệu chứng (ví dụ: mẹ đau lưng, con sốt), hãy liệt kê TẤT CẢ chuyên khoa phù hợp.
            - Ưu tiên: "Con/Bé" -> "Nhi khoa".
            - OUTPUT FORMAT: Chỉ trả về một mảng JSON (JSON Array) chứa tên các chuyên khoa. 
            - Ví dụ: ["Nội khoa"] hoặc ["Nhi khoa", "Sản phụ khoa"].
            - Không giải thích thêm.
            """

            model = genai.GenerativeModel('models/gemini-flash-latest')
            response = model.generate_content(prompt)
            
            raw_text = response.text.strip()
            
            # --- SỬA ĐỔI 2: Xử lý chuỗi JSON trả về ---
            # Gemini thường trả về dạng ```json [...] ```, cần cắt bỏ markdown
            if "```" in raw_text:
                match = re.search(r'\[.*\]', raw_text, re.DOTALL)
                if match:
                    raw_text = match.group(0)
                else:
                    raw_text = "[]"
            
            # Parse string thành List Python
            try:
                suggested_list = json.loads(raw_text)
            except json.JSONDecodeError:
                # Nếu lỗi parse, thử fallback về text thuần nếu nó khớp với 1 chuyên khoa
                for spec in valid_specialties:
                    if spec.lower() in raw_text.lower():
                        return [spec]
                return ["Nội khoa"]

            # --- SỬA ĐỔI 3: Validate lại với DB ---
            final_list = []
            if isinstance(suggested_list, list):
                for item in suggested_list:
                    # Tìm item trong valid_specialties (so sánh không phân biệt hoa thường)
                    for db_spec in valid_specialties:
                        if str(item).lower() == db_spec.lower():
                            final_list.append(db_spec)
                            break
            
            # Nếu list rỗng hoặc lỗi, fallback
            if not final_list:
                return ["Nội khoa"] if "Nội khoa" in valid_specialties else ([valid_specialties[0]] if valid_specialties else [])
            
            # Xóa trùng lặp và trả về LIST
            return list(set(final_list))

        except Exception as e:
            print(f"[ERROR] Gemini API Error: {e}")
            # Luôn trả về LIST, kể cả khi lỗi
            return ["Nội khoa"] if "Nội khoa" in valid_specialties else []

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        
        # Lấy input (Code bạn đã viết tốt rồi)
        user_msg = tracker.latest_message.get('text')
        symptom_slot = tracker.get_slot("symptoms")
        desc_slot = tracker.get_slot("decription")
        
        possible_inputs = [user_msg, symptom_slot, desc_slot]
        valid_inputs = [str(t) for t in possible_inputs if t]
        
        if not valid_inputs:
            dispatcher.utter_message(text="Vui lòng mô tả lại triệu chứng.")
            return []
            
        final_symptom_text = max(valid_inputs, key=len)

        # dispatcher.utter_message(text=f"⏳ Đang phân tích: \"{final_symptom_text}\"...")

        valid_specialties = self._get_all_specialties()
        
        # Gọi hàm (Bây giờ chắc chắn trả về List)
        suggested_specialties = self._consult_gemini_for_specialty(final_symptom_text, valid_specialties)

        print(f"[DEBUG] Input: {final_symptom_text} -> Gemini: {suggested_specialties}")

        # Logic hiển thị (Code cũ của bạn sẽ chạy đúng với List)
        if len(suggested_specialties) == 1:
             dispatcher.utter_message(
                text=f"""
                <div style="font-family: Arial, sans-serif; background: #e7f3ff; padding: 10px; border-radius: 8px; border: 1px solid #b3d7ff;">
                    🔍 Dựa trên mô tả, tôi đề xuất chuyên khoa: <b>{suggested_specialties[0]}</b>.
                </div>
                """,
                html=True
            )
        else:
            specs_text = ", ".join(suggested_specialties)
            dispatcher.utter_message(
                text=f"""
                <div style="font-family: Arial, sans-serif; background: #fff3cd; padding: 10px; border-radius: 8px; border: 1px solid #ffeeba;">
                    🔍 Tôi nhận thấy có <b>{len(suggested_specialties)} vấn đề cần khám</b> ({specs_text}).<br>
                    Dưới đây là bác sĩ cho từng chuyên khoa:
                </div>
                """,
                html=True
            )

        # Query DB và hiển thị bác sĩ
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            
            for spec in suggested_specialties:
                query = """
                SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS
                FROM bacsi bs
                JOIN chuyenmon cm ON bs.maBS = cm.maBS
                JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
                WHERE ck.tenCK = %s
                LIMIT 3
                """
                cursor.execute(query, (spec,))
                doctors = cursor.fetchall()
                
                if doctors:
                    # 1. Khởi tạo khối HTML (Container) đẹp mắt
                    # Bao gồm cả Tiêu đề (Header) và nội dung bên trong
                    html_block = f"""
                    <div style="font-family: Arial, sans-serif; border: 1px solid #cce0ff; border-radius: 10px; overflow: hidden; margin-bottom: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.05);">
                        <div style="background-color: #e7f3ff; color: #0056b3; padding: 10px 15px; font-weight: bold; border-bottom: 1px solid #cce0ff;">
                            🏥 Danh sách bác sĩ {spec}
                        </div>
                        <div style="padding: 10px 15px; background-color: #fff;">
                    """
                    
                    # 2. Danh sách nút bấm (sẽ gom lại để hiển thị cuối tin nhắn)
                    buttons_list = []

                    # 3. Lặp qua từng bác sĩ để nối chuỗi HTML và tạo nút
                    for i, doc in enumerate(doctors):
                        # Tạo đường kẻ mờ giữa các bác sĩ (trừ người cuối cùng)
                        border_style = "border-bottom: 1px dashed #eee; padding-bottom: 8px; margin-bottom: 8px;" if i < len(doctors) - 1 else ""
                        
                        html_block += f"""
                        <div style="{border_style}">
                            <div style="font-weight: bold; color: #333; font-size: 15px;">👨‍⚕️ BS {doc['tenBS']}</div>
                            <div style="color: #666; font-size: 14px;">📞 SĐT: {doc['sdtBS']}</div>
                        </div>
                        """
                        
                        # Thêm nút đặt lịch cho bác sĩ này
                        buttons_list.append({
                            "title": f"📅 Đặt lịch BS {doc['tenBS']}", 
                            "payload": f"/book_with_doctor{{\"doctor_id\":\"{doc['maBS']}\", \"specialty\":\"{doc['tenCK']}\"}}"
                        })

                    # 4. Đóng thẻ div
                    html_block += "</div></div>"

                    # 5. Gửi MỘT LẦN DUY NHẤT cho chuyên khoa này
                    dispatcher.utter_message(text=html_block, buttons=buttons_list, html=True)

                else:
                    dispatcher.utter_message(text=f"⚠️ Hiện chưa có bác sĩ trực thuộc khoa {spec}.")

            cursor.close()
            conn.close()

        except Error as e:
            dispatcher.utter_message(text=f"Lỗi DB: {e}")
        
        # Reset slots
        return [
            SlotSet("specialty_suggested", ", ".join(suggested_specialties)),
            SlotSet("current_task", None),
            SlotSet("symptoms", None),
            SlotSet("decription", None)
        ]

class ActionBookWithDoctor(Action):
    def name(self) -> Text:
        return "action_book_with_doctor"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        # Extract entities từ latest_message
        entities = tracker.latest_message.get('entities', [])
        doctor_id = next((e['value'] for e in entities if e['entity'] == 'doctor_id'), None)
        specialty = next((e['value'] for e in entities if e['entity'] == 'specialty'), None)
        
        # Fallback parse thủ công nếu entity fail (từ text payload)
        if not doctor_id or not specialty:
            text = tracker.latest_message.get('text', '')
            match = re.search(r'"doctor_id":"(BS\d+)"\s*,\s*"specialty":"([^"]+)"', text)
            if match:
                doctor_id, specialty = match.groups()

        if not doctor_id:
            dispatcher.utter_message(text="Không nhận được ID bác sĩ từ lựa chọn. Hãy thử lại.")
            return []

        # Query DB lấy tenBS và verify specialty
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT tenBS, ck.tenCK as specialty 
            FROM bacsi bs 
            JOIN chuyenmon cm ON bs.maBS = cm.maBS 
            JOIN chuyenkhoa ck ON cm.maCK = ck.maCK 
            WHERE bs.maBS = %s
            """
            cursor.execute(query, (doctor_id,))
            doctor = cursor.fetchone()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
            return []

        if not doctor:
            dispatcher.utter_message(text="Không tìm thấy bác sĩ với ID này.")
            return []

        doctor_name = doctor['tenBS']
        final_specialty = specialty or doctor['specialty'] or tracker.get_slot("specialty_suggested")

        # RESET slots lộn xộn trước (bao gồm date, time, decription)
        events = [
            SlotSet("doctor_name", None),
            SlotSet("specialty", None),
            SlotSet("date", None),
            SlotSet("appointment_time", None),
            SlotSet("decription", None)
        ]
        
        # Set đúng
        events += [
            SlotSet("doctor_name", doctor_name),
            SlotSet("specialty", final_specialty),
            SlotSet("current_task", "book_appointment")
        ]
        
        # Utter xác nhận
        dispatcher.utter_message(
            text=f"Bạn đã chọn đặt lịch với bác sĩ **{doctor_name}** (chuyên khoa {final_specialty}). Bây giờ, hãy cung cấp ngày hẹn (DD/MM/YYYY)."
        )
        
        return events


class ValidateBookAppointmentForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_book_appointment_form"

    # ============================================================
    # 1. CÁC HÀM HỖ TRỢ (HELPER) - ĐỂ CHẮC CHẮN KHÔNG BỊ THIẾU
    # ============================================================
    def _format_time(self, time_obj):
        """Chuyển đổi time/timedelta sang chuỗi HH:MM"""
        if isinstance(time_obj, timedelta):
            return (datetime.min + time_obj).time().strftime('%H:%M')
        elif isinstance(time_obj, time):
            return time_obj.strftime('%H:%M')
        return str(time_obj)

    def _get_vietnamese_day_name(self, weekday_index):
        days_vn = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ Nhật"]
        return days_vn[weekday_index]

    def _detect_wrong_input(self, slot_name: str, slot_value: str) -> bool:
        input_lower = slot_value.lower()
        keywords = WRONG_INPUT_KEYWORDS.get(slot_name, [])
        return any(kw in input_lower for kw in keywords)

    def _handle_form_interruption(self, dispatcher, tracker):
        latest_intent = tracker.latest_message.get('intent', {}).get('name')

        if latest_intent == "explain_specialty":
            ActionExplainSpecialtyInForm().run(dispatcher, tracker, {})
            return {"specialty": tracker.get_slot("specialty"), "just_explained": False}
        
        if latest_intent == "ask_doctor_info":
            ActionShowDoctorInfoInForm().run(dispatcher, tracker, {})
            return {"doctor_name": tracker.get_slot("doctor_name"), "just_asked_doctor_info": False}
        
        if latest_intent == "list_doctors_by_specialty":
            ActionListDoctorsInForm().run(dispatcher, tracker, {})
            return {"specialty": tracker.get_slot("specialty"), "just_listed_doctors": False}
        
        if latest_intent == "ask_who_examined_me":
            ActionShowExaminingDoctorInForm().run(dispatcher, tracker, {})
            return {"just_asked_examining_doctor": False}
        
        if latest_intent == "list_all_doctors":
            ActionListAllDoctors().run(dispatcher, tracker, {})
            return {"just_listed_all_doctors_dummy": False}
        
        if latest_intent == "ask_doctor_schedule":
            ActionShowDoctorSchedule().run(dispatcher, tracker, {})
            return {"just_asked_doctor_schedule_dummy": False}

        # === THÊM MỚI: Xử lý list_all_specialties ===
        if latest_intent == "list_all_specialties":
            list_action = ActionListAllSpecialties()
            list_action.run(dispatcher, tracker, {})
            # Trả về slot dummy để form tiếp tục mà không bị gãy flow
            return {"just_listed_all_specialties_dummy": False}
        
        return {}

    def _show_doctor_schedule_in_form(self, maBS: str, tenBS: str, dispatcher: CollectingDispatcher):
        """Hiển thị lịch làm việc (Helper)"""
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            today = datetime.now().date()
            start_of_week = today - timedelta(days=today.weekday())
            end_of_week = start_of_week + timedelta(days=6)

            query = """
            SELECT ngaythangnam, giobatdau, gioketthuc, trangthai
            FROM thoigiankham
            WHERE maBS = %s AND DATE(ngaythangnam) BETWEEN %s AND %s
            ORDER BY ngaythangnam, giobatdau
            """
            cursor.execute(query, (maBS, start_of_week, end_of_week))
            schedule_rows = cursor.fetchall()
            cursor.close()
            conn.close()

            # Xử lý HTML
            schedule_by_date = {}
            if schedule_rows:
                for row in schedule_rows:
                    d = row['ngaythangnam']
                    if d not in schedule_by_date: schedule_by_date[d] = []
                    schedule_by_date[d].append(row)

            html_table = f"""
            <style>
                .schedule-table {{ width: 100%; max-width: 450px; border-collapse: collapse; font-family: Arial, sans-serif; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin-top: 8px; }}
                .schedule-table th, .schedule-table td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; }}
                .schedule-table th {{ background-color: #f8faff; color: #007bff; font-size: 14px; }}
                .schedule-table .date-cell {{ font-weight: bold; color: #333; font-size: 14px; width: 40%; }}
                .status-ghi {{ color: #dc3545; font-weight: bold; font-style: italic; }}
                .status-ok {{ color: #28a745; font-weight: bold; }}
                .status-full {{ color: #6c757d; text-decoration: line-through; }}
                .empty-schedule {{ text-align: center; color: #888; font-style: italic; padding: 20px; }}
            </style>
            <div style="font-family: Arial, sans-serif; font-size: 15px; margin-bottom: 8px; margin-top: 8px;">
                📅 <strong>Lịch làm việc tuần này của Bác sĩ {tenBS}</strong><br>(Từ {start_of_week.strftime('%d/%m')} đến {end_of_week.strftime('%d/%m')})
            </div>
            <table class="schedule-table">
                <thead><tr><th>Ngày</th><th>Ca làm việc</th></tr></thead><tbody>
            """
            
            if not schedule_rows:
                html_table += "<tr><td colspan='2' class='empty-schedule'>Không có lịch làm việc trong tuần này.</td></tr>"
            else:
                for date_obj, shifts in sorted(schedule_by_date.items()):
                    day_vn = self._get_vietnamese_day_name(date_obj.weekday())
                    d_str = date_obj.strftime('%d/%m')
                    shifts_html = ""
                    for shift in shifts:
                        s_start = self._format_time(shift['giobatdau'])
                        s_end = self._format_time(shift['gioketthuc'])
                        stt = shift['trangthai']
                        cls = "status-ghi" if stt == "Nghỉ" else ("status-full" if stt in ["Đã đầy", "Hoàn thành"] else "status-ok")
                        shifts_html += f"<div class='shift-item'>{s_start} - {s_end} <span class='{cls}'>({stt})</span></div>"
                    html_table += f"<tr><td class='date-cell'>{day_vn} ({d_str})</td><td>{shifts_html}</td></tr>"
            
            html_table += "</tbody></table>"
            dispatcher.utter_message(text=html_table, html=True)
        except Exception as e:
            print(f"[ERROR] Helper Schedule: {e}")

    # ============================================================
    # 2. VALIDATE DOCTOR NAME
    # ============================================================
    def validate_doctor_name(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        interruption = self._handle_form_interruption(dispatcher, tracker)
        if interruption: return interruption

        if not slot_value:
            dispatcher.utter_message(text="Vui lòng chọn bác sĩ.")
            return {"doctor_name": None}

        doctor_input = str(slot_value).strip()
        specialty = tracker.get_slot("specialty")

        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)

            if specialty:
                query = "SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS FROM bacsi bs JOIN chuyenmon cm ON bs.maBS = cm.maBS JOIN chuyenkhoa ck ON cm.maCK = ck.maCK WHERE ck.tenCK = %s AND LOWER(bs.tenBS) LIKE %s"
                cursor.execute(query, (specialty, f"%{doctor_input.lower()}%"))
                matched = cursor.fetchall()
                cursor.close()
                conn.close()

                if matched:
                    doc = matched[0]
                    confirm_html = f"""<div style="font-family: Arial, sans-serif; background: #d1ecf1; border-left: 5px solid #0c5460; border-radius: 8px; padding: 12px 16px;"><p style="font-weight: bold; color: #0c5460; margin: 0;">✅ Xác nhận bác sĩ:</p><p style="margin: 2px 0;"><strong>👨‍⚕️ {doc['tenBS']}</strong></p><p style="margin: 2px 0;">🏥 {doc['tenCK']}</p></div>"""
                    dispatcher.utter_message(text=confirm_html, html=True)
                    self._show_doctor_schedule_in_form(doc["maBS"], doc["tenBS"], dispatcher)
                    return {"doctor_name": doc["tenBS"]}
                else:
                    dispatcher.utter_message(text=f"Bác sĩ '{doctor_input}' không thuộc khoa {specialty}.")
                    return {"doctor_name": None}
            else:
                query = "SELECT bs.tenBS, ck.tenCK, bs.maBS, bs.sdtBS FROM bacsi bs JOIN chuyenmon cm ON bs.maBS = cm.maBS JOIN chuyenkhoa ck ON cm.maCK = ck.maCK WHERE LOWER(bs.tenBS) LIKE %s"
                cursor.execute(query, (f"%{doctor_input.lower()}%",))
                doctors = cursor.fetchall()
                cursor.close()
                conn.close()

                if not doctors:
                    dispatcher.utter_message(text=f"Không tìm thấy bác sĩ '{doctor_input}'.")
                    return {"doctor_name": None}

                unique_names = set(d['tenBS'] for d in doctors)
                unique_specs = set(d['tenCK'] for d in doctors)

                if len(unique_names) == 1 and len(unique_specs) == 1:
                    doc = doctors[0]
                    confirm_html = f"""<div style="font-family: Arial, sans-serif; background: #d1ecf1; border-left: 5px solid #0c5460; border-radius: 8px; padding: 12px 16px;"><p style="font-weight: bold; color: #0c5460; margin: 0;">✅ Xác nhận bác sĩ:</p><p style="margin: 2px 0;"><strong>👨‍⚕️ {doc['tenBS']}</strong></p><p style="margin: 2px 0;">🏥 Tự động chọn: {doc['tenCK']}</p></div>"""
                    dispatcher.utter_message(text=confirm_html, html=True)
                    self._show_doctor_schedule_in_form(doc["maBS"], doc["tenBS"], dispatcher)
                    return {"doctor_name": list(unique_names)[0], "specialty": list(unique_specs)[0]}
                
                if len(unique_names) == 1 and len(unique_specs) > 1:
                    doc = doctors[0]
                    specs_str = ", ".join(unique_specs)
                    msg = f"""<div style="font-family: Arial, sans-serif; background: #fff3cd; border-left: 5px solid #ffc107; border-radius: 8px; padding: 12px 16px;"><p style="font-weight: bold; margin: 0;">✅ Xác nhận: 👨‍⚕️ {doc['tenBS']}</p><p>⚠️ Bác sĩ làm nhiều khoa: <i>{specs_str}</i></p><p>👉 Vui lòng chọn chuyên khoa.</p></div>"""
                    dispatcher.utter_message(text=msg, html=True)
                    # KHÔNG hiện lịch ở đây
                    return {"doctor_name": list(unique_names)[0]}

                dispatcher.utter_message(text=f"Tên '{doctor_input}' chưa rõ ràng. Vui lòng nhập đầy đủ hơn.")
                return {"doctor_name": None}

        except Exception as e:
            dispatcher.utter_message(text=f"Lỗi hệ thống: {str(e)}")
            return {"doctor_name": None}

    # ============================================================
    # 3. VALIDATE SPECIALTY
    # ============================================================
    def validate_specialty(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        
        latest_intent = tracker.latest_message.get('intent', {}).get('name')
        old_specialty = tracker.get_slot("specialty")
        if latest_intent in ["explain_specialty", "ask_doctor_info", "list_doctors_by_specialty", "ask_who_examined_me", "list_all_doctors", "ask_doctor_schedule"]:
            interruption_result = self._handle_form_interruption(dispatcher, tracker)
            if interruption_result: return {"specialty": old_specialty}

        if not slot_value:
            dispatcher.utter_message(text="Vui lòng chọn chuyên khoa.")
            return {"specialty": None}

        specialty_input = str(slot_value).strip().lower()
        if self._detect_wrong_input('specialty', specialty_input):
            return {"specialty": None}

        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = "SELECT tenCK FROM chuyenkhoa WHERE LOWER(tenCK) = %s"
            cursor.execute(query, (specialty_input,))
            result = cursor.fetchone()
            
            if not result:
                dispatcher.utter_message(text=f"Chuyên khoa '{slot_value}' không tồn tại.")
                cursor.close(); conn.close()
                return {"specialty": None}

            validated_specialty = result['tenCK']
            doctor_name = tracker.get_slot("doctor_name")
            
            # Logic chống trùng lặp hiển thị lịch
            entities = tracker.latest_message.get('entities', [])
            has_doctor_entity = any(e['entity'] in ['doctor_name', 'doctor_id'] for e in entities)

            if doctor_name and not has_doctor_entity:
                query_doc = "SELECT bs.maBS, bs.tenBS FROM bacsi bs JOIN chuyenmon cm ON bs.maBS = cm.maBS JOIN chuyenkhoa ck ON cm.maCK = ck.maCK WHERE ck.tenCK = %s AND LOWER(bs.tenBS) LIKE %s"
                cursor.execute(query_doc, (validated_specialty, f"%{doctor_name.lower()}%"))
                doc_match = cursor.fetchone()
                if doc_match:
                    self._show_doctor_schedule_in_form(doc_match["maBS"], doc_match["tenBS"], dispatcher)
            
            cursor.close()
            conn.close()
            return {"specialty": validated_specialty}

        except Exception as e:
            dispatcher.utter_message(text=f"Lỗi hệ thống (Specialty): {str(e)}")
            return {"specialty": None}

    # ============================================================
    # 4. VALIDATE DATE (ĐÃ SỬA ĐỂ BÁO LỖI CHI TIẾT)
    # ============================================================
    # ============================================================
    # 4. VALIDATE DATE (ĐÃ SỬA LỖI UNREAD RESULT)
    # ============================================================
    def validate_date(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        
        if not slot_value: return {"date": None}
        date_input = str(slot_value).strip()
        
        try:
            parsed_date = datetime.strptime(date_input, '%d/%m/%Y').date()
        except ValueError:
            dispatcher.utter_message(text="Ngày sai định dạng (DD/MM/YYYY). Ví dụ: 25/10/2025")
            return {"date": None}

        if parsed_date < datetime.now().date():
            dispatcher.utter_message(text="Vui lòng chọn ngày trong tương lai.")
            return {"date": None}

        doctor_name = tracker.get_slot("doctor_name")
        if not doctor_name:
            dispatcher.utter_message(text="Lỗi: Thiếu thông tin bác sĩ.")
            return {"date": None}

        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            
            # 👇 FIX QUAN TRỌNG: Thêm buffered=True để tránh lỗi "Unread result found"
            cursor = conn.cursor(dictionary=True, buffered=True) 
            
            # 1. Lấy mã bác sĩ
            cursor.execute("SELECT maBS FROM bacsi WHERE tenBS = %s", (doctor_name,))
            
            # Dùng fetchall() cho an toàn, sau đó lấy phần tử đầu tiên
            bs_results = cursor.fetchall() 
            
            if not bs_results:
                cursor.close(); conn.close()
                dispatcher.utter_message(text=f"Không tìm thấy bác sĩ {doctor_name}.")
                return {"date": None}
            
            # Lấy maBS đầu tiên tìm thấy
            maBS = bs_results[0]['maBS']
            
            # 2. Lấy lịch làm việc
            query = """
            SELECT giobatdau, gioketthuc, trangthai
            FROM thoigiankham
            WHERE maBS = %s AND DATE(ngaythangnam) = %s
            ORDER BY giobatdau
            """
            cursor.execute(query, (maBS, parsed_date))
            schedule = cursor.fetchall()
            
            cursor.close()
            conn.close()
            
            if not schedule:
                dispatcher.utter_message(text=f"Bác sĩ {doctor_name} không có lịch vào ngày {date_input}.")
                return {"date": None}

            # Hiển thị HTML
            html = f"""<div style="font-family: Arial, sans-serif; background: #e7f3ff; border-left: 5px solid #007bff; border-radius: 8px; padding: 12px 16px; margin: 10px 0;"><p style="font-weight: bold; color: #007bff; margin: 0 0 8px 0;">✅ Các khung giờ ngày {date_input}:</p><div style="display: flex; flex-wrap: wrap; gap: 8px;">"""
            
            for slot in schedule:
                s_start = self._format_time(slot['giobatdau'])
                s_end = self._format_time(slot['gioketthuc'])
                stt = slot['trangthai']
                
                # Tô màu trạng thái
                cls = "status-ok"
                if stt == "Nghỉ": cls = "status-ghi"
                elif stt in ["Đã đầy", "Hoàn thành", "Full"]: cls = "status-full"

                html += f"""<span style="background: white; border: 1px solid #007bff; color: #007bff; padding: 4px 8px; border-radius: 4px; font-size: 14px;">{s_start} - {s_end} <small style='color:#666'>({stt})</small></span>"""
            
            html += """</div><p style="margin: 8px 0 0 0; font-size: 14px;">👉 Vui lòng nhập giờ (HH:MM).</p></div>"""
            dispatcher.utter_message(text=html, html=True)
            
            return {"date": date_input}

        except Exception as e:
            print(f"[CRITICAL ERROR] Validate Date: {e}")
            dispatcher.utter_message(text=f"🔥 Lỗi hệ thống khi tra cứu ngày: {str(e)}")
            return {"date": None}

    # ============================================================
    # 5. VALIDATE TIME & DESCRIPTION
    # ============================================================
    def validate_appointment_time(self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> Dict[Text, Any]:
        if not slot_value: return {"appointment_time": None}
        time_input = str(slot_value).strip()
        try:
            parsed_time = datetime.strptime(time_input, '%H:%M').time()
        except ValueError:
            dispatcher.utter_message(text="Giờ sai định dạng HH:MM.")
            return {"appointment_time": None}
            
        # Check logic giờ nằm trong ca làm việc...
        # (Giản lược để test DB trước, bạn có thể paste lại logic cũ vào đây nếu muốn check chặt chẽ)
        return {"appointment_time": time_input}

    def validate_decription(self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> Dict[Text, Any]:
        if not slot_value: return {"decription": None}
        desc = str(slot_value).strip()
        if len(desc) < 4: 
            dispatcher.utter_message(text="Mô tả quá ngắn.")
            return {"decription": None}
        return {"decription": desc}
    

class ActionBookAppointment(Action):
    def name(self) -> Text:
        return "action_book_appointment"

    def run(self, dispatcher, tracker, domain):
        slots = {
            "doctor_name": tracker.get_slot("doctor_name"),
            "specialty": tracker.get_slot("specialty"),
            "date": tracker.get_slot("date"),
            "appointment_time": tracker.get_slot("appointment_time"),
            "decription": tracker.get_slot("decription")
        }
        if not all(slots.values()):
            dispatcher.utter_message(text="Thông tin chưa đầy đủ. Vui lòng hoàn tất form.")
            return []

        dispatcher.utter_message(
            text=f"""
            <div style="font-family: Arial, sans-serif; font-size: 15px; color: #333;
                        background: #f8f9fa; border-left: 4px solid #0d6efd; border-radius: 8px;
                        padding: 12px 14px; margin: 6px 0;">
                <div style="font-weight: bold; color: #0d6efd; margin-bottom: 6px;">
                    ✅ Xác nhận thông tin đặt lịch
                </div>
                <div><strong>Bác sĩ:</strong> {slots['doctor_name']}</div>
                <div><strong>Chuyên khoa:</strong> {slots['specialty']}</div>
                <div><strong>Thời gian:</strong> {slots['appointment_time']} ngày {slots['date']}</div>
                <div><strong>Mô tả:</strong> {slots['decription']}</div>
                <div style="margin-top: 8px;">👉 Vui lòng xác nhận để hoàn tất đặt lịch.</div>
            </div>
            """,
            buttons=[
                {"title": "✅ Xác nhận", "payload": "/affirm"},
                {"title": "❌ Hủy", "payload": "/deny"}
            ],
            metadata={"html": True}
        )

        return []  # Không reset ngay, chờ affirm/deny qua rules


# Phần mới: Tra cứu thông tin bác sĩ
class ActionSearchDoctor(Action):
    def name(self) -> Text:
        return "action_search_doctor"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        doctor_name_search = tracker.get_slot("doctor_name")  # Reuse doctor_name slot for search
        if not doctor_name_search:
            dispatcher.utter_message(text="Không nhận được tên bác sĩ để tra cứu. Hãy thử lại.")
            return [SlotSet("doctor_name", None)]

        # Query MySQL để tìm bác sĩ matching tên (LIKE %name%)
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS
            FROM bacsi bs
            JOIN chuyenmon cm ON bs.maBS = cm.maBS
            JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
            WHERE bs.tenBS LIKE %s
            """
            cursor.execute(query, (f"%{doctor_name_search}%",))
            doctors = cursor.fetchall()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
            return [SlotSet("doctor_name", None)]

        if not doctors:
            dispatcher.utter_message(text=f"Không tìm thấy bác sĩ nào có tên chứa '{doctor_name_search}'. Hãy thử tên khác.")
            return [SlotSet("doctor_name", None)]

        dispatcher.utter_message(
            text=f"""
            <div style="font-family: Arial, sans-serif; font-size: 15px; color: #333;">
                <div style="font-weight: bold; color: #0d6efd; margin-bottom: 8px;">
                    🔍 Tìm thấy {len(doctors)} bác sĩ phù hợp với từ khóa "<span style='color:#dc3545;'>{doctor_name_search}</span>":
                </div>
            </div>
            """,
            metadata={"html": True}
        )

        for doc in doctors:
            doc_card = f"""
                <div style="background: #f8f9fa; border-left: 4px solid #0d6efd;
                            border-radius: 8px; padding: 10px 14px; margin-bottom: 8px;">
                    <div style="font-weight: bold; color: #0d6efd; margin-bottom: 4px;">
                        🩺 Bác sĩ {doc['tenBS']}
                    </div>
                    <div><strong>Chuyên khoa:</strong> {doc['tenCK']}</div>
                    <div><strong>SĐT:</strong> {doc['sdtBS']}</div>
                </div>
            """
            dispatcher.utter_message(
                text=doc_card,
                buttons=[
                    {
                        "title": "📄 Xem chi tiết",
                        "payload": f"/view_doctor_detail{{\"doctor_id\":\"{doc['maBS']}\"}}"
                    }
                ],
                metadata={"html": True}
            )

        return [SlotSet("current_task", None),
                SlotSet("doctor_name", None)]


class ActionViewDoctorDetail(Action):
    def name(self) -> Text:
        return "action_view_doctor_detail"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        # Lấy doctor_id từ latest_message entities (giả sử NLU extract entity doctor_id từ payload)
        entities = tracker.latest_message.get('entities', [])
        doctor_id = next((e['value'] for e in entities if e['entity'] == 'doctor_id'), None)
        
        if not doctor_id:
            dispatcher.utter_message(text="Không nhận được ID bác sĩ. Hãy thử lại.")
            return []

        # Query MySQL để lấy chi tiết bác sĩ theo maBS (thêm fields nếu có: email, kinhnghiem, dia_chi, etc.)
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS, bs.emailBS
            FROM bacsi bs
            JOIN chuyenmon cm ON bs.maBS = cm.maBS
            JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
            WHERE bs.maBS = %s
            """
            cursor.execute(query, (doctor_id,))
            doctor = cursor.fetchone()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
            return []

        if not doctor:
            dispatcher.utter_message(text="Không tìm thấy thông tin bác sĩ.")
            return []

        # Utter chi tiết
        detail_html = f"""
        <div style="font-family: Arial, sans-serif; background-color: #f8f9fa;
                    border-radius: 10px; border-left: 5px solid #0d6efd;
                    padding: 14px 18px; max-width: 420px; box-shadow: 0 2px 6px rgba(0,0,0,0.1);">
            <h3 style="color: #0d6efd; margin-top: 0; margin-bottom: 8px;">📋 Thông tin chi tiết bác sĩ</h3>
            <p style="margin: 4px 0;"><strong>👨‍⚕️ Họ tên:</strong> {doctor['tenBS']}</p>
            <p style="margin: 4px 0;"><strong>🆔 Mã BS:</strong> {doctor['maBS']}</p>
            <p style="margin: 4px 0;"><strong>🏥 Chuyên khoa:</strong> {doctor['tenCK']}</p>
            <p style="margin: 4px 0;"><strong>📞 SĐT:</strong> {doctor['sdtBS']}</p>
            <p style="margin: 4px 0;"><strong>📧 Email:</strong> {doctor.get('emailBS', 'Chưa có thông tin')}</p>
            <p style="margin: 4px 0;"><strong>💼 Kinh nghiệm:</strong> 20 năm</p>
            <p style="margin: 4px 0;"><strong>🩺 Dịch vụ:</strong> Tư vấn và khám chuyên sâu về {doctor['tenCK']}.</p>
            <hr style="border: none; border-top: 1px solid #dee2e6; margin: 10px 0;">
            <p style="font-weight: bold; color: #333;">Bạn có muốn đặt lịch với bác sĩ này không?</p>
        </div>
        """

        buttons = [
            {"title": "📅 Đặt lịch", "payload": "/book_appointment"},
            {"title": "🔍 Tìm bác sĩ khác", "payload": "/search_doctor_info"}
        ]

        dispatcher.utter_message(text=detail_html, buttons=buttons, metadata={"html": True})


        return []


class ActionSearchSpecialty(Action):
    def name(self) -> Text:
        return "action_search_specialty"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        print(f"[DEBUG] action_search_specialty START")
        print(f"[DEBUG] just_explained = {tracker.get_slot('just_explained')}")
        
        entities = tracker.latest_message.get('entities', [])
        specialty_entity = next((ent['value'] for ent in entities if ent['entity'] == 'specialty'), None)
        specialty = specialty_entity or tracker.get_slot("specialty") 

        if not specialty:
            dispatcher.utter_message(text="Vui lòng nhập tên chuyên khoa bạn muốn tra cứu.")
            return [SlotSet("just_explained", False), FollowupAction("book_appointment_form")]

        # Query DB...
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = "SELECT tenCK, mo_ta FROM chuyenkhoa WHERE tenCK = %s"
            cursor.execute(query, (specialty,))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi DB: {e}")
            return [SlotSet("just_explained", False), FollowupAction("book_appointment_form")]

        if not result:
            dispatcher.utter_message(text=f"Không tìm thấy '{specialty}'.")
            return [
                SlotSet("just_explained", False),
                SlotSet("specialty", None),
                FollowupAction("book_appointment_form")
            ]

        # Explain
        explanation = result.get('mo_ta', f"Chuyên khoa {specialty}...")
        dispatcher.utter_message(text=f"📋 **{specialty.title()}**\n{explanation}\n\nTiếp tục đặt lịch...")

        print(f"[DEBUG] action_search_specialty DONE, reactivating form")
        
        # Reactivate form with FollowupAction
        return [
            SlotSet("just_explained", False),
            SlotSet("specialty", specialty),
            FollowupAction("book_appointment_form")  # ← Force reactivate!
        ]


class ActionSubmitBooking(Action):
    def name(self) -> Text:
        return "action_submit_booking"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        # ... (Phần lấy slot giữ nguyên) ...
        doctor_name = tracker.get_slot("doctor_name")
        specialty_name = tracker.get_slot("specialty")
        date_str = tracker.get_slot("date")
        appointment_time = tracker.get_slot("appointment_time")
        decription = tracker.get_slot("decription")
        
        patient_id = get_patient_id(tracker)
        if not patient_id:
            dispatcher.utter_message(text="Lỗi: Bạn cần đăng nhập để đặt lịch")
            return []
        
        if not all([doctor_name, specialty_name, date_str, appointment_time, decription]):
            dispatcher.utter_message(text="Thông tin chưa đầy đủ. Vui lòng hoàn tất form.")
            return []

        try:
            parsed_date = datetime.strptime(date_str, '%d/%m/%Y').date()
        except ValueError:
            dispatcher.utter_message(text="Ngày không hợp lệ.")
            return []

        # ================= SỬA LỖI TẠI ĐÂY =================
        # Lấy maBS từ tenBS
        try:
            conn_bs = mysql.connector.connect(**DB_CONFIG)
            
            # THÊM buffered=True ĐỂ TRÁNH LỖI "Unread result found"
            cursor_bs = conn_bs.cursor(dictionary=True, buffered=True) 
            
            query_bs = "SELECT maBS FROM bacsi WHERE tenBS = %s"
            cursor_bs.execute(query_bs, (doctor_name,))
            bs_result = cursor_bs.fetchone()
            
            cursor_bs.close() # Đóng cursor an toàn vì đã buffer
            conn_bs.close()
            
            if not bs_result:
                dispatcher.utter_message(text=f"Không tìm thấy bác sĩ tên {doctor_name} trong hệ thống.")
                return []
            maBS = bs_result['maBS']
            
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi DB (lấy mã BS): {e}")
            return []
        # ===================================================

        # Bắt đầu khối Transaction để Insert
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            # Cũng nên thêm buffered=True ở đây cho an toàn
            cursor = conn.cursor(dictionary=True, buffered=True) 
            
            # === BƯỚC 1: Tạo mahen tuần tự ===
            query_max_id = "SELECT MAX(CAST(SUBSTRING(mahen, 3) AS UNSIGNED)) as max_id FROM lichhen"
            cursor.execute(query_max_id)
            result = cursor.fetchone()

            current_max_id = 0 
            if result and result['max_id'] is not None:
                current_max_id = int(result['max_id'])
            
            next_id_num = current_max_id + 1
            mahen = f"LH{next_id_num:08d}"

            # === BƯỚC 2: Lấy maCK ===
            maCK = None
            if specialty_name:
                cursor.execute("SELECT maCK FROM chuyenkhoa WHERE tenCK = %s", (specialty_name,))
                ck_result = cursor.fetchone()
                if ck_result:
                    maCK = ck_result['maCK']
            
            if not maCK:
                dispatcher.utter_message(text=f"Lỗi nghiêm trọng: Không tìm thấy mã chuyên khoa cho '{specialty_name}'.")
                cursor.close()
                conn.close()
                return []

            # === BƯỚC 3: Insert vào DB ===
            query_insert = """
            INSERT INTO lichhen (mahen, maBN, maBS, ngaythangnam, khunggio, trangthai, maCK, mota)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            cursor.execute(query_insert, (mahen, patient_id, maBS, parsed_date, appointment_time, 'ChuaKham', maCK, decription))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            dispatcher.utter_message(text=f"Đặt lịch thành công! Mã hẹn của bạn là: {mahen}. Cảm ơn bạn.")
            
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi đặt lịch: {e}")
            return []

        # Reset slots
        events = [
            SlotSet("current_task", None),
            SlotSet("doctor_name", None),
            SlotSet("specialty", None),
            SlotSet("date", None),
            SlotSet("appointment_time", None),
            SlotSet("decription", None)
        ]
        return events

class ActionResetBooking(Action):
    def name(self) -> Text:
        return "action_reset_booking"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        dispatcher.utter_message(text="Đã hủy yêu cầu đặt lịch. Bạn có thể bắt đầu lại.")
        events = [
            SlotSet("current_task", None),
            SlotSet("doctor_name", None),
            SlotSet("specialty", None),
            SlotSet("date", None),
            SlotSet("appointment_time", None),
            SlotSet("decription", None)
        ]
        return events


class ActionResetCancel(Action):
    def name(self) -> Text:
        return "action_reset_cancel"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        dispatcher.utter_message(text="Đã hủy hành động hủy lịch. Lịch hẹn vẫn giữ nguyên.")
        events = [
            SlotSet("selected_appointment_id", None),
            SlotSet("current_task", None),
            SlotSet("appointment_date", None)
        ]
        return events

# ================================ TÌM TOA THUỐC ============================

class ValidateSearchPrescriptionForm(FormValidationAction):
    """Validation cho search_prescription_form với hỗ trợ interruption"""
    
    def name(self) -> Text:
        return "validate_search_prescription_form"

    def _handle_form_interruption(self, dispatcher, tracker):
        """Xử lý interruption trong prescription form"""
        latest_message = tracker.latest_message
        
        if hasattr(latest_message, 'intent'):
            latest_intent = latest_message.intent.get('name')
        else:
            latest_intent = latest_message.get('intent', {}).get('name')

        # === THÊM MỚI: Xử lý list_all_specialties ===
        if latest_intent == "list_all_specialties":
            list_action = ActionListAllSpecialties()
            list_action.run(dispatcher, tracker, {})
            # Trả về slot dummy để form tiếp tục mà không bị gãy flow
            return {"just_listed_all_specialties_dummy": False}

        # === Xử lý explain_specialty ===
        if latest_intent == "explain_specialty":
            explain_action = ActionExplainSpecialtyInForm()
            explain_action.run(dispatcher, tracker, {})
            return {
                "prescription_date": tracker.get_slot("prescription_date"),
                "just_explained": False,
            }
        
        # === Xử lý ask_doctor_info ===
        if latest_intent == "ask_doctor_info":
            info_action = ActionShowDoctorInfoInForm()
            info_action.run(dispatcher, tracker, {})
            return {
                "prescription_date": tracker.get_slot("prescription_date"),
                "just_asked_doctor_info": False,
            }
        
        # === Xử lý list_doctors_by_specialty ===
        if latest_intent == "list_doctors_by_specialty":
            list_action = ActionListDoctorsInForm()
            list_action.run(dispatcher, tracker, {})
            return {
                "prescription_date": tracker.get_slot("prescription_date"),
                "just_listed_doctors": False,
            }
        
        # === THÊM MỚI: Xử lý ask_who_examined_me ===
        if latest_intent == "ask_who_examined_me":
            info_action = ActionShowExaminingDoctorInForm()
            info_action.run(dispatcher, tracker, {})
            # Trả về slot dummy để form tiếp tục
            return {"just_asked_examining_doctor": False}

        if latest_intent == "list_all_doctors":
            list_action = ActionListAllDoctors()
            list_action.run(dispatcher, tracker, {}) # Dùng {} cho domain
            return {"just_listed_all_doctors_dummy": False} # Trả về slot dummy để form tiếp tục
        
        if latest_intent == "ask_doctor_schedule":
            schedule_action = ActionShowDoctorSchedule()
            schedule_action.run(dispatcher, tracker, {})
            return {"just_asked_doctor_schedule_dummy": False}

        return {}

    def validate_prescription_date(
        self, 
        slot_value: Any, 
        dispatcher: CollectingDispatcher, 
        tracker: Tracker, 
        domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        """Validate ngày khám để tra cứu toa thuốc"""
        # Lấy maBN động
        patient_id = get_patient_id(tracker)
        
        # Kiểm tra nếu user đã đăng nhập
        if not patient_id:
            dispatcher.utter_message(text="Lỗi: Bạn cần đăng nhập để xem toa thuốc.")
            return [] # Dừng action
        
        # === CHECK INTERRUPTION TRƯỚC ===
        interruption_result = self._handle_form_interruption(dispatcher, tracker)
        if interruption_result:
            return interruption_result
        
        # Kiểm tra nếu user muốn tìm toa thuốc mới nhất
        if tracker.get_slot("search_latest_prescription"):
            # Bỏ qua validation, để action_get_latest_prescription xử lý
            return {"prescription_date": "latest"}
        
        # === VALIDATION BÌNH THƯỜNG ===
        if not slot_value:
            # dispatcher.utter_message(text="Vui lòng cung cấp ngày khám bạn muốn tra cứu toa thuốc (DD/MM/YYYY).")
            return {"prescription_date": None}

        date_input = str(slot_value).strip()
        
        # Validate format
        try:
            parsed_date = datetime.strptime(date_input, '%d/%m/%Y').date()
        except ValueError:
            dispatcher.utter_message(
                text="Ngày không hợp lệ. Vui lòng nhập theo định dạng DD/MM/YYYY.\n"
            )
            return {"prescription_date": None}

        # Không cần kiểm tra ngày trong quá khứ vì tra cứu toa thuốc có thể là ngày cũ
        
        return {"prescription_date": date_input}


class ActionSearchPrescription(Action):
    """Action khởi tạo search prescription form - CHỈ set context"""
    
    def name(self) -> Text:
        return "action_search_prescription"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        # CHỈ set context, KHÔNG utter message
        return [
            SlotSet("current_task", "search_prescription"),
            SlotSet("prescription_date", None),
            SlotSet("search_latest_prescription", False)
        ]


class ActionGetLatestPrescription(Action):
    """Action lấy toa thuốc mới nhất"""
    
    def name(self) -> Text:
        return "action_get_latest_prescription"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        
        # Set flag để form biết đây là tìm toa thuốc mới nhất
        return [
            SlotSet("search_latest_prescription", True),
            SlotSet("prescription_date", "latest")
        ]


class ActionShowPrescriptionResults(Action):
    """Action hiển thị kết quả tìm toa thuốc (sau khi form hoàn tất)"""
    
    def name(self) -> Text:
        return "action_show_prescription_results"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        
        prescription_date = tracker.get_slot("prescription_date")
        search_latest = tracker.get_slot("search_latest_prescription")
        # Lấy maBN động
        patient_id = get_patient_id(tracker)
        
        # Kiểm tra nếu user đã đăng nhập
        if not patient_id:
            dispatcher.utter_message(text="Lỗi: Bạn cần đăng nhập để xem toa thuốc.")
            return [] # Dừng action
        
        if not prescription_date and not search_latest:
            dispatcher.utter_message(text="Không có thông tin ngày khám hoặc yêu cầu tìm toa thuốc.")
            return []

        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            
            if search_latest or prescription_date == "latest":
                # Tìm toa thuốc mới nhất
                query = """
                SELECT 
                    lk.maLanKham,
                    lk.ngaythangnamkham,
                    t.tenThuoc,
                    tt.lieuluong,
                    tt.soluong,
                    tt.donvi,
                    tt.thoigianSD
                FROM lankham lk
                JOIN hosobenhnhan hs ON lk.maHS = hs.maHS
                JOIN toathuoc tt ON lk.maLanKham = tt.maLanKham
                JOIN thuoc t ON tt.maThuoc = t.maThuoc
                WHERE hs.maBN = %s
                ORDER BY lk.ngaythangnamkham DESC
                LIMIT 20
                """
                cursor.execute(query, (patient_id,))
                prescriptions = cursor.fetchall()
                
                if not prescriptions:
                    dispatcher.utter_message(
                        text="Không tìm thấy toa thuốc nào trong hồ sơ của bạn."
                    )
                    cursor.close()
                    conn.close()
                    return self._reset_slots()
                
                # Lấy ngày khám mới nhất
                latest_date = prescriptions[0]['ngaythangnamkham']
                title = f"Toa thuốc mới nhất (Ngày khám: {latest_date.strftime('%d/%m/%Y')})"
                
            else:
                # Tìm toa thuốc theo ngày cụ thể
                parsed_date = datetime.strptime(prescription_date, '%d/%m/%Y').date()
                
                query = """
                SELECT 
                    lk.maLanKham,
                    lk.ngaythangnamkham,
                    t.tenThuoc,
                    tt.lieuluong,
                    tt.soluong,
                    tt.donvi,
                    tt.thoigianSD
                FROM lankham lk
                JOIN hosobenhnhan hs ON lk.maHS = hs.maHS
                JOIN toathuoc tt ON lk.maLanKham = tt.maLanKham
                JOIN thuoc t ON tt.maThuoc = t.maThuoc
                WHERE hs.maBN = %s AND DATE(lk.ngaythangnamkham) = %s
                ORDER BY t.tenThuoc
                """
                cursor.execute(query, (patient_id, parsed_date))
                prescriptions = cursor.fetchall()
                
                if not prescriptions:
                    dispatcher.utter_message(
                        text=f"Không tìm thấy toa thuốc nào trong ngày {prescription_date}."
                    )
                    buttons = [
                        {"title": "📋 Xem toa thuốc mới nhất", "payload": "/request_latest_prescription"},
                        {"title": "📅 Tìm theo ngày khác", "payload": "/search_prescription"},
                        {"title": "🏠 Quay lại menu", "payload": "/greet"}
                    ]
                    dispatcher.utter_message(
                        text="Bạn có muốn thử cách khác không?", 
                        buttons=buttons
                    )
                    cursor.close()
                    conn.close()
                    return self._reset_slots()
                
                title = f"Toa thuốc ngày {prescription_date}"
            
            cursor.close()
            conn.close()
            
            # Hiển thị kết quả bằng HTML table
            self._display_prescription_table(dispatcher, prescriptions, title)
            
            # Offer next action
            buttons = [
                {"title": "📅 Tìm toa thuốc khác", "payload": "/search_prescription"},
                {"title": "📅 Đặt lịch hẹn", "payload": "/book_appointment"},
                {"title": "🏠 Quay lại menu", "payload": "/greet"}
            ]
            dispatcher.utter_message(text="Bạn có muốn làm gì tiếp theo?", buttons=buttons)
            
            return self._reset_slots()
            
        except Error as e:
            dispatcher.utter_message(text=f"❌ Lỗi kết nối cơ sở dữ liệu: {e}")
            return self._reset_slots()

    def _display_prescription_table(self, dispatcher, prescriptions, title):
        """Hiển thị toa thuốc dưới dạng bảng HTML"""
        
        # Tạo HTML table với styling đẹp
        html_table = f"""
        <style>
            .prescription-container {{
                font-family: Arial, sans-serif;
                /* SỬA ĐỔI: 
                   - Bỏ max-width để khung co lại
                   - Thêm width: fit-content để tự động co theo nội dung
                   - Thêm min-width để không bị quá hẹp
                   - Chuyển box-shadow, border-radius, overflow từ con sang cha
                */
                width: fit-content;
                min-width: 350px; 
                margin: 10px 0;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                
                /* ================================= */
                /* ===== THÊM MỚI THEO YÊU CẦU ===== */
                border: 1px solid #dee2e6; /* <-- Thêm đường viền này */
                /* ================================= */
            }}
            .prescription-title {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 12px 16px;
                font-weight: bold;
                font-size: 16px;
                /* SỬA ĐỔI: Bỏ border-radius, container cha sẽ xử lý */
            }}
            .prescription-table {{
                /* SỬA ĐỔI: Bỏ width: 100% để bảng co lại theo nội dung */
                border-collapse: collapse;
                background: white;
                /* SỬA ĐỔI: Bỏ box-shadow, border-radius, overflow */
            }}
            .prescription-table thead {{
                background: #f8f9fa;
            }}
            .prescription-table th {{
                padding: 12px 8px;
                text-align: left;
                font-weight: bold;
                color: #495057;
                border-bottom: 2px solid #dee2e6;
                font-size: 14px;
            }}
            .prescription-table td {{
                padding: 10px 8px;
                border-bottom: 1px solid #e9ecef;
                font-size: 14px;
                color: #333;
            }}
            .prescription-table tr:last-child td {{
                border-bottom: none;
            }}
            .prescription-table tr:hover {{
                background: #f8f9fa;
            }}
            .medicine-name {{
                font-weight: 600;
                color: #667eea;
            }}
            .dosage {{
                color: #28a745;
                font-weight: 500;
            }}
            .prescription-footer {{
                background: #f8f9fa;
                padding: 10px 16px;
                font-size: 13px;
                color: #6c757d;
                border-top: 2px solid #dee2e6;
                /* SỬA ĐỔI: Bỏ border-radius và margin-top */
            }}
            @media screen and (max-width: 600px) {{
                /* SỬA ĐỔI: Đảm bảo container vẫn chiếm 100% trên màn hình nhỏ */
                .prescription-container {{
                    width: 100%; 
                    min-width: 0;
                }}
                .prescription-table th,
                .prescription-table td {{
                    font-size: 12px;
                    padding: 8px 6px;
                }}
                .prescription-title {{
                    font-size: 14px;
                }}
            }}
        </style>
        
        <div class="prescription-container">
            <div class="prescription-title">
                💊 {title}
            </div>
            <table class="prescription-table">
                <thead>
                    <tr>
                        <th>STT</th>
                        <th>Tên thuốc</th>
                        <th>Liều lượng</th>
                        <th>Số lượng</th>
                        <th>Đơn vị</th>
                        <th>Thời gian SD</th>
                    </tr>
                </thead>
                <tbody>
        """
        
        # Thêm các dòng dữ liệu
        for idx, med in enumerate(prescriptions, 1):
            html_table += f"""
                    <tr>
                        <td>{idx}</td>
                        <td class="medicine-name">{med['tenThuoc']}</td>
                        <td class="dosage">{med['lieuluong']}</td>
                        <td>{med['soluong']}</td>
                        <td>{med['donvi']}</td>
                        <td>{med['thoigianSD']}</td>
                    </tr>
            """
        
        html_table += f"""
                </tbody>
            </table>
            <div class="prescription-footer">
                <strong>Tổng số thuốc:</strong> {len(prescriptions)} loại | 
                <strong>Mã lần khám:</strong> {prescriptions[0]['maLanKham']}
            </div>
        </div>
        """
        
        dispatcher.utter_message(text=html_table)

    def _reset_slots(self):
        """Reset các slots sau khi hoàn thành"""
        return [
            SlotSet("prescription_date", None),
            SlotSet("search_latest_prescription", False),
            SlotSet("current_task", None)
        ]


class ActionSetCurrentTask(Action):
    def name(self) -> Text:
        return "action_set_current_task"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        intent = tracker.latest_message['intent'].get('name', '')
        if intent == 'request_doctor':
            return [SlotSet("current_task", "request_doctor")]
        elif intent == 'book_appointment':
            return [SlotSet("current_task", "book_appointment")]
        elif intent == 'cancel_appointment' or intent == 'cancel_specific_appointment': # <-- SỬA ĐỔI Ở ĐÂY
            return [SlotSet("current_task", "cancel_appointment")]
        elif intent == 'search_prescription':  # ← THÊM MỚI
            return [SlotSet("current_task", "search_prescription")]
        return []


class ActionHandleDeny(Action):
    """
    Custom Action để xử lý intent 'deny': Dừng tất cả forms active, reset slots liên quan,
    và đưa bot về trạng thái mặc định (ví dụ: chào hỏi hoặc menu chính).
    """
    def name(self) -> Text:
        return "action_handle_deny"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        # Utter thông báo hủy
        dispatcher.utter_message(
            text="Đã hủy yêu cầu hiện tại. Bạn có muốn làm gì khác không? (Ví dụ: đặt lịch mới, tra cứu lịch hẹn, hoặc chào hỏi để quay về menu chính.)"
        )
        
        # Deactivate form hiện tại (nếu có)
        events = [ActiveLoop(None)]
        
        # Reset slots chung cho các task (tùy theo current_task)
        current_task = tracker.get_slot("current_task")
        if current_task == "book_appointment":
            events += [
                SlotSet("doctor_name", None),
                SlotSet("specialty", None),
                SlotSet("date", None),
                SlotSet("appointment_time", None),
                SlotSet("decription", None),
                SlotSet("just_listed_doctors", None),
                SlotSet("just_explained", None),
                SlotSet("just_asked_doctor_info", None)
            ]
        elif current_task == "cancel_appointment":
            events += [
                SlotSet("selected_appointment_id", None),
                SlotSet("appointment_date", None)
            ]
        elif current_task == "search_prescription":  # ← THÊM MỚI
            events += [
                SlotSet("prescription_date", None),
                SlotSet("search_latest_prescription", False)
            ]
        
        # Reset current_task và requested_slot
        events += [
            SlotSet("current_task", None),
            SlotSet("requested_slot", None)
        ]
        
        return events


# (Dán vào cuối file actions.py)
# ================================ NHẮC LỊCH HẸN ============================

class ActionCheckUpcomingAppointments(Action):
    """
    Action tự động kiểm tra và nhắc nhở lịch hẹn sắp tới khi người dùng
    gửi intent 'greet' (được coi như vừa đăng nhập).
    
    SỬA ĐỔI: Thêm nút "Hủy lịch" cho từng lịch hẹn.
    """
    def name(self) -> Text:
        return "action_check_upcoming_appointments"

    def _format_time(self, time_obj):
        """Helper để xử lý time_obj (có thể là timedelta hoặc time)"""
        if isinstance(time_obj, timedelta):
            return (datetime.min + time_obj).time().strftime('%H:%M')
        elif isinstance(time_obj, time):
            return time_obj.strftime('%H:%M')
        return str(time_obj)

    def _get_vietnamese_day_name(self, weekday_index):
        """Helper để chuyển 0-6 sang Thứ 2 - Chủ Nhật"""
        days_vn = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ Nhật"]
        return days_vn[weekday_index]

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        
        # 1. Lấy maBN (patient_id) từ metadata
        patient_id = get_patient_id(tracker)
        
        print(f"[DEBUG] ActionCheckUpcomingAppointments: Đã nhận được patient_id: {patient_id}")

        # 2. Chỉ chạy nếu user đã đăng nhập (có patient_id)
        if not patient_id:
            print("[DEBUG] ActionCheckUpcomingAppointments: Không có patient_id, bỏ qua.")
            return []

        print(f"[DEBUG] Đang chạy ActionCheckUpcomingAppointments cho bệnh nhân: {patient_id}")
        
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            
            # 3. Lấy ngày hôm nay
            today_date = datetime.now().date()
            
            # 4. Query lịch hẹn SẮP TỚI (từ hôm nay) và CHƯA KHÁM
            query = """
            SELECT 
                lh.mahen, 
                lh.ngaythangnam, 
                lh.khunggio, 
                bs.tenBS, 
                ck.tenCK,
                lh.mota
            FROM lichhen lh
            JOIN bacsi bs ON lh.maBS = bs.maBS
            JOIN chuyenkhoa ck ON lh.maCK = ck.maCK
            WHERE lh.maBN = %s 
              AND DATE(lh.ngaythangnam) >= %s
              AND lh.trangthai = 'ChuaKham'
            ORDER BY lh.ngaythangnam, lh.khunggio
            LIMIT 3 
            """ # Giới hạn 3 lịch hẹn gần nhất cho gọn
            
            cursor.execute(query, (patient_id, today_date))
            appointments = cursor.fetchall()
            cursor.close()
            conn.close()

            # 5. Nếu có lịch hẹn, gửi thông báo
            if appointments:
                # ===============================================
                # === SỬA ĐỔI: CHIA NHỎ LOGIC HIỂN THỊ ===
                # ===============================================
                
                # Hiển thị tiêu đề trước
                title_message = f"""
                <div style="font-family: Arial, sans-serif; font-size: 15px; color: #333;
                            background: #fffbef; border-left: 5px solid #ffc107; border-radius: 8px;
                            padding: 12px 16px; margin: 10px 0 4px 0;">
                    <div style="font-weight: bold; color: #856404; margin-bottom: 8px;">
                        🔔 **Thông báo lịch hẹn sắp tới:**
                    </div>
                </div>
                """
                dispatcher.utter_message(text=title_message, html=True)

                # Lặp qua từng lịch hẹn và gửi kèm nút bấm
                for appt in appointments:
                    date_obj = appt['ngaythangnam']
                    day_name_vn = self._get_vietnamese_day_name(date_obj.weekday())
                    date_str = date_obj.strftime('%d/%m/%Y')
                    time_str = self._format_time(appt['khunggio'])
                    
                    # HTML cho 1 lịch hẹn
                    html_appt = f"""
                    <div style="font-family: Arial, sans-serif; font-size: 15px; color: #333;
                                background: #fffbef; border-left: 5px solid #ffc107; border-radius: 8px; 
                                padding: 8px 10px; margin: 0 0 4px 0;">
                        <div><strong>Ngày:</strong> {day_name_vn}, {date_str}</div>
                        <div><strong>Giờ:</strong> {time_str}</div>
                        <div><strong>Bác sĩ:</strong> {appt['tenBS']} ({appt['tenCK']})</div>
                        <div><strong>Mã hẹn:</strong> {appt['mahen']}</div>
                        <div><strong>Mô tả:</strong> {appt['mota']}</div>
                    </div>
                    """
                    
                    # Nút bấm với payload chứa mahen
                    buttons = [
                        {
                            "title": f"❌ Hủy lịch hẹn này ({appt['mahen']})",
                            # Intent mới sẽ được tạo ở nlu.yml
                            "payload": f"/cancel_specific_appointment{{\"appointment_id\":\"{appt['mahen']}\"}}"
                        }
                    ]
                    
                    # Gửi tin nhắn
                    dispatcher.utter_message(text=html_appt, buttons=buttons, html=True)

                # Hiển thị footer
                footer_message = """
                <div style="font-family: Arial, sans-serif; font-size: 14px; color: #333; margin-top: 4px;">
                    👉 Vui lòng đến đúng giờ.
                </div>
                """
                dispatcher.utter_message(text=footer_message, html=True)
                # ===============================================
                # === KẾT THÚC SỬA ĐỔI ===
                # ===============================================
            else:
                # ⚠️ THÊM DÒNG NÀY ĐỂ DEBUG ⚠️
                print(f"[DEBUG] ActionCheckUpcomingAppointments: Không tìm thấy lịch hẹn nào cho {patient_id}.")
                dispatcher.utter_message(text="Bạn không có lịch hẹn nào!", html=True)

        except Error as e:
            print(f"[ERROR] Lỗi DB trong ActionCheckUpcomingAppointments: {e}")
            # Không báo lỗi cho user, chỉ log
        
        return []

class ActionListAllSpecialties(Action):
    """
    Action tra cứu và hiển thị TẤT CẢ chuyên khoa trong hệ thống.
    """
    def name(self) -> Text:
        return "action_list_all_specialties"

    def run(self, dispatcher, tracker, domain):
        print(f"[DEBUG] Running ActionListAllSpecialties")
        
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            # Query lấy tên chuyên khoa và mô tả
            query = "SELECT tenCK, mota FROM chuyenkhoa ORDER BY tenCK"
            cursor.execute(query)
            specialties = cursor.fetchall()
            cursor.close()
            conn.close()
            
            if specialties:
                html_list = f"""
                <div style="font-family: Arial, sans-serif; font-size: 15px; color: #333; background: #f0fdf4; border-radius: 10px; padding: 12px; border: 1px solid #bbf7d0;">
                    <div style="color: #16a34a; font-weight: bold; margin-bottom: 8px; font-size: 16px;">
                        🏥 Danh sách các chuyên khoa hiện có:
                    </div>
                """
                
                for spec in specialties:
                    desc = spec['mota'] if spec['mota'] else "Chuyên điều trị các bệnh lý liên quan."
                    # Cắt ngắn mô tả nếu quá dài
                    if len(desc) > 60: desc = desc[:60] + "..."
                    
                    html_list += f"""
                    <div style="background: #ffffff; border-left: 4px solid #16a34a; border-radius: 6px; padding: 8px 12px; margin-bottom: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.05);">
                        <div style="font-weight: bold; color: #15803d;">🩺 {spec['tenCK']}</div>
                        <div style="font-size: 13px; color: #555;">{desc}</div>
                    </div>
                    """
                
                html_list += """
                    <div style="margin-top: 6px; font-style: italic; color: #666;">👉 Vui lòng tiếp tục yêu cầu của bạn...</div>
                </div>
                """
                dispatcher.utter_message(text=html_list, html=True)
            else:
                dispatcher.utter_message(text="Hiện tại hệ thống chưa cập nhật danh sách chuyên khoa.")
                
        except Error as e:
            print(f"[ERROR] DB Error in ActionListAllSpecialties: {e}")
            dispatcher.utter_message(text=f"Lỗi khi tra cứu cơ sở dữ liệu: {e}")
        
        return []
    
    # actions.py

class ActionCheckReexaminationDate(Action):
    def name(self) -> Text:
        return "action_check_reexamination_date"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        
        patient_id = get_patient_id(tracker)
        if not patient_id:
            dispatcher.utter_message(text="Bạn cần đăng nhập để xem lịch tái khám.")
            return []

        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            
            today_date = datetime.now().date()
            
            # 1. SỬA CÂU QUERY: Thêm bs.maBS vào SELECT
            query = """
            SELECT 
                lk.ngaytaikham,
                lk.ngaythangnamkham,
                lk.chuandoan,
                lk.lieutrinhdieutri,
                bs.maBS, 
                bs.tenBS,
                ck.tenCK
            FROM lankham lk
            JOIN hosobenhnhan hs ON lk.maHS = hs.maHS
            LEFT JOIN bacsi bs ON lk.maBS = bs.maBS
            LEFT JOIN chuyenmon cm ON bs.maBS = cm.maBS
            LEFT JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
            WHERE hs.maBN = %s 
              AND lk.ngaytaikham >= %s
            ORDER BY lk.ngaytaikham ASC
            LIMIT 1
            """
            
            cursor.execute(query, (patient_id, today_date))
            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result:
                date_taikham_str = result['ngaytaikham'].strftime('%d/%m/%Y')
                date_kham_cu_str = result['ngaythangnamkham'].strftime('%d/%m/%Y')
                
                # Lấy thông tin để tạo payload
                ma_bs = result['maBS']
                ten_bs = result['tenBS'] if result['tenBS'] else "Không rõ"
                ten_ck = result['tenCK'] if result['tenCK'] else "Tổng quát"
                
                diagnosis = result['chuandoan']
                note = result['lieutrinhdieutri']

                message = f"""
                <div style="font-family: Arial, sans-serif; font-size: 15px; color: #333;
                            background: #e3f2fd; border-left: 5px solid #2196f3; border-radius: 8px; 
                            padding: 12px 16px; margin: 10px 0;">
                    <div style="font-weight: bold; color: #1976d2; margin-bottom: 8px;">
                        🩺 Thông báo tái khám:
                    </div>
                    <div><strong>📅 Ngày hẹn tái khám:</strong> <span style="color: #d32f2f; font-weight: bold;">{date_taikham_str}</span></div>
                    <hr style="border: 0; border-top: 1px solid #bbdefb; margin: 8px 0;">
                    <div style="font-size: 14px; color: #555;">
                        <em>Thông tin lần khám trước ({date_kham_cu_str}):</em><br>
                        - <strong>Bác sĩ:</strong> {ten_bs} ({ten_ck})<br>
                        - <strong>Chẩn đoán:</strong> {diagnosis}<br>
                        - <strong>Lời dặn:</strong> {note}
                    </div>
                </div>
                """
                
                # 2. SỬA PAYLOAD NÚT BẤM: Truyền doctor_id và specialty vào
                # Bot sẽ hiểu là "Tôi muốn đặt với bác sĩ này", và sẽ bỏ qua bước hỏi tên bác sĩ
                buttons = [
                    {
                        "title": f"📅 Đặt lịch với BS {ten_bs}",
                        "payload": f"/book_with_doctor{{\"doctor_id\":\"{ma_bs}\", \"specialty\":\"{ten_ck}\"}}"
                    }
                ]
                
                dispatcher.utter_message(text=message, buttons=buttons, html=True)
                
            else:
                dispatcher.utter_message(
                    text="Hiện tại bạn không có lịch hẹn tái khám nào được ghi nhận trong hồ sơ."
                )
                buttons = [
                    {"title": "📅 Đặt lịch khám mới", "payload": "/book_appointment"}
                ]
                dispatcher.utter_message(text="Bạn có muốn đặt lịch khám mới không?", buttons=buttons)

        except Error as e:
            print(f"[ERROR] Lỗi DB ActionCheckReexaminationDate: {e}")
            dispatcher.utter_message(text="Có lỗi xảy ra khi tra cứu hồ sơ. Vui lòng thử lại sau.")
        
        return []