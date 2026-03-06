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
TOKEN = "vk1.a.mE0SWCVEIIWRMBUC055DLAFOzcKsXMVzwjv3wRRJPDFo4U7aQPfF6xy6WCsrkDRMTcxyFFVzyQMhnR66FLYA44vhIEOAV5sprqEZ4NcjC3UYeIEuf1KJiUxo99A8j01bxCOLeSqF7eRsVkHu8ABqF7Dh6W-SPhjskQKRn-yB8Dw34H4XEcABoMcdwzPgJltjD7HNUT5VlM8avjCUGEnl1Q"
GROUP_ID = 236397367
DATA_FILE = "data.json"
TARGET_SCREEN_NAMES = ["georgiy_gosha", "onereset"]  # администраторы

# Плановые показатели по маршрутам (неделя)
PLAN_TARGETS = {"73p": 25, "80": 15}
WEEK_SECONDS = 7 * 24 * 60 * 60

# ---------- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ----------
vk_session = vk_api.VkApi(token=TOKEN)
vk = vk_session.get_api()
longpoll = VkLongPoll(vk_session)

names_cache = {}
target_user_ids = []  # ID администраторов

# Данные:
# { user_id: { "name": "...", "current_route": None/"73p"/"75"/"80",
#              "routes": { "73p": {"laps":0, "pax":0}, "75": {...}, "80": {...} },
#              "diesel": 0,         # накоплено за месяц
#              "repair": 0 },        # накоплено за месяц
#   "week_stats": { "73p": {"pax":0, "completed":false}, ... },
#   "last_week_reset": timestamp }
data = {}

peer_id_for_midnight = None

# ---------- ЗАГРУЗКА / СОХРАНЕНИЕ ДАННЫХ ----------
def load_data():
    global data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                    data = {int(k) if k.isdigit() else k: v for k, v in data.items()}
                else:
                    data = {}
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Ошибка чтения {DATA_FILE}: {e}. Используем пустые данные.")
            data = {}
    else:
        data = {}

    # Инициализация недельной статистики, если отсутствует
    if "week_stats" not in data:
        data["week_stats"] = {
            "73p": {"pax": 0, "completed": False},
            "80": {"pax": 0, "completed": False}
        }
    if "last_week_reset" not in data:
        data["last_week_reset"] = datetime.now().timestamp()

    # Добавление полей diesel и repair для существующих пользователей
    for uid, user_data in data.items():
        if isinstance(uid, int):
            if "diesel" not in user_data:
                user_data["diesel"] = 0
            if "repair" not in user_data:
                user_data["repair"] = 0

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---------- ПРОВЕРКА И СБРОС НЕДЕЛЬНОЙ СТАТИСТИКИ ----------
def perform_week_reset():
    """Обнуление недельной статистики по пассажирам (план)"""
    for route in data["week_stats"]:
        data["week_stats"][route]["pax"] = 0
        data["week_stats"][route]["completed"] = False
    # Счётчики diesel и repair НЕ сбрасываем – они месячные
    save_data()
    if peer_id_for_midnight:
        try:
            vk.messages.send(
                peer_id=peer_id_for_midnight,
                message="🔄 Недельный план сброшен. Новая неделя началась!",
                random_id=0
            )
        except:
            pass

def check_week_reset():
    """Проверка, не прошло ли 7 дней с последнего сброса плана"""
    now = datetime.now().timestamp()
    last_reset = data.get("last_week_reset", now)
    if now - last_reset >= WEEK_SECONDS:
        while now - last_reset >= WEEK_SECONDS:
            last_reset += WEEK_SECONDS
            perform_week_reset()
        data["last_week_reset"] = last_reset
        save_data()

# ---------- СБРОС МЕСЯЧНЫХ СЧЁТЧИКОВ (ДЛЯ АДМИНИСТРАТОРА) ----------
def reset_monthly_stats():
    """Обнуляет diesel и repair у всех пользователей"""
    for uid, user_data in data.items():
        if isinstance(uid, int):
            user_data["diesel"] = 0
            user_data["repair"] = 0
    save_data()
    if peer_id_for_midnight:
        try:
            vk.messages.send(
                peer_id=peer_id_for_midnight,
                message="📆 Месячные счётчики солярки и ремонта обнулены.",
                random_id=0
            )
        except:
            pass

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
            },
            "diesel": 0,
            "repair": 0
        }
        save_data()

# ---------- ФОРМАТИРОВАНИЕ АКТИВА ----------
def format_activity():
    check_week_reset()

    lines = []
    
    # Активные на 73р
    lines.append("Маршрут 73р:")
    active_73 = [(uid, u) for uid, u in data.items() if isinstance(uid, int) and u.get("current_route") == "73p"]
    if active_73:
        for uid, u in active_73:
            laps = u["routes"]["73p"]["laps"]
            pax = u["routes"]["73p"]["pax"]
            diesel = u["diesel"]
            repair = u["repair"]
            lines.append(f"{u['name']}, {laps} кругов, {pax} паксов, солярка: {diesel}, ремонт: {repair}")
    else:
        lines.append("Машин на линии нет.")
    lines.append("")
    
    

    
    # Активные на 80
    lines.append("Маршрут 80:")
    active_80 = [(uid, u) for uid, u in data.items() if isinstance(uid, int) and u.get("current_route") == "80"]
    if active_80:
        for uid, u in active_80:
            laps = u["routes"]["80"]["laps"]
            pax = u["routes"]["80"]["pax"]
            diesel = u["diesel"]
            repair = u["repair"]
            lines.append(f"{u['name']}, {laps} кругов, {pax} паксов, солярка: {diesel}, ремонт: {repair}")
    else:
        lines.append("Машин на линии нет.")
    lines.append("")
    
    # Статистика за сутки (круги и пассажиры)
    total_73_laps = sum(u["routes"]["73p"]["laps"] for u in data.values() if isinstance(u, dict) and "routes" in u)
    total_73_pax = sum(u["routes"]["73p"]["pax"] for u in data.values() if isinstance(u, dict) and "routes" in u)
    total_80_laps = sum(u["routes"]["80"]["laps"] for u in data.values() if isinstance(u, dict) and "routes" in u)
    total_80_pax = sum(u["routes"]["80"]["pax"] for u in data.values() if isinstance(u, dict) and "routes" in u)
    
    lines.append("Статистика за сутки:")
    lines.append(f"Маршрут 73р: {total_73_laps} кругов, {total_73_pax} паксов")
    lines.append(f"Маршрут 80: {total_80_laps} кругов, {total_80_pax} паксов")
    lines.append("")
    
    # Блок плана (недельный)
    lines.append("План (недельный):")
    for route, display_name in [("73p", "73р"), ("80", "80")]:
        stats = data["week_stats"][route]
        target = PLAN_TARGETS[route]
        current = stats["pax"]
        emoji = "🟩" if current >= target else "🟥"
        lines.append(f"{display_name} маршрут: {emoji}")
        lines.append(f"Пассажиров: {current}/{target}")
        lines.append("")
    
    # Общие суммы солярки и ремонта за месяц
    total_diesel_month = sum(u.get("diesel", 0) for u in data.values() if isinstance(u, dict))
    total_repair_month = sum(u.get("repair", 0) for u in data.values() if isinstance(u, dict))
    lines.append(f"Солярка (всего за месяц): {total_diesel_month}")
    lines.append(f"Ремонт (всего за месяц): {total_repair_month}")
    
    return "\n".join(lines).rstrip()

def format_plan():
    """Формирует сообщение только с планом (недельным)"""
    check_week_reset()
    lines = ["План:"]
    for route, display_name in [("73p", "73р"), ("80", "80")]:
        stats = data["week_stats"][route]
        target = PLAN_TARGETS[route]
        current = stats["pax"]
        emoji = "🟩" if current >= target else "🟥"
        lines.append(f"{display_name} маршрут: {emoji}")
        lines.append(f"Пассажиров: {current}/{target}")
        lines.append("")
    return "\n".join(lines).rstrip()

# ---------- СБРОС ДНЯ (В ПОЛНОЧЬ) ----------
def reset_day():
    global data, peer_id_for_midnight
    # Формируем отчёт
    report = format_activity()
    mentions = " ".join([f"[id{uid}|@???]" for uid in target_user_ids])
    message = f"{mentions}\n\nСтатистика за сутки:\n{report}"
    
    if peer_id_for_midnight is not None:
        try:
            vk.messages.send(
                peer_id=peer_id_for_midnight,
                message=message,
                random_id=0
            )
        except Exception as e:
            print(f"Не удалось отправить ночной отчёт: {e}")
    
    # Сброс дневных данных (круги, пассажиры, текущий маршрут)
    for uid in list(data.keys()):
        if isinstance(uid, int):
            data[uid]["current_route"] = None
            for route in data[uid]["routes"]:
                data[uid]["routes"][route]["laps"] = 0
                data[uid]["routes"][route]["pax"] = 0
            # diesel и repair НЕ сбрасываем
    save_data()
    schedule_midnight_reset()

def schedule_midnight_reset():
    now = datetime.now()
    next_midnight = datetime(now.year, now.month, now.day) + timedelta(days=1)
    seconds_until = (next_midnight - now).total_seconds()
    timer = threading.Timer(seconds_until, reset_day)
    timer.daemon = True
    timer.start()

# ---------- ПОЛУЧЕНИЕ ID АДМИНИСТРАТОРОВ ----------
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

def is_admin(user_id):
    return user_id in target_user_ids

# ---------- ОТПРАВКА СООБЩЕНИЯ ----------
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
    keyboard.add_button("На 73р", color=VkKeyboardColor.PRIMARY)
    keyboard.add_button("На 80", color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button("Актив", color=VkKeyboardColor.SECONDARY)
    keyboard.add_button("План", color=VkKeyboardColor.POSITIVE)
    keyboard.add_button("Сойти с маршрута", color=VkKeyboardColor.NEGATIVE)
    return keyboard

# ---------- ОБРАБОТКА СООБЩЕНИЙ ----------
def handle_message(event):
    global peer_id_for_midnight
    user_id = event.user_id
    peer_id = event.peer_id
    raw_text = event.text.strip()
    
    # ----- НОВАЯ ПРОВЕРКА -----
    # Если сообщение пришло в личные сообщения — игнорируем и уведомляем
    if peer_id == user_id:
        vk.messages.send(
            peer_id=peer_id,
            random_id=0
        )
        return
    # --------------------------
    
    text = re.sub(r'^\[club\d+\|[^\]]+\]\s*', '', raw_text)
    text = re.sub(r'^@club\d+\s*', '', text)
    text = text.strip()

    if user_id < 0:
        return

    if peer_id_for_midnight is None:
        peer_id_for_midnight = peer_id

    ensure_user(user_id)
    keyboard = create_keyboard()

    # Проверка недельного сброса для команд, связанных с планом
    if text.lower() in ["круг", "план", "актив", "солярка", "ремонт"] or text.lower().startswith(("круг ", "солярка ", "ремонт ")):
        check_week_reset()

    # --- Выход на маршруты ---
    if text == "На 73р":
        old_route = data[user_id]["current_route"]
        data[user_id]["current_route"] = "73p"
        save_data()
        reply = "Вы вышли на маршрут 73р." if old_route is None else f"Вы переключились на маршрут 73р (ранее были на {old_route})."
        send_message(peer_id, reply, keyboard)


    elif text == "На 80":
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

    elif text.lower() == "план":
        plan_message = format_plan()
        send_message(peer_id, plan_message, keyboard)

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

        old_week_pax = data["week_stats"][current_route]["pax"]
        new_week_pax = old_week_pax + pax
        data["week_stats"][current_route]["pax"] = new_week_pax

        target = PLAN_TARGETS[current_route]
        if not data["week_stats"][current_route]["completed"] and new_week_pax >= target and old_week_pax < target:
            data["week_stats"][current_route]["completed"] = True
            completion_msg = (f"🚌 На маршруте {current_route} выполнен недельный план "
                              f"({new_week_pax}/{target} пассажиров)!")
            send_message(peer_id, completion_msg, keyboard)

        save_data()

        laps_now = data[user_id]["routes"][current_route]["laps"]
        pax_now = data[user_id]["routes"][current_route]["pax"]
        reply = f"Круг засчитан! Теперь у вас на маршруте {current_route}: {laps_now} кругов, {pax_now} паксов."
        send_message(peer_id, reply, keyboard)

    # --- Команды солярка и ремонт ---
    elif text.lower().startswith("солярка"):
        parts = text.split()
        if len(parts) >= 2 and parts[1].lstrip('-').isdigit():
            value = int(parts[1])
            data[user_id]["diesel"] += value
            save_data()
            reply = f"Солярка +{value}. Всего за месяц: {data[user_id]['diesel']}."
        else:
            reply = "Укажите число после команды, например: солярка 50"
        send_message(peer_id, reply, keyboard)

    elif text.lower().startswith("ремонт"):
        parts = text.split()
        if len(parts) >= 2 and parts[1].lstrip('-').isdigit():
            value = int(parts[1])
            data[user_id]["repair"] += value
            save_data()
            reply = f"Ремонт +{value}. Всего за месяц: {data[user_id]['repair']}."
        else:
            reply = "Укажите число после команды, например: ремонт 2"
        send_message(peer_id, reply, keyboard)

    # --- Команда для администратора: сброс месяца ---
    elif text.lower() == "сброс месяца":
        if is_admin(user_id):
            reset_monthly_stats()
            send_message(peer_id, "✅ Месячные счётчики солярки и ремонта обнулены.", keyboard)
        else:
            send_message(peer_id, "❌ У вас нет прав на эту команду.", keyboard)

# ---------- ЗАПУСК БОТА ----------
def main():
    print("Бот запущен...")
    
    load_data()
    
    global target_user_ids
    target_user_ids = get_target_ids()
    print("Администраторы (ID):", target_user_ids)
    
    schedule_midnight_reset()
    
    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.to_me:
            handle_message(event)

if __name__ == "__main__":
    main()
