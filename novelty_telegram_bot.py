#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tempfile
import os
from cfg.defines import PID_FILE
from cfg.external import get_bot_token, get_servers
from pkg.constants.bot_actions import \
    BOT_ACTIONS_MAIN, \
    BOT_ACTION_SET_CREDENTIALS, \
    BOT_ACTION_RELOAD_METADATA, \
    BOT_ACTION_UNSET_RESCINDING_REASON
from pkg.constants.bot_messages import BOT_MESSAGE_NOT_AUTHORIZED, BOT_MESSAGE_EXCEPTION
from pkg.constants.emoji import EMOJI_WHITE_HEAVY_CHECK_MARK, EMOJI_CROSS_MARK
from pkg.utils.console import write_stdout
from pkg.utils.decorators.handle_exceptions import handle_exceptions
from pkg.utils.modules import import_nonstandart_module
from pkg.connectors.novelty import Novelty
from pkg.connectors.oracle import Oracle, get_connection_string
from pkg.sql.queries import GET_NONEXISTANT_POLICIES, UPDATE_RESCINDING_REASON_TO_NULL, SET_USER_ID

telebot = import_nonstandart_module('telebot')
pid = import_nonstandart_module('pid')


SERVERS = get_servers()
bot = telebot.TeleBot(get_bot_token())
sessions = {}


def get_session(user_id):
    global sessions
    return sessions.get(user_id)


def set_last_server(session, server):
    if session:
        session['last_server'] = server


def get_last_server(session):
    if session:
        return session['last_server']


def set_last_ticket(session, ticket):
    if session:
        session['last_ticket'] = ticket


def get_last_ticket(session):
    if session:
        return session['last_ticket']


def not_authorized(chat_id):
    bot.send_message(chat_id, BOT_MESSAGE_NOT_AUTHORIZED)
    send_menu_main(chat_id)


def reload_metadata(session, chat_id):
    server = get_last_server(session)
    if server:
        try:
            bot.send_message(chat_id, 'Начинаю перезагрузку данных на сервере "%s"...' % (server['name']))
            for subdomain in server['subdomains']:
                with Novelty(subdomain,
                             session['login'],
                             session['password'],
                             raise_errors=True,
                             use_local_addr=True) as ws:
                    if ws.is_authentificated():
                        ws.reload()
            bot.send_message(chat_id,
                             EMOJI_WHITE_HEAVY_CHECK_MARK +
                             ' Метаданные на сервере "%s" успешно перезагружены' % (server['name']))
        except Exception as e:
            bot.send_message(chat_id, BOT_MESSAGE_EXCEPTION + str(e))


def unset_rescinding_reason(chat_id):
    msg = bot.send_message(chat_id,
                           'Введи номер тикета, согласно которому необходимо снять причину расторжения с полисов:')
    bot.register_next_step_handler(msg, handler_set_ticket)


@handle_exceptions
def handler_set_ticket(message):
    if len(message.text) != 16:
        msg = bot.send_message(message.chat.id, EMOJI_CROSS_MARK + ' Это не номер тикета. Давай ещё раз:')
        bot.register_next_step_handler(msg, handler_set_ticket)
    else:
        session = get_session(message.from_user.id)
        if session:
            set_last_ticket(session, message.text)
            msg = bot.send_message(message.chat.id, 'Теперь укажи номера полисов, каждый с новой строчки:')
            bot.register_next_step_handler(msg, handler_set_policies)
        else:
            not_authorized(message.chat.id)


@handle_exceptions
def handler_set_policies(message):
    session = get_session(message.from_user.id)
    if session:
        server = get_last_server(session)
        if server:
            connection_string = get_connection_string(server)
            try:
                bot.send_message(message.chat.id, 'Начинаю работу с базой данных...')
                policies = message.text.split('\n')
                policies_str = ', '.join('\'%s\'' % p for p in policies)
                policies_tbl = u' union all '.join('select \'%s\' as num from dual' % p for p in policies)

                with Oracle(connection_string) as db:
                    def get_nonexistant_policies(cur):
                        sql = GET_NONEXISTANT_POLICIES % policies_tbl
                        cur.execute(sql.encode('utf-8'))
                        for row in cur:
                            return row[0], row[1]

                    def erase_policies_rescinding_reason(cur):
                        sql = SET_USER_ID % ('Updated by NoveltyBot; Ticket# %s' % get_last_ticket(session))
                        cur.execute(sql, user_name=session['login'])
                        sql = UPDATE_RESCINDING_REASON_TO_NULL % policies_str
                        cur.execute(sql.encode('utf-8'))
                        return cur.rowcount

                    p_list, p_count = db.execute(get_nonexistant_policies)
                    if p_count:
                        bot.send_message(message.chat.id,
                                         EMOJI_CROSS_MARK +
                                         ' Некоторые полисы (%s) не найдены: %s' % (p_count, p_list))

                    p_count = db.execute(erase_policies_rescinding_reason)
                    bot.send_message(message.chat.id, EMOJI_WHITE_HEAVY_CHECK_MARK + ' Обновлено полисов: %s' % p_count)

            except Exception as e:
                bot.send_message(message.chat.id, BOT_MESSAGE_EXCEPTION + str(e))
        send_menu_main(message.chat.id)
    else:
        not_authorized(message.chat.id)


@handle_exceptions
def send_menu_main(chat_id):
    keyboard = telebot.types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(*[telebot.types.InlineKeyboardButton(text=name,
                                                      callback_data=data) for (data, name) in BOT_ACTIONS_MAIN])
    bot.send_message(chat_id, 'Что хочешь сделать?', reply_markup=keyboard)


@handle_exceptions
def send_menu_select_server(chat_id, action_id):
    keyboard = telebot.types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        *[telebot.types.InlineKeyboardButton(text=server['name'],
                                             callback_data='%s:%s' % (server['id'], action_id)) for server in SERVERS])
    bot.send_message(chat_id, 'Выбери сервер:', reply_markup=keyboard)


@bot.message_handler(commands=['start'])
def handler_start(message):
    send_menu_main(message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data in [BOT_ACTION_SET_CREDENTIALS,
                                                      BOT_ACTION_RELOAD_METADATA,
                                                      BOT_ACTION_UNSET_RESCINDING_REASON])
@handle_exceptions
def handler_main_menu_commands(c):
    if c.data == BOT_ACTION_SET_CREDENTIALS:
        msg = bot.send_message(c.message.chat.id, 'На первой строке введи логин, а на второй пароль:')
        bot.register_next_step_handler(msg, handler_set_credentials)
    else:
        send_menu_select_server(c.message.chat.id, c.data)


@bot.callback_query_handler(func=lambda c: c.data[:9] == 'server_id')
@handle_exceptions
def handler_select_server(c):
    session = get_session(c.from_user.id)
    if session:
        server_id = c.data[:12]
        action_id = c.data[13:]
        servers = list(filter(lambda s: s['id'] == server_id, SERVERS))
        if servers:
            server = servers[0]
            set_last_server(session, server)
            if action_id == BOT_ACTION_RELOAD_METADATA:
                reload_metadata(session, c.message.chat.id)
                send_menu_main(c.message.chat.id)
            elif action_id == BOT_ACTION_UNSET_RESCINDING_REASON:
                unset_rescinding_reason(c.message.chat.id)
    else:
        not_authorized(c.message.chat.id)


@handle_exceptions
def handler_set_credentials(message):
    global sessions
    lines = message.text.split('\n')
    if len(lines) != 2:
        msg = bot.send_message(message.chat.id,
                               EMOJI_CROSS_MARK +
                               ' Я же говорю, на первой строчке логин, а на второй пароль. Давай ещё раз:')
        bot.register_next_step_handler(msg, handler_set_credentials)
    else:
        user_id = message.from_user.id
        sessions[user_id] = {
            'telegram_id': user_id,
            'login': lines[0],
            'password': lines[1]
        }
        bot.send_message(message.chat.id, EMOJI_WHITE_HEAVY_CHECK_MARK + ' Отлично, данные для авторизации приняты!')
        send_menu_main(message.chat.id)


if __name__ == '__main__':
    os.environ['NLS_LANG'] = 'Russian.AL32UTF8'
    with pid.PidFile(PID_FILE, piddir=tempfile.gettempdir()):
        write_stdout('Бот начал работу...\n')
        bot.polling(none_stop=True)
