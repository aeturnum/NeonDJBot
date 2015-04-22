# -*- coding: utf-8 -*-
import random
import json
from time import time, localtime, strftime
from datetime import timedelta, datetime, tzinfo
import asyncio
import sys
import websockets
import aiohttp
import re
import pprint
import traceback
import requests
from urllib.parse import parse_qs, urlparse
from tinydb import TinyDB, where

class Message(object):
	def __init__(self, data, past=False):
		self.data = data
		self.past = past

		if 'time' in self.data:
			self.timestamp = int(self.data['time'])
			self.uid = self.data['id']

class Packet(object):
	def __init__(self, packet):
		packet = json.loads(packet)
		self.timestamp = int(time())
		if 'type' in packet:
			self.type = packet['type']
		else:
			self.type = ''
		if 'data' in packet:
			self.data = packet['data']
			if 'time' in self.data:
				self.timestamp = int(self.data['time'])
		else:
			self.data = packet

	def messages(self):
		if self.type == 'send-event' or self.type == 'send-reply':
			return [Message(self.data)]
		elif self.type == 'snapshot-event':
			return [Message(m, past=True) for m in self.data['log']]

		return []

	def __str__(self):
		return '[{}]{}'.format(self.type, self.data)

class User(object):
	@classmethod
	def create_from_message(cls, message):
		sender = message.data['sender']
		return User(sender['id'].split(':')[1], sender['name'])	

	@staticmethod
	def from_db_dict(db_dict):
		return User(db_dict['user']['id'], db_dict['user']['name'])
	
	def to_db_dict(self, db_dict):
		db_dict['user'] = {
			'id': self.user_id,
			'name': self.name
		}

		return db_dict

	def __init__(self, user_id, name):
		super(User, self).__init__()
		self.user_id = user_id
		self.name = name

	def display(self):
		return self.name

	def __str__(self):
		return self.name

class YoutubeInfo(object):

	INFO_URL = 'http://gdata.youtube.com/feeds/api/videos/{}?alt=json'
	REGEX = r'((https?://)?youtube\S+)'

	@classmethod
	def create_from_message(cls, message):
		# returns list of tuples
		urls = re.findall(cls.REGEX, message.data['content'], flags=re.I)
		return YoutubeInfo(urls[0][0])	

	@classmethod
	def from_db_dict(cls, db_dict):
		yt = db_dict['youtube_info']
		self = YoutubeInfo(yt['url'])
		self.set_data(yt['youtube_id'],
			 yt['title'], yt['sub_title'],
			 yt['thumbnails'], yt['duration'])
		return self

	def to_db_dict(self, db_dict):
		db_dict['youtube_info'] = {
			'url': self.url,
			'youtube_id': self.youtube_id,
			'title': self.title,
			'sub_title': self.sub_title,
			'thumbnails': self.thumbnails,
			'duration': self.duration,
		}
		return db_dict

	def __init__(self, url):
		super(YoutubeInfo, self).__init__()
		self.url = url if url.find('http') == 0 else 'https://' + url
		self.prepared = False
		
	def set_data(self,youtube_id, title, sub_title, thumbnails, duration):
		self.youtube_id = youtube_id
		self.title = title
		self.sub_title = sub_title
		self.thumbnails = thumbnails
		self.duration = duration
		self.prepared = True

	@asyncio.coroutine
	def prepare(self):
		query = urlparse(self.url).query
		youtube_id = parse_qs(query)['v'][0]
		url = self.INFO_URL.format(youtube_id)
		resp = yield from aiohttp.request('GET', url)
		youtube_info = yield from resp.json()
		youtube_info = youtube_info['entry']

		self.set_data(youtube_id,
			youtube_info['title']['$t'], youtube_info['content']['$t'],
			youtube_info['media$group']['media$thumbnail'], youtube_info['media$group']['yt$duration'])
			

	def time_seconds(self):
		if not self.prepared:
			return 0
		assert(len(self.duration.keys()) == 1)
		return int(self.duration['seconds'])

	def time_string(self):
		return str(timedelta(seconds=self.time_seconds()))

	def select_thumbnail(self):
		if not self.prepared:
			return ''
		return self.thumbnails[0]['url']

	def __eq__(self, other):
		if hasattr(other, 'youtube_id'):
			return self.youtube_id == other.youtube_id
		return False

	def display(self):
		return '"{}"'.format(self.title)

	def __repr__(self):
		return str(self)

	def __str__(self):
		if not self.prepared:
			return self.url
		else:
			return '{}|{} [{}]'.format(self.youtube_id, self.title, self.time_string())


class DBItem(object):
	DB_TAG = "database_"

	SUBCLASSES = {}

	def __init__(self, message_or_db):
		self.backlog = False
		if isinstance(message_or_db, Message):
			self.timestamp = message_or_db.timestamp
			self.user = User.create_from_message(message_or_db)
			self.uid = message_or_db.uid
			self.backlog = message_or_db.past
		else:
			self.timestamp = message_or_db['timestamp']
			self.uid = message_or_db['uid']
			self.user = User.from_db_dict(message_or_db)

	def prepare(self):
		return

	def is_prepared(self):
		return True

	def to_db_dict(self):
		db_dict = {
			'type': self.DB_TAG,
			'uid': self.uid,
			'timestamp': self.timestamp,
		}
		return self.user.to_db_dict(db_dict)

	def __lt__(self, other):
		if hasattr(other, 'timestamp'):
			return self.timestamp < other.timestamp 

	def __eq__(self, other):
		if hasattr(other, 'uid'):
			return self.uid == other.uid

	@staticmethod
	def create_object_from_db_entry(db_dict):
		if db_dict['type'] in DBItem.SUBCLASSES:
			return DBItem.SUBCLASSES[db_dict['type']](db_dict)


class Action(object):
	def __init__(self):
		self.ws = None
		self.timestamp = int(time())

	def packet_to_send(self, db):
		return None

	def get_coroutine(self, db, id_generator, action_queue):
		@asyncio.coroutine
		def task():
			message = self.packet_to_send(db)
			if message:
				# set id
				message['id'] = str(next(id_generator))
				try:
					yield from self.ws.send(json.dumps(message))
				except InvalidState:
					# websocket closed
					yield from action_queue.put(self)
				except TypeError:
					print('{} - Failed to send: {}'.format(self, json.dumps(message)))

		return task


class PacketAction(Action):

	@staticmethod
	def _wrap(p_type, data):
		return {"type":p_type,"data":data}

	@classmethod
	def ping_packet(cls, timestamp):
		return cls._wrap('ping-reply', {"time":timestamp})

	@classmethod
	def send_packet(cls, text, parent_message):
		print("Send: {}".format(text))
		#return cls._wrap('send', {"content":text,"parent":parent_message})

	@classmethod
	def nick_packet(cls, nick):
		print("Nick: {}".format(nick))
		#return cls._wrap('nick', {"name":nick})

class PingAction(PacketAction):
	def __init__(self):
		super(PingAction, self).__init__()

	def packet_to_send(self, db):
		return self.ping_packet(self.timestamp)

class ReplyAction(PacketAction):
	def __init__(self, text, reply_to = ''):
		super(ReplyAction, self).__init__()
		self.text = text
		self.reply_to = reply_to

	def packet_to_send(self, db):
		return self.send_packet(self.text, self.reply_to)


class SetNickAction(PacketAction):
	def __init__(self, nick):
		super(SetNickAction, self).__init__()
		self.nick = nick

	def packet_to_send(self, db):
		return self.nick_packet(self.nick)


class SongAction(PacketAction):
	def currently_playing(self, db):
		events = db.search(where('type') == PlayEvent.DB_TAG)
		if events:
			events = sorted(events, key=lambda x: x['timestamp'])
			if len(events):
				event = DBItem.create_object_from_db_entry(events[-1]) 
				if event.is_active():
					return event
		return None

	def skip_count(self, db, seconds):
		now = int(time())
		return db.search((where('type') == SkipCommand.DB_TAG) & (where('timestamp') > (now - seconds)) ) 

class QueuedNotificationAction(SongAction):
	def __init__(self, queue, currently_playing, song_info, reply_to = ''):
		super(QueuedNotificationAction, self).__init__()
		self.song_info = song_info
		self.reply_to = reply_to
		self.queue = queue
		self.current_song = currently_playing

	def packet_to_send(self, db):
		queue = self.queue
		current_song = self.current_song

		wait = timedelta(seconds=0)
		song_string = '{} queued'.format(self.song_info.display())
		wait_str = 'now.'
		if current_song:
			wait = timedelta(seconds=current_song.remaining_duration())
			wait_str = 'in {}'.format(str(wait))
		text = '{} first and will be played {}'.format(song_string, wait_str)
		if queue:
			position = 1
			for task in queue:
				if self.reply_to in task.uid:
					break
				wait = wait + timedelta(seconds=task.youtube_info.time_seconds())
				position += 1

			if position > 1:
				text = '{} at position [{}] and will be played in {}'.format(song_string, position, str(wait))


		return self.send_packet(text, self.reply_to)

class PlayQueuedSongAction(SongAction):
	def __init__(self, reply_to=''):
		super(PlayQueuedSongAction, self).__init__()
		self.reply_to = reply_to

	@classmethod
	def song_message(cls, play_song_task):
		return '{} (from {})'.format(
			play_song_task.youtube_info.display(),
			play_song_task.user.display())

	@classmethod
	def play_message(cls, song_to_play, next_song=None):
		next = 'Nothing'
		if next_song:
			next = cls.song_message(next_song)
		return '{}\n!play {}\nNext: {}'.format(
			cls.song_message(song_to_play),
			song_to_play.youtube_info.url,
			next)

	def get_song_to_play(self, db):
		return None, None

	def packet_to_send(self, db):
		(song_to_play, next_song) = self.get_song_to_play(db)
		if song_to_play:
			return self.send_packet(
				self.play_message(song_to_play, next_song),
				self.reply_to)
		return None

class PlayOneSongAction(PlayQueuedSongAction):
	def __init__(self, song_one=None, song_two=None, reply_to=''):
		super(PlayOneSongAction, self).__init__(reply_to)
		self.song_one = song_one
		self.song_two = song_two

	def get_song_to_play(self, db):
		return self.song_one, self.song_two

class DumpQueue(SongAction):
	def __init__(self, reply_to=''):
		super(DumpQueue, self).__init__()
		self.reply_to = reply_to

	def packet_to_send(self, db):
		song_queue = self.queued_songs(db)
		message = 'Nothing Queued'
		if song_queue:
			strings = []
			for song in song_queue:
				strings.append('{} added by @{}\n command(copy & paste w/ !): play {}'.format(str(song.youtube_info.display()), song.user.name, str(song.youtube_info.url)))

			return self.send_packet('\n'.join(strings), self.reply_to)

		return self.send_packet(message, self.reply_to)

class ListQueueAction(SongAction):
	def __init__(self, queue, current_song, reply_to):
		super(ListQueueAction, self).__init__()
		self.reply_to = reply_to
		self.queue = queue
		self.current_song = current_song

	def packet_to_send(self, db):
		song_queue = self.queue
		current_song = self.current_song
		message = 'Nothing Queued'
		if song_queue:
			place = 1
			total_duration = timedelta(seconds=current_song.remaining_duration())
			max_duration_width = max(len(str(total_duration)),len('wait time'))
			max_position_width = 1
			max_song_title_width = len('song title')
			for song in song_queue:
				max_position_width = max(len(str(place)), max_position_width)
				place += 1

			place = 1
			time_and_pos_formatting_string = '[{{:^{}}}][{{:^{}}}]'
			time_format = time_and_pos_formatting_string.format(
					max_position_width, max_duration_width,
				)
			strings = [time_format.format('#', 'wait time')]
			for song in song_queue:
				song_string = time_format.format(place, str(total_duration))
				song_string += ' {} added by [{}]'.format(song.youtube_info.display(), song.user.name)
				strings.append(song_string)

				place += 1
				total_duration = total_duration + timedelta(seconds=song.youtube_info.time_seconds())

			return self.send_packet('\n'.join(strings), self.reply_to)

		return self.send_packet(message, self.reply_to)


class Event(DBItem):
	DB_TAG = 'database_event'
	EVENT_TEXT = ''

	def remaining_duration(self):
		now = datetime.utcnow()
		event_time = datetime.utcfromtimestamp(self.timestamp)
		end_time = event_time + self.get_duration()
		if now > end_time:
			return 0
		else:
			return (end_time - now).seconds

	def is_active(self):
		now = datetime.utcnow()
		event_time = datetime.utcfromtimestamp(self.timestamp)
		end_time = event_time + self.get_duration()
		return now < end_time

	def get_duration(self):
		return timedelta(seconds=0)

	def get_duration_int(self):
		return self.get_duration().seconds

	@classmethod
	def is_this(cls, message):
		return cls.EVENT_TEXT in message.lower()

	def __repr__(self):
		return str(self)

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


class PlayEvent(Event):
	DB_TAG = 'database_event_play'
	EVENT_TEXT = '!play'

	def __init__(self, message_or_db):
		super(PlayEvent, self).__init__(message_or_db)
		if isinstance(message_or_db, Message):
			self.youtube_info = YoutubeInfo.create_from_message(message_or_db)
		else:
			self.youtube_info = YoutubeInfo.from_db_dict (message_or_db)

	def get_duration(self):
		return timedelta(seconds=(self.youtube_info.time_seconds()) + 3)

	def to_db_dict(self):
		return self.youtube_info.to_db_dict(super(PlayEvent, self).to_db_dict())

	@asyncio.coroutine
	def prepare(self):
		yield from self.youtube_info.prepare()

	def is_prepared(self):
		return self.youtube_info.prepared

	def __repr__(self):
		return str(self)

	def __str__(self):
		return '[PlayEvent] {} ({})'.format(self.youtube_info, 'playing' if self.is_active() else 'finished')

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
		images = ['http://i.imgur.com/eBZO67G.gif', 'http://i.imgur.com/0bprD6k.gif', 'http://i.imgur.com/van2j15.gif', 'http://i.imgur.com/sYjX7Qv.gif']
		return [ReplyAction(random.choice(images), str(self.parent_id))]


class HelpCommand(Command):
	DB_TAG = 'database_command_help'
	COMMAND = '!help'
	COMMAND_LOCATION = 'start'

	def set_commands(self, commands):
		self.commands = commands

	def get_help_lines(self):
		lines = []
		for command in self.commands:
			if command.help_string():
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

DBItem.SUBCLASSES.update(
	{PlayEvent.DB_TAG: PlayEvent}
	)

class BotMiddleware(object):
	TAG = ''
	TAG_CONSUMES = []
	TAG_PRODUCES = []
	MIDDLEWARE_REQUIRED = []
	MIDDLEWARE_SUPPORT_REQUESTS = {}

	def __init__(self):
		self.input = asyncio.JoinableQueue()
		self.output = {}
		self.finished = False
		self.task = None

	def get_middleware_required(self):
		middleware = []
		for cls in self.__class__.__bases__:
			middleware.extend(cls.MIDDLEWARE_REQUIRED)
		middleware.extend(self.MIDDLEWARE_REQUIRED)
		return middleware

	def load_state_from_db(self, db):
		return

	def save_state_to_db(self, db):
		return

	def collect_tags(self):
		consumes = []
		produces = []
		for cls in self.__class__.__bases__:
			consumes.extend(cls.TAG_CONSUMES)
			produces.extend(cls.TAG_PRODUCES)
		consumes.extend(self.TAG_CONSUMES)
		produces.extend(self.TAG_PRODUCES)
		if self.TAG:
			produces.append(self.TAG)

		return consumes, produces

	def register_queues(self, host):
		consumes, produces = self.collect_tags()
		for tag in consumes:
			host.recieve_messages_for_tag(tag, self.input)
		for tag in produces:
			self.output[tag] = host.get_input_queue(tag)

	def create_task(self, loop, db):
		self.task = loop.create_task(self.run(db))

	def request_support(self, request):
		return False

	def support_request_failed(self, middleware_tag, middleware_response):
		raise ValueError('Middleware {} returned an errror: {}'.format(middleware_tag, middleware_response))

	def cancel(self):
		if self.task and not self.task.done():
			self.task.cancel()

	def close(self):
		self.finished = True

	@asyncio.coroutine
	def setup(self, db):
		self.db = db

	@asyncio.coroutine
	def loop(self):
		self.close()

	@asyncio.coroutine
	def run(self, db):
		yield from self.setup(db)
		while True:
			yield from self.loop()
			if self.finished:
				break

	def __eq__(self, other):
		if hasattr(other, 'TAG'):
			return type(self.TAG) == type(other.TAG)
		return False

	def __hash__(self):
		return hash(self.TAG)

class Log(object):
	VERBOSE = 4
	DEBUG = 3
	WARN = 2
	ERROR = 1
	EXCEPTION = 0

	def __init__(self, level, source='', *args):
		self.args = args
		self.level = level
		self.traceback = None
		if level == Log.EXCEPTION:
			self.traceback = traceback.format_exc()
		else:
			self.traceback = ''.join(traceback.format_stack())
			
		self.file = None
		self.timestamp = int(time())
		self.source = source

	def add_traceback(self, log):
		for line in self.traceback.split('\n'):
			log += '\n{}'.format(line)

		return log


	def log_str(self):
		level_string = {self.VERBOSE:'V', self.DEBUG:'D', self.WARN:'W', self.ERROR:'E', self.EXCEPTION:'X'}	
		timestring = strftime("%m/%d|%H:%M:%S", localtime(self.timestamp))
		info_string = '[{}|{}|{}]: '.format(timestring, self.source, level_string[self.level])
		try:
			arg_string = str(self.args[0]).format(*self.args[1:])
		except Exception as e:
			arg_string = 'malformed log: {} -> {}'.format(self, e)
			self.level = Log.EXCEPTION

		full_log = arg_string
		if self.level == Log.EXCEPTION:
			full_log = self.add_traceback(full_log)

		log_lines = []
		for line in full_log.split('\n'):
			log_lines.append('{} {}'.format(info_string, line))

		return '\n'.join(log_lines)

	def __str__(self):
		return 'source: {}, level: {}, args: {}'.format(self.source, self.level, self.args)



class LoggerMiddleware(BotMiddleware):
	TAG = 'tag_logger_middleware'
	TAG_CONSUMES = [TAG]

	def __init__(self, default_log = 'Bot.log'):
		super(LoggerMiddleware, self).__init__()
		self.log_file_names = [default_log]
		self.default_file = default_log
		self.log_files = {}

	def create_files(self):
		for file_name in self.log_file_names:
			self.log_files[file_name] = open(file_name, 'a')

	def load_state_from_db(self, db):
		super(LoggerMiddleware, self).load_state_from_db(db)
		self.create_files()

	@asyncio.coroutine
	def loop(self):
		log = yield from self.input.get()
		if isinstance(log, Log):
			file_name = log.file if log.file else self.default_file
			fh = self.log_files[file_name]
			print(log.log_str())
			if log.level < Log.VERBOSE:
				fh.write(log.log_str() + '\n')

		self.input.task_done()

class LoggingMiddleware(BotMiddleware):
	TAG = ''
	LOG_NAME = ''
	TAG_PRODUCES = [LoggerMiddleware.TAG]
	MIDDLEWARE_REQUIRED = [LoggerMiddleware]

	@asyncio.coroutine
	def _log_coroutine(self, log):
		yield from self.log_queue.put(log)

	def load_state_from_db(self, db):
		super(LoggingMiddleware, self).load_state_from_db(db)
		self.log_queue = self.output[LoggerMiddleware.TAG]

	def log(self, level, *args):
		l = Log(level, self.LOG_NAME, *args)
		asyncio.async(self._log_coroutine(l))

	def exception(self, *args):
		return self.log(Log.EXCEPTION, *args)

	def error(self, *args):
		return self.log(Log.ERROR, *args)

	def debug(self, *args):
		return self.log(Log.DEBUG, *args)

	def verbose(self, *args):
		return self.log(Log.VERBOSE, *args)

	@asyncio.coroutine
	def run(self, db):
		yield from self.setup(db)
		while True:
			try:
				yield from self.loop()
			except Exception as e:
				self.exception('Caught exception in {}.loop: {}', self.__class__, e)
				yield from asyncio.sleep(1)
			if self.finished:
				break


class PacketMiddleware(LoggingMiddleware):
	TAG_DB_OBJECT = 'tag_db_object'
	TAG = 'tag_bot_message'
	LOG_NAME = 'Packet'

	TAG_CONSUMES = ['tag_raw']
	TAG_PRODUCES = [TAG_DB_OBJECT]
	MIDDLEWARE_REQUIRED = []
	ENABLED_MESSAGES = [
		NeonLightShowCommand,
		HelpCommand,
	]

	def __init__(self):
		super(PacketMiddleware, self).__init__()
		self.enabled_messages = []
		self.enabled_messages.extend(self.ENABLED_MESSAGES)

	def request_support(self, request):
		incompatible_classes = []
		for _class in request:
			if hasattr(_class, 'is_this'):
				self.enabled_messages.append(_class)
			else:
				incompatible_classes.append(_class)
		if incompatible_classes:
			return incompatible_classes
		else:
			return True

	def message_id_exists(self, uid):
		exists = self.db.search(where('uid') == uid)
		return len(exists) > 0

	def create_db_object(self, packet):
		content = packet.data['content']
		for event_class in self.enabled_messages:
			if event_class.is_this(content):
				try:
					return event_class(packet)
				except:
					self.exception('failed to create: {}', packet.data)
		return None

	@asyncio.coroutine
	def setup(self, db):
		# wtf is this garbage
		yield from super(PacketMiddleware, self).setup(db)
		self.db_queue = self.output[self.TAG_DB_OBJECT]
		self.message_queue = self.output[self.TAG]

	@asyncio.coroutine
	def loop(self):
		message = yield from self.input.get()
		if self.message_id_exists(message.uid):
			if not message.past:
				self.verbose('ignoring {}', message.data['content'])
			self.input.task_done()
			return

		self.verbose('message: {}', message.data['content'])
		db_object = self.create_db_object(message)
		if db_object:
			self.debug('DB Object: {}', db_object)
			if not db_object.is_prepared():
				try:
					yield from db_object.prepare() 
				except Exception as e:
					self.error('Failed to process: {}; Exception: {}', db_object, e)
					self.input.task_done()
					return

			yield from self.db_queue.put(db_object)

			if db_object.DB_TAG == HelpCommand.DB_TAG:
				db_object.set_commands(self.enabled_messages)

			yield from self.message_queue.put(db_object)

		self.input.task_done()


class SimpleActionMiddleware(LoggingMiddleware):
	TAG = 'tag_simple_action_middleware'
	TAG_CONSUMES = [PacketMiddleware.TAG]
	TAG_PRODUCES = ['tag_do_action']
	LOG_NAME = 'Action'

	@asyncio.coroutine
	def setup(self, db):
		yield from super(SimpleActionMiddleware, self).setup(db)
		self.action_queue = self.output['tag_do_action']

	@asyncio.coroutine
	def loop(self):
		command = yield from self.input.get()
		if hasattr(command, 'get_actions'):
			for action in command.get_actions():
				yield from self.action_queue.put(action)

		self.input.task_done()
	

class PlayQueuedSongsMiddleware(LoggingMiddleware):
	TAG = 'tag_queue_events'
	TAG_CONSUMES = [PacketMiddleware.TAG]
	LOG_NAME = 'Queue'
	TAG_PRODUCES = ['tag_do_action']
	MIDDLEWARE_REQUIRED = [PacketMiddleware]
	MIDDLEWARE_SUPPORT_REQUESTS = {
		PacketMiddleware.TAG: [
			QueueCommand, SkipCommand, ClearQueueCommand, ListQueueCommand, TestSkipCommand, DumpQueueCommand, PlayEvent
		]
	}

	def __init__(self):
		super(PlayQueuedSongsMiddleware, self).__init__()
		self.message_queue = asyncio.JoinableQueue()
		self.song_queue = []
		self.current_song = None
		self.play_callback = None
		# queued a song, waiting to see if it turns up
		self.expecting_song = False
		self.in_backlog = False

	def __str__(self):
		return '\n'.join([
			'QueueMiddleware: Current Song: {}({}s)'.format(self.current_song, self.current_song.remaining_duration() if self.current_song else 'NaN'),
			'\tCurrent Queue: {}'.format(self.song_queue)
			])

	def load_state_from_db(self, db):
		super(PlayQueuedSongsMiddleware, self).load_state_from_db(db)
		self.debug('load state from db')
		saved_state = db.search(where('type') == self.TAG)
		if saved_state:
			queue = [db.search(where('uid') == uid)[0] for uid in saved_state[0]['queue']]
			self.song_queue = [DBItem.create_object_from_db_entry(song) for song in queue]
			self.song_queue.sort()
			self.debug('loaded queue: {}', self.song_queue)
		events = db.search(where('type') == PlayEvent.DB_TAG)
		if events:
			events = sorted(events, key=lambda x: x['timestamp'])
			if len(events):
				event = DBItem.create_object_from_db_entry(events[-1]) 
				self.current_song = event
				self.debug('loded current song: {}', self.current_song)
				if self.song_queue:
					self.play_song()	

	def save_state_to_db(self, db):
		db_dict = {
			'type': self.TAG,
			'queue': [str(item.uid) for item in self.song_queue]
		}
		if db.search(where('type') == self.TAG):
			db.update(db_dict, where('type') == self.TAG)
		else:
			db.insert(db_dict)

	def get_next_songs(self):
		first = None
		next = None
		if len(self.song_queue) > 0:
			first = self.song_queue[0]
		if len(self.song_queue) > 1:
			next = self.song_queue[1]

		return first, next

	@asyncio.coroutine
	def play_later(self, delay):
		song_one, song_two = self.get_next_songs()
		self.debug("Playing {} in {} seconds.", song_one, delay)
		yield from asyncio.sleep(delay)
		song_one, song_two = self.get_next_songs()
		if not song_one:
			return
		while self.in_backlog:
			self.debug("In backlog messages, holding off on play")
			# 200ms
			yield from asyncio.sleep(0.2)

		self.expecting_song = True
		yield from self.action_queue.put(PlayOneSongAction(song_one, song_two))

	def play_song(self):
		if self.play_callback:
			self.play_callback.cancel()

		delay = 0
		if self.current_song:
			delay = self.current_song.remaining_duration()
		if self.expecting_song:
			delay += 3

		self.play_callback = asyncio.get_event_loop().create_task(
			self.play_later(delay)
			)

	def handle_queue_command(self, command):
		self.debug('handle_queue_command: ', command)
		self.song_queue.append(command)
		self.debug('\tqueue:', self.song_queue)

	def handle_play_event(self, play):
		self.current_song = play
		if self.song_queue and self.song_queue[0].youtube_info == play.youtube_info:
			self.song_queue.pop(0)
		to_delete = []
		for qcommand in self.song_queue:
			if play.timestamp > qcommand.timestamp\
				and play.youtube_info == qcommand.youtube_info:
				self.song_queue.remove(qcommand)
				break

	@asyncio.coroutine
	def setup(self, db):
		yield from super(PlayQueuedSongsMiddleware, self).setup(db)
		self.action_queue = self.output['tag_do_action']

	@asyncio.coroutine
	def loop(self):
		message = yield from self.input.get()

		self.debug('Got Message: {}', message.DB_TAG)
		reply_to = message.uid
		self.in_backlog = message.backlog

		if QueueCommand.DB_TAG in message.DB_TAG:
			self.handle_queue_command(message)
			yield from self.action_queue.put(QueuedNotificationAction(self.song_queue, self.current_song, message.youtube_info, reply_to))
		elif PlayEvent.DB_TAG in message.DB_TAG:
			self.expecting_song = False
			self.handle_play_event(message)
		elif ClearQueueCommand.DB_TAG in message.DB_TAG:
			self.song_queue = []
		elif ListQueueCommand.DB_TAG in message.DB_TAG:
			action = ListQueueAction(self.song_queue, self.current_song, reply_to)
			yield from self.action_queue.put(action)
		elif DumpQueueCommand.DB_TAG in message.DB_TAG:
			self.error('dump queue fixme')
			pass

		self.play_song()
		self.save_state_to_db(self.db)
		self.debug(str(self))


class NeonDJBot(object):
	TAG_RAW = 'tag_raw'
	TAG_DO_ACTION = 'tag_do_action'

	def __init__(self, 	room_address):
		self.loop = asyncio.get_event_loop()
		self.message_queue = asyncio.JoinableQueue()
		self.database_queue = asyncio.JoinableQueue()
		self.action_queue = asyncio.JoinableQueue()
		#self.packet_queue = packet_queue

		self.room_address = room_address
		self.ws_lock = asyncio.Lock()
		self.ws = None
		self.db = TinyDB('./MusicBotDB_dev.json')
		self.internal_coroutines = []
		self.task_queues = []
		self.tasks = []
		self.mid = 0

		self.reset_mid()
		### middleware

		self.middleware = {}
		self.queues = {}
		self.packet_queue = self.get_input_queue(self.TAG_RAW)
		self.recieve_messages_for_tag(PacketMiddleware.TAG, self.message_queue)
		self.recieve_messages_for_tag(PacketMiddleware.TAG_DB_OBJECT, self.database_queue)
		self.recieve_messages_for_tag(self.TAG_DO_ACTION, self.action_queue)

	def add_middleware(self, middleware):
		if not middleware.TAG in self.middleware:
			#print('new middleware: ', middleware)
			for required_middleware in middleware.get_middleware_required():
				#print('adding required middleware: ', required_middleware)
				self.add_middleware(required_middleware())

			
			middleware.register_queues(self)
			middleware.load_state_from_db(self.db)
			middleware.create_task(self.loop, self.db)
			for tag, request in middleware.MIDDLEWARE_SUPPORT_REQUESTS.items():
				result = self.middleware[tag].request_support(request)
				if result != True:
					middleware.support_request_failed(tag, result)
					print('middleware request for support failed: {} -> {}'.format(middleware, tag))
					return
			#print('indexing middleware: ', middleware)
			self.middleware[middleware.TAG] = middleware


	def _get_create_queue(self, tag):
		if tag not in self.queues:
			#print('creating_queue: ', tag)
			self.queues[tag] = {
				'producer': asyncio.JoinableQueue(),
				'consumers': [],
				'message_exchange': None,
			}
			self.queues[tag]['message_exchange'] = self.loop.create_task(self.exchange_messages(self.queues[tag]))
		
		return self.queues[tag]

	def recieve_messages_for_tag(self, tag, queue):
		#print('recieve_messages_for_tag({}, {})'.format(tag, queue))
		tagged_queue = self._get_create_queue(tag)
		tagged_queue['consumers'].append(queue)

	def get_input_queue(self, tag):
		tagged_queue = self._get_create_queue(tag)
		#print('get_input_queue({}) ->'.format(tag, tagged_queue['producer']))
		return tagged_queue['producer']

	@asyncio.coroutine
	def exchange_messages(self, tag_data):
		while True:
			message = yield from tag_data['producer'].get()

			for q in tag_data['consumers']:
				yield from q.put(message)

			tag_data['producer'].task_done()


	def reset_mid(self):
		def mid_itr():
			i = 0
			while True:
				yield i
				i += 1
		self.mid = mid_itr()

	def add_task(self, task):
		t = self.loop.create_task(task.coroutine(self.action_queue, self.db))
		self.tasks.append(t)
		self.task_queues.append(task.message_queue)

	@asyncio.coroutine
	def setup(self):
		yield from self.action_queue.put(SetNickAction("♬|NeonDJBot"))

	@asyncio.coroutine
	def recv_loop(self):
		yield from self.setup()
		while True:
			if not self.ws or not packet: #server d/c or connect
				yield from self.ws_lock
				try:
					self.ws = yield from websockets.connect(self.room_address)
				except:
					print('connection failed')
					yield from asyncio.sleep(1)
					continue

				print('connection succeeded')
				self.ws_lock.release()
				#yield from self.action_queue.put(SetNickAction("♬|NeonDJBot"))

			packet = yield from self.ws.recv()
			if packet:
				try:
					packet = Packet(packet)
				except:
					print('Packet {} did not meet expectations! Please investigate!'.format(packet))
					continue

				if packet.type == 'ping-event':
					yield from self.action_queue.put(PingAction())
				else:
					for message in packet.messages():
						yield from self.packet_queue.put(message)

	@asyncio.coroutine
	def execute_actions_task(self):
		while True:
			if self.ws:
				action = yield from self.action_queue.get()

				# set websocket
				yield from self.ws_lock
				action.ws = self.ws
				self.ws_lock.release()

				#print('processing action: {}'.format(action))
				task = action.get_coroutine(self.db, self.mid, self.action_queue)
				asyncio.async(task())

				self.action_queue.task_done()
			else:
				yield from asyncio.sleep(1)

	def message_in_db(self, message):
		return self.db.count(where('uid') == message['uid']) > 0

	@asyncio.coroutine
	def add_to_db_task(self):
		while True:
			db_item = yield from self.database_queue.get()
			#print('adding {} to DB'.format(db_item))
			if hasattr(db_item, 'to_db_dict'):
				db_dict = db_item.to_db_dict()
				#if not message_id_exists(db_item['uid']):
				if not self.message_in_db(db_dict):
						self.db.insert(db_dict)

			self.database_queue.task_done()	

	def connect(self):
		db_task = self.loop.create_task(self.add_to_db_task())
		action_task = self.loop.create_task(self.execute_actions_task())

		self.internal_coroutines = [db_task, action_task]
		self.loop.run_until_complete(self.recv_loop())

#bot = NeonDJBot('ws://localhost:8765/')
bot = NeonDJBot('wss://euphoria.io/room/music/ws')
#bot = NeonDJBot('wss://euphoria.io/room/test/ws')
bot.add_middleware(SimpleActionMiddleware())
bot.add_middleware(PlayQueuedSongsMiddleware())


bot.connect()