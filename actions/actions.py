from typing import Any, Text, Dict, List
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, FollowupAction, ActiveLoop
from rasa_sdk.forms import FormValidationAction
import mysql.connector
from mysql.connector import Error
from datetime import datetime
import os
from dotenv import load_dotenv
import re  # Thêm để parse payload fallback
from rasa_sdk.types import DomainDict
from datetime import datetime, timedelta



# Load file .env
load_dotenv()

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
MA_BN_GLOBAL = "BN0001"  # Ví dụ: "BN001", thay bằng giá trị thực tế hoặc từ tracker.get_slot("patient_id")

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
                text="Xin lỗi, tôi không hiểu rõ câu trả lời của bạn. 🤔"
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
            # NGOÀI FORM - gợi ý chức năng
            message = (
                "Xin lỗi, tôi không hiểu yêu cầu của bạn. 😕\n\n"
                "Tôi có thể giúp bạn:\n"
                "🩺 Đề xuất bác sĩ dựa trên triệu chứng\n"
                "📅 Đặt lịch hẹn khám bệnh\n"
                "❌ Hủy lịch hẹn\n"
                "📋 Tra cứu thông tin bác sĩ và chuyên khoa\n\n"
                "Bạn muốn làm gì?"
            )
            dispatcher.utter_message(
                text=message,
                buttons=[
                    {"title": "Đề xuất bác sĩ", "payload": "/request_doctor"},
                    {"title": "Đặt lịch hẹn", "payload": "/book_appointment"},
                    {"title": "Hủy lịch hẹn", "payload": "/cancel_appointment"}
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
            dispatcher.utter_message(text="Vui lòng cung cấp ngày bạn muốn hủy lịch hẹn (DD/MM/YYYY).")
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
            SELECT lh.mahen, lh.ngaythangnam, lh.khunggio, bs.tenBS, ck.tenCK
            FROM lichhen lh
            JOIN bacsi bs ON lh.maBS = bs.maBS
            JOIN chuyenkhoa ck ON lh.maCK = ck.maCK
            WHERE lh.maBN = %s AND DATE(lh.ngaythangnam) = %s AND lh.trangthai != 'hủy'
            ORDER BY lh.khunggio
            """
            cursor.execute(query, (MA_BN_GLOBAL, parsed_date))
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
        dispatcher.utter_message(text=f"📋 **Danh sách lịch hẹn ngày {date_input}:**")
        
        for idx, appt in enumerate(appointments, 1):
            appt_text = f"{idx}. 🩺 **Bác sĩ {appt['tenBS']}** ({appt['tenCK']})\n   - Giờ: {appt['khunggio']}\n   - Mã lịch: {appt['mahen']}"
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
        
        if not slot_value:
            dispatcher.utter_message(text="Vui lòng chọn một lịch hẹn để hủy.")
            return {"selected_appointment_id": None}
        
        # Validate appointment_id tồn tại trong DB
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT lh.mahen, lh.ngaythangnam, lh.khunggio, bs.tenBS, ck.tenCK
            FROM lichhen lh
            JOIN bacsi bs ON lh.maBS = bs.maBS
            JOIN chuyenkhoa ck ON lh.maCK = ck.maCK
            WHERE lh.mahen = %s AND lh.maBN = %s AND lh.trangthai != 'hủy'
            """
            cursor.execute(query, (slot_value, MA_BN_GLOBAL))
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
        ✅ **Đã chọn lịch hẹn:**

        - Mã lịch: {appointment['mahen']}
        - Bác sĩ: {appointment['tenBS']}
        - Chuyên khoa: {appointment['tenCK']}
        - Ngày: {appointment['ngaythangnam']}
        - Giờ: {appointment['khunggio']}
        """
        dispatcher.utter_message(text=confirm_text)

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
        
        if not selected_id:
            dispatcher.utter_message(text="Không có lịch hẹn được chọn.")
            return []

        # Query thông tin lịch hẹn để hiển thị confirm
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT lh.mahen, lh.ngaythangnam, lh.khunggio, bs.tenBS, ck.tenCK
            FROM lichhen lh
            JOIN bacsi bs ON lh.maBS = bs.maBS
            JOIN chuyenkhoa ck ON lh.maCK = ck.maCK
            WHERE lh.mahen = %s AND lh.maBN = %s AND lh.trangthai != 'hủy'
            """
            cursor.execute(query, (selected_id, MA_BN_GLOBAL))
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
            📋 **Xác nhận hủy lịch hẹn**

            - Mã lịch: {appointment['mahen']}
            - Bác sĩ: {appointment['tenBS']}
            - Chuyên khoa: {appointment['tenCK']}
            - Ngày: {appointment['ngaythangnam']}
            - Giờ: {appointment['khunggio']}

            Bạn có chắc chắn muốn hủy lịch hẹn này không?
        """
        
        dispatcher.utter_message(
            text=confirm_text,
            buttons=[
                {"title": "Xác nhận hủy", "payload": "/affirm"},
                {"title": "Không hủy", "payload": "/deny"}
            ]
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
        
        if not selected_id:
            dispatcher.utter_message(text="Không có lịch hẹn được chọn.")
            return []

        # Update DB: Set trangthai = 'hủy'
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor()
            query = "UPDATE lichhen SET trangthai = 'hủy' WHERE mahen = %s AND maBN = %s"
            cursor.execute(query, (selected_id, MA_BN_GLOBAL))
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
            SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS, bs.emailBS
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
        # Lấy tên bác sĩ từ entities hoặc slot
        entities = tracker.latest_message.get('entities', [])
        doctor_name = next((e['value'] for e in entities if e['entity'] == 'doctor_name'), None)
        
        if not doctor_name:
            doctor_name = tracker.get_slot("doctor_name")
        
        if not doctor_name:
            dispatcher.utter_message(text="Vui lòng cung cấp tên bác sĩ bạn muốn tra cứu.")
            return []
        
        print(f"[DEBUG] Showing doctor info: {doctor_name}")
        
        # Query DB để lấy thông tin bác sĩ
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS, bs.emailBS
            FROM bacsi bs
            JOIN chuyenmon cm ON bs.maBS = cm.maBS
            JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
            WHERE bs.tenBS LIKE %s
            """
            cursor.execute(query, (f"%{doctor_name}%",))
            doctor = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if doctor:
                # Trình bày thông tin theo kiểu danh thiếp sử dụng HTML
                info_html = f"""
                <div style="border-left: 4px solid #007bff; background: #eef6ff; border-radius: 8px; padding: 10px 14px; font-family: Arial, sans-serif; font-size: 15px; line-height: 1.4; color: #333;">
                    <div style="font-weight: bold; color: #007bff; margin-bottom: 6px;">👨‍⚕️ Bác sĩ {doctor['tenBS']}</div>
                    <div><strong>Mã BS:</strong> {doctor['maBS']}</div>
                    <div><strong>Chuyên khoa:</strong> {doctor['tenCK']}</div>
                    <div><strong>SĐT:</strong> {doctor['sdtBS']}</div>
                    <div><strong>Email:</strong> {doctor.get('emailBS', 'Chưa có')}</div>
                    <div><strong>Kinh nghiệm:</strong> 20 năm</div>
                </div>
                <div style="margin-top: 6px; font-size: 15px;">Tiếp tục đặt lịch...</div>
                """
                dispatcher.utter_message(text=info_html)
                
                # Nếu user chưa chọn bác sĩ này, set vào slot
                current_doctor = tracker.get_slot("doctor_name")
                if not current_doctor or current_doctor.lower() != doctor['tenBS'].lower():
                    return [SlotSet("doctor_name", doctor['tenBS'])]
                
                return []
            else:
                dispatcher.utter_message(text=f"Không tìm thấy bác sĩ '{doctor_name}'. Vui lòng kiểm tra lại tên.")
                return []
                
        except Exception as e:
            print(f"[ERROR] {e}")
            dispatcher.utter_message(text="Có lỗi khi tra cứu thông tin bác sĩ. Vui lòng thử lại.")
            return []


class ActionExplainSpecialtyInForm(Action):
    def name(self) -> Text:
        return "action_explain_specialty_in_form"

    def run(self, dispatcher, tracker, domain):
        specialty = tracker.get_slot("specialty")
        
        if not specialty:
            return []
        
        print(f"[DEBUG] Explaining specialty: {specialty}")
        
        # Query DB
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = "SELECT tenCK, maCK FROM chuyenkhoa WHERE tenCK LIKE %s"
            cursor.execute(query, (f"%{specialty}%",))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if result:
                explanation = result.get('maCK', f"Chuyên khoa {specialty}...")
                dispatcher.utter_message(
                    text=f"📋 **{result['tenCK']}**: {explanation}\n\nTiếp tục đặt lịch..."
                )
                return [SlotSet("specialty", result['tenCK'])]
            else:
                dispatcher.utter_message(text=f"Không tìm thấy '{specialty}'.")
                return [SlotSet("specialty", None)]
        except Exception as e:
            print(f"[ERROR] {e}")
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
        symptoms = tracker.latest_message.get('entities', [])
        symptom_list = [e['value'] for e in symptoms if e['entity'] == 'symptom']
        return {"symptoms": symptom_list}


class ActionRecommendDoctor(Action):
    def name(self) -> Text:
        return "action_recommend_doctor"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        symptoms = tracker.get_slot("symptoms") or []
        if not symptoms:
            dispatcher.utter_message(text="Không nhận được triệu chứng. Hãy thử lại.")
            return []

        symptom_to_specialty = {
            "đau đầu": "Thần kinh", "chóng mặt": "Thần kinh", "mất ngủ": "Thần kinh", "co giật": "Thần kinh",
            "tê bì tay chân": "Thần kinh", "rối loạn trí nhớ": "Thần kinh", "đau nửa đầu": "Thần kinh",
            "run tay": "Thần kinh", "mất thăng bằng": "Thần kinh",
            "sốt": "Nội khoa", "mệt mỏi": "Nội khoa", "ho": "Nội khoa", "khó thở": "Nội khoa",
            "đau ngực": "Nội khoa", "đau khớp": "Nội khoa", "tiêu chảy": "Nội khoa", "buồn nôn": "Nội khoa",
            "đau bụng": "Nội khoa", "chán ăn": "Nội khoa",
            "chấn thương": "Ngoại khoa", "gãy xương": "Ngoại khoa", "vết thương hở": "Ngoại khoa",
            "đau lưng": "Ngoại khoa", "đau vai gáy": "Ngoại khoa", "u bướu ngoài da": "Ngoại khoa",
            "sưng tấy": "Ngoại khoa", "đau sau phẫu thuật": "Ngoại khoa",
            "sốt ở trẻ em": "Nhi khoa", "ho ở trẻ em": "Nhi khoa", "nôn trớ": "Nhi khoa",
            "khò khè": "Nhi khoa", "biếng ăn": "Nhi khoa", "tiêu chảy ở trẻ em": "Nhi khoa",
            "phát ban": "Nhi khoa", "sổ mũi": "Nhi khoa",
            "trễ kinh": "Sản khoa", "đau bụng dưới": "Sản khoa", "ra khí hư bất thường": "Sản khoa",
            "chảy máu âm đạo": "Sản khoa", "ốm nghén": "Sản khoa", "đau lưng khi mang thai": "Sản khoa",
            "rối loạn kinh nguyệt": "Sản khoa", "nghi ngờ mang thai": "Sản khoa",
            "đau răng": "Răng Hàm Mặt", "sưng nướu": "Răng Hàm Mặt", "hôi miệng": "Răng Hàm Mặt",
            "chảy máu chân răng": "Răng Hàm Mặt", "viêm lợi": "Răng Hàm Mặt", "sâu răng": "Răng Hàm Mặt",
            "nhức răng": "Răng Hàm Mặt", "hàm lệch": "Răng Hàm Mặt",
        }

        specialties = set()
        for symptom in symptoms:
            specialty = symptom_to_specialty.get(symptom.lower(), "Tổng quát")
            specialties.add(specialty)

        suggested_specialty = ", ".join(specialties) if specialties else "Tổng quát"

        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            placeholders = ','.join(['%s'] * len(specialties))
            query = f"""
            SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS
            FROM bacsi bs
            JOIN chuyenmon cm ON bs.maBS = cm.maBS
            JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
            WHERE ck.tenCK IN ({placeholders})
            """
            cursor.execute(query, tuple(specialties))
            doctors = cursor.fetchall()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
            return []

        if not doctors:
            dispatcher.utter_message(text="Rất tiếc, không tìm thấy bác sĩ phù hợp.")
            return [SlotSet("specialty_suggested", None)]

        dispatcher.utter_message(
            text=f"""
            <div style="font-family: Arial, sans-serif; font-size: 15px; color: #333; background: #f9fbff; border-radius: 10px; padding: 10px 12px; border: 1px solid #cce0ff;">
                <div style="color: #007bff; font-weight: bold; margin-bottom: 8px;">
                    🔍 Dựa trên triệu chứng, tôi đề xuất chuyên khoa <span style="color:#0056b3;">{suggested_specialty}</span>.
                </div>
                <div style="margin-bottom: 6px;">Dưới đây là danh sách bác sĩ phù hợp:</div>
            </div>
            """, 
            html=True
        )

        for doc in doctors:
            doc_card = f"""
            <div style="font-family: Arial, sans-serif; font-size: 15px; color: #333; background: #ffffff; border-left: 3px solid #007bff; border-radius: 8px; padding: 8px 10px; margin: 6px 0;">
                <div style="font-weight: bold; color: #007bff;">🩺 Bác sĩ {doc['tenBS']}</div>
                <div><strong>Chuyên khoa:</strong> {doc['tenCK']}</div>
                <div><strong>Kinh nghiệm:</strong> 10 năm</div>
                <div><strong>Liên hệ:</strong> {doc['sdtBS']}</div>
            </div>
            """
            dispatcher.utter_message(
                text=doc_card,
                buttons=[{
                    "title": "📅 Đặt lịch", 
                    "payload": f"/book_with_doctor{{\"doctor_id\":\"{doc['maBS']}\", \"specialty\":\"{doc['tenCK']}\"}}"
                }],
                html=True
            )


        return [SlotSet("specialty_suggested", suggested_specialty),
                SlotSet("current_task", None),
                SlotSet("symptoms", None),
                SlotSet("decription", None)]


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

    def _detect_wrong_input(self, slot_name: str, slot_value: str) -> bool:
        """Check nếu input match keywords của slot khác"""
        input_lower = slot_value.lower()
        keywords = WRONG_INPUT_KEYWORDS.get(slot_name, [])
        return any(kw in input_lower for kw in keywords)

    def _handle_form_interruption(self, dispatcher, tracker):
        latest_message = tracker.latest_message
        
        if hasattr(latest_message, 'intent'):
            latest_intent = latest_message.intent.get('name')
        else:
            latest_intent = latest_message.get('intent', {}).get('name')

        # === Xử lý explain_specialty ===
        if latest_intent == "explain_specialty":
            entities = (getattr(latest_message, 'entities', []) 
                    if hasattr(latest_message, 'entities') 
                    else latest_message.get('entities', []))
            specialty_entity = next((e.get('value') for e in entities 
                                    if e.get('entity') == 'specialty'), None)
            
            # LUÔN LUÔN thực hiện giải thích
            explain_action = ActionExplainSpecialtyInForm()
            explain_action.run(dispatcher, tracker, {})
            
            # KHÔNG set specialty vào slot - chỉ hỏi thôi, chưa chọn
            # Giữ nguyên giá trị specialty hiện tại (hoặc None nếu chưa có)
            return {
                "specialty": tracker.get_slot("specialty"),  # ← Giữ nguyên giá trị cũ
                "just_explained": False,
            }
        
        # === Xử lý ask_doctor_info ===
        if latest_intent == "ask_doctor_info":
            # LUÔN LUÔN thực hiện tra cứu
            info_action = ActionShowDoctorInfoInForm()
            info_action.run(dispatcher, tracker, {})
            
            # KHÔNG set doctor_name vào slot - chỉ tra cứu thông tin
            # Giữ nguyên giá trị đã chọn trước đó (hoặc None)
            return {
                "doctor_name": tracker.get_slot("doctor_name"),  # ← Giữ nguyên giá trị cũ
                "just_asked_doctor_info": False,
            }
        
        # === Xử lý list_doctors_by_specialty ===
        if latest_intent == "list_doctors_by_specialty":
            entities = (getattr(latest_message, 'entities', []) 
                    if hasattr(latest_message, 'entities') 
                    else latest_message.get('entities', []))
            specialty_entity = next((e.get('value') for e in entities 
                                    if e.get('entity') == 'specialty'), None)
            
            # LUÔN LUÔN thực hiện list
            list_action = ActionListDoctorsInForm()
            list_action.run(dispatcher, tracker, {})
            
            # KHÔNG set specialty vào slot - chỉ xem danh sách
            return {
                "specialty": tracker.get_slot("specialty"),  # ← Giữ nguyên giá trị cũ
                "just_listed_doctors": False,
            }
        
        return {}

    def validate_date(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        if not slot_value:
            dispatcher.utter_message(text="Vui lòng cung cấp ngày hẹn.")
            return {"date": None}

        date_input = str(slot_value).strip()
        if self._detect_wrong_input('date', date_input):
            dispatcher.utter_message(text="Tôi nghĩ bạn đang mô tả bệnh, nhưng hiện tại tôi cần ngày hẹn trước. Vui lòng nhập ngày theo định dạng DD/MM/YYYY (ví dụ: 15/10/2025).")
            return {"date": None}

        try:
            parsed_date = datetime.strptime(date_input, '%d/%m/%Y')
        except ValueError:
            dispatcher.utter_message(text="Ngày bạn nhập không hợp lệ. Vui lòng nhập theo định dạng DD/MM/YYYY.")
            return {"date": None}

        today = datetime.now().date()
        if parsed_date.date() < today:
            dispatcher.utter_message(text="Ngày hẹn phải trong tương lai. Vui lòng chọn ngày khác.")
            return {"date": None}

        return {"date": date_input}

    def validate_appointment_time(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        if not slot_value:
            dispatcher.utter_message(text="Vui lòng cung cấp thời gian hẹn.")
            return {"appointment_time": None}

        time_input = str(slot_value).strip()
        if self._detect_wrong_input('appointment_time', time_input):
            dispatcher.utter_message(text="Đó có vẻ là thông tin khác (như ngày hoặc mô tả). Vui lòng nhập thời gian theo định dạng HH:MM (ví dụ: 14:30).")
            return {"appointment_time": None}

        try:
            parsed_time = datetime.strptime(time_input, '%H:%M')
            hour = parsed_time.hour
            if not (8 <= hour < 17):
                dispatcher.utter_message(text="Thời gian hẹn phải trong giờ làm việc (8:00 - 17:00). Vui lòng chọn lại.")
                return {"appointment_time": None}
        except ValueError:
            dispatcher.utter_message(text="Thời gian bạn nhập không hợp lệ. Vui lòng nhập theo định dạng HH:MM.")
            return {"appointment_time": time_input}

        return {"appointment_time": time_input}

    def validate_decription(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        if not slot_value:
            dispatcher.utter_message(text="Vui lòng cung cấp mô tả chi tiết về tình trạng của bạn.")
            return {"decription": None}

        desc_input = str(slot_value).strip()
        if self._detect_wrong_input('decription', desc_input):
            dispatcher.utter_message(text="Đó có vẻ là thông tin khác (như ngày hoặc bác sĩ). Vui lòng mô tả bệnh chi tiết.")
            return {"decription": None}

        if len(desc_input) < 5:
            dispatcher.utter_message(text="Mô tả quá ngắn. Vui lòng cung cấp thêm chi tiết.")
            return {"decription": None}

        # RE-SET tất cả required slots khác về giá trị hiện tại
        slot_values = {
            "decription": desc_input,
            "date": tracker.get_slot("date"),
            "specialty": tracker.get_slot("specialty"),
            "doctor_name": tracker.get_slot("doctor_name"),
            "appointment_time": tracker.get_slot("appointment_time")
        }

        return slot_values

    def validate_specialty(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        # ===== CHECK INTERRUPTION TRƯỚC =====
        interruption_result = self._handle_form_interruption(dispatcher, tracker)
        if interruption_result:
            # Interruption đã xử lý và return kết quả với flag reset
            return interruption_result
        
        # ===== VALIDATION BÌNH THƯỜNG =====
        if not slot_value:
            dispatcher.utter_message(text="Vui lòng chọn chuyên khoa.")
            return {"specialty": None}

        specialty_input = str(slot_value).strip().lower()
        if self._detect_wrong_input('specialty', specialty_input):
            dispatcher.utter_message(
                text="Đó có vẻ là thông tin khác. Vui lòng nhập tên chuyên khoa."
            )
            return {"specialty": None}

        # Validate với DB
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT tenCK FROM chuyenkhoa")
            specialties = [row['tenCK'].lower() for row in cursor.fetchall()]
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi DB: {e}")
            return {"specialty": None}

        if specialty_input not in specialties:
            dispatcher.utter_message(text=f"Chuyên khoa '{slot_value}' không có.")
            # for s in specialties[:5]:
            #     dispatcher.utter_message(text=f"- {s.title()}")
            # return {"specialty": None}

        return {"specialty": slot_value.title()}

       
    def validate_doctor_name(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        # ===== CHECK INTERRUPTION TRƯỚC =====
        interruption_result = self._handle_form_interruption(dispatcher, tracker)
        if interruption_result:
            # Interruption đã xử lý và return kết quả với flag reset
            return interruption_result
        
        # ===== VALIDATION BÌNH THƯỜNG =====
        if not slot_value:
            dispatcher.utter_message(text="Vui lòng chọn bác sĩ.")
            return {"doctor_name": None}

        doctor_input = str(slot_value).strip()
        if self._detect_wrong_input('doctor_name', doctor_input):
            dispatcher.utter_message(text="Đó có vẻ là thông tin khác. Vui lòng nhập tên bác sĩ hoặc chọn từ danh sách.")
            return {"doctor_name": None}

        specialty = tracker.get_slot("specialty")
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            if specialty:
                cursor.execute("""
                    SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS 
                    FROM bacsi bs JOIN chuyenmon cm ON bs.maBS = cm.maBS
                    JOIN chuyenkhoa ck ON cm.maCK = ck.maCK WHERE ck.tenCK = %s
                """, (specialty,))
            else:
                cursor.execute("""
                    SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS 
                    FROM bacsi bs JOIN chuyenmon cm ON bs.maBS = cm.maBS
                    JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
                """)
            doctors = cursor.fetchall()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi DB: {e}")
            cursor.close()
            conn.close()
            return {"doctor_name": None}

        matched = [doc for doc in doctors if doctor_input.lower() in doc["tenBS"].lower()]
        if not matched:
            # Khi không tìm thấy bác sĩ
            not_found_html = f"""
            <div style="font-family: Arial, sans-serif; background: #f8f9fa;
                        border-left: 5px solid #dc3545; border-radius: 8px;
                        padding: 12px 16px; margin-bottom: 10px;">
                <p style="color: #dc3545; font-weight: bold; margin: 0 0 6px 0;">
                    ❌ Không tìm thấy bác sĩ "<span style='color:#000;'>{doctor_input}</span> ở chuyên khoa {specialty or 'Tất cả chuyên khoa'}".
                </p>
                <p style="margin: 4px 0;">👉 Vui lòng chọn một trong các bác sĩ trong chuyên khoa {specialty} được gợi ý dưới đây:</p>
            </div>
            """
            dispatcher.utter_message(text=not_found_html, metadata={"html": True})

            # Hiển thị tối đa 3 bác sĩ gợi ý
            for doc in doctors[:3]:
                suggestion_html = f"""
                <div style="background: #ffffff; border: 1px solid #dee2e6;
                            border-radius: 6px; padding: 8px 12px; margin: 6px 0;
                            box-shadow: 0 1px 3px rgba(0,0,0,0.05); font-family: Arial, sans-serif;">
                    <p style="margin: 0;"><strong>🩺 {doc['tenBS']}</strong></p>
                    <p style="margin: 2px 0;">🏥 {doc['tenCK']}</p>
                    <p style="margin: 2px 0;">📞 {doc['sdtBS']}</p>
                </div>
                """
                dispatcher.utter_message(text=suggestion_html, metadata={"html": True})

            # dispatcher.utter_message(
            #     text="<p style='margin-top:8px;'>👉 Vui lòng chọn một trong các bác sĩ trên.</p>",
            #     metadata={"html": True}
            # )

            cursor.close()
            conn.close()
            return {"doctor_name": None}

        # Khi tìm thấy bác sĩ
        doc = matched[0]
        confirm_html = f"""
        <div style="font-family: Arial, sans-serif; background: #f8f9fa;
                    border-left: 5px solid #0d6efd; border-radius: 8px;
                    padding: 12px 16px; margin-top: 10px;">
            <p style="font-weight: bold; color: #0d6efd; margin: 0 0 6px 0;">✅ Xác nhận thông tin bác sĩ:</p>
            <p style="margin: 2px 0;"><strong>👨‍⚕️ {doc['tenBS']}</strong></p>
            <p style="margin: 2px 0;">🏥 {doc['tenCK']}</p>
            <p style="margin: 2px 0;">📞 {doc['sdtBS']}</p>
        </div>
        """
        dispatcher.utter_message(text=confirm_html, metadata={"html": True})

        # ===== FETCH DOCTOR'S SCHEDULE =====
        try:
            # Define the date range (today + 6 days)
            today = datetime.now().date()
            end_date = today + timedelta(days=6)
            cursor.execute("""
                SELECT ngaythangnam, giobatdau, gioketthuc, trangthai
                FROM thoigiankham
                WHERE maBS = %s AND ngaythangnam BETWEEN %s AND %s
                ORDER BY ngaythangnam
            """, (doc['maBS'], today, end_date))
            schedule = cursor.fetchall()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi DB khi lấy lịch làm việc: {e}")
            return {"doctor_name": doc["tenBS"]}

        # ===== GENERATE HTML SCHEDULE TABLE =====
        if not schedule:
            dispatcher.utter_message(text="Không có lịch làm việc cho bác sĩ này trong tuần tới.")
            return {"doctor_name": doc["tenBS"]}

        # Create HTML table
        html_table = """
        <style>
            .schedule-table {
                width: 100%;
                max-width: 600px;
                border-collapse: collapse;
                font-family: Arial, sans-serif;
                margin: 20px 0;
            }
            .schedule-table th, .schedule-table td {
                border: 1px solid #ddd;
                padding: 8px;
                text-align: left;
            }
            .schedule-table th {
                background-color: #f2f2f2;
                color: #333;
            }
            .schedule-table tr:nth-child(even) {
                background-color: #f9f9f9;
            }
            .schedule-table tr:hover {
                background-color: #f5f5f5;
            }
            @media screen and (max-width: 600px) {
                .schedule-table th, .schedule-table td {
                    font-size: 14px;
                    padding: 6px;
                }
            }
        </style>
        <table class="schedule-table">
            <thead>
                <tr>
                    <th>Ngày</th>
                    <th>Giờ bắt đầu</th>
                    <th>Giờ kết thúc</th>
                    <th>Trạng thái</th>
                </tr>
            </thead>
            <tbody>
        """

        # Generate table rows
        for entry in schedule:
            date_str = entry['ngaythangnam'].strftime('%Y-%m-%d')
            start_time = entry['giobatdau'].strftime('%H:%M') if entry['giobatdau'] else 'N/A'
            end_time = entry['gioketthuc'].strftime('%H:%M') if entry['gioketthuc'] else 'N/A'
            status = entry['trangthai'] if entry['trangthai'] else 'N/A'
            html_table += f"""
                <tr>
                    <td>{date_str}</td>
                    <td>{start_time}</td>
                    <td>{end_time}</td>
                    <td>{status}</td>
                </tr>
            """

        html_table += """
            </tbody>
        </table>
        """

        # Send the HTML table to the dispatcher
        dispatcher.utter_message(text=f"Lịch làm việc của bác sĩ {doc['tenBS']} trong tuần tới:")
        dispatcher.utter_message(text=html_table)

        return {"doctor_name": doc["tenBS"]}

    def validate_any_slot(self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> Dict[Text, Any]:
        # Kiểm tra nếu latest intent là deny, thì dừng form ngay
        latest_intent = tracker.latest_message.get('intent', {}).get('name')
        if latest_intent == 'deny':
            dispatcher.utter_message(text="Đã hủy yêu cầu. Nếu bạn muốn bắt đầu lại, hãy cho tôi biết!")
            return {
                "requested_slot": None,
            }
        return {}


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


# class ActionSearchPrescription(Action):
#     def name(self) -> Text:
#         return "action_search_prescription"

#     def run(
#         self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
#     ) -> List[Dict]:
#         prescription_date = tracker.get_slot("prescription_date")
#         if not prescription_date:
#             dispatcher.utter_message(
#                 text="Vui lòng nhập ngày bạn muốn tra cứu toa thuốc (định dạng DD/MM/YYYY).",
#                 buttons=[{"title": "Quay lại menu", "payload": "/greet"}]
#             )
#             return [SlotSet("prescription_date", None)]

#         # Parse ngày
#         try:
#             parsed_date = datetime.strptime(prescription_date, '%d/%m/%Y').date()
#         except ValueError:
#             dispatcher.utter_message(text="Ngày không hợp lệ. Vui lòng nhập theo định dạng DD/MM/YYYY.")
#             return [SlotSet("prescription_date", None)]

#         # Query MySQL: Lấy toa thuốc của maBN trong ngày đó
#         try:
#             conn = mysql.connector.connect(**DB_CONFIG)
#             cursor = conn.cursor(dictionary=True)
#             query = """
#             SELECT maTT, ngay_ke, noi_dung_toa
#             FROM toa_thuoc
#             WHERE maBN = %s AND DATE(ngay_ke) = %s
#             ORDER BY ngay_ke
#             """
#             cursor.execute(query, (MA_BN_GLOBAL, parsed_date))
#             prescriptions = cursor.fetchall()
#             cursor.close()
#             conn.close()
#         except Error as e:
#             dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
#             return [SlotSet("prescription_date", None)]

#         if not prescriptions:
#             dispatcher.utter_message(text=f"Không có toa thuốc nào trong ngày {prescription_date}.")
#             buttons = [{"title": "Quay lại menu", "payload": "/greet"}]
#             dispatcher.utter_message(text="Bạn có muốn tra cứu ngày khác không?", buttons=buttons)
#             return [SlotSet("prescription_date", None)]

#         # Hiển thị danh sách toa thuốc
#         dispatcher.utter_message(text=f"Toa thuốc ngày {prescription_date}:")
#         for rx in prescriptions:
#             rx_text = f"📋 Toa thuốc ID {rx['maTT']} - Ngày kê: {rx['ngay_ke']}\nNội dung: {rx['noi_dung_toa']}"
#             dispatcher.utter_message(text=rx_text)

#         buttons = [{"title": "Tra cứu ngày khác", "payload": "/search_prescription"}, {"title": "Quay lại menu", "payload": "/greet"}]
#         dispatcher.utter_message(text="Bạn có muốn tra cứu thêm không?", buttons=buttons)

#         return [SlotSet("prescription_date", None)]


class ActionSubmitBooking(Action):
    def name(self) -> Text:
        return "action_submit_booking"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        doctor_name = tracker.get_slot("doctor_name")
        specialty = tracker.get_slot("specialty")
        date_str = tracker.get_slot("date")
        appointment_time = tracker.get_slot("appointment_time")
        decription = tracker.get_slot("decription")

        if not all([doctor_name, specialty, date_str, appointment_time, decription]):
            dispatcher.utter_message(text="Thông tin chưa đầy đủ. Vui lòng hoàn tất form.")
            return []

        try:
            parsed_date = datetime.strptime(date_str, '%d/%m/%Y').date()
        except ValueError:
            dispatcher.utter_message(text="Ngày không hợp lệ.")
            return []

        # Lấy maBS từ tenBS
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = "SELECT maBS FROM bacsi WHERE tenBS = %s"
            cursor.execute(query, (doctor_name,))
            bs_result = cursor.fetchone()
            if not bs_result:
                dispatcher.utter_message(text="Không tìm thấy bác sĩ.")
                cursor.close()
                conn.close()
                return []
            maBS = bs_result['maBS']
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi DB: {e}")
            return []

        # Tạo mahen
        now = datetime.now()
        mahen = f"LH{now.strftime('%Y%m%d%H%M%S')}"

        # Insert vào DB
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor()
            query = """
            INSERT INTO lichhen (mahen, maBN, maBS, ngaythangnam, khunggio, trangthai, maCK)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(query, (mahen, MA_BN_GLOBAL, maBS, parsed_date, appointment_time, 'chờ', decription))
            conn.commit()
            cursor.close()
            conn.close()
            dispatcher.utter_message(text="Đặt lịch thành công! Cảm ơn bạn.")
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
        
        return {}

    def validate_prescription_date(
        self, 
        slot_value: Any, 
        dispatcher: CollectingDispatcher, 
        tracker: Tracker, 
        domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        """Validate ngày khám để tra cứu toa thuốc"""
        
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
            dispatcher.utter_message(text="Vui lòng cung cấp ngày khám bạn muốn tra cứu toa thuốc (DD/MM/YYYY).")
            return {"prescription_date": None}

        date_input = str(slot_value).strip()
        
        # Validate format
        try:
            parsed_date = datetime.strptime(date_input, '%d/%m/%Y').date()
        except ValueError:
            dispatcher.utter_message(
                text="Ngày không hợp lệ. Vui lòng nhập theo định dạng DD/MM/YYYY.\nVí dụ: 15/10/2025"
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
                cursor.execute(query, (MA_BN_GLOBAL,))
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
                cursor.execute(query, (MA_BN_GLOBAL, parsed_date))
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
                max-width: 800px;
                margin: 10px 0;
            }}
            .prescription-title {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 12px 16px;
                border-radius: 8px 8px 0 0;
                font-weight: bold;
                font-size: 16px;
            }}
            .prescription-table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                border-radius: 0 0 8px 8px;
                overflow: hidden;
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
                border-radius: 0 0 8px 8px;
                margin-top: -1px;
                font-size: 13px;
                color: #6c757d;
                border-top: 2px solid #dee2e6;
            }}
            @media screen and (max-width: 600px) {{
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
        elif intent == 'cancel_appointment':
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
