# -*- coding: utf-8 -*-
import random
import json
from time import time
from datetime import timedelta, datetime
import asyncio
import websockets
import re
import pprint
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


class DBField(object):
	FIELD_NAME = ""

	# don't touch these

	@classmethod
	def create_field(cls, db_item, message_or_db):
		if isinstance(message_or_db, Message):
			self = cls.create_from_message(message_or_db)
		else:
			self = cls.create_from_db_dict(message_or_db[cls.FIELD_NAME])
		cls._init(db_item, self)

	@classmethod
	def field_to_db(cls, db_item, db_dict):
		db_dict[cls.FIELD_NAME] = cls._self(db_item).to_db_dict()
		return db_dict

	@classmethod
	def _init(cls, db_item, new_self):
		setattr(db_item, cls.FIELD_NAME, new_self)

	@classmethod
	def _self(cls, db_item):
		if hasattr(db_item, cls.FIELD_NAME):
			return getattr(db_item, cls.FIELD_NAME)
		return None

	@classmethod
	def is_prepared(cls, db_item):
		self = cls._self(db_item)
		if self:
			return self.is_prepared
		return True

	@classmethod
	def prepare(cls, db_item):
		cls._self(db_item).prepare_self()
		pass

	# override these

	def __init__(self, prepared=True):
		self.is_prepared = prepared

	@classmethod
	def create_from_message(cls, message):
		return {}

	def prepare_self(self):
		pass

	# single value fields can keep these
	@classmethod
	def create_from_db_dict(cls, db_dict):
		return cls(db_dict)

	def to_db_dict(self):
		return str(self)



class User(DBField):
	FIELD_NAME = "user"

	@classmethod
	def create_from_message(cls, message):
		sender = message.data['sender']
		return User(sender['id'].split(':')[1], sender['name'])	

	@classmethod
	def create_from_db_dict(cls, db_dict):
		return User(db_dict['id'], db_dict['name'])
	
	def to_db_dict(self):
		return {
			'id': self.user_id,
			'name': self.name
		}

	def __init__(self, user_id, name):
		super(User, self).__init__()
		self.user_id = user_id
		self.name = name

	def display(self):
		return self.name

	def __str__(self):
		return self.name

class Parent(DBField):
	FIELD_NAME = "parent_id"

	@classmethod
	def create_from_message(cls, message):
		return Parent(message.data['id'])

	def __init__(self, parent_id):
		super(Parent, self).__init__()
		self.parent_id = parent_id

	def __str__(self):
		return self.parent_id

class YoutubeInfo(DBField):

	INFO_URL = 'http://gdata.youtube.com/feeds/api/videos/{}?alt=json'
	#REGEX = '{}\s*([^\s]+)\s?'
	REGEX = r'((https?://)?youtube\S+)'
	FIELD_NAME = 'youtube_info'

	@classmethod
	def create_from_message(cls, message):
		# returns list of tuples
		urls = re.findall(cls.REGEX, message.data['content'], flags=re.I)
		return YoutubeInfo(urls[0][0])	

	@classmethod
	def create_from_db_dict(cls, db_dict):
		self = YoutubeInfo(db_dict['url'])
		self.set_data(db_dict['youtube_id'],
			 db_dict['title'], db_dict['sub_title'],
			 db_dict['thumbnails'], db_dict['duration'])
		return self

	def to_db_dict(self):
		return {
			'url': self.url,
			'youtube_id': self.youtube_id,
			'title': self.title,
			'sub_title': self.sub_title,
			'thumbnails': self.thumbnails,
			'duration': self.duration,
		}

	def __init__(self, url):
		super(YoutubeInfo, self).__init__(prepared=False)
		self.url = url if url.find('http') == 0 else 'https://' + url
		
	def set_data(self,youtube_id, title, sub_title, thumbnails, duration):
		self.youtube_id = youtube_id
		self.title = title
		self.sub_title = sub_title
		self.thumbnails = thumbnails
		self.duration = duration
		self.is_prepared = True

	def prepare_self(self):
		query = urlparse(self.url).query
		youtube_id = parse_qs(query)['v'][0]
		url = self.INFO_URL.format(youtube_id)
		resp = requests.get(url)
		youtube_info = resp.json()['entry']

		self.set_data(youtube_id,
			youtube_info['title']['$t'], youtube_info['content']['$t'],
			youtube_info['media$group']['media$thumbnail'], youtube_info['media$group']['yt$duration'])
			

	def time_seconds(self):
		if not self.is_prepared:
			return 0
		assert(len(self.duration.keys()) == 1)
		return int(self.duration['seconds'])

	def time_string(self):
		return str(timedelta(seconds=self.time_seconds()))

	def select_thumbnail(self):
		if not self.is_prepared:
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
		if not self.is_prepared:
			return self.url
		else:
			return '{}|{} [{}]'.format(self.youtube_id, self.title, self.time_string())


class DBItem(object):
	DB_TAG = "database_"

	SUBCLASSES = {}

	CLASS_FIELDS = [User]

	def __init__(self, message_or_db):
		if isinstance(message_or_db, Message):
			self.timestamp = message_or_db.timestamp
			self.uid = message_or_db.uid
		else:
			self.timestamp = message_or_db['timestamp']
			self.uid = message_or_db['uid']

		self.add_fields(self, DBItem.CLASS_FIELDS)
		self.create_fields(message_or_db)

	@staticmethod
	def add_fields(self, fields):
		if not hasattr(self, '_fields'):
			self._fields = set()
		for field in fields:
			self._fields.add(field)

	def create_fields(self, message_or_db):
		for field in self._fields:
			field.create_field(self, message_or_db)

	def prepare(self):
		for field in self._fields:
			field.prepare(self)

	def is_prepared(self):
		prepared = True
		for field in self._fields:
			prepared = prepared and field.is_prepared(self)

		return prepared

	def to_db_dict(self):
		db_dict = {
			'type': self.DB_TAG,
			'uid': self.uid,
			'timestamp': self.timestamp,
		}

		return self._fields_to_db_dict(db_dict)

	def _fields_to_db_dict(self, db_dict):
		for field in self._fields:
			field.field_to_db(self, db_dict)

		return db_dict

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


class Task(DBItem):
	DB_TAG = 'database_task'

	@classmethod
	def new_task(cls, user, uid, timestamp, fields=[]):
		fake_db_dict = { 'user':user.to_db_dict(), 'uid':uid, 'timestamp':timestamp }
		# avoid re-processing if we can
		for field in fields:
			fake_db_dict[field.FIELD_NAME] = field.to_db_dict()
		return cls(fake_db_dict)

	def __init__(self, message_or_db):
		super(Task, self).__init__(message_or_db)

	def satisfied_by_event(self, state, event):
		db = state['db']
		if self.process_event(state, event):
			db.remove((where('type') == self.DB_TAG) & (where('uid') == self.uid))
			return True
		return False

	def check_expired(self, state):
		if self.expired():
			db.remove((where('type') == self.DB_TAG) & (where('uid') == self.uid))
			return True
		return False

	def process_event(self, state, event):
		return True

	def expired(self):
		return False

	def get_actions(self):
		return []

	def __repr__(self):
		return str(self)

	def __str__(self):
		return '{}:{}'.format(self.DB_TAG, self.satisfied)

class PlaySongTask(Task):
	DB_TAG = 'database_task_playsong'

	CLASS_FIELDS = [YoutubeInfo]

	def __init__(self, message_or_db):
		self.add_fields(self, PlaySongTask.CLASS_FIELDS)
		super(PlaySongTask, self).__init__(message_or_db)

	def process_event(self, state, event):
		if event.DB_TAG == PlayEvent.DB_TAG:
			if event.youtube_info == self.youtube_info:
				return True

	def __repr__(self):
		return str(self)

	def __str__(self):
		return '[PlayTask] {}'.format(self.youtube_info)

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

	@classmethod
	def is_this(cls, message):
		return cls.EVENT_TEXT in message.lower()

	def __repr__(self):
		return str(self)

class PlayEvent(Event):
	DB_TAG = 'database_event_play'
	EVENT_TEXT = '!play'

	CLASS_FIELDS = [YoutubeInfo]

	def __init__(self, message_or_db):
		self.add_fields(self, PlayEvent.CLASS_FIELDS)
		super(PlayEvent, self).__init__(message_or_db)

	def get_duration(self):
		return timedelta(seconds=(self.youtube_info.time_seconds()) + 6)

	def __repr__(self):
		return str(self)

	def __str__(self):
		return '[PlayEvent] {} ({})'.format(self.youtube_info, 'playing' if self.is_active() else 'finished')


class Action(object):
	START_DELAY = 0
	LOOP_DELAY = 0.2
	MESSAGE_DELAY = 0

	def __init__(self):
		self.ws = None
		self.timestamp = int(time())

	def packet_to_send(self, state):
		return None

	def actions_to_queue(self, state):
		return []

	def should_repeat(self, state):
		return False

	def get_coroutine(self, state, id_generator, action_queue):
		@asyncio.coroutine
		def task():
			yield from asyncio.sleep(self.START_DELAY)

			while True:
				message = self.packet_to_send(state)
				if message:
					# set id
					message['id'] = str(next(id_generator))
					try:
						yield from self.ws.send(json.dumps(message))
					except InvalidState:
						# websocket closed
						yield from action_queue.put(self)
						break
					except TypeError:
						print('{} - Failed to send: {}'.format(self, json.dumps(message)))
						break # bail the hell out
					yield from asyncio.sleep(self.MESSAGE_DELAY)
				for action in self.actions_to_queue(state):
					yield from action_queue.put(action)

				if not self.should_repeat(state):
					break
				else:
					yield from asyncio.sleep(self.LOOP_DELAY)

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
		return cls._wrap('send', {"content":text,"parent":parent_message})

	@classmethod
	def nick_packet(cls, nick):
		return cls._wrap('nick', {"name":nick})

class PingAction(PacketAction):
	def __init__(self):
		super(PingAction, self).__init__()

	def packet_to_send(self, state):
		return self.ping_packet(self.timestamp)

class ReplyAction(PacketAction):
	def __init__(self, text, reply_to = ''):
		super(ReplyAction, self).__init__()
		self.text = text
		self.reply_to = reply_to

	def packet_to_send(self, state):
		return self.send_packet(self.text, self.reply_to)


class SetNickAction(PacketAction):
	def __init__(self, nick):
		super(SetNickAction, self).__init__()
		self.nick = nick

	def packet_to_send(self, state):
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

	def clear_queue(self, db):
		db.remove(where('type') == PlaySongTask.DB_TAG)

	def queued_songs(self, db):
		queued_songs = db.search(where('type') == PlaySongTask.DB_TAG)
		if queued_songs:
			queued_songs = sorted(queued_songs, key=lambda x: x['timestamp'])
			return [DBItem.create_object_from_db_entry(song) for song in queued_songs]

		return []

	def skip_count(self, db, seconds):
		now = int(time())
		return db.search((where('type') == SkipCommand.DB_TAG) & (where('timestamp') > (now - seconds)) ) 


class QueuedNotificationAction(SongAction):
	def __init__(self, song_info, reply_to = ''):
		super(QueuedNotificationAction, self).__init__()
		self.song_info = song_info
		self.reply_to = reply_to

	def packet_to_send(self, state):
		queue = self.queued_songs(state['db'])
		current_song = self.currently_playing(state['db'])

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

class ClearQueueAction(SongAction):
	def should_repeat(self, state):
		self.clear_queue(state['db'])

		return False

	def __str__(self):
		return 'ClearQueue'

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

	def get_song_to_play(self, state):
		return None, None

	def packet_to_send(self, state):
		(song_to_play, next_song) = self.get_song_to_play(state)
		if song_to_play:
			return self.send_packet(
				self.play_message(song_to_play, next_song),
				self.reply_to)
		return None

class PlayNextQueuedSongAction(PlayQueuedSongAction):
	START_DELAY = 5
	MESSAGE_DELAY = 2

	def get_song_to_play(self, state):
		db = state['db']
		if not self.currently_playing(db):
			q = self.queued_songs(db)
			return q[0] if len(q) > 0 else None, q[1] if len(q) > 1 else None

		return None, None

	def should_repeat(self, state):
		return True

class SkipCurrentSongAction(PlayQueuedSongAction):
	def get_song_to_play(self, state):
		db = state['db']
		if self.currently_playing(db):
			q = self.queued_songs(db)
			return q[0] if len(q) > 0 else None, q[1] if len(q) > 1 else None

		return None, None

class VoteSkipAction(SongAction):

	def should_repeat(self, state):
		print('skip count:', self.skip_count(state['db'], 60*60))
		return False

	def process_state(self, state):
		print('skip count:', self.skip_count(state['db'], 60*60))


class DebugListCurrentSong(SongAction):
	LOOP_DELAY = 5

	def __init__(self):
		super(DebugListCurrentSong, self).__init__()
		self.previous_song = None

	def should_repeat(self, state):
		current_song = self.currently_playing(state['db'])
		if current_song:
			if current_song.remaining_duration() < 20:
				print("Current song:{}, remaining: {}".format(current_song, current_song.remaining_duration()))
			elif current_song.remaining_duration() % 20 == 0:
				print("Current song:{}, remaining: {}".format(current_song, current_song.remaining_duration()))

		if current_song != self.previous_song:
			print("Song change: {} -> {}".format(self.previous_song, current_song))

		self.previous_song = current_song

		return True

class DumpQueue(SongAction):
	def __init__(self, reply_to=''):
		super(DumpQueue, self).__init__()
		self.reply_to = reply_to

	def packet_to_send(self, state):
		song_queue = self.queued_songs(state['db'])
		message = 'Nothing Queued'
		if song_queue:
			strings = []
			for song in song_queue:
				strings.append('{} added by @{}\n command(copy & paste w/ !): play {}'.format(str(song.youtube_info.display()), song.user.name, str(song.youtube_info.url)))

			return self.send_packet('\n'.join(strings), self.reply_to)

		return self.send_packet(message, self.reply_to)

class ListQueue(SongAction):
	def __init__(self, reply_to):
		super(ListQueue, self).__init__()
		self.reply_to = reply_to

	def packet_to_send(self, state):
		song_queue = self.queued_songs(state['db'])
		current_song = self.currently_playing(state['db'])
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

class DebugListQueue(SongAction):
	def __init__(self):
		super(DebugListQueue, self).__init__()
		self.previous_queue = None

	def should_repeat(self, state):
		queue = self.queued_songs(state['db'])
		if queue:
			queue = ['{} added by {}'.format(str(song.youtube_info), song.user.name) for song in queue]

		if self.previous_queue == None or len(self.previous_queue) != len(queue):
			print("Current queue:{}".format('\n'.join(queue)))

		self.previous_queue = queue

		return True


class Command(DBItem):
	DB_TAG = 'database_command'
	TYPE = 'base'
	COMMAND = ''
	COMMAND_LOCATION = 'any'

	CLASS_FIELDS = [Parent]

	def __init__(self, message_or_db):
		self.add_fields(self, Command.CLASS_FIELDS)
		super(Command, self).__init__(message_or_db)

	def get_actions(self):
		return []

	def get_tasks(self):
		return []

	@classmethod
	def help_string(cls):
		return None

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

	CLASS_FIELDS = [YoutubeInfo]

	def __init__(self, message_or_db):
		self.add_fields(self, QueueCommand.CLASS_FIELDS)
		super(QueueCommand, self).__init__(message_or_db)

	def _generate_confirmation_text(self):
		return 'Added to queue: {}'.format(self.youtube_info.display())

	def get_actions(self):
		#return [ReplyAction(self._generate_confirmation_text(), str(self.parent_id))]
		return [QueuedNotificationAction(self.youtube_info, str(self.parent_id))]

	def get_tasks(self):
		return [PlaySongTask.new_task(self.user, self.uid+'_1', self.timestamp, fields=[self.youtube_info])]

	@classmethod
	def help_string(cls):
		return "{} <youtube url> : Add a song to the queue. Does not support any link shorterners (youtube's included). Songs will be played in order.".format(cls.COMMAND)

	def __str__(self):
		return '[QueueCommand] {}'.format(self.youtube_info)

class ClearQueueCommand(Command):
	DB_TAG = 'database_command_clearqueue'
	COMMAND = '!clearqueue'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [ClearQueueAction()]

class ListQueueCommand(Command):
	DB_TAG = 'database_command_listqueue'
	COMMAND = '!list'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [ListQueue(str(self.parent_id))]

	@classmethod
	def help_string(cls):
		return "{}: List the songs currently in the queue.".format(cls.COMMAND)

class DumpQueueCommand(Command):
	DB_TAG = 'database_command_dumpqueue'
	COMMAND = '!dumpqueue'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [DumpQueue(str(self.parent_id))]

	@classmethod
	def help_string(cls):
		return "{}: List all queued songs with youtube URL included and then DELETES the queue. Useful for shutting down the bot.".format(cls.COMMAND)

class SkipCommand(Command):
	DB_TAG = 'database_command_skip'
	COMMAND = '!skip'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [SkipCurrentSongAction()]

	@classmethod
	def help_string(cls):
		return "{}: Skip a song. Please note: one song will be skipped per {} command! Use carefully.".format(cls.COMMAND, cls.COMMAND)

class TestSkipCommand(Command):
	DB_TAG = 'database_command_testskip'
	COMMAND = '!testskip'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [VoteSkipAction()]

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

	def get_help_lines(self):
		global enabled_commands

		lines = []
		for command in enabled_commands:
			print('help string: {}'.format(command))
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
		PlaySongTask.DB_TAG: PlaySongTask,
	})

DBItem.SUBCLASSES.update(
	{PlayEvent.DB_TAG: PlayEvent}
	)

enabled_commands = [
		QueueCommand,
		SkipCommand,
		ClearQueueCommand,
		ListQueueCommand,
		TestSkipCommand,
		NeonLightShowCommand,
		HelpCommand,
		DumpQueueCommand
	]

enabled_events = [
		PlayEvent,
	]



def create_db_object(packet):
	global enabled_events
	global enabled_commands
	db_objects = []
	db_objects.extend(enabled_events)
	db_objects.extend(enabled_commands)
	content = packet.data['content']
	for event_class in db_objects:
		if event_class.is_this(content):
			try:
				return event_class(packet)
			except:
				print('failed to create: {}'.format(packet.data))
	return None


db = TinyDB('./MusicBotDB.json')

def message_id_exists(uid):
	exists = db.search(where('uid') == uid)
	return len(exists) > 0


action_queue = asyncio.JoinableQueue()
message_queue = asyncio.JoinableQueue()
command_task_queue = asyncio.JoinableQueue()
command_action_queue = asyncio.JoinableQueue()
database_queue = asyncio.JoinableQueue()
task_queue = asyncio.JoinableQueue()

websocket_queue = asyncio.JoinableQueue()

state = {
	'db': db
}

@asyncio.coroutine
def database_task():
	global state
	while True:
		db_item = yield from database_queue.get()
		if not message_id_exists(db_item['uid']):
				state['db'].insert(db_item)

		database_queue.task_done()


@asyncio.coroutine
def cull_tasks_and_add_to_db():
	global state
	while True:
		task = yield from task_queue.get()
		#yield from message_queue.join() # process all messages
		yield from database_queue.join() # process all db inserts
		events = state['db'].search( where('type').contains(Event.DB_TAG) & (where('timestamp') > task.timestamp)) 
		events = [DBItem.create_object_from_db_entry(event) for event in events]
		#print('Checking Task: {}'.format(task))
		satisfied = False
		for event in events:
			if task.satisfied_by_event(state, event):
				#print('{} satisfied by {}'.format(task, event))
				for action in task.get_actions():
					yield from action_queue.put(action)

				satisfied = True
				break
		if not satisfied:
			yield from database_queue.put(task.to_db_dict())

		task_queue.task_done()

@asyncio.coroutine
def execute_actions_task():
	global state
	def mid():
		i = 0
		while True:
			yield i
			i +=1
	id_gen = mid()
	while True:
		action = yield from action_queue.get()

		# set websocket
		current_ws = yield from websocket_queue.get()
		if not websocket_queue.empty():
			print('got websocket, but queue is not empty!')
		action.ws = current_ws
		yield from websocket_queue.put(current_ws)
		websocket_queue.task_done()

		#print('processing action: {}'.format(action))
		task = action.get_coroutine(state,  id_gen, action_queue)
		asyncio.Task(task())

		action_queue.task_done()


@asyncio.coroutine
def queue_command_tasks():
	while True:
		command = yield from command_task_queue.get()
		#print('Adding tasks from command: {}'.format(command))
		for task in command.get_tasks():
			if not task.is_prepared():
				print('Wont queue un-prepared task: {}'.format(task))
				continue
			
			yield from task_queue.put(task)
		command_task_queue.task_done()
		

@asyncio.coroutine
def parse_messages():
	global state
	while True:
		message = yield from message_queue.get()
		#print('message: {}'.format(message.data['content']))
		if message_id_exists(message.uid):
			if not message.past:
				print('ignoring {}'.format(message.data['content']))
			message_queue.task_done()
			continue
		db_object = create_db_object(message)
		if db_object:
			#print('DB Object: {}'.format(db_object))
			if not db_object.is_prepared():
				try:
					db_object.prepare()
				except:
					print('Failed to process: {}'.format(db_object))
					message_queue.task_done()
					continue

			#print('Adding to DB: {}'.format(db_object))
			if Event.DB_TAG in db_object.DB_TAG:
				tasks = db.search(where('type').contains(Task.DB_TAG))
				tasks = [DBItem.create_object_from_db_entry(task) for task in tasks]
				#print('Checking Event: {}'.format(db_object))
				#print('Current Tasks: {}'.format(tasks))
				for task in tasks:
					if task.satisfied_by_event(state, db_object):
						#print('{} satisfied by {}'.format(task, db_object))
						for action in task.get_actions():
							yield from action_queue.put(action)
						break

			yield from database_queue.put(db_object.to_db_dict())

			if Command.DB_TAG in db_object.DB_TAG:

				#print('Adding to task queue: {}'.format(db_object))
				yield from command_task_queue.put(db_object)

				if not message.past:
					#print('Adding to action queue: {}'.format(db_object))
					yield from command_action_queue.put(db_object)
					
		message_queue.task_done()

@asyncio.coroutine
def queue_command_actions():
	while True:
		command = yield from command_action_queue.get()	
		for action in command.get_actions():
			#print('{} -> {}'.format(command, action))
			yield from action_queue.put(action)

		command_action_queue.task_done()



@asyncio.coroutine
def loop():
	ws = yield from websockets.connect('wss://euphoria.io/room/music/ws')
	#ws = yield from websockets.connect('ws://localhost:8765/')

	yield from websocket_queue.put(ws)

	yield from action_queue.put(SetNickAction("♬|NeonDJBot"))
	yield from action_queue.put(DebugListCurrentSong())
	yield from action_queue.put(DebugListQueue())
	yield from action_queue.put(PlayNextQueuedSongAction())

	while True:
		packet = yield from ws.recv()
		if not packet: #server d/c
			outdated_ws = yield from websocket_queue.get()
			if not websocket_queue.empty():
				print('got websocket, but queue is not empty!')
			while True:
				yield from asyncio.sleep(1)
				try:
					ws = yield from websockets.connect('wss://euphoria.io/room/music/ws')
				except:
					continue

				yield from websocket_queue.put(ws)
				packet = yield from ws.recv()
				break
			websocket_queue.task_done()
		packet = Packet(packet)

		if packet.type == 'ping-event':
			yield from action_queue.put(PingAction())
		else:
			for message in packet.messages():
				yield from message_queue.put(message)

asyncio.Task(database_task())
asyncio.Task(execute_actions_task())
asyncio.Task(parse_messages())
asyncio.Task(queue_command_actions())
asyncio.Task(queue_command_tasks())
asyncio.Task(cull_tasks_and_add_to_db())

asyncio.get_event_loop().run_until_complete(loop())