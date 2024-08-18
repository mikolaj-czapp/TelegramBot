import os.path
import logging

from matplotlib import pyplot as plt
from telegram import Update
from telegram.ext import ContextTypes
import telegram
import pandas as pd

from definitions import USERS_PATH, CLEANED_CHAT_HISTORY_PATH, REACTIONS_PATH, UPDATE_REQUIRED_PATH, EmojiType, ArgType, MessageType, MAX_USERNAME_LENGTH, TEMP_DIR
import src.stats.utils as utils
import src.core.utils as core_utils
from src.models.command_args import CommandArgs

pd.options.mode.chained_assignment = None
pd.set_option('display.max_columns', None)
pd.set_option('display.max_rows', None)
pd.set_option('display.width', 1000)
log = logging.getLogger(__name__)

negative_emojis = ['👎', '😢', '😭', '🤬', '🤡', '💩', '😫', '😩', '🥶', '🤨', '🧐', '🙃', '😒', '😠', '😣', '🗿']
MAX_INT = 24 * 365 * 20
MAX_NICKNAMES_NUM = 5


class ChatCommands:
    def __init__(self):
        self.users_df = utils.read_df(USERS_PATH)
        self.chat_df = utils.read_df(CLEANED_CHAT_HISTORY_PATH)
        self.reactions_df = utils.read_df(REACTIONS_PATH)

    def update(self):
        """If chat data was updated recentely, reload it."""
        if not os.path.isfile(UPDATE_REQUIRED_PATH):
            log.info(f"Update not required, {UPDATE_REQUIRED_PATH} doesn't exist.")
            return

        log.info('Reloading chat data due to the recent update.')
        self.chat_df = utils.read_df(CLEANED_CHAT_HISTORY_PATH)
        self.reactions_df = utils.read_df(REACTIONS_PATH)
        self.users_df = utils.read_df(USERS_PATH)

        utils.remove_file(UPDATE_REQUIRED_PATH)

    def preprocess_input(self, command_args, emoji_type: EmojiType = EmojiType.ALL):
        self.update()

        command_args = utils.parse_args(self.users_df, command_args)
        if command_args.error != '':
            return self.chat_df, self.reactions_df, command_args

        filtered_chat_df = utils.filter_by_time_df(self.chat_df, command_args)
        filtered_reactions_df = utils.filter_by_time_df(self.reactions_df, command_args)

        filtered_chat_df = utils.filter_emojis_by_emoji_type(filtered_chat_df, emoji_type, 'reaction_emojis')

        filtered_chat_df['reactions_num'] = filtered_chat_df['reaction_emojis'].apply(lambda x: len(x))
        filtered_chat_df = filtered_chat_df.sort_values(['reactions_num', 'timestamp'], ascending=[False, True])
        filtered_chat_df['timestamp'] = filtered_chat_df['timestamp'].dt.tz_convert('Europe/Warsaw')

        if command_args.user is not None:
            filtered_chat_df = filtered_chat_df[filtered_chat_df['final_username'] == command_args.user]

        log.info('Last message:')
        print(filtered_chat_df.tail(1))

        return filtered_chat_df, filtered_reactions_df, command_args

    async def summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        command_args = CommandArgs(args=context.args, expected_args=[ArgType.USER, ArgType.PERIOD], optional=[True, True])
        chat_df, reactions_df, command_args = self.preprocess_input(command_args, EmojiType.ALL)

        shifted_chat_df = utils.filter_by_shifted_time_df(self.chat_df, command_args)
        shifted_reactions_df = utils.filter_by_shifted_time_df(self.reactions_df, command_args)
        await self.handle_args_error(context, update, command_args)

        sad_reactions_df = utils.filter_emoji_by_emoji_type(reactions_df, EmojiType.NEGATIVE, 'emoji')
        text_only_chat_df = chat_df[chat_df['text'] != '']

        # Calculate message and reaction count
        images_num = len(chat_df[chat_df['message_type'] == 'image'])
        reactions_received_counts = reactions_df.groupby('reacted_to_username').size().reset_index(name='count').sort_values('count', ascending=False)
        reactions_given_counts = reactions_df.groupby('reacting_username').size().reset_index(name='count').sort_values('count', ascending=False)
        sad_reactions_received_counts = sad_reactions_df.groupby('reacted_to_username').size().reset_index(name='count').sort_values('count', ascending=False)
        sad_reactions_given_counts = sad_reactions_df.groupby('reacting_username').size().reset_index(name='count').sort_values('count', ascending=False)
        message_counts = chat_df.groupby('final_username').size().reset_index(name='count').sort_values('count', ascending=False)

        # Ratios
        fun_metric = self.calculate_fun_metric(chat_df, reactions_df)
        wholesome_metric = self.calculate_wholesome_metric(reactions_df)

        # Calculate message and reaction count changes
        message_count_change = 0 if shifted_chat_df.empty else round((len(chat_df) - len(shifted_chat_df)) / len(shifted_chat_df) * 100, 1)
        reaction_count_change = 0 if shifted_reactions_df.empty else round((len(reactions_df) - len(shifted_reactions_df)) / len(shifted_reactions_df) * 100, 1)
        message_count_change_text = f'+{message_count_change}%' if message_count_change > 0 else f'{message_count_change}%'
        reaction_count_change_text = f'+{reaction_count_change}%' if reaction_count_change > 0 else f'{reaction_count_change}%'

        # Create summary
        text = "*Chat summary*"
        text += f"({command_args.period_mode.value}):" if command_args.period_time == -1 else f" (past {command_args.period_time}h):"
        text += f"\n- *Total*: *{len(chat_df)} ({message_count_change_text})* messages, *{len(reactions_df)} ({reaction_count_change_text})* reactions and *{images_num}* images"
        text += "\n- *Top spammer*: " + ", ".join([f"{row['final_username']}: *{row['count']}*" for _, row in message_counts.head(3).iterrows()])
        text += "\n- *Fun meter*: " + ", ".join([f"{row['final_username']}: *{row['ratio']}*" for _, row in fun_metric.head(3).iterrows()])
        text += "\n- *Wholesome meter*: " + ", ".join([f"{row['reacting_username']}: *{row['ratio']}*" for _, row in wholesome_metric.head(3).iterrows()])
        text += "\n- *Unwholesome meter*: " + ", ".join(
            [f"{row['reacting_username']}: *{row['ratio']}*" for _, row in wholesome_metric.sort_values('ratio', ascending=True).head(3).iterrows()])
        text += "\n- *Most liked*: " + ", ".join([f"{row['reacted_to_username']}: *{row['count']}*" for _, row in reactions_received_counts.head(3).iterrows()])
        text += "\n- *Most liking*: " + ", ".join([f"{row['reacting_username']}: *{row['count']}*" for _, row in reactions_given_counts.head(3).iterrows()])
        text += "\n- *Most disliked*: " + ", ".join(
            [f"{row['reacted_to_username']}: *{row['count']}*" for _, row in sad_reactions_received_counts.head(3).iterrows()])
        text += "\n- *Most disliking*: " + ", ".join(
            [f"{row['reacting_username']}: *{row['count']}*" for _, row in sad_reactions_given_counts.head(3).iterrows()])
        text += "\n- *Top message*: " + ", ".join(
            [f"{row['final_username']} [{utils.dt_to_str(row['timestamp'])}]: {row['text']} [{''.join(row['reaction_emojis'])}]" for _, row in
             text_only_chat_df.head(1).iterrows()])

        text = utils.escape_special_characters(text)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode=telegram.constants.ParseMode.MARKDOWN_V2)

    async def messages_by_reactions(self, update: Update, context: ContextTypes.DEFAULT_TYPE, emoji_type: EmojiType = EmojiType.ALL):
        """Top or worst 5 messages from selected time period by number of reactions"""
        command_args = CommandArgs(args=context.args, expected_args=[ArgType.USER, ArgType.PERIOD], optional=[True, True])
        chat_df, reactions_df, command_args = self.preprocess_input(command_args, emoji_type)
        await self.handle_args_error(context, update, command_args)

        chat_df = chat_df[chat_df['text'] != '']
        label = utils.emoji_sentiment_to_label(emoji_type)
        text = self.generate_response_headline(command_args, label=f"{label} Cinco messages")

        for i, (index, row) in enumerate(chat_df.head(5).iterrows()):
            if row['reactions_num'] == 0:
                break
            text += f"\n{i + 1}. {row['final_username']}" if command_args.user is None else f"\n{i + 1}."
            text += f" [{utils.dt_to_str(row['timestamp'])}]:"
            text += f" {row['text']} [{''.join(row['reaction_emojis'])}]"

        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)

    async def media_by_reactions(self, update: Update, context: ContextTypes.DEFAULT_TYPE, message_type: MessageType, emoji_type: EmojiType = EmojiType.ALL):
        """Top or sad 5 media (images, videos, video notes, audio, gifs) from selected time period by number of reactions. Videos and video notes are merged into one."""
        command_args = CommandArgs(args=context.args, expected_args=[ArgType.USER, ArgType.PERIOD], optional=[True, True])
        chat_df, reactions_df, command_args = self.preprocess_input(command_args, emoji_type)
        await self.handle_args_error(context, update, command_args)

        label = utils.emoji_sentiment_to_label(emoji_type)
        text = self.generate_response_headline(command_args, label=f"{label} Cinco {message_type.value}")

        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)

        # merging video and video notes as one
        if message_type == MessageType.VIDEO:
            chat_df = chat_df[chat_df['message_type'].isin([MessageType.VIDEO.value, MessageType.VIDEO_NOTE.value])]
        else:
            chat_df = chat_df[chat_df['message_type'] == message_type.value]

        chat_df = chat_df.sort_values(['reactions_num', 'timestamp'], ascending=[False, True])

        for i, (index, row) in enumerate(chat_df.head(5).iterrows()):
            text = f"\n{i + 1}. {row['final_username']}" if command_args.user is None else f"\n{i + 1}."
            text += f" [{utils.dt_to_str(row['timestamp'])}]:"
            text += f" {row['text']} [{''.join(row['reaction_emojis'])}]"

            current_message_type = MessageType(row['message_type'])
            path = core_utils.message_id_to_path(str(row['message_id']), current_message_type)
            await self.send_message(update, context, current_message_type, path, text)

    async def send_message(self, update, context, message_type: MessageType, path, text):
        log.info(f'Sending message: {text} with media type: {message_type} and media path: {path}')
        match message_type:
            case MessageType.GIF:
                await context.bot.send_animation(chat_id=update.effective_chat.id, animation=path, caption=text)
            case MessageType.VIDEO:
                await context.bot.send_video(chat_id=update.effective_chat.id, video=path, caption=text)
            case MessageType.VIDEO_NOTE:
                await context.bot.send_video_note(chat_id=update.effective_chat.id, video_note=path)
                await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
            case MessageType.IMAGE:
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=path, caption=text)
            case MessageType.AUDIO:
                await context.bot.send_audio(chat_id=update.effective_chat.id, audio=path, caption=text)

    async def last_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display last n messages from chat history"""
        command_args = CommandArgs(args=context.args, expected_args=[ArgType.USER, ArgType.NUMBER], number_limit=100)
        chat_df, reactions_df, command_args = self.preprocess_input(command_args, EmojiType.ALL)
        chat_df = chat_df.sort_values(by='timestamp', ascending=False)
        await self.handle_args_error(context, update, command_args)

        text = f"Last {command_args.number} messages"
        text += f" by {command_args.user}" if command_args.user is not None else ":"

        for i, (index, row) in enumerate(chat_df.head(command_args.number).iterrows()):
            text += f"\n{i + 1}. {row['final_username']}" if command_args.user is None else f"\n{i + 1}."
            text += f" [{utils.dt_to_str(row['timestamp'])}]:"
            text += f" {row['text']} [{''.join(row['reaction_emojis'])}]"

        if len(text) > 4096:
            text = "Too much text to display. Lower the number of messages."

        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)

    async def display_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Display all users in chat"""

        text = "All ye who dost partake in this discourse:"
        for index, row in self.users_df.iterrows():
            nicknames = ', '.join(row['nicknames'])
            text += f"\n- *{row['final_username']}*: [{nicknames}]"

        text = utils.escape_special_characters(text)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode=telegram.constants.ParseMode.MARKDOWN_V2)

    async def add_nickname(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set username for all users in chat"""
        command_args = CommandArgs(args=context.args, expected_args=[ArgType.STRING], args_with_spaces=True, min_string_length=3, max_string_length=20, label='Nickname')
        command_args = utils.parse_args(self.users_df, command_args)
        await self.handle_args_error(context, update, command_args)

        user_id = update.effective_user.id
        current_nicknames = self.users_df.at[user_id, 'nicknames']
        current_username = self.users_df.at[user_id, 'final_username']
        new_nickname = command_args.string

        if len(current_nicknames) >= MAX_NICKNAMES_NUM:
            error = f'Nickname *{new_nickname}* not added for *{current_username}*. Nicknames limit is {MAX_NICKNAMES_NUM}.'
            error = utils.escape_special_characters(error)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=error, parse_mode=telegram.constants.ParseMode.MARKDOWN_V2)
            return

        self.users_df.at[user_id, 'nicknames'] = [new_nickname] if len(current_nicknames) == 0 else current_nicknames + [new_nickname]
        utils.save_df(self.users_df, USERS_PATH)

        current_nicknames = self.users_df.at[user_id, 'nicknames']
        text = f'Nickname *{new_nickname}* added for *{current_username}*. Resulting in the following nicknames: *{", ".join(current_nicknames)}*. It will get updated in a few minutes.'
        text = utils.escape_special_characters(text)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode=telegram.constants.ParseMode.MARKDOWN_V2)

    async def set_username(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set username for all users in chat"""
        command_args = CommandArgs(args=context.args, expected_args=[ArgType.STRING], args_with_spaces=True, min_string_length=3, max_string_length=MAX_USERNAME_LENGTH, label='Username')
        command_args = utils.parse_args(self.users_df, command_args)
        await self.handle_args_error(context, update, command_args)

        user_id = update.effective_user.id
        current_username = self.users_df.at[user_id, 'final_username']
        new_username = command_args.string
        is_valid, error = utils.check_new_username(self.users_df, new_username)

        if not is_valid:
            error = utils.escape_special_characters(error)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=error, parse_mode=telegram.constants.ParseMode.MARKDOWN_V2)
            return

        self.users_df.at[user_id, 'final_username'] = new_username
        utils.save_df(self.users_df, USERS_PATH)
        text = f'Username changed from: *{current_username}* to *{new_username}*. It will get updated in a few minutes.'
        text = utils.escape_special_characters(text)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode=telegram.constants.ParseMode.MARKDOWN_V2)

    async def fun(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        command_args = CommandArgs(args=context.args, expected_args=[ArgType.PERIOD])
        command_args = utils.parse_args(self.users_df, command_args)
        chat_df, reactions_df, command_args = self.preprocess_input(command_args, EmojiType.ALL)
        await self.handle_args_error(context, update, command_args)

        fun_ratios = self.calculate_fun_metric(chat_df, reactions_df)

        text = "``` Funmeter"
        text += f" for {command_args.user}" if command_args.user is not None else " "
        text += f" ({command_args.period_mode.value}):" if command_args.period_time == -1 else f" (past {command_args.period_time}h):"

        for i, (index, row) in enumerate(fun_ratios.iterrows()):
            text += f"\n{i + 1}.".ljust(4) + f" {row['final_username']}:".ljust(MAX_USERNAME_LENGTH + 5) + f"{row['ratio']}" if command_args.user is None else f"\n{i + 1}."

        text += "```"
        text = utils.escape_special_characters(text)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode=telegram.constants.ParseMode.MARKDOWN_V2)

    async def wholesome(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        command_args = CommandArgs(args=context.args, expected_args=[ArgType.PERIOD])
        command_args = utils.parse_args(self.users_df, command_args)
        chat_df, reactions_df, command_args = self.preprocess_input(command_args, EmojiType.ALL)
        await self.handle_args_error(context, update, command_args)

        wholesome_ratios = self.calculate_wholesome_metric(reactions_df)
        text = self.generate_response_headline(command_args, label='Wholesome meter')

        for i, (index, row) in enumerate(wholesome_ratios.iterrows()):
            text += f"\n{i + 1}.".ljust(4) + f" {row['reacting_username']}:".ljust(MAX_USERNAME_LENGTH + 5) + f"{row['ratio']}" if command_args.user is None else f"\n{i + 1}."

        text += "```"
        text = utils.escape_special_characters(text)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode=telegram.constants.ParseMode.MARKDOWN_V2)

    async def funchart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        command_args = CommandArgs(args=context.args, expected_args=[ArgType.USER, ArgType.PERIOD], optional=[True, True])
        chat_df, reactions_df, command_args = self.preprocess_input(command_args, EmojiType.ALL)
        await self.handle_args_error(context, update, command_args)

        text = self.generate_response_headline(command_args, label='Funmeter')

        users = [command_args.user]
        if command_args.user is None:
            users = self.users_df['final_username'].unique()

        fun_ratios = self.calculate_fun_metric_periodized(chat_df, reactions_df, frequency='D')
        path = self.generate_plot(users, fun_ratios, 'final_username', 'period', 'ratio', text, x_label='time', y_label='funratio')

        current_message_type = MessageType.IMAGE
        await self.send_message(update, context, current_message_type, path, text)

    async def spamchart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        command_args = CommandArgs(args=context.args, expected_args=[ArgType.USER, ArgType.PERIOD], optional=[True, True])
        chat_df, reactions_df, command_args = self.preprocess_input(command_args, EmojiType.ALL)
        await self.handle_args_error(context, update, command_args)

        text = self.generate_response_headline(command_args, label='Spamchart')

        users = [command_args.user]
        if command_args.user is None:
            users = self.users_df['final_username'].unique()

        chat_df['period'] = chat_df['timestamp'].dt.to_period('D')
        message_counts = chat_df.groupby(['period', 'final_username']).size().reset_index(name='message_count')
        path = self.generate_plot(users, message_counts, 'final_username', 'period', 'message_count', text, x_label='time', y_label='messages')

        current_message_type = MessageType.IMAGE
        await self.send_message(update, context, current_message_type, path, text)

    async def likechart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        command_args = CommandArgs(args=context.args, expected_args=[ArgType.USER, ArgType.PERIOD], optional=[True, True])
        chat_df, reactions_df, command_args = self.preprocess_input(command_args, EmojiType.ALL)
        await self.handle_args_error(context, update, command_args)

        text = self.generate_response_headline(command_args, label='Likechart')

        users = [command_args.user]
        if command_args.user is None:
            users = self.users_df['final_username'].unique()

        reactions_df['period'] = reactions_df['timestamp'].dt.to_period('D')
        reaction_counts = reactions_df.groupby(['period', 'reacted_to_username']).size().reset_index(name='reaction_count')
        path = self.generate_plot(users, reaction_counts, 'reacted_to_username', 'period', 'reaction_count', text, x_label='time', y_label='likes received')

        current_message_type = MessageType.IMAGE
        await self.send_message(update, context, current_message_type, path, text)

    def generate_response_headline(self, command_args, label):
        text = label
        text += f" for {command_args.user}" if command_args.user is not None else " "
        text += f" ({command_args.period_mode.value}):" if command_args.period_time == -1 else f" (past {command_args.period_time}h):"
        return text

    async def handle_args_error(self, context, update, command_args):
        if command_args.error != '':
            await context.bot.send_message(chat_id=update.effective_chat.id, text=command_args.error)
            return

    def calculate_fun_metric(self, chat_df, reactions_df):
        reactions_received_counts = reactions_df.groupby('reacted_to_username').size().reset_index(name='reaction_count')
        message_counts = chat_df.groupby('final_username').size().reset_index(name='message_count')
        message_counts = message_counts[message_counts['message_count'] > 0]

        merged_df = reactions_received_counts.merge(message_counts, left_on='reacted_to_username', right_on='final_username', how='inner').fillna(0)
        merged_df['ratio'] = (merged_df['reaction_count'] / merged_df['message_count']).round(2)
        fun_ratios = merged_df[['final_username', 'ratio']].copy().sort_values('ratio', ascending=False)

        return fun_ratios

    def calculate_wholesome_metric(self, reactions_df):
        reactions_received_counts = reactions_df.groupby('reacted_to_username').size().reset_index(name='reactions_received_count')
        reactions_given_counts = reactions_df.groupby('reacting_username').size().reset_index(name='reactions_given_count')
        reactions_received_counts = reactions_received_counts[reactions_received_counts['reactions_received_count'] > 0]

        merged_df = reactions_received_counts.merge(reactions_given_counts, left_on='reacted_to_username', right_on='reacting_username', how='inner').fillna(0)
        merged_df['ratio'] = (merged_df['reactions_given_count'] / merged_df['reactions_received_count']).round(2)
        wholesome_ratios = merged_df[['reacting_username', 'ratio']].copy().sort_values('ratio', ascending=False)

        return wholesome_ratios

    def calculate_fun_metric_periodized(self, chat_df, reactions_df, frequency='D'):
        chat_df['period'] = chat_df['timestamp'].dt.to_period(frequency)
        reactions_df['period'] = reactions_df['timestamp'].dt.to_period(frequency)

        message_counts = chat_df.groupby(['period', 'final_username']).size().reset_index(name='message_count')
        reaction_counts = reactions_df.groupby(['period', 'reacted_to_username']).size().reset_index(name='reaction_count')

        merged_df = pd.merge(message_counts, reaction_counts,
                             left_on=['period', 'final_username'],
                             right_on=['period', 'reacted_to_username'],
                             how='inner').fillna(0)

        merged_df['ratio'] = (merged_df['reaction_count'] / merged_df['message_count']).round(2)
        result_df = merged_df[['period', 'final_username', 'ratio']].sort_values(['period', 'ratio'], ascending=[True, False])

        return result_df

    def generate_plot(self, users, df, user_col, x_col, y_col, title, x_label='time', y_label='value'):
        filtered_df = df[df[user_col].isin(users)]
        filtered_df.set_index(x_col, inplace=True)
        filtered_df.index = filtered_df.index.to_timestamp()

        if len(users) == 1:
            filtered_df.plot(y=y_col, kind='line', figsize=(10, 5), label='value')

            # Generate shifted weekly plot
            weekly_data = filtered_df[y_col].resample('W').mean()
            weekly_data.index = weekly_data.index - pd.offsets.Day(3)
            weekly_data.plot(kind='line', figsize=(10, 5), label='weekly avg')

            # Generate shifted monthly plot, so it shows in the middle of the month on chart
            monthly_data = filtered_df[y_col].resample('M').mean()
            monthly_data.index = monthly_data.index - pd.offsets.Day(15)
            monthly_data.plot(kind='line', figsize=(10, 5), label='monthly avg')
        else:
            pivot_df = df.pivot_table(index=x_col, columns=user_col, values=y_col, fill_value=0)
            pivot_df.plot(kind='bar', stacked=True, figsize=(10, 5))

        plt.title(title)
        plt.xlabel(x_label)
        plt.ylabel(y_label)
        plt.tight_layout()
        plt.legend(loc='best')

        path = os.path.abspath(os.path.join(TEMP_DIR, utils.generate_random_filename('jpg')))
        utils.create_dir(TEMP_DIR)
        plt.savefig(path, bbox_inches='tight')

        return path
