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
            
            # Giữ nguyên form, không deactivate
            return []
        
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
        
        if active_loop:
            # Trong form - yêu cầu làm rõ
            message = (
                "Xin lỗi, tôi không hiểu rõ ý bạn. "
                "Vui lòng trả lời câu hỏi hiện tại hoặc nói 'bỏ' để dừng lại."
            )
            dispatcher.utter_message(text=message)
            return []
        
        else:
            # Ngoài form - gợi ý chức năng
            message = (
                "Xin lỗi, tôi không hiểu yêu cầu của bạn. "
                "Tôi có thể giúp bạn:\n"
                "• Đề xuất bác sĩ dựa trên triệu chứng\n"
                "• Đặt lịch hẹn khám bệnh\n"
                "• Hủy lịch hẹn\n"
                "• Tra cứu thông tin bác sĩ và chuyên khoa\n\n"
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
            
            # Hiển thị danh sách bác sĩ
            dispatcher.utter_message(text=f"📋 **Danh sách bác sĩ chuyên khoa {doctors[0]['tenCK']}:**\n")
            
            for idx, doc in enumerate(doctors, 1):
                doc_info = f"{idx}. 🩺 **Bác sĩ {doc['tenBS']}**\n   - SĐT: {doc['sdtBS']}\n   - Email: {doc.get('emailBS', 'Chưa có')}"
                dispatcher.utter_message(text=doc_info)
            
            dispatcher.utter_message(text=f"\nTổng cộng: {len(doctors)} bác sĩ\n\nTiếp tục đặt lịch...")
            
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
                info_text = f"""
                    📋 **Thông tin Bác sĩ {doctor['tenBS']}**
                    - Mã BS: {doctor['maBS']}
                    - Chuyên khoa: {doctor['tenCK']}
                    - SĐT: {doctor['sdtBS']}
                    - Email: {doctor.get('emailBS', 'Chưa có thông tin')}
                    - Kinh nghiệm: 20 năm

                    Tiếp tục đặt lịch...
                """
                dispatcher.utter_message(text=info_text)
                
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

        dispatcher.utter_message(text=f"Dựa trên triệu chứng, tôi đề xuất chuyên khoa {suggested_specialty}. Dưới đây là danh sách bác sĩ phù hợp:")
        for doc in doctors:
            doc_card = f"🩺 **Bác sĩ {doc['tenBS']}** - Chuyên khoa: {doc['tenCK']} - Kinh nghiệm: 10 năm - Liên hệ: {doc['sdtBS']}"
            dispatcher.utter_message(
                text=doc_card,
                buttons=[{
                    "title": "Đặt lịch", 
                    "payload": f"/book_with_doctor{{\"doctor_id\":\"{doc['maBS']}\", \"specialty\":\"{doc['tenCK']}\"}}"
                }]
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
            for s in specialties[:5]:
                dispatcher.utter_message(text=f"- {s.title()}")
            return {"specialty": None}

        return {"specialty": slot_value.title()}

    # def validate_doctor_name(
    #     self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    # ) -> Dict[Text, Any]:
    #     # ===== CHECK INTERRUPTION TRƯỚC =====
    #     interruption_result = self._handle_form_interruption(dispatcher, tracker)
    #     if interruption_result:
    #         # Interruption đã xử lý và return kết quả với flag reset
    #         return interruption_result
        
    #     # ===== VALIDATION BÌNH THƯỜNG =====
    #     if not slot_value:
    #         dispatcher.utter_message(text="Vui lòng chọn bác sĩ.")
    #         return {"doctor_name": None}

    #     doctor_input = str(slot_value).strip()
    #     if self._detect_wrong_input('doctor_name', doctor_input):
    #         dispatcher.utter_message(text="Đó có vẻ là thông tin khác. Vui lòng nhập tên bác sĩ hoặc chọn từ danh sách.")
    #         return {"doctor_name": None}

    #     specialty = tracker.get_slot("specialty")
    #     try:
    #         conn = mysql.connector.connect(**DB_CONFIG)
    #         cursor = conn.cursor(dictionary=True)
    #         if specialty:
    #             cursor.execute("""
    #                 SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS 
    #                 FROM bacsi bs JOIN chuyenmon cm ON bs.maBS = cm.maBS
    #                 JOIN chuyenkhoa ck ON cm.maCK = ck.maCK WHERE ck.tenCK = %s
    #             """, (specialty,))
    #         else:
    #             cursor.execute("""
    #                 SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS 
    #                 FROM bacsi bs JOIN chuyenmon cm ON bs.maBS = cm.maBS
    #                 JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
    #             """)
    #         doctors = cursor.fetchall()
    #         cursor.close()
    #         conn.close()
    #     except Error as e:
    #         dispatcher.utter_message(text=f"Lỗi DB: {e}")
    #         return {"doctor_name": None}

    #     matched = [doc for doc in doctors if doctor_input.lower() in doc["tenBS"].lower()]
    #     if not matched:
    #         dispatcher.utter_message(text=f"Không tìm thấy bác sĩ '{doctor_input}'. Các bác sĩ có sẵn:")
    #         for doc in doctors[:3]:
    #             dispatcher.utter_message(text=f"- 🩺 {doc['tenBS']} - {doc['tenCK']} ({doc['sdtBS']})")
    #         dispatcher.utter_message(text="Vui lòng chọn một trong số chúng.")
    #         return {"doctor_name": None}

    #     doc = matched[0]
    #     dispatcher.utter_message(
    #         text=f"Xác nhận: 🩺 {doc['tenBS']} - {doc['tenCK']} - {doc['sdtBS']}"
    #     )
    #     return {"doctor_name": doc["tenBS"]}
    
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
            dispatcher.utter_message(text=f"Không tìm thấy bác sĩ '{doctor_input}'. Các bác sĩ có sẵn:")
            for doc in doctors[:3]:
                dispatcher.utter_message(text=f"- 🩺 {doc['tenBS']} - {doc['tenCK']} ({doc['sdtBS']})")
            dispatcher.utter_message(text="Vui lòng chọn một trong số chúng.")
            cursor.close()
            conn.close()
            return {"doctor_name": None}

        doc = matched[0]
        dispatcher.utter_message(
            text=f"Xác nhận: 🩺 {doc['tenBS']} - {doc['tenCK']} - {doc['sdtBS']}"
        )

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
            text=f"Xác nhận: Bác sĩ **{slots['doctor_name']}** - {slots['specialty']} - {slots['appointment_time']} ngày {slots['date']}. Mô tả: {slots['decription']}",
            buttons=[
                {"title": "Xác nhận", "payload": "/affirm"},
                {"title": "Hủy", "payload": "/deny"}
            ]
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

        dispatcher.utter_message(text=f"Tìm thấy {len(doctors)} bác sĩ phù hợp với '{doctor_name_search}':")
        for doc in doctors:
            doc_card = f"""
                - 🩺 **Bác sĩ {doc['tenBS']}**
                - Chuyên khoa: {doc['tenCK']}
                - SĐT: {doc['sdtBS']}
            """
            dispatcher.utter_message(
                text=doc_card,
                buttons=[{"title": "Xem chi tiết", "payload": f"/view_doctor_detail{{\"doctor_id\":\"{doc['maBS']}\"}}"}]
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
        detail_text = f"""
        📋 **Chi tiết Bác sĩ {doctor['tenBS']}**
        - Mã BS: {doctor['maBS']}
        - Chuyên khoa: {doctor['tenCK']}
        - SĐT: {doctor['sdtBS']}
        - Email: {doctor.get('emailBS', 'Chưa có thông tin')}
        - Kinh nghiệm: 20 năm
        - Các dịch vụ khác: Tư vấn và khám chuyên sâu về {doctor['tenCK']}.

        Bạn có muốn đặt lịch với bác sĩ này không?
        """
        buttons = [
            {"title": "Đặt lịch", "payload": "/book_appointment"},
            {"title": "Tìm bác sĩ khác", "payload": "/search_doctor_info"}
        ]
        dispatcher.utter_message(text=detail_text, buttons=buttons)

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
    
# class ActionCancelAppointment(Action):
#     def name(self) -> Text:
#         return "action_cancel_appointment"

#     def run(
#         self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
#     ) -> List[Dict]:
#         SlotSet("current_task", "cancel_appointment")  # Set context
#         appointment_date = tracker.get_slot("appointment_date")
#         if not appointment_date:
#             dispatcher.utter_message(
#                 text="Vui lòng nhập ngày bạn muốn hủy lịch hẹn (định dạng DD/MM/YYYY).",
#                 buttons=[{"title": "Quay lại menu", "payload": "/greet"}]
#             )
#             return [SlotSet("appointment_date", None)]

#         # Parse ngày (giả sử format %d/%m/%Y)
#         try:
#             parsed_date = datetime.strptime(appointment_date, '%d/%m/%Y').date()
#         except ValueError:
#             dispatcher.utter_message(text="Ngày không hợp lệ. Vui lòng nhập theo định dạng DD/MM/YYYY.")
#             return [SlotSet("appointment_date", None)]

#         # Query MySQL: Lấy danh sách lịch hẹn của maBN trong ngày đó (trang_thai != 'hủy')
#         try:
#             conn = mysql.connector.connect(**DB_CONFIG)
#             cursor = conn.cursor(dictionary=True)
#             query = """
#             SELECT lh.mahen, lh.ngaythangnam, lh.khunggio, bs.tenBS
#             FROM lichhen lh
#             JOIN bacsi bs ON lh.maBS = bs.maBS
#             WHERE lh.maBN = %s AND DATE(lh.ngaythangnam) = %s AND lh.trangthai != 'hủy'
#             ORDER BY lh.khunggio
#             """
#             cursor.execute(query, (MA_BN_GLOBAL, parsed_date))
#             appointments = cursor.fetchall()
#             cursor.close()
#             conn.close()
#         except Error as e:
#             dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
#             return [SlotSet("appointment_date", None)]

#         if not appointments:
#             dispatcher.utter_message(text=f"Không có lịch hẹn nào trong ngày {appointment_date}.")
#             buttons = [{"title": "Quay lại menu", "payload": "/greet"}]
#             dispatcher.utter_message(text="Bạn có muốn hủy ngày khác không?", buttons=buttons)
#             return [SlotSet("appointment_date", None)]

#         # Hiển thị danh sách với buttons chọn
#         dispatcher.utter_message(text=f"Danh sách lịch hẹn ngày {appointment_date}:")
#         for appt in appointments:
#             appt_text = f"🩺 Bác sĩ {appt['tenBS']} - Giờ: {appt['khunggio']}"
#             dispatcher.utter_message(
#                 text=appt_text,
#                 buttons=[
#                     {
#                         "title": f"Chọn lịch {appt['khunggio']}",
#                         "payload": f"/select_appointment{{\"appointment_id\":\"{appt['mahen']}\"}}"
#                     }
#                 ]
#             )

#         return [SlotSet("appointment_date", None)]

# class ActionConfirmCancel(Action):
#     def name(self) -> Text:
#         return "action_confirm_cancel"

#     def run(
#         self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
#     ) -> List[Dict]:
#         # Lấy mahen từ latest_message entities (từ payload chọn)
#         entities = tracker.latest_message.get('entities', [])
#         selected_id = next((e['value'] for e in entities if e['entity'] == 'appointment_id'), None)
        
#         if not selected_id:
#             dispatcher.utter_message(text="Không nhận được lịch hẹn để hủy. Hãy thử lại.")
#             return []

#         # Xác nhận hủy
#         dispatcher.utter_message(
#             text=f"Bạn có chắc muốn hủy lịch hẹn ID {selected_id}?",
#             buttons=[
#                 {"title": "Xác nhận hủy", "payload": "/affirm"},
#                 {"title": "Hủy bỏ", "payload": "/deny"}
#             ]
#         )
#         return [SlotSet("selected_appointment_id", selected_id)]

# class ActionPerformCancel(Action):
#     def name(self) -> Text:
#         return "action_perform_cancel"

#     def run(
#         self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
#     ) -> List[Dict]:
#         selected_id = tracker.get_slot("selected_appointment_id")
#         if not selected_id:
#             dispatcher.utter_message(text="Không có lịch hẹn được chọn.")
#             return []

#         # Update DB: Set trang_thai = 'hủy'
#         try:
#             conn = mysql.connector.connect(**DB_CONFIG)
#             cursor = conn.cursor()
#             query = "UPDATE lichhen SET trangthai = 'hủy' WHERE mahen = %s AND maBN = %s"
#             cursor.execute(query, (selected_id, MA_BN_GLOBAL))
#             conn.commit()
#             cursor.close()
#             conn.close()
#             if cursor.rowcount > 0:
#                 dispatcher.utter_message(text=f"Đã hủy thành công lịch hẹn ID {selected_id}.")
#             else:
#                 dispatcher.utter_message(text="Không tìm thấy lịch hẹn để hủy.")
#         except Error as e:
#             dispatcher.utter_message(text=f"Lỗi cập nhật DB: {e}")

#         buttons = [{"title": "Quay lại menu", "payload": "/greet"}]
#         dispatcher.utter_message(text="Bạn có muốn hủy lịch khác không?", buttons=buttons)
#         return [SlotSet("selected_appointment_id", None),
#                 SlotSet("current_task", None)]

class ActionSearchPrescription(Action):
    def name(self) -> Text:
        return "action_search_prescription"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        prescription_date = tracker.get_slot("prescription_date")
        if not prescription_date:
            dispatcher.utter_message(
                text="Vui lòng nhập ngày bạn muốn tra cứu toa thuốc (định dạng DD/MM/YYYY).",
                buttons=[{"title": "Quay lại menu", "payload": "/greet"}]
            )
            return [SlotSet("prescription_date", None)]

        # Parse ngày
        try:
            parsed_date = datetime.strptime(prescription_date, '%d/%m/%Y').date()
        except ValueError:
            dispatcher.utter_message(text="Ngày không hợp lệ. Vui lòng nhập theo định dạng DD/MM/YYYY.")
            return [SlotSet("prescription_date", None)]

        # Query MySQL: Lấy toa thuốc của maBN trong ngày đó
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT maTT, ngay_ke, noi_dung_toa
            FROM toa_thuoc
            WHERE maBN = %s AND DATE(ngay_ke) = %s
            ORDER BY ngay_ke
            """
            cursor.execute(query, (MA_BN_GLOBAL, parsed_date))
            prescriptions = cursor.fetchall()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
            return [SlotSet("prescription_date", None)]

        if not prescriptions:
            dispatcher.utter_message(text=f"Không có toa thuốc nào trong ngày {prescription_date}.")
            buttons = [{"title": "Quay lại menu", "payload": "/greet"}]
            dispatcher.utter_message(text="Bạn có muốn tra cứu ngày khác không?", buttons=buttons)
            return [SlotSet("prescription_date", None)]

        # Hiển thị danh sách toa thuốc
        dispatcher.utter_message(text=f"Toa thuốc ngày {prescription_date}:")
        for rx in prescriptions:
            rx_text = f"📋 Toa thuốc ID {rx['maTT']} - Ngày kê: {rx['ngay_ke']}\nNội dung: {rx['noi_dung_toa']}"
            dispatcher.utter_message(text=rx_text)

        buttons = [{"title": "Tra cứu ngày khác", "payload": "/search_prescription"}, {"title": "Quay lại menu", "payload": "/greet"}]
        dispatcher.utter_message(text="Bạn có muốn tra cứu thêm không?", buttons=buttons)

        return [SlotSet("prescription_date", None)]

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
        return []
    
class ActionHandleDeny(Action):
    """
    Custom Action để xử lý intent 'deny': Dừng tất cả forms active, reset slots liên quan,
    và đưa bot về trạng thái mặc định (ví dụ: chào hỏi hoặc menu chính).
    
    Sử dụng: 
    - Trong domain.yml: Thêm intent 'deny' với action này.
    - Trong rules.yml: Rule như:
      - rule: Deactivate form on deny
        condition:
        - active_loop: book_appointment_form  # Hoặc form khác
        steps:
        - intent: deny
        - action: action_handle_deny
        - active_loop: null
    
    Điều này sẽ tự động deactivate form khi deny trong bất kỳ form nào.
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
        elif current_task == "search_prescription":
            events += [SlotSet("prescription_date", None)]
        
        # Reset current_task chung
        events += [SlotSet("current_task", None)]
        
        # Optional: Followup với action mặc định, ví dụ quay về greet
        # events += [FollowupAction("action_greet")]  # Nếu có action greet custom
        
        return events