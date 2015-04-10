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
	def __init__(self, ws, data, past=False):
		self.data = data
		self.ws = ws
		self.past = past

		if 'time' in self.data:
			self.timestamp = int(self.data['time'])
			self.uid = self.data['id']

class Packet(object):
	def __init__(self, ws, packet):
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
		self.ws = ws

	def messages(self):
		if self.type == 'send-event' or self.type == 'send-reply':
			return [Message(self.ws, self.data)]
		elif self.type == 'snapshot-event':
			return [Message(self.ws, m, past=True) for m in self.data['log']]

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
		return User(sender['id'].split('-')[0], sender['name'])	

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
		urls = re.findall(cls.REGEX, message.data['content'])
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
		return '{} [{}]'.format(self.title, self.time_string())

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
	def create_object_from_db_entry(db_dict, ws=None):
		if db_dict['type'] in DBItem.SUBCLASSES:
			return DBItem.SUBCLASSES[db_dict['type']](db_dict, ws)


class Task(DBItem):
	DB_TAG = 'database_task'

	@classmethod
	def new_task(cls, user, uid, timestamp, fields=[]):
		fake_db_dict = { 'user':user.to_db_dict(), 'uid':uid, 'timestamp':timestamp }
		# avoid re-processing if we can
		for field in fields:
			fake_db_dict[field.FIELD_NAME] = field.to_db_dict()
		return cls(fake_db_dict)

	def __init__(self, message_or_db, ws = None):
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

	def __init__(self, message_or_db, ws = None):
		self.add_fields(self, PlaySongTask.CLASS_FIELDS)
		super(PlaySongTask, self).__init__(message_or_db, ws)

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

	def __init__(self, message_or_db, ws):
		super(Event, self).__init__(message_or_db)
		self.ws = ws

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

	def __init__(self, message_or_db, ws):
		self.add_fields(self, PlayEvent.CLASS_FIELDS)
		super(PlayEvent, self).__init__(message_or_db, ws)

	def get_duration(self):
		return timedelta(seconds=(self.youtube_info.time_seconds() + 5))

	def __repr__(self):
		return str(self)

	def __str__(self):
		return '[PlayEvent] {} ({})'.format(self.youtube_info, 'playing' if self.is_active() else 'finished')


class Action(object):
	def __init__(self, ws):
		self.ws = ws
		self.timestamp = int(time())
		self.delay = timedelta(seconds=0) 

	def process_state(self, state):
		pass

	def get_packet_to_send(self, id):
		return None

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def nop():
			return
		return nop

class ReplyAction(Action):
	def __init__(self, ws, text, reply_to):
		super(ReplyAction, self).__init__(ws)
		self.text = text
		self.reply_to = reply_to

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def reply():
			m = {"type":"send","data":{"content":self.text,"parent":self.reply_to},"id":str(next(id_generator))}
			yield from self.ws.send(json.dumps(m))

		return reply

	def __str__(self):
		return 'ReplyAction -> {}'.format(self.text)

class SongAction(Action):
	@staticmethod
	def set_executed():
	    """
	    Increment a given field in the element.
	    """
	    def transform(element):
	        element['executed'] = True

	    return transform

	def currently_playing(self, db):
		events = db.search(where('type') == PlayEvent.DB_TAG)
		if events:
			events = sorted(events, key=lambda x: x['timestamp'])
			if len(events):
				event = DBItem.create_object_from_db_entry(events[-1], self.ws) 
				if event.is_active():
					return event
		return None

	def clear_queue(self, db):
		db.remove(where('type') == PlaySongTask.DB_TAG)

	def queued_songs(self, db):
		queued_songs = db.search(where('type') == PlaySongTask.DB_TAG)
		if queued_songs:
			queued_songs = sorted(queued_songs, key=lambda x: x['timestamp'])
			return [DBItem.create_object_from_db_entry(song, self.ws) for song in queued_songs]

		return []

	def get_next_if_nothing_playing(self, db):
		currently_playing = self.currently_playing(db)
		if currently_playing:
			return None
		else:
			return self.pop_queue(db)

	def pop_queue(self, db):
		queue = self.queued_songs(db)
		if queue and len(queue) > 0:
			command = queue[0]
			#db.update({'state':Task.STATE_EXPIRED}, where('type') == PlaySongTask.DB_TAG and where('uid') == command.uid)
			return command
		else:
			return None

	def skip_count(self, db, seconds):
		now = int(time())
		return db.search((where('type') == SkipCommand.DB_TAG) & (where('timestamp') > (now - seconds)) ) 

	def skip_current(self, db):
		currently_playing = self.currently_playing(db)
		if not currently_playing:
			return None
		else:
			return self.pop_queue(db)

class SetNickAction(Action):
	def __init__(self, ws, nick):
		super(SetNickAction, self).__init__(ws)
		self.nick = nick

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def reply():
			m = {"type":"nick","data":{"name":self.nick},"id":str(next(id_generator))}
			yield from self.ws.send(json.dumps(m))

		return reply

class ClearQueueAction(SongAction):
	def process_state(self, state):
		self.clear_queue(state['db'])
		# queue = self.queued_songs(state['db'])
		# for command in queue:
		# 	db.remove(self.set_executed(), where('type') == QueueCommand.DB_TAG and where('uid') == command.uid)

	def __str__(self):
		return 'ClearQueue'


class PlayNextSong(SongAction):
	FIRST_PASS = True

	def process_state(self, state):
		self.next_song = None
		self.song_after = None
		if not self.currently_playing(state['db']):
			queue = self.queued_songs(state['db'])
			if len(queue) > 0:
				self.next_song = queue[0]
			if len(queue) > 1:
				self.song_after = queue[1]

	def get_message(self):
		song_after = 'Nothing' if self.song_after == None else '{} (from {})'.format(
			self.song_after.youtube_info.display(),
			self.song_after.user
			)
		return 'Playing: {} (from {})\n!play {}\nNext: {}'.format(
				self.next_song.youtube_info.display(),
				self.next_song.user,
				self.next_song.youtube_info.url,
				song_after)

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def play_and_repeat():
			if self.FIRST_PASS:
				yield from asyncio.sleep(5)
				self.FIRST_PASS = False
			
			if self.next_song:
				print("playing {}".format(self.next_song.youtube_info))
				yield from action_queue.put(ReplyAction(self.ws, self.get_message(), ''))
				yield from asyncio.sleep(1)

			yield from asyncio.sleep(0.1)
			yield from action_queue.put(self)

		return play_and_repeat

class SkipSongAction(SongAction):
	FIRST_PASS = True

	def process_state(self, state):
		self.next_song = None
		self.song_after = None
		if self.currently_playing(state['db']):
			queue = self.queued_songs(state['db'])
			if len(queue) > 0:
				self.next_song = queue[0]
			if len(queue) > 1:
				self.song_after = queue[1]

	def get_message(self):
		song_after = 'Nothing' if self.song_after == None else '{} (from {})'.format(
			self.song_after.youtube_info.display(),
			self.song_after.user
			)
		return 'Playing: {} (from {})\n!play {}\nNext: {}'.format(
				self.next_song.youtube_info.display(),
				self.next_song.user,
				self.next_song.youtube_info.url,
				song_after)

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def skip():			
			if self.next_song:
				print("Skipping to {}".format(self.next_song.youtube_info))
				yield from action_queue.put(ReplyAction(self.ws, self.get_message(), ''))
				yield from asyncio.sleep(1)

		return skip

class VoteSkipAction(SongAction):

	def process_state(self, state):
		print('skip count:', self.skip_count(state['db'], 60*60))


class DebugListCurrentSong(SongAction):
	def process_state(self, state):
		current_song = self.currently_playing(state['db'])
		if current_song:
			if current_song.remaining_duration() < 20:
				print("Current song:{}, remaining: {}".format(current_song, current_song.remaining_duration()))
		if not hasattr(self, 'current_song'):
			self.current_song = current_song
			if current_song:
				print("Song change: Current song:{}, remaining: {}".format(current_song, current_song.remaining_duration()))
		else:
			if self.current_song != current_song:
				self.current_song = current_song
				if current_song:
					print("Song change: Current song:{}, remaining: {}".format(current_song, current_song.remaining_duration()))

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def wait_and_repeat():
			yield from asyncio.sleep(5)
			yield from action_queue.put(self)
		return wait_and_repeat

class DumpQueue(SongAction):
	def __init__(self, ws, reply_to):
		super(DumpQueue, self).__init__(ws)
		self.reply_to = reply_to

	def process_state(self, state):
		song_queue = self.queued_songs(state['db'])
		if song_queue:
			self.song_queue = ['{} added by @{}\n command(copy & paste w/ !): play {}'.format(str(song.youtube_info.display()), song.user.name, str(song.youtube_info.url)) for song in song_queue]
		else:
			self.song_queue = None

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def wait_and_repeat():

			if self.song_queue:
				text = '\n'.join(self.song_queue)
			else:
				text = 'Nothing Queued'
			m = {"type":"send","data":{"content":text,"parent":self.reply_to},"id":str(next(id_generator))}
			yield from self.ws.send(json.dumps(m))
			yield from action_queue.put(ClearQueueAction(self.ws))

		return wait_and_repeat

class ListQueue(SongAction):
	def __init__(self, ws, reply_to):
		super(ListQueue, self).__init__(ws)
		self.reply_to = reply_to

	def process_state(self, state):
		song_queue = self.queued_songs(state['db'])
		if song_queue:
			self.song_queue = ['{} added by {}'.format(str(song.youtube_info.display()), song.user.name) for song in song_queue]
		else:
			self.song_queue = None

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def wait_and_repeat():

			if self.song_queue:
				text = '\n'.join(self.song_queue)
			else:
				text = 'Nothing Queued'
			m = {"type":"send","data":{"content":text,"parent":self.reply_to},"id":str(next(id_generator))}
			yield from self.ws.send(json.dumps(m))

		return wait_and_repeat

class DebugListQueue(SongAction):
	def process_state(self, state):
		song_queue = self.queued_songs(state['db'])
		if song_queue:
			song_queue = ['{} added by {}'.format(str(song.youtube_info), song.user.name) for song in song_queue]
		if not hasattr(self, 'song_queue'):
			self.song_queue = song_queue
			print("Current queue:{}".format(song_queue))
		else:
			if len(self.song_queue) != len(song_queue):
				self.song_queue = song_queue
				print("Current queue:{}".format(song_queue))

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def wait_and_repeat():
			yield from asyncio.sleep(0.1)
			yield from action_queue.put(self)
		return wait_and_repeat


class PingAction(Action):
	def __init__(self, ws):
		super(PingAction, self).__init__(ws)
		self.timestamp = int(time())

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def ping():
			m = {"type":"ping-reply","data":{"time":self.timestamp},"id":str(next(id_generator))}
			yield from self.ws.send(json.dumps(m))

		return ping


	def __str__(self):
		return 'PingAction'


class Command(DBItem):
	DB_TAG = 'database_command'
	TYPE = 'base'
	COMMAND = ''
	COMMAND_LOCATION = 'any'

	CLASS_FIELDS = [Parent]

	def __init__(self, message_or_db, ws):
		self.add_fields(self, Command.CLASS_FIELDS)
		super(Command, self).__init__(message_or_db)
		self.ws = ws

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

	def __init__(self, message_or_db, ws):
		self.add_fields(self, QueueCommand.CLASS_FIELDS)
		super(QueueCommand, self).__init__(message_or_db, ws)

	def _generate_confirmation_text(self):
		return 'Added to queue: {}'.format(self.youtube_info.display())

	def get_actions(self):
		return [ReplyAction(self.ws, self._generate_confirmation_text(), str(self.parent_id))]
		#return []

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
		return [ClearQueueAction(self.ws)]
		#return []

class ListQueueCommand(Command):
	DB_TAG = 'database_command_listqueue'
	COMMAND = '!list'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [ListQueue(self.ws, str(self.parent_id))]
		#return []

	@classmethod
	def help_string(cls):
		return "{}: List the songs currently in the queue.".format(cls.COMMAND)

class DumpQueueCommand(Command):
	DB_TAG = 'database_command_dumpqueue'
	COMMAND = '!dumpqueue'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [DumpQueue(self.ws, str(self.parent_id))]

	@classmethod
	def help_string(cls):
		return "{}: List all queued songs with youtube URL included and then DELETES the queue. Useful for shutting down the bot.".format(cls.COMMAND)

class SkipCommand(Command):
	DB_TAG = 'database_command_skip'
	COMMAND = '!skip'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [SkipSongAction(self.ws)]

	@classmethod
	def help_string(cls):
		return "{}: Skip a song. Please note: one song will be skipped per {} command! Use carefully.".format(cls.COMMAND, cls.COMMAND)

class TestSkipCommand(Command):
	DB_TAG = 'database_command_testskip'
	COMMAND = '!testskip'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [VoteSkipAction(self.ws)]

class NeonLightShowCommand(Command):
	DB_TAG = 'database_command_neonlightshow'
	COMMAND = '!neonlightshow'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		images = ['http://i.imgur.com/eBZO67G.gif', 'http://i.imgur.com/0bprD6k.gif', 'http://i.imgur.com/van2j15.gif', 'http://i.imgur.com/sYjX7Qv.gif']
		return [ReplyAction(self.ws, random.choice(images), str(self.parent_id))]


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
			return [ReplyAction(self.ws, self.get_help_lines(), str(self.parent_id))]
			#return []


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
				return event_class(packet, packet.ws)
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
		yield from message_queue.join() # process all messages
		yield from database_queue.join() # process all db inserts
		events = state['db'].search( where('type').contains(Event.DB_TAG) & (where('timestamp') > task.timestamp)) 
		events = [DBItem.create_object_from_db_entry(event, None) for event in events]
		satisfied = False
		for event in events:
			if task.satisfied_by_event(state, event):
				for action in task.get_actions():
					yield from action_queue.put(action)

				satisfied = True
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
		#print('processing action: {}'.format(action))
		action.process_state(state)
		task = action.get_task(id_gen, action_queue)
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
				tasks = [DBItem.create_object_from_db_entry(task, db_object.ws) for task in tasks]
				for task in tasks:
					if task.satisfied_by_event(state, db_object):
						for action in task.get_actions():
							yield from action_queue.put(action)
						break

			yield from database_queue.put(db_object.to_db_dict())

			if Command.DB_TAG in db_object.DB_TAG:
				if message.past:
					print('Adding to task queue: {}'.format(db_object))
					yield from command_task_queue.put(db_object)
				else:
					print('Adding to action queue: {}'.format(db_object))
					yield from command_action_queue.put(db_object)

		message_queue.task_done()

@asyncio.coroutine
def queue_command_actions():
	while True:
		command = yield from command_action_queue.get()	
		for action in command.get_actions():
			print('{} -> {}'.format(command, action))
			yield from action_queue.put(action)
		yield from command_task_queue.put(command)
		command_action_queue.task_done()



@asyncio.coroutine
def loop():
	ws = yield from websockets.connect('wss://euphoria.io/room/music/ws')
	#ws = yield from websockets.connect('ws://localhost:8765/')
	yield from action_queue.put(SetNickAction(ws, "♬|NeonDJBot"))
	yield from action_queue.put(DebugListCurrentSong(ws))
	yield from action_queue.put(DebugListQueue(ws))
	yield from action_queue.put(PlayNextSong(ws))

	while True:
		packet = yield from ws.recv()
		if not packet: #server d/c
			while True:
				yield from asyncio.sleep(1)
				try:
					ws = yield from websockets.connect('wss://euphoria.io/room/music/ws')
					packet = yield from ws.recv()
					break
				except:
					pass
		packet = Packet(ws, packet)

		if packet.type == 'ping-event':
			yield from action_queue.put(PingAction(ws))
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