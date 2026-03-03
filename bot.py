import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
import json
import time
import threading
import re
import os
from datetime import datetime, timedelta

# ---------- НАСТРОЙКИ ----------
TOKEN = "vk1.a.mE0SWCVEIIWRMBUC055DLAFOzcKsXMVzwjv3wRRJPDFo4U7aQPfF6xy6WCsrkDRMTcxyFFVzyQMhnR66FLYA44vhIEOAV5sprqEZ4NcjC3UYeIEuf1KJiUxo99A8j01bxCOLeSqF7eRsVkHu8ABqF7Dh6W-SPhjskQKRn-yB8Dw34H4XEcABoMcdwzPgJltjD7HNUT5VlM8avjCUGEnl1Q"          # Токен сообщества VK
GROUP_ID = 236397367                 # ID группы (число)
DATA_FILE = "data.json"              # Файл для хранения данных
TARGET_SCREEN_NAMES = ["georgiy_gosha", "onereset"]  # Пользователи для упоминания в полночь

# ---------- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ----------
vk_session = vk_api.VkApi(token=TOKEN)
vk = vk_session.get_api()
longpoll = VkLongPoll(vk_session)

# Кэш имён пользователей
names_cache = {}

# ID целевых пользователей (заполнятся при старте)
target_user_ids = []

# Данные: { user_id: { "name": "...", "current_route": None/"73p"/"80",
#                      "routes": { "73p": {"laps":0, "pax":0}, "80": {"laps":0, "pax":0} } } }
data = {}

peer_id_for_midnight = None  # ID беседы для ночного отчёта

# ---------- ЗАГРУЗКА / СОХРАНЕНИЕ ДАННЫХ ----------
def load_data():
    global data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:  # если файл не пуст
                    data = json.loads(content)
                    # Преобразуем ключи из строк в int
                    data = {int(k): v for k, v in data.items()}
                else:
                    data = {}  # файл пуст – начинаем с пустого словаря
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Ошибка чтения {DATA_FILE}: {e}. Используем пустые данные.")
            data = {}
    else:
        data = {}

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---------- ПОЛУЧЕНИЕ ИМЕНИ ПОЛЬЗОВАТЕЛЯ ----------
def get_user_name(user_id):
    if user_id in names_cache:
        return names_cache[user_id]
    try:
        user = vk.users.get(user_ids=user_id)[0]
        name = f"{user['first_name']} {user['last_name']}"
        names_cache[user_id] = name
        return name
    except:
        return f"id{user_id}"


def ensure_user(user_id):
    if user_id not in data:
        name = get_user_name(user_id)
        data[user_id] = {
            "name": name,
            "current_route": None,
            "routes": {
                "73p": {"laps": 0, "pax": 0},
                "80": {"laps": 0, "pax": 0}
            }
        }
        save_data()


def format_activity():
    lines = []
    
    # Активные на 73р
    lines.append("Маршрут 73р:")
    active_73 = [(uid, u) for uid, u in data.items() if u["current_route"] == "73p"]
    if active_73:
        for uid, u in active_73:
            laps = u["routes"]["73p"]["laps"]
            pax = u["routes"]["73p"]["pax"]
            lines.append(f"{u['name']}, {laps} кругов, {pax} паксов.")
    else:
        lines.append("Машин на линии нет.")
    
    lines.append("")  
    
    # Активные на 80
    lines.append("Маршрут 80:")
    active_80 = [(uid, u) for uid, u in data.items() if u["current_route"] == "80"]
    if active_80:
        for uid, u in active_80:
            laps = u["routes"]["80"]["laps"]
            pax = u["routes"]["80"]["pax"]
            lines.append(f"{u['name']}, {laps} кругов, {pax} паксов.")
    else:
        lines.append("Машин на линии нет.")
    
    lines.append("")  
    

    total_73_laps = sum(u["routes"]["73p"]["laps"] for u in data.values())
    total_73_pax = sum(u["routes"]["73p"]["pax"] for u in data.values())
    total_80_laps = sum(u["routes"]["80"]["laps"] for u in data.values())
    total_80_pax = sum(u["routes"]["80"]["pax"] for u in data.values())
    
    lines.append("Статистика за сутки:")
    lines.append(f"Маршрут 73р: {total_73_laps} кругов, {total_73_pax} паксов")
    lines.append(f"Маршрут 80: {total_80_laps} кругов, {total_80_pax} паксов")
    
    return "\n".join(lines)

# ---------- СБРОС ДНЯ (В ПОЛНОЧЬ) ----------
def reset_day():
    global data, peer_id_for_midnight
    # Формируем отчёт
    report = format_activity()
    mentions = " ".join([f"[id{uid}|@???]" for uid in target_user_ids])
    message = f"{mentions}\n\nСтатистика за сутки:\n{report}"
    
    # Отправляем, если известен peer_id
    if peer_id_for_midnight is not None:
        try:
            vk.messages.send(
                peer_id=peer_id_for_midnight,
                message=message,
                random_id=0
            )
        except Exception as e:
            print(f"Не удалось отправить ночной отчёт: {e}")
    
    # Сброс данных
    for uid in data:
        data[uid]["current_route"] = None
        data[uid]["routes"]["73p"]["laps"] = 0
        data[uid]["routes"]["73p"]["pax"] = 0
        data[uid]["routes"]["80"]["laps"] = 0
        data[uid]["routes"]["80"]["pax"] = 0
    save_data()
    schedule_midnight_reset()

def schedule_midnight_reset():
    now = datetime.now()
    # Следующая полночь
    next_midnight = datetime(now.year, now.month, now.day) + timedelta(days=1)
    seconds_until = (next_midnight - now).total_seconds()
    
    timer = threading.Timer(seconds_until, reset_day)
    timer.daemon = True
    timer.start()

# ---------- ПОЛУЧЕНИЕ ID ПОЛЬЗОВАТЕЛЕЙ ПО SCREEN_NAME ----------
def get_target_ids():
    ids = []
    for screen_name in TARGET_SCREEN_NAMES:
        try:
            user = vk.utils.resolve_screen_name(screen_name=screen_name)
            if user['type'] == 'user':
                ids.append(user['object_id'])
        except:
            print(f"Не удалось найти пользователя @{screen_name}")
    return ids

# ---------- ОТПРАВКА СООБЩЕНИЯ С КЛАВИАТУРОЙ ----------
def send_message(peer_id, message, keyboard=None):
    params = {
        "peer_id": peer_id,
        "message": message,
        "random_id": 0
    }
    if keyboard:
        params["keyboard"] = keyboard.get_keyboard()
    vk.messages.send(**params)

# ---------- СОЗДАНИЕ КЛАВИАТУРЫ ----------
def create_keyboard():
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("Выйти на 73р", color=VkKeyboardColor.PRIMARY)
    keyboard.add_button("Выйти на 80", color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button("Актив", color=VkKeyboardColor.SECONDARY)
    keyboard.add_button("Сойти с маршрута", color=VkKeyboardColor.NEGATIVE)
    return keyboard

# ---------- ОБРАБОТКА СООБЩЕНИЙ ----------
def handle_message(event):
    global peer_id_for_midnight
    user_id = event.user_id
    peer_id = event.peer_id
    raw_text = event.text.strip()
    
    # Удаляем упоминание бота из текста (если оно есть)
    # Примеры: "[club234372178|@Мамонтовское АТП Бот] Актив" -> "Актив"
    #          "@club234372178 Актив" -> "Актив"
    text = re.sub(r'^\[club\d+\|[^\]]+\]\s*', '', raw_text)
    text = re.sub(r'^@club\d+\s*', '', text)
    text = text.strip()

    # Игнорируем сообщения от групп (чтобы бот не отвечал сам себе)
    if user_id < 0:
        return

    # Запоминаем peer_id для ночного отчёта
    if peer_id_for_midnight is None:
        peer_id_for_midnight = peer_id

    ensure_user(user_id)
    keyboard = create_keyboard()

    # Обработка команд (теперь text уже без упоминания)
    if text == "Выйти на 73р":
        old_route = data[user_id]["current_route"]
        data[user_id]["current_route"] = "73p"
        save_data()
        reply = "Вы вышли на маршрут 73р." if old_route is None else f"Вы переключились на маршрут 73р (ранее были на {old_route})."
        send_message(peer_id, reply, keyboard)

    elif text == "Выйти на 80":
        old_route = data[user_id]["current_route"]
        data[user_id]["current_route"] = "80"
        save_data()
        reply = "Вы вышли на маршрут 80." if old_route is None else f"Вы переключились на маршрут 80 (ранее были на {old_route})."
        send_message(peer_id, reply, keyboard)

    elif text == "Сойти с маршрута":
        if data[user_id]["current_route"] is None:
            reply = "Вы и так не на линии."
        else:
            data[user_id]["current_route"] = None
            save_data()
            reply = "Вы сошли с маршрута."
        send_message(peer_id, reply, keyboard)

    elif text == "Актив":
        activity = format_activity()
        send_message(peer_id, activity, keyboard)

    elif text.lower().startswith("круг"):
        parts = text.split()
        pax = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0

        current_route = data[user_id]["current_route"]
        if current_route is None:
            reply = "Вы не на линии. Сначала выйдите на маршрут."
            send_message(peer_id, reply, keyboard)
            return

        data[user_id]["routes"][current_route]["laps"] += 1
        data[user_id]["routes"][current_route]["pax"] += pax
        save_data()

        laps_now = data[user_id]["routes"][current_route]["laps"]
        pax_now = data[user_id]["routes"][current_route]["pax"]
        reply = f"Круг засчитан! Теперь у вас на маршруте {current_route}: {laps_now} кругов, {pax_now} паксов."
        send_message(peer_id, reply, keyboard)

    else:
        # Ничего не делаем – игнорируем нераспознанные сообщения
        pass

# ---------- ЗАПУСК БОТА ----------
def main():
    print("Бот запущен...")
    
    # Загружаем данные
    load_data()
    
    # Получаем ID пользователей для упоминания
    global target_user_ids
    target_user_ids = get_target_ids()
    print("Целевые пользователи (ID):", target_user_ids)
    
    # Планируем первый сброс в полночь
    schedule_midnight_reset()
    
    # Запускаем прослушивание событий
    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.to_me:
            handle_message(event)

if __name__ == "__main__":
    main()
