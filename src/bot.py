import json
import logging
import asyncio
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from aiogram.utils.exceptions import BotBlocked, ChatNotFound, RetryAfter, UserDeactivated, Unauthorized
from aiogram.utils.exceptions import MessageNotModified
from docx import Document
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

API_TOKEN = os.getenv("BOT_TOKEN", "")
if not API_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

MAX_SCORE = 30
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATA_DIRECTORY = os.getenv("DATA_DIR", "data")
os.makedirs(DATA_DIRECTORY, exist_ok=True)
SCORES_FILE = os.path.join(DATA_DIRECTORY, "scores.json")
EVENTS_FILE = os.path.join(DATA_DIRECTORY, "events.json")
students_file = os.path.join(DATA_DIRECTORY, "students.json")
mailing_file = os.path.join(DATA_DIRECTORY, "mailing.json")
# Директория для хранения файла расписания
SCHEDULE_DIRECTORY = os.getenv("SCHEDULE_DIR", "schedule")
SCHEDULE_FILENAME = os.path.join(SCHEDULE_DIRECTORY, "Расписание УМЦ.xlsx")  # Пример, можно изменить расширение

# Убедитесь, что директория для хранения файла существует
if not os.path.exists(SCHEDULE_DIRECTORY):
    os.makedirs(SCHEDULE_DIRECTORY)

class EventState:
    def __init__(self, file_path):
        self.file_path = file_path
        self.events = self.load_events()

    def load_events(self):
        if not os.path.exists(self.file_path):
            return {}
        try:
            with open(self.file_path, 'r') as file:
                data = json.load(file)
                logging.debug("Loaded %d events", len(data))
                return {int(k): v for k, v in data.items()}  # Убедитесь, что ключи — числа
        except FileNotFoundError:
            logging.info("Events file not found, initializing empty")
            return {}
        except json.JSONDecodeError as e:
            logging.error("Error decoding events JSON: %s", e)
            return {}

    def save_events(self):
        try:
            with open(self.file_path, 'w') as file:
                json.dump(self.events, file, indent=4)
                logging.debug("Saved %d events", len(self.events))
        except IOError as e:
            logging.error("Error saving events: %s", e)

# Инициализация состояния
event_state = EventState(EVENTS_FILE)

# Блок функций

# Функция для загрузки баллов из файла
def load_scores():
    if not os.path.exists(SCORES_FILE):
        return {}
    with open(SCORES_FILE, 'r') as f:
        return json.load(f)

# Функция для сохранения баллов в файл
def save_scores(scores):
    with open(SCORES_FILE, 'w') as f:
        json.dump(scores, f, indent=4)

scores = load_scores()  # Загрузка баллов при запуске

# Функция для получения имени студента по его ID
def get_student_name(user_id):
    for student in students:
        if student['id'] == user_id:
            return student  # Возвращаем словарь студента, а не его имя
    return None

def load_students():
    try:
        with open(students_file, 'r') as file:
            students = json.load(file)
        return students
    except FileNotFoundError:
        return []

def load_mailing():
    try:
        with open(mailing_file, 'r') as file:
            mailing_list = json.load(file)
    except FileNotFoundError:
        mailing_list = []
    return mailing_list

def save_students(students):
    with open(students_file, 'w') as file:
        json.dump(students, file)

async def forward_message_to_student(student_id, chat_id, message_id):
    try:
        await bot.send_chat_action(student_id, 'typing')
        await bot.forward_message(student_id, chat_id, message_id)
    except (BotBlocked, ChatNotFound, RetryAfter, UserDeactivated, Unauthorized) as e:
        logging.error(f"Error sending message to {student_id}: {e}")

async def write_message_to_student(student_id, chat_id, message_id):
    try:
        await bot.send_chat_action(student_id, 'typing')
        await bot.copy_message(student_id, chat_id, message_id)
    except (BotBlocked, ChatNotFound, RetryAfter, UserDeactivated, Unauthorized) as e:
        logging.error(f"Error sending message to {student_id}: {e}")

students = load_students()

# Блок команд

@dp.message_handler(commands=['uploadschedule'])
async def upload_schedule(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return
    # Проверяем, что файл загружен через цитирование
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply("Пожалуйста, используйте команду /uploadschedule в ответ на сообщение с файлом.")
        return

    # Загружаем файл
    try:
        document = message.reply_to_message.document
        file_id = document.file_id
        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path
        
        # Скачиваем файл и сохраняем его
        downloaded_file = await bot.download_file(file_path)
        with open(SCHEDULE_FILENAME, "wb") as f:
            f.write(downloaded_file.getvalue())

        # Текст уведомления для пользователей
        students = load_students()  # Загружаем список всех студентов
        notification_text = "Было опубликовано новое расписание. Используйте команду /schedule, чтобы ознакомиться с ним."

        # Формирование задач для рассылки текстового сообщения всем пользователям
        tasks = [
            asyncio.create_task(bot.send_message(student['id'], notification_text))
            for student in students
        ]

        # Выполняем задачи с задержкой между сообщениями
        await asyncio.gather(*tasks)

        # Подтверждаем загрузку расписания инициатору команды
        await message.reply("Файл расписания успешно загружен и уведомления разосланы всем студентам.")
    
    except Exception as e:
        await message.reply(f"Произошла ошибка при загрузке файла: {str(e)}")

@dp.message_handler(commands=['schedule'])
async def send_schedule(message: types.Message):
    # Проверяем, существует ли файл расписания
    if os.path.exists(SCHEDULE_FILENAME):
        await bot.send_document(message.chat.id, open(SCHEDULE_FILENAME, 'rb'), caption="Прикладываю актуальное расписание для студентов")
    else:
        await message.reply("Расписание не было загружено. Обратитесь к администратору.")

@dp.message_handler(commands=['createevent'])
async def create_event(message: types.Message, state: FSMContext):
    students = load_students()
    is_moderator = any(student['id'] == message.from_user.id and student.get('moderator') == 1 for student in students)
    user_group = next((student['group'] for student in students if student['id'] == message.from_user.id), None)
    
    if not is_moderator or not user_group:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return

    event_name = message.get_args().strip()
    if not event_name:
        await message.reply("Пожалуйста, укажите название мероприятия после команды /createevent.")
        return

    creator_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()

    event_id = max(event_state.events.keys(), default=0) + 1  # Уникальный идентификатор мероприятия
    event_state.events[event_id] = {
        'name': event_name,
        'group': user_group,
        'participants': [],
        'message_id': None,
        'chat_id': None,
        'creator_id': message.from_user.id
    }

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(text="Записаться", callback_data=f"join_event_{event_id}"))

    sent_message = await message.reply(
        f"{creator_name} открыл(-а) запись на \"{event_name}\"! Нажмите кнопку ниже, чтобы записаться.",
        reply_markup=markup
    )

    event_state.events[event_id]['message_id'] = sent_message.message_id
    event_state.events[event_id]['chat_id'] = sent_message.chat.id
    event_state.save_events()

@dp.callback_query_handler(lambda callback: callback.data and callback.data.startswith('join_event_'))
async def join_event(callback: types.CallbackQuery):
    try:
        event_id = int(callback.data.rsplit('_', 1)[-1])
    except (TypeError, ValueError):
        await callback.answer("Неверный идентификатор мероприя.", show_alert=True)
        return

    event = event_state.events.get(event_id)
    if not event:
        await callback.answer("Мероприятие уже завершено.", show_alert=True)
        return

    student = next(
        (item for item in load_students() if item['id'] == callback.from_user.id),
        None,
    )
    if not student or student.get('group') != event.get('group'):
        await callback.answer("Мероприятие недоступно для вашей группы.", show_alert=True)
        return

    participant_name = student['name']
    if participant_name in event['participants']:
        await callback.answer("Вы уже записаны.")
        return

    event['participants'].append(participant_name)
    event_state.save_events()
    await callback.answer("Вы записаны на мероприятие.")

@dp.message_handler(commands=['sendgroup'])
async def send_group(message: types.Message):
    students = load_students()
    is_moderator = any(student['id'] == message.from_user.id and student.get('moderator') == 1 for student in students)
    user_group = next((student['group'] for student in students if student['id'] == message.from_user.id), None)
    
    if not is_moderator or not user_group:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return

    if not message.reply_to_message:
        await message.reply("Эта команда должна быть использована при цитировании сообщения.")
        return

    # Список студентов из той же группы
    eligible_students = [student for student in students if student['group'] == user_group]

    # Список студентов, которые не получили сообщение и причина
    failed_students = []

    is_custom_poll = any(event_data['message_id'] == message.reply_to_message.message_id for event_data in event_state.events.values())

    if is_custom_poll:
        event_id = next(event_id for event_id, event_data in event_state.events.items() if event_data['message_id'] == message.reply_to_message.message_id)
        event_message_text = message.reply_to_message.text

        # Исключаем инициатора команды из списка рассылки
        eligible_students = [student for student in eligible_students if student['id'] != message.from_user.id]

        # Создаем кнопку "Записаться"
        join_button = InlineKeyboardButton("Записаться", callback_data=f"join_event_{event_id}")
        new_markup = InlineKeyboardMarkup().add(join_button)

        # Отправка сообщений студентам
        tasks = []
        for student in eligible_students:
            tasks.append(send_message_with_error_handling(student, event_message_text, new_markup, failed_students))
        
    else:
        tasks = []
        for student in eligible_students:
            tasks.append(forward_message_with_error_handling(student, message.chat.id, message.reply_to_message.message_id, failed_students))
    
    await asyncio.gather(*tasks)

    # Формируем список студентов, которые не получили сообщение
    if failed_students:
        failed_students_message = "Студенты, не получившие сообщение:\n" + "\n".join(failed_students)

    # Отправляем инициатору список студентов, которым не удалось отправить сообщение (или уведомление, что все успешно)
    if failed_students:
        await bot.send_message(message.from_user.id, failed_students_message)

    # Удаляем сообщения после рассылки
    if not is_custom_poll:
        await bot.delete_message(message.chat.id, message.reply_to_message.message_id)
    await bot.delete_message(message.chat.id, message.message_id)

    if is_custom_poll:
        await bot.send_message(message.chat.id, "Мероприятие было разослано студентам вашей группы.")

# Функция для отправки сообщения с обработкой ошибок
async def send_message_with_error_handling(student, text, markup, failed_students):
    try:
        await bot.send_message(student['id'], text, reply_markup=markup)
    except Exception as e:
        # Добавляем информацию о неудачной попытке отправки с именем студента
        failed_students.append(f"{student['name']} (ID {student['id']}): ошибка при отправке сообщения - ({str(e)})")

# Функция для пересылки сообщения с обработкой ошибок
async def forward_message_with_error_handling(student, chat_id, message_id, failed_students):
    try:
        await bot.forward_message(student['id'], chat_id, message_id)
    except Exception as e:
        # Добавляем информацию о неудачной попытке пересылки с именем студента
        failed_students.append(f"{student['name']} (ID {student['id']}): ошибка при пересылке сообщения - ({str(e)})")

@dp.message_handler(commands=['finishevent'])
async def finish_event(message: types.Message, state: FSMContext):
    students = load_students()
    is_moderator = any(student['id'] == message.from_user.id and student.get('moderator') == 1 for student in students)
    user_group = next((student['group'] for student in students if student['id'] == message.from_user.id), None)
    
    if not is_moderator or not user_group:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return

    if not message.reply_to_message or not message.reply_to_message.text:
        await message.reply("Пожалуйста, используйте команду, отвечая на сообщение с созданным мероприятием.")
        return

    found_event_id = None

    for event_id, event_data in event_state.events.items():
        if message.reply_to_message.message_id == event_data['message_id'] and message.reply_to_message.chat.id == event_data['chat_id']:
            found_event_id = event_id
            break
        elif event_data['name'] in message.reply_to_message.text and event_data['chat_id'] == message.chat.id:
            found_event_id = event_id
            break

    if found_event_id:
        event_data = event_state.events[found_event_id]
        event_name = event_data['name']
        participants = event_data['participants']

        doc = Document()
        for name in participants:
            doc.add_paragraph(name)

        file_name = f"{event_name}.docx"
        doc.save(file_name)

        with open(file_name, "rb") as file:
            await bot.send_document(message.from_user.id, document=file)

        os.remove(file_name)

        del event_state.events[found_event_id]

        await state.finish()
    else:
        await message.reply("Мероприятие не найдено. Пожалуйста, убедитесь, что вы отвечаете на правильное сообщение.")
    event_state.save_events()

@dp.message_handler(commands=['get'])
async def send_user_id(message: types.Message):
    chat_id = message.chat.id
    await message.reply(f"{chat_id}")

@dp.message_handler(commands=['sendall'])
async def send_all(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return

    if not message.reply_to_message:
        await message.reply("Эта команда должна быть использована при цитировании сообщения.")
        return

    students = load_students()
    all_student_ids = [student['id'] for student in students]

    tasks = [asyncio.create_task(forward_message_to_student(student_id, message.chat.id, message.reply_to_message.message_id)) for student_id in all_student_ids]
    
    await asyncio.gather(*tasks)

    # Удаление исходного сообщения и сообщения с командой
    await bot.delete_message(message.chat.id, message.reply_to_message.message_id)
    await bot.delete_message(message.chat.id, message.message_id)

@dp.message_handler(commands=['writeall'])
async def send_all(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return

    if not message.reply_to_message:
        await message.reply("Эта команда должна быть использована при цитировании сообщения.")
        return

    students = load_students()
    all_student_ids = [student['id'] for student in students]

    tasks = [asyncio.create_task(write_message_to_student(student_id, message.chat.id, message.reply_to_message.message_id)) for student_id in all_student_ids]
    
    await asyncio.gather(*tasks)

    # Удаление исходного сообщения и сообщения с командой
    await bot.delete_message(message.chat.id, message.reply_to_message.message_id)
    await bot.delete_message(message.chat.id, message.message_id)

@dp.message_handler(commands=['writegroup'])
async def send_group(message: types.Message):
    students = load_students()
    user_group = next((student['group'] for student in students if student['id'] == message.from_user.id), None)
    
    if message.from_user.id != ADMIN_ID:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return
    
    if not user_group:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return

    if not message.reply_to_message:
        await message.reply("Эта команда должна быть использована при цитировании сообщения.")
        return

    eligible_students = [student['id'] for student in students if student['group'] == user_group]

    tasks = [asyncio.create_task(write_message_to_student(student_id, message.chat.id, message.reply_to_message.message_id)) for student_id in eligible_students]
    
    await asyncio.gather(*tasks)

    # Удаление исходного сообщения и сообщения с командой
    await bot.delete_message(message.chat.id, message.reply_to_message.message_id)
    await bot.delete_message(message.chat.id, message.message_id)


@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    chat_id = message.chat.id
    students = load_students()
    if chat_id not in [student['id'] for student in students]:
        await bot.send_chat_action(message.chat.id, 'typing')
        await bot.send_message(
            chat_id,
            'Привет! Это учебный бот-помощник для студентов.\n\n'
            'Бот помогает рассылать расписание, голосования, файлы и конспекты по учебной группе.\n\n'
            'Доступ для вашей группы ещё не настроен. Обратитесь к администратору.'
        )
    else:
        await bot.send_chat_action(message.chat.id, 'typing')
        await bot.send_message(
            chat_id,
            'Привет!\n\nБот подключён к вашей учебной группе. '
            'Доступные функции показаны в описании кейса.\n\nХорошей учёбы!'
        )

@dp.message_handler(commands=['addstudent'])
async def add_student(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        # Разделяем текст команды
        parts = message.text.split()

        if len(parts) < 4:
            await message.reply("Недостаточно аргументов. Используйте /addstudent <имя> <ID> <группа> [уровень модератора]")
            return

        student_name = " ".join(parts[1:-3])
        user_id = int(parts[-3])
        group_name = parts[-2]
        moderator_level = int(parts[-1]) if len(parts) == 5 else 0

        students = load_students()
        students.append({"id": user_id, "moderator": moderator_level, "group": group_name, "name": student_name})
        save_students(students)

        await bot.send_chat_action(message.chat.id, 'typing')
        await message.reply(f"Студент {student_name} с ID {user_id} добавлен(-а).")
    except Exception as e:
        await message.reply(f"Произошла ошибка: {e}")

@dp.message_handler(commands=['deletestudent'])
async def delete_student(message: types.Message):
    # Проверка прав на выполнение команды
    if message.from_user.id != ADMIN_ID:
        return

    try:
        parts = message.text.split(maxsplit=1)
        
        # Проверка на наличие аргумента
        if len(parts) < 2:
            await message.reply("Недостаточно аргументов. Используйте /deletestudent <имя> или <ID>")
            return
        
        identifier = parts[1].strip()  # Получаем идентификатор (имя или ID)
        students = load_students()  # Загружаем список студентов
        initial_length = len(students)  # Запоминаем исходное количество студентов
        
        # Если идентификатор - это число (ID)
        if identifier.isdigit():
            user_id = int(identifier)
            # Найдём студента по ID, чтобы сохранить его имя
            student_to_delete = next((student for student in students if student['id'] == user_id), None)
            if student_to_delete:
                students = [student for student in students if student['id'] != user_id]  # Удаление студента по ID
                student_name = student_to_delete['name']
            else:
                student_name = None
        else:
            student_name = identifier
            # Найдём студента по имени, чтобы сохранить его ID
            student_to_delete = next((student for student in students if student['name'] == student_name), None)
            if student_to_delete:
                students = [student for student in students if student['name'] != student_name]  # Удаление студента по имени
                user_id = student_to_delete['id']
            else:
                user_id = None
        
        # Если список студентов не изменился, значит студент не найден
        if len(students) == initial_length:
            await message.reply("Студент с указанным именем или ID не найден.")
            return
        
        # Сохранение обновлённого списка студентов
        save_students(students)
        
        # Отправляем сообщение об удалении
        await bot.send_chat_action(message.chat.id, 'typing')
        if student_name and user_id:
            await message.reply(f"Студент {student_name} (ID {user_id}) был удален из списка студентов.")
        elif student_name:
            await message.reply(f"Студент {student_name} был удален из списка студентов.")
        else:
            await message.reply(f"Студент с ID {user_id} был удален из списка студентов.")

    except Exception as e:
        await message.reply(f"Произошла ошибка: {e}")

@dp.message_handler(commands=['changegroup'])
async def change_group_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    # Получаем текст после команды
    new_group = message.text.split(' ', 1)[1].strip()

    # Загрузим список студентов из файла
    students = load_students()

    # Найдем информацию о вашем пользователе в списке студентов
    for student in students:
        if student['id'] == message.from_user.id:
            # Изменяем значение названия группы
            student['group'] = new_group
            # Сохраняем обновленный список студентов в файл
            save_students(students)
            await bot.send_chat_action(message.chat.id, 'typing')
            await message.reply(f"Ваша группа успешно изменена на '{new_group}'.")
            return

@dp.message_handler(commands=['list'])
async def list_students(message: types.Message):
    students = load_students()
    is_moderator = any(student['id'] == message.from_user.id and student.get('moderator') == 1 for student in students)
    user_group = next((student['group'] for student in students if student['id'] == message.from_user.id), None)
    
    if not is_moderator or not user_group:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return

    group_members = sorted([student['name'] for student in students if student['group'] == user_group])

    if group_members:
        response = "Список студентов в вашей группе:\n" + "\n".join(group_members)
    else:
        response = "В вашей группе нет других студентов."

    await message.reply(response)

@dp.message_handler(commands=['checkaccess'])
async def check_access(message: types.Message):
    students = load_students()
    is_moderator = any(student['id'] == message.from_user.id and student.get('moderator') == 1 for student in students)
    user_group = next((student['group'] for student in students if student['id'] == message.from_user.id), None)
    
    if not is_moderator or not user_group:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return
    
    reachable_users = []
    unreachable_users = []

    async def send_test_message(student):
        try:
            await bot.send_chat_action(student['id'], 'typing')
            sent_message = await bot.send_message(student['id'], ".", disable_notification=True)
            await bot.delete_message(student['id'], sent_message.message_id)
            reachable_users.append(student['name'])
        except (BotBlocked, ChatNotFound, RetryAfter, UserDeactivated, Unauthorized) as e:
            logging.error(f"Error sending message to {student['id']}: {e}")
            unreachable_users.append(student['name'])
    
    tasks = [
        asyncio.create_task(send_test_message(student))
        for student in students if student['group'] == user_group
    ]
    
    await asyncio.gather(*tasks)
    
    reachable_users.sort()
    unreachable_users.sort()

    response = "Результаты отправки проверочного сообщения:\n\n"

    if reachable_users:
        response += "Пользователи, до которых дошло проверочное сообщение:\n"
        response += "\n".join(reachable_users)
    
    if unreachable_users:
        response += "\n\nПользователи, до которых НЕ дошло проверочное сообщение:\n"
        response += "\n".join(unreachable_users)
    
    await message.reply(response)

@dp.message_handler(commands=['listid'])
async def list_students_with_id(message: types.Message):
    # Загружаем список студентов
    students = load_students()

    # Проверяем, является ли пользователь модератором
    if message.from_user.id != ADMIN_ID:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return
    user_group = next((student['group'] for student in students if student['id'] == message.from_user.id), None)

    # Получаем список студентов в той же группе, что и у инициатора команды
    group_members = sorted([f"{student['name']} ({student['id']})" for student in students if student['group'] == user_group])

    # Формируем ответ
    if group_members:
        response = "Список студентов в вашей группе:\n" + "\n".join(group_members)
    else:
        response = "В вашей группе нет других студентов."

    # Отправляем ответ пользователю
    await message.reply(response)

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
