from typing import Any, Text, Dict, List
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet
from rasa_sdk.forms import FormValidationAction
import mysql.connector
from mysql.connector import Error
from datetime import datetime
import os
from dotenv import load_dotenv

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

        messages = [{"text": f"Dựa trên triệu chứng, tôi đề xuất chuyên khoa {suggested_specialty}. Dưới đây là danh sách bác sĩ phù hợp:"}]
        for doc in doctors:
            doc_card = f"🩺 **Bác sĩ {doc['tenBS']}** - Chuyên khoa: {doc['tenCK']} - Kinh nghiệm: 10 năm - Liên hệ: {doc['sdtBS']}"
            messages.append({
                "text": doc_card,
                "buttons": [{"title": "Đặt lịch", "payload": f"book_with_{doc['maBS']}"}]
            })

        for msg in messages:
            dispatcher.utter_message(**msg)

        return [SlotSet("specialty_suggested", suggested_specialty),
                SlotSet("current_task", None),
                SlotSet("symptoms", None),
                SlotSet("decription", None)]

class ValidateBookAppointmentForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_book_appointment_form"

    def _detect_wrong_input(self, slot_name: str, slot_value: str) -> bool:
        """Check nếu input match keywords của slot khác"""
        input_lower = slot_value.lower()
        keywords = WRONG_INPUT_KEYWORDS.get(slot_name, [])
        return any(kw in input_lower for kw in keywords)

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
            return {"appointment_time": None}

        return {"appointment_time": time_input}

    def validate_decription(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
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
        print(f"[DEBUG] Slots before re-set: {dict(tracker.slots)}")
        print(f"[DEBUG] Re-setting slots: {slot_values}")

        # Kiểm tra nếu tất cả required đầy đủ trước khi return
        required_slots = ["date", "specialty", "doctor_name", "appointment_time", "decription"]
        if all(slot_values.get(slot) for slot in required_slots):
            print("[DEBUG] All slots full, form will submit.")
        else:
            print("[DEBUG] Some slots still None, form will continue.")

        return slot_values  # Return dict với tất cả để re-set

    def validate_specialty(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
        if not slot_value:
            dispatcher.utter_message(text="Vui lòng chọn chuyên khoa.")
            return {"specialty": None}

        specialty_input = str(slot_value).strip().lower()
        if self._detect_wrong_input('specialty', specialty_input):
            dispatcher.utter_message(text="Đó có vẻ là thông tin khác (như mô tả bệnh hoặc ngày). Vui lòng nhập tên chuyên khoa (ví dụ: Nội khoa).")
            return {"specialty": None}

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
            buttons = [{"title": s.title(), "payload": f"/provide_specialty{{\"specialty\":\"{s}\"}}"} for s in specialties[:5]]
            dispatcher.utter_message(text=f"Chuyên khoa '{slot_value}' không có. Vui lòng chọn:", buttons=buttons)
            return {"specialty": None}

        return {"specialty": slot_value.title()}

    def validate_doctor_name(
        self, slot_value: Any, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> Dict[Text, Any]:
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
            dispatcher.utter_message(text=f"Không tìm thấy bác sĩ '{doctor_input}'. Hãy chọn:")
            for doc in doctors[:3]:
                dispatcher.utter_message(
                    text=f"🩺 {doc['tenBS']} - {doc['tenCK']} ({doc['sdtBS']})",
                    buttons=[{"title": f"Chọn {doc['tenBS']}", "payload": f"/choose_doctor_name{{\"doctor_name\":\"{doc['tenBS']}\"}}"}]
                )
            return {"doctor_name": None}

        doc = matched[0]
        dispatcher.utter_message(
            text=f"Xác nhận: 🩺 {doc['tenBS']} - {doc['tenCK']} - {doc['sdtBS']}",
            buttons=[{"title": "Xác nhận", "payload": f"/choose_doctor_name{{\"doctor_name\":\"{doc['tenBS']}\"}}"}]
        )
        return {"doctor_name": doc["tenBS"]}

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
        return [SlotSet("current_task", None),
                SlotSet("doctor_name", None),
                SlotSet("specialty", None),
                SlotSet("date", None),
                SlotSet("appointment_time", None),
                SlotSet("decription", None)]

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

        # Tạo messages cho danh sách bác sĩ (cards với buttons xem chi tiết)
        dispatcher.utter_message(text=f"Tìm thấy {len(doctors)} bác sĩ phù hợp với '{doctor_name_search}':")
        for doc in doctors:
            doc_card = f"""
            🩺 **Bác sĩ {doc['tenBS']}**
            - Chuyên khoa: {doc['tenCK']}
            - SĐT: {doc['sdtBS']}
            """
            dispatcher.utter_message(
                text=doc_card,
                buttons=[
                    {
                        "title": "Xem chi tiết",
                        "payload": f"/view_doctor_detail{{\"doctor_id\":\"{doc['maBS']}\"}}"
                    }
                ]
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
        - Email: {doctor.get('email', 'Chưa có thông tin')}
        - Kinh nghiệm: 20 năm
        - Các dịch vụ khác: Tư vấn và khám chuyên sâu về {doctor['tenCK']}.

        Bạn có muốn đặt lịch với bác sĩ này không?
        """
        buttons = [
            {"title": "Đặt lịch", "payload": f"/book_appointment"},
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
        specialty = tracker.get_slot("specialty")
        if not specialty:
            dispatcher.utter_message(text="Vui lòng nhập tên chuyên khoa bạn muốn tra cứu.")
            return [SlotSet("specialty", None)]

        # Query MySQL để lấy mô tả chuyên khoa (giả sử bảng chuyenkhoa có field 'mo_ta' chứa giải thích bệnh chữa)
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT tenCK, maCK
            FROM chuyenkhoa
            WHERE tenCK = %s
            """
            cursor.execute(query, (specialty,))
            result = cursor.fetchone()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
            return [SlotSet("specialty", None)]

        if not result:
            dispatcher.utter_message(
                text=f"Không tìm thấy thông tin về chuyên khoa '{specialty}'. Vui lòng thử tên khác.",
                buttons=[
                    {"title": "Quay lại menu", "payload": "/greet"}
                ]
            )
            return [SlotSet("specialty", None)]

        # Hiển thị giải thích
        explanation = result['maCK'] or f"Chuyên khoa {specialty} chuyên chữa các bệnh liên quan đến {specialty.lower()}."
        message_text = f"""
        📋 **Thông tin chuyên khoa {specialty}**
        {explanation}

        Bạn có muốn tra cứu chuyên khoa khác không?
        """
        buttons = [
            {"title": "Tra cứu khác", "payload": "/search_specialty"},
            {"title": "Quay lại menu", "payload": "/greet"}
        ]
        dispatcher.utter_message(text=message_text, buttons=buttons)

        return [SlotSet("specialty", None)]

class ActionSetCurrentTask(Action):
    def name(self) -> Text:
        return "action_set_current_task"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        intent = tracker.latest_message['intent'].get('name', '')
        if intent == 'request_doctor':
            return [SlotSet("current_task", "request_doctor")]
        elif intent == 'book_appointment':
            return [SlotSet("current_task", "book_appointment")]
        elif intent == 'search_doctor_info':
            return [SlotSet("current_task", "search_doctor_info")]
        return []