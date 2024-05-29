import os.path
import time

from telegram import Update
from telegram.ext import ContextTypes
import logging
import datetime
from zoneinfo import ZoneInfo

from definitions import USERS_PATH, CLEANED_CHAT_HISTORY_PATH, REACTIONS_PATH, PeriodFilterMode, METADATA_PATH, CHAT_IMAGES_DIR_PATH
import src.stats.utils as stats_utils

log = logging.getLogger(__name__)


class ChatCommands:
    def __init__(self):
        self.users_df = stats_utils.read_df(USERS_PATH)
        self.chat_df = stats_utils.read_df(CLEANED_CHAT_HISTORY_PATH)
        self.reactions_df = stats_utils.read_df(REACTIONS_PATH)

    def update(self):
        """If chat data was updated recentely, reload it."""
        metadata = stats_utils.load_metadata()
        modification_time = os.path.getmtime(METADATA_PATH)
        print(f"Last modification time: {time.ctime(modification_time)}")
        log.info(f"{time.ctime(modification_time)}: {metadata}")
        if not metadata['new_latest_data']:
            return

        self.chat_df = stats_utils.read_df(CLEANED_CHAT_HISTORY_PATH)
        self.reactions_df = stats_utils.read_df(REACTIONS_PATH)
        metadata['new_latest_data'] = False
        stats_utils.save_metadata(metadata)
        log.info('Reloading chat data due to the recent update.')

    async def summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_df, reactions_df, mode, user, error = self.preprocess_input(context.args)

        text = f"""Chat summary ({mode.value}):
        - {len(chat_df)} messages, {len(reactions_df)} reactions in total
                """

        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)

    async def top_messages_by_reactions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Top 5 messages from selected time period by number of reactions"""
        chat_df, reactions_df, mode, user, error = self.preprocess_input(context.args)
        if error != '':
            await context.bot.send_message(chat_id=update.effective_chat.id, text=error)
            return

        chat_df = chat_df[chat_df['text'] != '']
        if user is not None:
            chat_df = chat_df[chat_df['final_username'] == user]
            text = f"Top Cinco messages by {user} ({mode.value}):"
        else:
            text = f"Top Cinco messages ({mode.value}):"

        chat_df['reactions_num'] = chat_df['all_emojis'].apply(lambda x: len(x))
        chat_df = chat_df.sort_values('reactions_num', ascending=False)

        for i, (index, row) in enumerate(chat_df.head(5).iterrows()):
            text += f"\n{i + 1}. {row['final_username']}: {row['text']} [{''.join(row['all_emojis'])}]"

        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)

    async def top_memes_by_reactions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Top 5 memes (images) from selected time period by number of reactions"""
        chat_df, reactions_df, mode, user, error = self.preprocess_input(context.args)
        if error != '':
            await context.bot.send_message(chat_id=update.effective_chat.id, text=error)
            return

        if user is not None:
            chat_df = chat_df[chat_df['final_username'] == user]
            text = f"Top Cinco memes by {user} ({mode.value}):"
        else:
            text = f"Top Cinco memes ({mode.value}):"

        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)

        chat_df = chat_df[chat_df['photo']==True]
        chat_df['reactions_num'] = chat_df['all_emojis'].apply(lambda x: len(x))
        chat_df = chat_df.sort_values('reactions_num', ascending=False)

        for i, (index, row) in enumerate(chat_df.head(5).iterrows()):
            img_path = os.path.join(CHAT_IMAGES_DIR_PATH, f'{str(row['message_id'])}.jpg')
            caption = f"\n{i + 1}. {row['final_username']}: {row['text']} [{''.join(row['all_emojis'])}]"
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=img_path, caption=caption)

    def parse_args(self, args: list[str]) -> (PeriodFilterMode, str, str):
        error = ''
        mode = PeriodFilterMode.TOTAL
        user = None

        try:
            mode = PeriodFilterMode(args[0]) if args else PeriodFilterMode.TOTAL
        except ValueError:
            error = f"There is no such command mode as {args[0]}."
            log.error(error)
        user, error = self.parse_user(args)

        return mode, user, error

    def parse_user(self, args: list[str]) -> (str, str):
        if len(args) <= 1:
            return None, ''

        user = None
        error = ''
        user_str = ' '.join(args[1:]).replace('@', '')

        exact_matching_users = self.users_df[self.users_df['final_username'].str.lower() == user_str.lower()]
        partially_matching_users = self.users_df[self.users_df['final_username'].str.contains(user_str, case=False)]

        if not exact_matching_users.empty:
            user = exact_matching_users.iloc[0]['final_username']
        elif len(args[1]) >= 3 and not partially_matching_users.empty:
            user = partially_matching_users.iloc[0]['final_username']
        else:
            error = f"User {args[1]} doesn't exist and cannot hurt you. Existing users are: {self.users_df['final_username'].tolist()}"
            log.error(error)
        return user, error

    def get_today_midnight_dt(self):
        return datetime.datetime.now().replace(tzinfo=ZoneInfo('Europe/Warsaw')).replace(hour=0, minute=0, second=0, microsecond=0)

    def filter_df(self, df, mode):
        today_dt = self.get_today_midnight_dt()
        print('mode', mode, today_dt)
        match mode:
            case PeriodFilterMode.TODAY:
                return df[df['timestamp'] >= today_dt]
            case PeriodFilterMode.YESTERDAY:
                return df[(df['timestamp'] >= today_dt - datetime.timedelta(days=1)) & (df['timestamp'] < today_dt)]
            case PeriodFilterMode.WEEK:
                return df[df['timestamp'] >= today_dt - datetime.timedelta(days=7)]
            case PeriodFilterMode.MONTH:
                return df[df['timestamp'] >= today_dt - datetime.timedelta(days=30)]
            case PeriodFilterMode.YEAR:
                return df[df['timestamp'] >= today_dt - datetime.timedelta(days=365)]
            case PeriodFilterMode.TOTAL:
                return df.copy(deep=True)

    def preprocess_input(self, args):
        self.update()

        mode, user, error = self.parse_args(args)
        filtered_chat_df = self.filter_df(self.chat_df, mode)
        filtered_reactions_df = self.filter_df(self.reactions_df, mode)
        return filtered_chat_df, filtered_reactions_df, mode, user, error

    def parse_int(self, num_str):
        try:
            return int(num_str)
        except ValueError:
            log.error(f"{num_str} is not a number.")
            return None
