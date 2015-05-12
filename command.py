import asyncio
import random

from db_object import DBItem
from core import Message
from media import YoutubeInfo

from actions import (
	ReplyAction, 
	)

class Command(DBItem):
	DB_TAG = 'database_command'
	TYPE = 'base'
	COMMAND = ''
	COMMAND_LOCATION = 'any'

	def __init__(self, message_or_db):
		super(Command, self).__init__(message_or_db)
		if isinstance(message_or_db, Message):
			self.parent_id = message_or_db.uid
		else:
			self.uid = message_or_db['uid']

	def get_actions(self):
		return []

	@classmethod
	def help_string(cls):
		return None

	def to_db_dict(self):
		base = super(Command, self).to_db_dict()
		base['parent_id'] = self.parent_id
		return base

	@classmethod
	def is_this(cls, message):
		if cls.COMMAND_LOCATION == 'any':
			return cls.COMMAND in message.lower()
		elif cls.COMMAND_LOCATION == 'start':
			return message.lower().find(cls.COMMAND) == 0 

	def __repr__(self):
		return str(self)

	def __str__(self):
		return '{}({})'.format(self.DB_TAG, self.user)


class QueueCommand(Command):
	DB_TAG = 'database_command_queue'
	TYPE = 'queue'
	COMMAND = '!queue'
	COMMAND_LOCATION = 'start'

	def __init__(self, message_or_db):
		super(QueueCommand, self).__init__(message_or_db)
		if isinstance(message_or_db, Message):
			self.youtube_info = YoutubeInfo.create_from_message(message_or_db)
		else:
			self.youtube_info = YoutubeInfo.from_db_dict(message_or_db)

	def _generate_confirmation_text(self):
		return 'Added to queue: {}'.format(self.youtube_info.display())

	def get_actions(self):
		if not self.youtube_info.prepared:
			return [ReplyAction("Sorry, I could not find a youtube url in your command. I can't understand anything other than 'youtube.com/watch?v=...' links (including youtu.be).", self.parent_id)]
		else:
			return []

	def to_db_dict(self):
		return self.youtube_info.to_db_dict(super(QueueCommand, self).to_db_dict())

	@asyncio.coroutine
	def prepare(self):
		yield from self.youtube_info.prepare()

	def is_prepared(self):
		return self.youtube_info.prepared

	@classmethod
	def help_string(cls):
		return "{} <youtube url> : Add a song to the queue. Does not support any link shorterners (youtube's included). Songs will be played in order.".format(cls.COMMAND)

	def __str__(self):
		return '[QueueCommand] {}'.format(self.youtube_info)

class ClearQueueCommand(Command):
	DB_TAG = 'database_command_clearqueue'
	COMMAND = '!clearqueue'
	COMMAND_LOCATION = 'start'

class ListQueueCommand(Command):
	DB_TAG = 'database_command_listqueue'
	COMMAND = '!list'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return []

	@classmethod
	def help_string(cls):
		return "{}: List the songs currently in the queue.".format(cls.COMMAND)

class DumpQueueCommand(Command):
	DB_TAG = 'database_command_dumpqueue'
	COMMAND = '!dumpqueue'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return []

	@classmethod
	def help_string(cls):
		return "{}: List all queued songs with youtube URL included and then DELETES the queue. Useful for shutting down the bot.".format(cls.COMMAND)

class SkipCommand(Command):
	DB_TAG = 'database_command_skip'
	COMMAND = '!skip'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return []

	@classmethod
	def help_string(cls):
		return "{}: Skip a song. Please note: one song will be skipped per {} command! Use carefully.".format(cls.COMMAND, cls.COMMAND)

class DramaticSkipCommand(Command):
	DB_TAG = 'database_command_dramatic_skip'
	COMMAND = '!dramaticskip'
	COMMAND_LOCATION = 'start'

class TestSkipCommand(Command):
	DB_TAG = 'database_command_testskip'
	COMMAND = '!testskip'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return []

class NeonLightShowCommand(Command):
	DB_TAG = 'database_command_neonlightshow'
	COMMAND = '!neonlightshow'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		images = [
		'http://i.imgur.com/eBZO67G.gif', 'http://i.imgur.com/0bprD6k.gif', 'http://i.imgur.com/van2j15.gif', 'http://i.imgur.com/sYjX7Qv.gif',
		'http://i.imgur.com/sNm4j9n.gif', 'http://i.imgur.com/uXSlR5b.gif', 'http://i.imgur.com/hGSXbsa.gif', 'http://i.imgur.com/UlpqRbK.gif',
		'http://i.imgur.com/Wmm7EZg.gif', 'http://i.imgur.com/QdYSbbA.gif', 'http://i.imgur.com/Zy5heqF.gif', 'http://i.imgur.com/H4vsVkh.gif'
		]
		return [ReplyAction(random.choice(images), str(self.parent_id))]


class HelpCommand(Command):
	DB_TAG = 'database_command_help'
	COMMAND = '!help'
	COMMAND_LOCATION = 'start'

	def set_commands(self, commands):
		self.commands = commands

	def get_help_lines(self):
		lines = ['Commands with help text:']
		for command in self.commands:
			if hasattr(command, 'help_string') and command.help_string():
				lines.append(command.help_string())
		if len(lines):

			return '\n'.join(lines)
		else:
			return None

	def get_actions(self):
		if self.get_help_lines():
			return [ReplyAction(self.get_help_lines(), str(self.parent_id))]


DBItem.SUBCLASSES.update({
		QueueCommand.DB_TAG: QueueCommand,
		SkipCommand.DB_TAG: SkipCommand,
		ClearQueueCommand.DB_TAG: ClearQueueCommand,
		ListQueueCommand.DB_TAG: ListQueueCommand,
		TestSkipCommand.DB_TAG: TestSkipCommand,
		NeonLightShowCommand.DB_TAG: NeonLightShowCommand,
		HelpCommand.DB_TAG: HelpCommand,
		DumpQueueCommand.DB_TAG: DumpQueueCommand,
	})