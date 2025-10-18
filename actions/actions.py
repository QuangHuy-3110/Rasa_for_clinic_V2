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
from rasa_sdk.events import SlotSet



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

class ActionDeactivateForm(Action):
    def name(self) -> Text:
        return "action_deactivate_my_form"

    async def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict[Text, Any]]:
        dispatcher.utter_message(text="Đã dừng form. Bạn muốn làm gì tiếp theo?")

        events = []
        events.append(SlotSet("requested_slot", None))
        

        # Nếu bạn muốn xóa các slot cụ thể của form khi dừng, hãy uncomment đoạn code dưới đây
        # form_slots_to_clear = ["pizza_size", "topping", "crust_type"] # Thay thế bằng tên các slot của bạn
        # for slot_name in form_slots_to_clear:
        #     if tracker.get_slot(slot_name) is not None:
        #         events.append(SlotSet(slot_name, None))

        return events

class ActionStopForm(Action):
    def name(self):
        return "action_stop_form"

    async def run(self, dispatcher, tracker, domain):
        # Action này chỉ đơn giản là vô hiệu hóa ActiveLoop
        return [ActiveLoop(None)]

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

    # === Bổ sung hàm trợ giúp để xử lý ngắt form ===
    # def _handle_form_interruption(self, dispatcher: CollectingDispatcher, tracker: Tracker) -> Dict[Text, Any]:
    #     """
    #     Xử lý logic khi form bị ngắt bởi một intent khác.
    #     Đặt requested_slot về None và xóa các slot của form.
    #     """
    #     latest_intent = tracker.latest_message['intent'].get('name')
        
    #     # Danh sách các intent bạn muốn dùng để ngắt form
    #     interrupt_intents = ["explain_specialty", "ask_info_doctor"] # Thêm các intent khác nếu cần

    #     if latest_intent in interrupt_intents:
    #         dispatcher.utter_message(text=f"Đã dừng form đặt lịch để trả lời yêu cầu của bạn về '{latest_intent}'.")
            
    #         # Reset tất cả các slot của form để khi bắt đầu lại sẽ trống
    #         # Thay thế bằng TẤT CẢ các slot mà form của bạn sử dụng
    #         slots_to_reset = {
    #             "requested_slot": None,
    #             # "specialty": None,
    #             # "doctor_name": None,
    #             # "date": None,
    #             # "appointment_time": None,
    #             # "decription": None,
    #             # Thêm các slot khác của form nếu có
    #         }
    #         return slots_to_reset
    #     return {} # Trả về dictionary rỗng nếu không có ngắt


    # def _handle_form_interruption(self, dispatcher, tracker) -> Dict[Text, Any]:
    #     latest_message = tracker.latest_message
        
    #     if hasattr(latest_message, 'intent'):
    #         latest_intent = latest_message.intent.get('name')
    #     else:
    #         latest_intent = latest_message.get('intent', {}).get('name')

    #     interrupt_intents = ["explain_specialty", "ask_info_doctor"]
        
    #     if latest_intent in interrupt_intents:
    #         dispatcher.utter_message(
    #             text=f"Đã dừng form đặt lịch để trả lời yêu cầu của bạn về '{latest_intent}'."
    #         )
            
    #         entities = (getattr(latest_message, 'entities', []) 
    #                 if hasattr(latest_message, 'entities') 
    #                 else latest_message.get('entities', []))
    #         specialty_entity = next((e.get('value') for e in entities 
    #                                 if e.get('entity') == 'specialty'), None)
            
    #         print(f"[DEBUG] Triggering FollowupAction: action_search_specialty")
            
    #         # CRITICAL: Return as list of events, not dict!
    #         return [
    #             SlotSet("specialty", specialty_entity or tracker.get_slot("specialty")),
    #             SlotSet("requested_slot", None),
    #             SlotSet("just_explained", True),
    #             SlotSet("prescription_date", None),
    #             SlotSet("date", tracker.get_slot("date")),
    #             SlotSet("appointment_time", tracker.get_slot("appointment_time")),
    #             SlotSet("decription", tracker.get_slot("decription")),
    #             SlotSet("doctor_name", tracker.get_slot("doctor_name")),
    #             FollowupAction("action_search_specialty")  # ← FORCE chạy action này!
    #         ]
        
    #     return {}

    def _handle_form_interruption(self, dispatcher, tracker):
        latest_message = tracker.latest_message
        
        if hasattr(latest_message, 'intent'):
            latest_intent = latest_message.intent.get('name')
        else:
            latest_intent = latest_message.get('intent', {}).get('name')

        if latest_intent == "explain_specialty":
            entities = (getattr(latest_message, 'entities', []) 
                    if hasattr(latest_message, 'entities') 
                    else latest_message.get('entities', []))
            specialty_entity = next((e.get('value') for e in entities 
                                    if e.get('entity') == 'specialty'), None)
            
            # Gọi custom action
            
            explain_action = ActionExplainSpecialtyInForm()
            explain_action.run(dispatcher, tracker, {})
            
            return {
                "specialty": specialty_entity or tracker.get_slot("specialty"),
                "prescription_date": None,
            }
        
        return {}

    def validate_date(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        # interruption_result = self._handle_form_interruption(dispatcher, tracker)
        # if interruption_result:
        #     return interruption_result # Nếu có ngắt, dừng validation và trả về kết quả ngắt

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
        # interruption_result = self._handle_form_interruption(dispatcher, tracker)
        # if interruption_result:
        #     return interruption_result

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
            return {"appointment_time": None}

        return {"appointment_time": time_input}

    def validate_decription(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        # interruption_result = self._handle_form_interruption(dispatcher, tracker)
        # if interruption_result:
        #     return interruption_result

        """Validate mô tả + RE-SET tất cả required slots khác để prevent override từ extraction"""
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

        # RE-SET tất cả required slots khác về giá trị hiện tại (từ tracker) để override extraction nhầm
        # Điều này đảm bảo không bị reset sau khi input mô tả
        slot_values = {
            "decription": desc_input,
            "date": tracker.get_slot("date"),
            "specialty": tracker.get_slot("specialty"),
            "doctor_name": tracker.get_slot("doctor_name"),
            "appointment_time": tracker.get_slot("appointment_time")
        }

        # Debug log (bỏ sau khi test)
        # print(f"[DEBUG] Slots before re-set: {dict(tracker.slots)}")
        # print(f"[DEBUG] Re-setting slots: {slot_values}")

        # Kiểm tra nếu tất cả required đầy đủ trước khi return (logic này vẫn tốt)
        required_slots = ["date", "specialty", "doctor_name", "appointment_time", "decription"]
        if all(slot_values.get(slot) for slot in required_slots):
            # print("[DEBUG] All slots full, form will submit.")
            pass
        else:
            # print("[DEBUG] Some slots still None, form will continue.")
            pass

        return slot_values  # Return dict với tất cả để re-set

    def validate_specialty(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        # Check interruption FIRST
        interruption_result = self._handle_form_interruption(dispatcher, tracker)
        if interruption_result:
            return interruption_result  # Already has all keys including requested_slot
        
        # Normal validation below
        if not slot_value:
            dispatcher.utter_message(text="Vui lòng chọn chuyên khoa.")
            return {"specialty": None}

        specialty_input = str(slot_value).strip().lower()
        if self._detect_wrong_input('specialty', specialty_input):
            dispatcher.utter_message(
                text="Đó có vẻ là thông tin khác. Vui lòng nhập tên chuyên khoa."
            )
            return {"specialty": None}

        # Validate with DB...
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

    def validate_doctor_name(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        # interruption_result = self._handle_form_interruption(dispatcher, tracker)
        # if interruption_result:
        #     return interruption_result

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
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi DB: {e}")
            return {"doctor_name": None}

        matched = [doc for doc in doctors if doctor_input.lower() in doc["tenBS"].lower()]
        if not matched:
            dispatcher.utter_message(text=f"Không tìm thấy bác sĩ '{doctor_input}'. Các bác sĩ có sẵn:")
            for doc in doctors[:3]: # Chỉ hiển thị 3 bác sĩ đầu
                dispatcher.utter_message(text=f"- 🩺 {doc['tenBS']} - {doc['tenCK']} ({doc['sdtBS']})")
            dispatcher.utter_message(text="Vui lòng chọn một trong số chúng.")
            return {"doctor_name": None}

        doc = matched[0]
        dispatcher.utter_message(
            text=f"Xác nhận: 🩺 {doc['tenBS']} - {doc['tenCK']} - {doc['sdtBS']}"
        )
        return {"doctor_name": doc["tenBS"]}

# class ValidateBookAppointmentForm(FormValidationAction):
#     def name(self) -> Text:
#         return "validate_book_appointment_form"

#     def _detect_wrong_input(self, slot_name: str, slot_value: str) -> bool:
#         """Check nếu input match keywords của slot khác"""
#         input_lower = slot_value.lower()
#         keywords = WRONG_INPUT_KEYWORDS.get(slot_name, [])
#         return any(kw in input_lower for kw in keywords)

#     # =========================================================
#     # HÀM _handle_form_interruption ĐÃ BỊ LOẠI BỎ KHỎI ĐÂY
#     # Việc ngắt form sẽ được xử lý hoàn toàn bởi RulePolicy.
#     # =========================================================

#     async def validate_date(
#         self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
#     ) -> Dict[Text, Any]:
#         """Validate date value."""
#         # Không còn gọi _handle_form_interruption ở đây

#         if not slot_value:
#             # Điều này sẽ không xảy ra nếu slot là required và form đang hỏi nó
#             # Nhưng tốt cho tính an toàn nếu có sự cố
#             dispatcher.utter_message(text="Vui lòng cung cấp ngày hẹn.")
#             return {"date": None}

#         date_input = str(slot_value).strip()
#         if self._detect_wrong_input('date', date_input):
#             dispatcher.utter_message(text="Tôi nghĩ bạn đang mô tả bệnh, nhưng hiện tại tôi cần ngày hẹn trước. Vui lòng nhập ngày theo định dạng DD/MM/YYYY (ví dụ: 15/10/2025).")
#             return {"date": None}

#         try:
#             parsed_date = datetime.strptime(date_input, '%d/%m/%Y')
#         except ValueError:
#             dispatcher.utter_message(text="Ngày bạn nhập không hợp lệ. Vui lòng nhập theo định dạng DD/MM/YYYY.")
#             return {"date": None}

#         today = datetime.now().date()
#         if parsed_date.date() < today:
#             dispatcher.utter_message(text="Ngày hẹn phải trong tương lai. Vui lòng chọn ngày khác.")
#             return {"date": None}

#         return {"date": date_input}

#     async def validate_appointment_time(
#         self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
#     ) -> Dict[Text, Any]:
#         """Validate appointment_time value."""
#         # Không còn gọi _handle_form_interruption ở đây

#         if not slot_value:
#             dispatcher.utter_message(text="Vui lòng cung cấp thời gian hẹn.")
#             return {"appointment_time": None}

#         time_input = str(slot_value).strip()
#         if self._detect_wrong_input('appointment_time', time_input):
#             dispatcher.utter_message(text="Đó có vẻ là thông tin khác (như ngày hoặc mô tả). Vui lòng nhập thời gian theo định dạng HH:MM (ví dụ: 14:30).")
#             return {"appointment_time": None}

#         try:
#             parsed_time = datetime.strptime(time_input, '%H:%M')
#             hour = parsed_time.hour
#             # Giả sử giờ làm việc từ 8:00 đến 16:59
#             if not (8 <= hour < 17):
#                 dispatcher.utter_message(text="Thời gian hẹn phải trong giờ làm việc (8:00 - 17:00). Vui lòng chọn lại.")
#                 return {"appointment_time": None}
#         except ValueError:
#             dispatcher.utter_message(text="Thời gian bạn nhập không hợp lệ. Vui lòng nhập theo định dạng HH:MM.")
#             return {"appointment_time": None}

#         return {"appointment_time": time_input}

#     async def validate_decription(
#         self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
#     ) -> Dict[Text, Any]:
#         """Validate decription (mô tả bệnh) value."""
#         # Không còn gọi _handle_form_interruption ở đây

#         if not slot_value:
#             dispatcher.utter_message(text="Vui lòng cung cấp mô tả chi tiết về tình trạng của bạn.")
#             return {"decription": None}

#         desc_input = str(slot_value).strip()
#         if self._detect_wrong_input('decription', desc_input):
#             dispatcher.utter_message(text="Đó có vẻ là thông tin khác (như ngày hoặc bác sĩ). Vui lòng mô tả bệnh chi tiết.")
#             return {"decription": None}

#         if len(desc_input) < 5:
#             dispatcher.utter_message(text="Mô tả quá ngắn. Vui lòng cung cấp thêm chi tiết.")
#             return {"decription": None}

#         # Phần này vẫn tốt để ngăn chặn việc ghi đè các slot khác nếu NLU trích xuất nhầm
#         slot_values = {
#             "decription": desc_input,
#             "date": tracker.get_slot("date"),
#             "specialty": tracker.get_slot("specialty"),
#             "doctor_name": tracker.get_slot("doctor_name"),
#             "appointment_time": tracker.get_slot("appointment_time")
#         }
#         return slot_values

#     async def validate_specialty(
#         self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
#     ) -> Dict[Text, Any]:
#         """Validate specialty value."""
#         # Không còn gọi _handle_form_interruption ở đây

#         if not slot_value:
#             dispatcher.utter_message(text="Vui lòng chọn chuyên khoa.")
#             return {"specialty": None}

#         specialty_input = str(slot_value).strip().lower()
#         if self._detect_wrong_input('specialty', specialty_input):
#             dispatcher.utter_message(text="Đó có vẻ là thông tin khác (như mô tả bệnh hoặc ngày). Vui lòng nhập tên chuyên khoa (ví dụ: Nội khoa).")
#             return {"specialty": None}

#         try:
#             conn = mysql.connector.connect(**DB_CONFIG)
#             cursor = conn.cursor(dictionary=True)
#             cursor.execute("SELECT tenCK FROM chuyenkhoa")
#             specialties = [row['tenCK'].lower() for row in cursor.fetchall()]
#             cursor.close()
#             conn.close()
#         except Error as e:
#             # Bạn có thể muốn có một cách xử lý lỗi tốt hơn ở đây,
#             # ví dụ: một fallback message hoặc cố gắng kết nối lại.
#             dispatcher.utter_message(text=f"Lỗi kết nối DB khi lấy danh sách chuyên khoa: {e}. Vui lòng thử lại sau.")
#             return {"specialty": None}

#         if specialty_input not in specialties:
#             dispatcher.utter_message(text=f"Chuyên khoa '{slot_value}' không có. Các chuyên khoa có sẵn:")
#             for s in specialties[:5]: # Chỉ hiển thị 5 chuyên khoa đầu
#                 dispatcher.utter_message(text=f"- {s.title()}")
#             dispatcher.utter_message(text="Vui lòng chọn một trong số chúng.")
#             return {"specialty": None}

#         return {"specialty": slot_value.title()}

#     async def validate_doctor_name(
#         self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
#     ) -> Dict[Text, Any]:
#         """Validate doctor_name value."""
#         # Không còn gọi _handle_form_interruption ở đây

#         if not slot_value:
#             dispatcher.utter_message(text="Vui lòng chọn bác sĩ.")
#             return {"doctor_name": None}

#         doctor_input = str(slot_value).strip()
#         if self._detect_wrong_input('doctor_name', doctor_input):
#             dispatcher.utter_message(text="Đó có vẻ là thông tin khác. Vui lòng nhập tên bác sĩ hoặc chọn từ danh sách.")
#             return {"doctor_name": None}

#         specialty = tracker.get_slot("specialty")
#         try:
#             conn = mysql.connector.connect(**DB_CONFIG)
#             cursor = conn.cursor(dictionary=True)
#             if specialty:
#                 cursor.execute("""
#                     SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS 
#                     FROM bacsi bs JOIN chuyenmon cm ON bs.maBS = cm.maBS
#                     JOIN chuyenkhoa ck ON cm.maCK = ck.maCK WHERE ck.tenCK = %s
#                 """, (specialty,))
#             else:
#                 # Nếu specialty là None, tìm kiếm tất cả bác sĩ
#                 cursor.execute("""
#                     SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS 
#                     FROM bacsi bs JOIN chuyenmon cm ON bs.maBS = cm.maBS
#                     JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
#                 """)
#             doctors = cursor.fetchall()
#             cursor.close()
#             conn.close()
#         except Error as e:
#             dispatcher.utter_message(text=f"Lỗi kết nối DB khi tìm bác sĩ: {e}. Vui lòng thử lại sau.")
#             return {"doctor_name": None}

#         matched = [doc for doc in doctors if doctor_input.lower() in doc["tenBS"].lower()]
#         if not matched:
#             dispatcher.utter_message(text=f"Không tìm thấy bác sĩ '{doctor_input}'. Các bác sĩ có sẵn:")
#             for doc in doctors[:3]: # Chỉ hiển thị 3 bác sĩ đầu
#                 dispatcher.utter_message(text=f"- 🩺 {doc['tenBS']} - {doc['tenCK']} ({doc['sdtBS']})")
#             dispatcher.utter_message(text="Vui lòng chọn một trong số chúng.")
#             return {"doctor_name": None}

#         doc = matched[0]
#         dispatcher.utter_message(
#             text=f"Xác nhận: 🩺 {doc['tenBS']} - {doc['tenCK']} - {doc['sdtBS']}"
#         )
#         return {"doctor_name": doc["tenBS"]}

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
    
class ActionCancelAppointment(Action):
    def name(self) -> Text:
        return "action_cancel_appointment"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        SlotSet("current_task", "cancel_appointment")  # Set context
        appointment_date = tracker.get_slot("appointment_date")
        if not appointment_date:
            dispatcher.utter_message(
                text="Vui lòng nhập ngày bạn muốn hủy lịch hẹn (định dạng DD/MM/YYYY).",
                buttons=[{"title": "Quay lại menu", "payload": "/greet"}]
            )
            return [SlotSet("appointment_date", None)]

        # Parse ngày (giả sử format %d/%m/%Y)
        try:
            parsed_date = datetime.strptime(appointment_date, '%d/%m/%Y').date()
        except ValueError:
            dispatcher.utter_message(text="Ngày không hợp lệ. Vui lòng nhập theo định dạng DD/MM/YYYY.")
            return [SlotSet("appointment_date", None)]

        # Query MySQL: Lấy danh sách lịch hẹn của maBN trong ngày đó (trang_thai != 'hủy')
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT lh.maLH, lh.ngaythangnam, lh.khunggio, bs.tenBS
            FROM lichhen lh
            JOIN bacsi bs ON lh.maBS = bs.maBS
            WHERE lh.maBN = %s AND DATE(lh.ngaythangnam) = %s AND lh.trangthai != 'hủy'
            ORDER BY lh.khunggio
            """
            cursor.execute(query, (MA_BN_GLOBAL, parsed_date))
            appointments = cursor.fetchall()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
            return [SlotSet("appointment_date", None)]

        if not appointments:
            dispatcher.utter_message(text=f"Không có lịch hẹn nào trong ngày {appointment_date}.")
            buttons = [{"title": "Quay lại menu", "payload": "/greet"}]
            dispatcher.utter_message(text="Bạn có muốn hủy ngày khác không?", buttons=buttons)
            return [SlotSet("appointment_date", None)]

        # Hiển thị danh sách với buttons chọn
        dispatcher.utter_message(text=f"Danh sách lịch hẹn ngày {appointment_date}:")
        for appt in appointments:
            appt_text = f"🩺 Bác sĩ {appt['tenBS']} - Giờ: {appt['khunggio']}"
            dispatcher.utter_message(
                text=appt_text,
                buttons=[
                    {
                        "title": f"Chọn lịch {appt['khunggio']}",
                        "payload": f"/select_appointment{{\"appointment_id\":\"{appt['maLH']}\"}}"
                    }
                ]
            )

        return [SlotSet("appointment_date", None)]

class ActionConfirmCancel(Action):
    def name(self) -> Text:
        return "action_confirm_cancel"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        # Lấy maLH từ latest_message entities (từ payload chọn)
        entities = tracker.latest_message.get('entities', [])
        selected_id = next((e['value'] for e in entities if e['entity'] == 'appointment_id'), None)
        
        if not selected_id:
            dispatcher.utter_message(text="Không nhận được lịch hẹn để hủy. Hãy thử lại.")
            return []

        # Xác nhận hủy
        dispatcher.utter_message(
            text=f"Bạn có chắc muốn hủy lịch hẹn ID {selected_id}?",
            buttons=[
                {"title": "Xác nhận hủy", "payload": "/affirm"},
                {"title": "Hủy bỏ", "payload": "/deny"}
            ]
        )
        return [SlotSet("selected_appointment_id", selected_id)]

class ActionPerformCancel(Action):
    def name(self) -> Text:
        return "action_perform_cancel"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        selected_id = tracker.get_slot("selected_appointment_id")
        if not selected_id:
            dispatcher.utter_message(text="Không có lịch hẹn được chọn.")
            return []

        # Update DB: Set trang_thai = 'hủy'
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor()
            query = "UPDATE lichhen SET trangthai = 'hủy' WHERE maLH = %s AND maBN = %s"
            cursor.execute(query, (selected_id, MA_BN_GLOBAL))
            conn.commit()
            cursor.close()
            conn.close()
            if cursor.rowcount > 0:
                dispatcher.utter_message(text=f"Đã hủy thành công lịch hẹn ID {selected_id}.")
            else:
                dispatcher.utter_message(text="Không tìm thấy lịch hẹn để hủy.")
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi cập nhật DB: {e}")

        buttons = [{"title": "Quay lại menu", "payload": "/greet"}]
        dispatcher.utter_message(text="Bạn có muốn hủy lịch khác không?", buttons=buttons)
        return [SlotSet("selected_appointment_id", None),
                SlotSet("current_task", None)]

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

        # Tạo maLH
        now = datetime.now()
        maLH = f"LH{now.strftime('%Y%m%d%H%M%S')}"

        # Insert vào DB
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor()
            query = """
            INSERT INTO lichhen (maLH, maBN, maBS, ngaythangnam, khunggio, trangthai, maCK)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(query, (maLH, MA_BN_GLOBAL, maBS, parsed_date, appointment_time, 'chờ', decription))
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