from typing import Any, Text, Dict, List
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet
from rasa_sdk.forms import FormValidationAction
import mysql.connector
from mysql.connector import Error

import os
from dotenv import load_dotenv

# Load file .env
load_dotenv()

# Kết nối DB từ .env (giả sử keys: DB_HOST, DB_USER, DB_PASSWORD, DB_NAME)
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME')
}

# Kiểm tra nếu thiếu config (tùy chọn, để tránh lỗi runtime)
if None in DB_CONFIG.values():
    raise ValueError("Thiếu thông tin kết nối DB trong file .env. Hãy kiểm tra các biến: DB_HOST, DB_USER, DB_PASSWORD, DB_NAME.")

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
        # Trích xuất entities từ message
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

        # Mapping triệu chứng đơn giản đến chuyên khoa (mở rộng theo nhu cầu)
        symptom_to_specialty = {
            # 🧠 Thần kinh
            "đau đầu": "Thần kinh",
            "chóng mặt": "Thần kinh",
            "mất ngủ": "Thần kinh",
            "co giật": "Thần kinh",
            "tê bì tay chân": "Thần kinh",
            "rối loạn trí nhớ": "Thần kinh",
            "đau nửa đầu": "Thần kinh",
            "run tay": "Thần kinh",
            "mất thăng bằng": "Thần kinh",

            # 🫀 Nội khoa
            "sốt": "Nội khoa",
            "mệt mỏi": "Nội khoa",
            "ho": "Nội khoa",
            "khó thở": "Nội khoa",
            "đau ngực": "Nội khoa",
            "đau khớp": "Nội khoa",
            "tiêu chảy": "Nội khoa",
            "buồn nôn": "Nội khoa",
            "đau bụng": "Nội khoa",
            "chán ăn": "Nội khoa",

            # 🔪 Ngoại khoa
            "chấn thương": "Ngoại khoa",
            "gãy xương": "Ngoại khoa",
            "vết thương hở": "Ngoại khoa",
            "đau lưng": "Ngoại khoa",
            "đau vai gáy": "Ngoại khoa",
            "u bướu ngoài da": "Ngoại khoa",
            "sưng tấy": "Ngoại khoa",
            "đau sau phẫu thuật": "Ngoại khoa",

            # 🧒 Nhi khoa
            "sốt ở trẻ em": "Nhi khoa",
            "ho ở trẻ em": "Nhi khoa",
            "nôn trớ": "Nhi khoa",
            "khò khè": "Nhi khoa",
            "biếng ăn": "Nhi khoa",
            "tiêu chảy ở trẻ em": "Nhi khoa",
            "phát ban": "Nhi khoa",
            "sổ mũi": "Nhi khoa",

            # 🤰 Sản khoa
            "trễ kinh": "Sản khoa",
            "đau bụng dưới": "Sản khoa",
            "ra khí hư bất thường": "Sản khoa",
            "chảy máu âm đạo": "Sản khoa",
            "ốm nghén": "Sản khoa",
            "đau lưng khi mang thai": "Sản khoa",
            "rối loạn kinh nguyệt": "Sản khoa",
            "nghi ngờ mang thai": "Sản khoa",

            # 🦷 Răng Hàm Mặt
            "đau răng": "Răng Hàm Mặt",
            "sưng nướu": "Răng Hàm Mặt",
            "hôi miệng": "Răng Hàm Mặt",
            "chảy máu chân răng": "Răng Hàm Mặt",
            "viêm lợi": "Răng Hàm Mặt",
            "sâu răng": "Răng Hàm Mặt",
            "nhức răng": "Răng Hàm Mặt",
            "hàm lệch": "Răng Hàm Mặt",
        }

        specialties = set()
        for symptom in symptoms:
            specialty = symptom_to_specialty.get(symptom.lower(), "Tổng quát")
            specialties.add(specialty)

        suggested_specialty = ", ".join(specialties) if specialties else "Tổng quát"

        # Query MySQL
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)
            query = """
            SELECT bs.maBS, bs.tenBS, ck.tenCK, bs.sdtBS
            FROM bacsi bs
            JOIN chuyenmon cm ON bs.maBS = cm.maBS
            JOIN chuyenkhoa ck ON cm.maCK = ck.maCK
            WHERE ck.tenCK IN ({})
            """.format(','.join(['%s'] * len(specialties)))

            cursor.execute(query, tuple(specialties))
            doctors = cursor.fetchall()
            cursor.close()
            conn.close()
        except Error as e:
            dispatcher.utter_message(text=f"Lỗi kết nối DB: {e}")
            return []

        if not doctors:
            dispatcher.utter_message(text="Rất tiếc, không tìm thấy bác sĩ phù hợp.")
            # return [SlotSet("symptoms", None)]
            return [SlotSet("specialty_suggested", None)]

        # Tạo messages cho danh thiếp (text + buttons cho mỗi bác sĩ)
        messages = [{"text": f"Dựa trên triệu chứng, tôi đề xuất chuyên khoa {suggested_specialty}. Dưới đây là danh sách bác sĩ phù hợp:"}]
        for doc in doctors:
            doc_card = f"""
            🩺 **Bác sĩ {doc['tenBS']}**
            - Chuyên khoa: {doc['tenCK']}
            - Kinh nghiệm: 10 năm
            - Liên hệ: {doc['sdtBS']}

            """
            messages.append({
                "text": doc_card,
                "buttons": [
                    {
                        "title": "Đặt lịch",
                        "payload": f"book_with_{doc['maBS']}"
                    }
                ]
            })

        for msg in messages:
            dispatcher.utter_message(**msg)

        return [SlotSet("specialty_suggested", suggested_specialty),
                SlotSet("current_task", None),
                SlotSet("symptoms", None)
                ]
    
class ActionSetCurrentTask(Action):
    def name(self) -> Text:
        return "action_set_current_task"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        intent = tracker.latest_message['intent'].get('name', '')
        if intent == 'request_doctor':
            return [SlotSet("current_task", "request_doctor")]
        return []