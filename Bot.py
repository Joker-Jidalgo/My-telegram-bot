"""
Telegram Dating & Social Group Bot
aiogram 2.25.1 | Python 3.8
"""

import os
import logging
import asyncio
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher.filters import Text
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery
)
from aiogram.utils import executor

# ============================================================
#  НАСТРОЙКИ — заполни перед запуском!
# ============================================================

BOT_TOKEN = os.getenv("8756108921:AAEyt1aAd55sH2_ZFZYpFkJixbvj_wzveWU")        # токен от @BotFather
GROUP_ID  = -1003706076241        # ID группы

# ID тем
TOPIC_DATING_ID     = 15   # Знакомства/Общение
TOPIC_STORIES_ID    = 19   # Истории
TOPIC_CITY_ID       = 18   # Город
TOPIC_VIP_DATING_ID = 20   # VIP знакомства
TOPIC_VIP_CHAT_ID   = 12   # VIP чат

VIP_PRICE_STARS   = 10
VIP_DURATION_DAYS = 30
VIP_BADGE         = "👑"
ADMIN_IDS         = [5618560527]
DB_FILE           = "bot_data.db"

# ============================================================
#  ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
#  БАЗА ДАННЫХ
# ============================================================

def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id   INTEGER PRIMARY KEY,
            username  TEXT,
            full_name TEXT,
            is_vip    INTEGER DEFAULT 0,
            vip_until TEXT,
            joined_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS profiles (
            user_id     INTEGER PRIMARY KEY,
            name        TEXT,
            age         INTEGER,
            city        TEXT,
            looking_for TEXT,
            about       TEXT,
            interests   TEXT,
            photo_id    TEXT,
            is_pinned   INTEGER DEFAULT 0,
            bumped_at   TEXT,
            updated_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS payments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            stars      INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS anon_messages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id    INTEGER,
            recipient_id INTEGER,
            message_text TEXT,
            sent_at      TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()


def upsert_user(user_id, username, full_name):
    conn = get_conn()
    conn.execute("""
        INSERT INTO users (user_id, username, full_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            full_name=excluded.full_name
    """, (user_id, username or "", full_name or ""))
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def is_vip(user_id):
    user = get_user(user_id)
    if not user or not user["is_vip"]:
        return False
    if user["vip_until"]:
        try:
            if datetime.now() > datetime.fromisoformat(user["vip_until"]):
                set_vip(user_id, False)
                return False
        except Exception:
            pass
    return bool(user["is_vip"])


def set_vip(user_id, status, days=0):
    conn = get_conn()
    vip_until = (datetime.now() + timedelta(days=days)).isoformat() if (status and days > 0) else None
    conn.execute(
        "UPDATE users SET is_vip=?, vip_until=? WHERE user_id=?",
        (1 if status else 0, vip_until, user_id)
    )
    conn.commit()
    conn.close()


def get_vip_until(user_id):
    user = get_user(user_id)
    return user["vip_until"] if user else None


def save_profile(user_id, data):
    conn = get_conn()
    interests_str = json.dumps(data.get("interests", []), ensure_ascii=False)
    conn.execute("""
        INSERT INTO profiles (user_id, name, age, city, looking_for, about, interests, photo_id, updated_at)
        VALUES (?,?,?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            name=excluded.name, age=excluded.age, city=excluded.city,
            looking_for=excluded.looking_for, about=excluded.about,
            interests=excluded.interests, photo_id=excluded.photo_id,
            updated_at=datetime('now')
    """, (user_id, data.get("name",""), data.get("age"), data.get("city",""),
          data.get("looking_for",""), data.get("about",""), interests_str, data.get("photo_id")))
    conn.commit()
    conn.close()


def get_profile(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return None
    p = dict(row)
    try:
        p["interests"] = json.loads(p["interests"] or "[]")
    except Exception:
        p["interests"] = []
    return p


def search_profiles(age_min=None, age_max=None, city=None):
    conn = get_conn()
    q = "SELECT p.*, u.is_vip FROM profiles p JOIN users u ON p.user_id=u.user_id WHERE 1=1"
    params = []
    if age_min:
        q += " AND p.age>=?"; params.append(age_min)
    if age_max:
        q += " AND p.age<=?"; params.append(age_max)
    if city:
        q += " AND LOWER(p.city) LIKE ?"; params.append(f"%{city.lower()}%")
    q += " ORDER BY u.is_vip DESC, p.bumped_at DESC LIMIT 15"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    results = []
    for row in rows:
        p = dict(row)
        try:
            p["interests"] = json.loads(p["interests"] or "[]")
        except Exception:
            p["interests"] = []
        results.append(p)
    return results


def bump_profile(user_id):
    conn = get_conn()
    conn.execute("UPDATE profiles SET bumped_at=datetime('now') WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def set_pinned(user_id, val):
    conn = get_conn()
    conn.execute("UPDATE profiles SET is_pinned=? WHERE user_id=?", (1 if val else 0, user_id))
    conn.commit()
    conn.close()


def save_payment(user_id, stars):
    conn = get_conn()
    conn.execute("INSERT INTO payments (user_id, stars) VALUES (?,?)", (user_id, stars))
    conn.commit()
    conn.close()


def format_profile(profile, uid=None):
    vip_badge = f"{VIP_BADGE} " if (uid and is_vip(uid)) else ""
    pin = "📌 " if profile.get("is_pinned") else ""
    interests = ", ".join(profile.get("interests", [])) or "не указаны"
    about = profile.get("about") or "не указано"
    return (
        f"{pin}👤 <b>{vip_badge}{profile['name']}</b>\n"
        f"🎂 Возраст: {profile.get('age','—')}\n"
        f"🏙 Город: {profile.get('city','—')}\n"
        f"💬 Ищу: {profile.get('looking_for','—')}\n"
        f"📝 О себе: {about}\n"
        f"❤️ Интересы: {interests}"
    )

# ============================================================
#  КЛАВИАТУРЫ
# ============================================================

def main_kb(vip=False):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(InlineKeyboardButton("📋 Моя анкета", callback_data="my_profile"))
    kb.add(InlineKeyboardButton("✏️ Создать/изменить анкету", callback_data="edit_profile"))
    if vip:
        kb.add(InlineKeyboardButton("🔍 Поиск анкет", callback_data="search"))
        kb.row(
            InlineKeyboardButton("📌 Закрепить анкету", callback_data="pin"),
            InlineKeyboardButton("⬆️ Поднять анкету", callback_data="bump")
        )
        kb.add(InlineKeyboardButton("📨 Анонимное сообщение", callback_data="anon"))
        kb.add(InlineKeyboardButton(f"{VIP_BADGE} VIP активен ✅", callback_data="vip_info"))
    else:
        kb.add(InlineKeyboardButton(f"⭐ Купить VIP ({VIP_PRICE_STARS} Stars)", callback_data="buy_vip"))
    return kb


def cancel_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("❌ Отмена", callback_data="cancel"))
    return kb


def skip_kb():
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("⏭ Пропустить", callback_data="skip"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    )
    return kb


def back_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("◀️ Назад", callback_data="main_menu"))
    return kb

# ============================================================
#  FSM СОСТОЯНИЯ
# ============================================================

class ProfileForm(StatesGroup):
    name        = State()
    age         = State()
    city        = State()
    looking_for = State()
    about       = State()
    interests   = State()
    photo       = State()


class SearchForm(StatesGroup):
    age_min = State()
    age_max = State()
    city    = State()


class AnonForm(StatesGroup):
    recipient = State()
    message   = State()

# ============================================================
#  БОТ И ДИСПЕТЧЕР
# ============================================================

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp  = Dispatcher(bot, storage=MemoryStorage())

VIP_TOPICS   = {TOPIC_VIP_DATING_ID, TOPIC_VIP_CHAT_ID}
MEDIA_TYPES  = ("photo", "video", "voice", "video_note", "animation", "sticker")

# ============================================================
#  МОДЕРАЦИЯ ГРУППЫ
# ============================================================

@dp.message_handler(
    lambda m: m.chat.id == GROUP_ID and m.from_user and not m.from_user.is_bot,
    content_types=types.ContentType.ANY
)
async def group_guard(message: types.Message):
    user = message.from_user
    upsert_user(user.id, user.username, user.full_name)
    thread = message.message_thread_id
    vip = is_vip(user.id)

    # VIP темы — только VIP могут писать
    if thread in VIP_TOPICS:
        if not vip:
            try:
                await message.delete()
                await bot.send_message(
                    user.id,
                    f"⛔ Ваше сообщение в VIP-теме удалено.\n"
                    f"Эта тема только для {VIP_BADGE} VIP пользователей.\n"
                    f"Купите VIP: /start"
                )
            except Exception as e:
                logger.error(f"Ошибка удаления: {e}")
        return

    # Медиа — только VIP
    has_media = any(getattr(message, t, None) for t in MEDIA_TYPES)
    if has_media and not vip:
        try:
            await message.delete()
            await bot.send_message(
                user.id,
                f"📸 Отправка медиа доступна только {VIP_BADGE} VIP.\n"
                f"Купите VIP: /start"
            )
        except Exception as e:
            logger.error(f"Ошибка удаления медиа: {e}")

# ============================================================
#  /start и главное меню
# ============================================================

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message, state: FSMContext):
    await state.finish()
    user = message.from_user
    upsert_user(user.id, user.username, user.full_name)
    vip = is_vip(user.id)
    badge = f"{VIP_BADGE} " if vip else ""
    status = f"{VIP_BADGE} VIP активен" if vip else "Обычный пользователь"
    await message.answer(
        f"👋 Привет, {badge}{user.first_name}!\n\n"
        f"Статус: {status}\n\n"
        f"Выбери действие:",
        reply_markup=main_kb(vip)
    )


@dp.callback_query_handler(text="main_menu", state="*")
async def cb_main_menu(call: types.CallbackQuery, state: FSMContext):
    await state.finish()
    user = call.from_user
    vip = is_vip(user.id)
    badge = f"{VIP_BADGE} " if vip else ""
    status = f"{VIP_BADGE} VIP активен" if vip else "Обычный пользователь"
    await call.message.edit_text(
        f"👋 Привет, {badge}{user.first_name}!\n\n"
        f"Статус: {status}\n\n"
        f"Выбери действие:",
        reply_markup=main_kb(vip)
    )
    await call.answer()


@dp.callback_query_handler(text="cancel", state="*")
async def cb_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await call.message.edit_text("❌ Отменено.", reply_markup=back_kb())
    await call.answer()


@dp.message_handler(commands=["help"])
async def cmd_help(message: types.Message):
    await message.answer(
        "ℹ️ <b>Команды:</b>\n\n"
        "/start — главное меню\n"
        "/vip — информация о VIP\n\n"
        "<b>VIP команды:</b>\n"
        "/search — поиск анкет\n"
        "/bump — поднять анкету\n"
        "/pin — закрепить анкету\n"
        "/anon — анонимное сообщение"
    )

# ============================================================
#  АНКЕТА
# ============================================================

@dp.callback_query_handler(text="my_profile")
async def cb_my_profile(call: types.CallbackQuery):
    profile = get_profile(call.from_user.id)
    if not profile:
        await call.message.edit_text(
            "У вас нет анкеты. Создайте её!",
            reply_markup=main_kb(is_vip(call.from_user.id))
        )
        await call.answer()
        return
    text = format_profile(profile, call.from_user.id)
    if profile.get("photo_id"):
        await call.message.delete()
        await bot.send_photo(
            call.from_user.id,
            photo=profile["photo_id"],
            caption=text,
            reply_markup=back_kb()
        )
    else:
        await call.message.edit_text(text, reply_markup=back_kb())
    await call.answer()


@dp.callback_query_handler(text="edit_profile")
async def cb_edit_profile(call: types.CallbackQuery, state: FSMContext):
    await ProfileForm.name.set()
    await call.message.edit_text(
        "📝 <b>Создание анкеты</b>\n\nШаг 1/7 — Введите ваше <b>имя</b>:",
        reply_markup=cancel_kb()
    )
    await call.answer()


@dp.message_handler(state=ProfileForm.name)
async def pf_name(message: types.Message, state: FSMContext):
    if len(message.text.strip()) > 50:
        await message.answer("⚠️ Имя слишком длинное. Введите снова:")
        return
    await state.update_data(name=message.text.strip())
    await ProfileForm.age.set()
    await message.answer("Шаг 2/7 — Введите ваш <b>возраст</b>:", reply_markup=cancel_kb())


@dp.message_handler(state=ProfileForm.age)
async def pf_age(message: types.Message, state: FSMContext):
    try:
        age = int(message.text.strip())
        if not (14 <= age <= 100):
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите корректный возраст (14–100):")
        return
    await state.update_data(age=age)
    await ProfileForm.city.set()
    await message.answer("Шаг 3/7 — Введите ваш <b>город</b>:", reply_markup=cancel_kb())


@dp.message_handler(state=ProfileForm.city)
async def pf_city(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text.strip())
    await ProfileForm.looking_for.set()
    await message.answer("Шаг 4/7 — <b>Кого вы ищете?</b>:", reply_markup=cancel_kb())


@dp.message_handler(state=ProfileForm.looking_for)
async def pf_looking_for(message: types.Message, state: FSMContext):
    await state.update_data(looking_for=message.text.strip())
    await ProfileForm.about.set()
    await message.answer("Шаг 5/7 — Расскажите <b>о себе</b> (или пропустите):", reply_markup=skip_kb())


@dp.callback_query_handler(text="skip", state=ProfileForm.about)
async def pf_about_skip(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(about="")
    await ProfileForm.interests.set()
    await call.message.edit_text(
        "Шаг 6/7 — Введите <b>интересы</b> через запятую (или пропустите):",
        reply_markup=skip_kb()
    )
    await call.answer()


@dp.message_handler(state=ProfileForm.about)
async def pf_about(message: types.Message, state: FSMContext):
    await state.update_data(about=message.text.strip())
    await ProfileForm.interests.set()
    await message.answer("Шаг 6/7 — Введите <b>интересы</b> через запятую (или пропустите):", reply_markup=skip_kb())


@dp.callback_query_handler(text="skip", state=ProfileForm.interests)
async def pf_interests_skip(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(interests=[])
    await ProfileForm.photo.set()
    await call.message.edit_text(
        "Шаг 7/7 — Отправьте <b>фото</b> (только VIP, или пропустите):",
        reply_markup=skip_kb()
    )
    await call.answer()


@dp.message_handler(state=ProfileForm.interests)
async def pf_interests(message: types.Message, state: FSMContext):
    interests = [i.strip() for i in message.text.split(",") if i.strip()]
    await state.update_data(interests=interests)
    await ProfileForm.photo.set()
    await message.answer("Шаг 7/7 — Отправьте <b>фото</b> (только VIP, или пропустите):", reply_markup=skip_kb())


@dp.callback_query_handler(text="skip", state=ProfileForm.photo)
async def pf_photo_skip(call: types.CallbackQuery, state: FSMContext):
    await _save_profile(call.from_user.id, state, None)
    await state.finish()
    await call.message.edit_text(
        "✅ <b>Анкета сохранена!</b>",
        reply_markup=main_kb(is_vip(call.from_user.id))
    )
    await call.answer()


@dp.message_handler(state=ProfileForm.photo, content_types=types.ContentTypes.PHOTO)
async def pf_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id if is_vip(message.from_user.id) else None
    if not is_vip(message.from_user.id):
        await message.answer("📸 Фото в анкете только для VIP. Сохраняю без фото.")
    await _save_profile(message.from_user.id, state, photo_id)
    await state.finish()
    await message.answer("✅ <b>Анкета сохранена!</b>", reply_markup=main_kb(is_vip(message.from_user.id)))


@dp.message_handler(state=ProfileForm.photo)
async def pf_photo_wrong(message: types.Message):
    await message.answer("⚠️ Отправьте фото или нажмите «Пропустить».")


async def _save_profile(user_id, state: FSMContext, photo_id):
    data = await state.get_data()
    data["photo_id"] = photo_id
    save_profile(user_id, data)

# ── Поднятие и закрепление ────────────────────────────────

@dp.callback_query_handler(text="bump")
@dp.message_handler(commands=["bump"])
async def cb_bump(event):
    if isinstance(event, types.CallbackQuery):
        user = event.from_user
    else:
        user = event.from_user
    if not is_vip(user.id):
        text = "❌ Поднятие анкеты только для VIP!"
        if isinstance(event, types.CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return
    profile = get_profile(user.id)
    if not profile:
        text = "⚠️ У вас нет анкеты. Создайте через /start"
        if isinstance(event, types.CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return
    bump_profile(user.id)
    text = "⬆️ <b>АНКЕТА ПОДНЯТА</b>\n\n" + format_profile(profile, user.id)
    try:
        await bot.send_message(GROUP_ID, text, message_thread_id=TOPIC_DATING_ID)
    except Exception as e:
        logger.error(f"Ошибка отправки анкеты: {e}")
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text("⬆️ Анкета поднята и опубликована!", reply_markup=back_kb())
        await event.answer()
    else:
        await event.answer("⬆️ Анкета поднята и опубликована!")


@dp.callback_query_handler(text="pin")
@dp.message_handler(commands=["pin"])
async def cb_pin(event):
    if isinstance(event, types.CallbackQuery):
        user = event.from_user
    else:
        user = event.from_user
    if not is_vip(user.id):
        text = "❌ Закрепление анкеты только для VIP!"
        if isinstance(event, types.CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return
    profile = get_profile(user.id)
    if not profile:
        text = "⚠️ У вас нет анкеты."
        if isinstance(event, types.CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
        return
    set_pinned(user.id, True)
    text = "📌 <b>ЗАКРЕПЛЁННАЯ АНКЕТА</b>\n\n" + format_profile(profile, user.id)
    try:
        await bot.send_message(GROUP_ID, text, message_thread_id=TOPIC_DATING_ID)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text("📌 Анкета закреплена и опубликована!", reply_markup=back_kb())
        await event.answer()
    else:
        await event.answer("📌 Анкета закреплена!")

# ============================================================
#  VIP ПОКУПКА
# ============================================================

@dp.callback_query_handler(text="vip_info")
@dp.message_handler(commands=["vip"])
async def vip_info(event):
    if isinstance(event, types.CallbackQuery):
        user = event.from_user
        send = event.message.edit_text
        await event.answer()
    else:
        user = event.from_user
        send = event.answer

    if is_vip(user.id):
        until = get_vip_until(user.id)
        date_str = until[:10] if until else "бессрочно"
        kb = back_kb()
        text = (
            f"{VIP_BADGE} <b>VIP активен!</b>\n\n"
            f"📅 До: {date_str}\n\n"
            f"✅ VIP темы\n✅ Закрепление анкеты\n"
            f"✅ Поднятие анкеты\n✅ Поиск анкет\n"
            f"✅ Анонимные сообщения\n✅ Медиа в группе"
        )
    else:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton(
            f"💳 Купить {VIP_PRICE_STARS} ⭐ Stars",
            callback_data="confirm_buy"
        ))
        kb.add(InlineKeyboardButton("❌ Отмена", callback_data="main_menu"))
        text = (
            f"⭐ <b>VIP статус</b>\n\n"
            f"👑 VIP темы группы\n"
            f"📌 Закрепление анкеты\n"
            f"⬆️ Поднятие анкеты\n"
            f"🔍 Поиск анкет\n"
            f"📨 Анонимные сообщения\n"
            f"📸 Медиа в группе\n\n"
            f"💰 Цена: <b>{VIP_PRICE_STARS} Stars</b>\n"
            f"⏳ Срок: <b>{VIP_DURATION_DAYS} дней</b>"
        )
    await send(text, reply_markup=kb)


@dp.callback_query_handler(text="buy_vip")
@dp.callback_query_handler(text="confirm_buy")
async def buy_vip(call: types.CallbackQuery):
    await call.answer()
    if is_vip(call.from_user.id):
        await call.message.edit_text(f"{VIP_BADGE} У вас уже есть VIP!", reply_markup=back_kb())
        return
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title=f"{VIP_BADGE} VIP статус",
        description=f"VIP на {VIP_DURATION_DAYS} дней. Доступ в VIP темы, поиск анкет и многое другое!",
        payload="vip_purchase",
        currency="XTR",
        prices=[LabeledPrice(label="VIP", amount=VIP_PRICE_STARS)],
        start_parameter="vip"
    )


@dp.pre_checkout_query_handler(lambda q: True)
async def pre_checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)


@dp.message_handler(content_types=types.ContentTypes.SUCCESSFUL_PAYMENT)
async def payment_success(message: types.Message):
    user = message.from_user
    upsert_user(user.id, user.username, user.full_name)
    set_vip(user.id, True, days=VIP_DURATION_DAYS)
    save_payment(user.id, message.successful_payment.total_amount)
    await message.answer(
        f"🎉 <b>VIP активирован на {VIP_DURATION_DAYS} дней!</b>\n\n"
        f"✅ VIP темы\n✅ Закрепление анкеты\n"
        f"✅ Поиск анкет\n✅ Анонимные сообщения\n"
        f"✅ Медиа в группе\n\n"
        f"Нажмите /start",
        reply_markup=main_kb(True)
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"💰 Новая покупка VIP!\n"
                f"{user.full_name} (@{user.username})\n"
                f"ID: {user.id}"
            )
        except Exception:
            pass

# ============================================================
#  ПОИСК АНКЕТ
# ============================================================

@dp.callback_query_handler(text="search")
@dp.message_handler(commands=["search"])
async def start_search(event):
    if isinstance(event, types.CallbackQuery):
        user = event.from_user
        send = event.message.edit_text
        await event.answer()
    else:
        user = event.from_user
        send = event.answer
    if not is_vip(user.id):
        await send("❌ Поиск анкет только для VIP!", reply_markup=back_kb())
        return
    await SearchForm.age_min.set()
    await send(
        "🔍 <b>Поиск анкет</b>\n\nШаг 1/3 — Минимальный возраст (или пропустите):",
        reply_markup=skip_kb()
    )


@dp.callback_query_handler(text="skip", state=SearchForm.age_min)
async def search_skip_age_min(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(age_min=None)
    await SearchForm.age_max.set()
    await call.message.edit_text("Шаг 2/3 — Максимальный возраст (или пропустите):", reply_markup=skip_kb())
    await call.answer()


@dp.message_handler(state=SearchForm.age_min)
async def search_age_min(message: types.Message, state: FSMContext):
    try:
        age = int(message.text.strip())
        if not (14 <= age <= 100): raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите корректный возраст:")
        return
    await state.update_data(age_min=age)
    await SearchForm.age_max.set()
    await message.answer("Шаг 2/3 — Максимальный возраст (или пропустите):", reply_markup=skip_kb())


@dp.callback_query_handler(text="skip", state=SearchForm.age_max)
async def search_skip_age_max(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(age_max=None)
    await SearchForm.city.set()
    await call.message.edit_text("Шаг 3/3 — Город (или пропустите):", reply_markup=skip_kb())
    await call.answer()


@dp.message_handler(state=SearchForm.age_max)
async def search_age_max(message: types.Message, state: FSMContext):
    try:
        age = int(message.text.strip())
        if not (14 <= age <= 100): raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите корректный возраст:")
        return
    await state.update_data(age_max=age)
    await SearchForm.city.set()
    await message.answer("Шаг 3/3 — Город (или пропустите):", reply_markup=skip_kb())


@dp.callback_query_handler(text="skip", state=SearchForm.city)
async def search_skip_city(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(city=None)
    await state.finish()
    await _do_search(call.message, await call.message.bot.get_state_data(call.from_user.id, call.message.chat.id))
    await call.answer()


@dp.message_handler(state=SearchForm.city)
async def search_city(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text.strip())
    data = await state.get_data()
    await state.finish()
    results = search_profiles(data.get("age_min"), data.get("age_max"), data.get("city"))
    if not results:
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔍 Новый поиск", callback_data="search"))
        await message.answer("😔 Никого не найдено. Попробуйте другие параметры.", reply_markup=kb)
        return
    await message.answer(f"✅ Найдено: <b>{len(results)}</b>")
    for p in results[:10]:
        vip_badge = f"{VIP_BADGE} " if p.get("is_vip") else ""
        interests = ", ".join(p.get("interests", [])) or "не указаны"
        text = (
            f"👤 <b>{vip_badge}{p['name']}</b>\n"
            f"🎂 {p.get('age','—')} лет\n"
            f"🏙 {p.get('city','—')}\n"
            f"💬 Ищу: {p.get('looking_for','—')}\n"
            f"❤️ {interests}"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📨 Написать анонимно", callback_data=f"anon_to_{p['user_id']}"))
        if p.get("photo_id"):
            await message.answer_photo(p["photo_id"], caption=text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)


async def _do_search(target, data):
    results = search_profiles(data.get("age_min"), data.get("age_max"), data.get("city"))
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🔍 Новый поиск", callback_data="search"))
    if not results:
        await target.answer("😔 Никого не найдено.", reply_markup=kb)
        return
    await target.answer(f"✅ Найдено: <b>{len(results)}</b>")

# ============================================================
#  АНОНИМНЫЕ СООБЩЕНИЯ
# ============================================================

@dp.callback_query_handler(text="anon")
@dp.message_handler(commands=["anon"])
async def start_anon(event):
    if isinstance(event, types.CallbackQuery):
        user = event.from_user
        send = event.message.edit_text
        await event.answer()
    else:
        user = event.from_user
        send = event.answer
    if not is_vip(user.id):
        await send("❌ Анонимные сообщения только для VIP!", reply_markup=back_kb())
        return
    await AnonForm.recipient.set()
    await send(
        "📨 <b>Анонимное сообщение</b>\n\nВведите username (без @) или ID получателя:",
        reply_markup=cancel_kb()
    )


@dp.callback_query_handler(lambda c: c.data.startswith("anon_to_"))
async def anon_to(call: types.CallbackQuery, state: FSMContext):
    if not is_vip(call.from_user.id):
        await call.answer("❌ Только для VIP!", show_alert=True)
        return
    recipient_id = int(call.data.split("_")[-1])
    if recipient_id == call.from_user.id:
        await call.answer("❌ Нельзя писать себе!", show_alert=True)
        return
    await AnonForm.message.set()
    await state.update_data(recipient_id=recipient_id)
    await call.message.answer("✍️ Введите текст анонимного сообщения:", reply_markup=cancel_kb())
    await call.answer()


@dp.message_handler(state=AnonForm.recipient)
async def anon_recipient(message: types.Message, state: FSMContext):
    text = message.text.strip().lstrip("@")
    recipient_id = None
    if text.isdigit():
        recipient_id = int(text)
        if not get_user(recipient_id):
            await message.answer("⚠️ Пользователь не найден. Введите снова:", reply_markup=cancel_kb())
            return
    else:
        conn = get_conn()
        row = conn.execute("SELECT user_id FROM users WHERE LOWER(username)=?", (text.lower(),)).fetchone()
        conn.close()
        if row:
            recipient_id = row["user_id"]
        else:
            await message.answer("⚠️ Пользователь не найден.", reply_markup=cancel_kb())
            return
    if recipient_id == message.from_user.id:
        await message.answer("❌ Нельзя писать себе!", reply_markup=cancel_kb())
        return
    await state.update_data(recipient_id=recipient_id)
    await AnonForm.message.set()
    await message.answer("✍️ Введите текст сообщения:", reply_markup=cancel_kb())


@dp.message_handler(state=AnonForm.message)
async def anon_send(message: types.Message, state: FSMContext):
    data = await state.get_data()
    recipient_id = data.get("recipient_id")
    await state.finish()
    if not recipient_id:
        await message.answer("❌ Ошибка.", reply_markup=back_kb())
        return
    try:
        await bot.send_message(
            recipient_id,
            f"📨 <b>Анонимное сообщение:</b>\n\n{message.text}\n\n<i>Отправитель скрыт</i>"
        )
        await message.answer("✅ Сообщение отправлено!", reply_markup=main_kb(True))
    except Exception:
        await message.answer("❌ Не удалось доставить сообщение.", reply_markup=back_kb())

# ============================================================
#  ADMIN КОМАНДЫ
# ============================================================

@dp.message_handler(commands=["addvip"])
async def cmd_addvip(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /addvip [user_id] [days=30]")
        return
    try:
        uid = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 30
    except ValueError:
        await message.answer("⚠️ Неверный формат.")
        return
    set_vip(uid, True, days=days)
    await message.answer(f"✅ VIP выдан пользователю {uid} на {days} дней.")
    try:
        await bot.send_message(uid, f"🎉 Вам выдан VIP на {days} дней! /start")
    except Exception:
        pass


@dp.message_handler(commands=["removevip"])
async def cmd_removevip(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /removevip [user_id]")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("⚠️ Неверный user_id.")
        return
    set_vip(uid, False)
    await message.answer(f"✅ VIP снят с {uid}.")


@dp.message_handler(commands=["stats"])
async def cmd_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    vips  = conn.execute("SELECT COUNT(*) FROM users WHERE is_vip=1").fetchone()[0]
    profs = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    pays  = conn.execute("SELECT COUNT(*), SUM(stars) FROM payments").fetchone()
    conn.close()
    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: {total}\n"
        f"{VIP_BADGE} VIP: {vips}\n"
        f"📋 Анкет: {profs}\n"
        f"💰 Покупок: {pays[0]}\n"
        f"⭐ Stars: {pays[1] or 0}"
    )


@dp.message_handler(commands=["userinfo"])
async def cmd_userinfo(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Использование: /userinfo [user_id]")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.answer("⚠️ Неверный user_id.")
        return
    user = get_user(uid)
    if not user:
        await message.answer(f"Пользователь {uid} не найден.")
        return
    vip_str = f"✅ VIP до {user['vip_until'][:10]}" if user["is_vip"] else "❌ Нет VIP"
    await message.answer(
        f"👤 <b>{uid}</b>\n"
        f"Имя: {user['full_name']}\n"
        f"Username: @{user['username']}\n"
        f"VIP: {vip_str}\n"
        f"Регистрация: {user['joined_at'][:10]}"
    )

# ============================================================
#  ЗАПУСК
# ============================================================

if __name__ == "__main__":
    init_db()
    logger.info("Бот запускается...")
    executor.start_polling(dp, skip_updates=True)