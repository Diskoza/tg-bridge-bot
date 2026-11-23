from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove


BTN_NEW = "🔗 Создать код"
BTN_JOIN = "🔑 Подключиться по коду"
BTN_STATUS = "ℹ️ Статус"
BTN_LEAVE = "❌ Отключиться"
BTN_CANCEL = "Отмена"


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_JOIN)],
            [KeyboardButton(text=BTN_STATUS), KeyboardButton(text=BTN_LEAVE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие",
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Введите код",
    )


def hide_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove(remove_keyboard=True)
