# -*- coding: utf-8 -*-
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

class User(object):
	def __init__(self, user_json_object):
		self.user_id = user_json_object['id'].split('-')[0]
		self.name = user_json_object['name']

	def to_db_dict(self):
		return {
			'id': self.user_id,
			'name': self.name
		}

	@staticmethod
	def from_db_dict(db_dict, ws=None):
		return User({'id': db_dict['id']+'-', 'name': db_dict['name']})

	def __str__(self):
		return self.name

class Message(object):
	def __init__(self, ws, data):
		self.data = data
		self.ws = ws

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
			return [Message(self.ws, m) for m in self.data['log']]

		return []

	def __str__(self):
		return '[{}]{}'.format(self.type, self.data)

#{"id":"","type":"send-event","data":{"id":"00cr0zhdfpj40","parent":"","time":1428420388,"sender":{"id":"0bf0b63a5b0434e8-000000b3","name":"G3","server_id":"heim.1","server_era":"00cqy7ymu43cw"},"content":"I'll have to explore their music a little as well.","edited":null,"deleted":null}}
#"sender":{"id":"0bf0b63a5b0434e8-000000b3","name":"G3","server_id":"heim.1","server_era":"00cqy7ymu43cw"}


class YoutubeInfo(object):

	@staticmethod
	def get_from_url(youtube_url):
		info_url = 'http://gdata.youtube.com/feeds/api/videos/{}?alt=json'
		query = urlparse(youtube_url).query
		youtube_id = parse_qs(query)['v'][0]
		url = info_url.format(youtube_id)
		resp = requests.get(url)
		youtube_info = resp.json()['entry']
		return YoutubeInfo(
			youtube_url,
			youtube_info['title']['$t'],
			youtube_info['content']['$t'],
			youtube_info['media$group']['media$thumbnail'],
			youtube_info['media$group']['yt$duration'])

	def __init__(self, url, title, sub_title, thumbnails, duration):
		self.url = url
		self.title = title
		self.sub_title = sub_title
		self.thumbnails = thumbnails
		self.duration = duration

	def time_seconds(self):
		assert(len(self.duration.keys()) == 1)
		return int(self.duration['seconds'])

	def time_string(self):
		return str(timedelta(seconds=self.time_seconds()))

	def select_thumbnail(self):
		return self.thumbnails[0]['url']

	def to_db_dict(self):
		return {
			'url': self.url,
			'title': self.title,
			'sub_title': self.sub_title,
			'thumbnails': self.thumbnails,
			'duration': self.duration,
		}

	@staticmethod
	def from_db_dict(db_dict, ws=None):
		return YoutubeInfo(
			db_dict['url'],
			db_dict['title'],
			db_dict['sub_title'],
			db_dict['thumbnails'],
			db_dict['duration']
			)

	def __str__(self):
		return '{} [{}]'.format(self.title, self.time_string())

class DBItem(object):
	DB_TAG = "database_"

	SUBCLASSES = {}

	def __init__(self, message_or_db):
		if not isinstance(message_or_db, Message):
			self.timestamp = message_or_db['timestamp']
			self.user = User.from_db_dict(message_or_db['user'])
			self.uid = message_or_db['uid']
		else:
			self.timestamp = message_or_db.timestamp
			self.user = User(message_or_db.data['sender'])
			self.uid = message_or_db.data['id']

	def to_db_dict(self):
		return {
			'type': self.DB_TAG,
			'uid': self.uid,
			'timestamp': self.timestamp,
			'user': self.user.to_db_dict(),
			'data': self._data_to_db_format(),
		}

	def _data_to_db_format(self):
		return {}

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


class Event(DBItem):
	DB_TAG = 'database_event'
	EVENT_TEXT = ''

	def __init__(self, message_or_db, ws):
		super(Event, self).__init__(message_or_db)
		self.ws = ws
		self.is_prepared = True
		if not isinstance(message_or_db, Message):
			self.data = message_or_db['data']
		else:
			self.data = message_or_db.data['content']
			self._process_data()

	def _process_data(self):
		pass

	def prepare(self):
		pass

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
	def is_this_event(cls, message):
		return cls.EVENT_TEXT in message.lower()

class PlayEvent(Event):
	DB_TAG = 'database_event_play'
	EVENT_TEXT = '!play'

	def __init__(self, message_or_db, ws):
		super(PlayEvent, self).__init__(message_or_db, ws)
		if not isinstance(message_or_db, Message):
			self.youtube_info = YoutubeInfo.from_db_dict(message_or_db['data'])
			self.data = self.youtube_info.url
		else:
			self.youtube_info = None
			self.data = message_or_db.data['content']
			self._process_data()

	def _process_data(self):
		self.is_prepared = False
		sstring = '{}\s*([^\s]+)\s?'.format(self.EVENT_TEXT)
		results = re.search(sstring, self.data)
		self.data = results.group(1)

	def prepare(self):
		self.youtube_info = YoutubeInfo.get_from_url(self.data)
		self.is_prepared = True

	def get_duration(self):
		if self.youtube_info:
			return timedelta(seconds=(self.youtube_info.time_seconds() + 10))
		return timedelta(seconds=0)

	def _data_to_db_format(self):
		return self.youtube_info.to_db_dict()

	def __str__(self):
		return '[Event] {} {} ({})'.format(self.EVENT_TEXT, self.data, 'playing' if self.is_active() else 'finished')


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
			m = {"type":"send","data":{"content":self.text,"parent":self.reply_to},"id":str(id_generator())}
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
	        element['data']['executed'] = True

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
		db.remove(where('type') == QueueCommand.DB_TAG and where('data').has('executed') == False)

	def queued_songs(self, db):
		queued_songs = db.search(where('type') == QueueCommand.DB_TAG and where('data').has('executed') == False)
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
			db.update(self.set_executed(), where('type') == QueueCommand.DB_TAG and where('uid') == command.uid)
			return command
		else:
			return None

	def skip_count(self, db, seconds):
		now = int(time())
		return db.search(where('type') == SkipCommand.DB_TAG and where('timestamp') > (now - seconds)) 

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
			m = {"type":"nick","data":{"name":self.nick},"id":str(id_generator())}
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


class PlaySongAction(SongAction):
	def __init__(self, ws, url):
		super(PlaySongAction, self).__init__(ws)
		self.text = '!play {}'.format(url)

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def reply():
			m = {"type":"send","data":{"content":self.text,"parent":''},"id":str(id_generator())}
			yield from self.ws.send(json.dumps(m))

		return reply

	def __str__(self):
		return 'PlaySongAction -> {}'.format(self.text)

class PlayNextSong(SongAction):
	FIRST_PASS = True

	def process_state(self, state):
		self.next_song = None
		next_queue_item = self.get_next_if_nothing_playing(state['db'])
		if next_queue_item:
			self.next_song = next_queue_item
			queue = self.queued_songs(state['db'])
			if len(queue):
				self.song_after = queue[0]
			else:
				self.song_after = None

	def get_message(self):
		if not self.song_after:
			return 'Playing: {} (from {})\n!play {}\nNext: None'.format(
				self.next_song.youtube_info,
				self.next_song.user,
				self.next_song.youtube_info.url)
		else:
			return 'Playing: {} (from {})\n!play {}\nNext: {} (from {})'.format(
				self.next_song.youtube_info,
				self.next_song.user,
				self.next_song.youtube_info.url, 
				self.song_after.youtube_info,
				self.song_after.user)

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
		next_queue_item = self.skip_current(state['db'])
		if next_queue_item:
			self.next_song = next_queue_item
			queue = self.queued_songs(state['db'])
			if len(queue):
				self.song_after = queue[0]
			else:
				self.song_after = None

	def get_message(self):
		if not self.song_after:
			return 'Playing: {} (from {})\n!play {}\nNext: None'.format(
				self.next_song.youtube_info,
				self.next_song.user,
				self.next_song.youtube_info.url)
		else:
			return 'Playing: {} (from {})\n!play {}\nNext: {} (from {})'.format(
				self.next_song.youtube_info,
				self.next_song.user,
				self.next_song.youtube_info.url, 
				self.song_after.youtube_info,
				self.song_after.user)

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
			self.song_queue = ['{}({}) added by @{}'.format(str(song.youtube_info), str(song.youtube_info.url), song.user.name) for song in song_queue]
		else:
			self.song_queue = None

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def wait_and_repeat():

			if self.song_queue:
				text = '\n'.join(self.song_queue)
			else:
				text = 'Nothing Queued'
			m = {"type":"send","data":{"content":text,"parent":self.reply_to},"id":str(id_generator())}
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
			self.song_queue = ['{} added by {}'.format(str(song.youtube_info), song.user.name) for song in song_queue]
		else:
			self.song_queue = None

	def get_task(self, id_generator, action_queue):
		@asyncio.coroutine
		def wait_and_repeat():

			if self.song_queue:
				text = '\n'.join(self.song_queue)
			else:
				text = 'Nothing Queued'
			m = {"type":"send","data":{"content":text,"parent":self.reply_to},"id":str(id_generator())}
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
			m = {"type":"ping-reply","data":{"time":self.timestamp},"id":str(id_generator())}
			yield from self.ws.send(json.dumps(m))

		return ping


	def __str__(self):
		return 'PingAction'


class Command(DBItem):
	DB_TAG = 'database_command'
	TYPE = 'base'
	COMMAND = ''
	COMMAND_LOCATION = 'any'

	def __init__(self, message_or_db, ws):
		super(Command, self).__init__(message_or_db)
		self.ws = ws
		self.is_prepared = True
		if not isinstance(message_or_db, Message):
			self.parent_id = message_or_db['data']['parent_id']
			self.data = {}
			self.executed = message_or_db['data']['executed']
		else:
			self.executed = True
			self.parent_id = message_or_db.data['id']
			self.data = message_or_db.data['content']
			self._process_data()

	def _data_to_db_format(self):
		base = super(Command, self)._data_to_db_format()
		base['parent_id'] = self.parent_id
		base['executed'] = self.executed

		return base

	def _process_data(self):
		pass

	def prepare(self):
		pass

	def get_actions(self):
		return []

	@classmethod
	def help_string(cls):
		return None

	@classmethod
	def is_this_command(cls, message):
		if cls.COMMAND_LOCATION == 'any':
			return cls.COMMAND in message.lower()
		elif cls.COMMAND_LOCATION == 'start':
			return message.lower().find(cls.COMMAND) == 0 

	def __str__(self):
		return '{} -> {}'.format(self.user, self.TYPE)


class QueueCommand(Command):
	DB_TAG = 'database_command_queue'
	TYPE = 'queue'
	COMMAND = '!queue'
	COMMAND_LOCATION = 'start'

	def __init__(self, message_or_db, ws):
		super(QueueCommand, self).__init__(message_or_db, ws)
		if not isinstance(message_or_db, Message):
			self.youtube_info = YoutubeInfo.from_db_dict(message_or_db['data']['youtube_info'])
			self.data = self.youtube_info.url
			self.is_prepared = True
		else:
			self.youtube_info = None

	def _process_data(self):
		self.is_prepared = False
		self.executed = False
		sstring = '{}\s*([^\s]+)\s?'.format(self.COMMAND)
		results = re.search(sstring, self.data)
		self.data = results.group(1)

	def prepare(self):
		self.youtube_info = YoutubeInfo.get_from_url(self.data)
		self.is_prepared = True

	def _generate_confirmation_text(self):
		return 'Added to queue: {}'.format(self.youtube_info)

	def get_actions(self):
		return [ReplyAction(self.ws, self._generate_confirmation_text(), self.parent_id)]

	def _data_to_db_format(self):
		base = super(QueueCommand, self)._data_to_db_format()
		base['youtube_info'] = self.youtube_info.to_db_dict()

		return base

	@classmethod
	def help_string(cls):
		return "{} <youtube url> : Add a song to the queue. Does not support any link shorterners (youtube's included). Songs will be played in order.".format(cls.COMMAND)

	def __str__(self):
		return '[QueueCommand] {}'.format(self.data)

class ClearQueueCommand(Command):
	DB_TAG = 'database_command_clearqueue'
	COMMAND = '!clearqueue'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [ClearQueueAction(self.ws)]

class ListQueueCommand(Command):
	DB_TAG = 'database_command_listqueue'
	COMMAND = '!list'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [ListQueue(self.ws, self.parent_id)]

	@classmethod
	def help_string(cls):
		return "{}: List the songs currently in the queue.".format(cls.COMMAND)

class DumpQueueCommand(Command):
	DB_TAG = 'database_command_dumpqueue'
	COMMAND = '!dumpqueue'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [DumpQueue(self.ws, self.parent_id)]

	@classmethod
	def help_string(cls):
		return "{}: List all queued songs with youtube URL included and then DELETES the queue. Useful for shutting down the bot.".format(cls.COMMAND)

class SkipCommand(Command):
	DB_TAG = 'database_command_listqueue'
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
	DB_TAG = 'database_command_testskip'
	COMMAND = '!neonlightshow'
	COMMAND_LOCATION = 'start'

	def get_actions(self):
		return [ReplyAction(self.ws, 'http://i.imgur.com/0bprD6k.gif', self.parent_id)]


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
			return [ReplyAction(self.ws, self.get_help_lines(), self.parent_id)]


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

def create_event(packet):
	enabled_events = [
		PlayEvent,
	]
	content = packet.data['content']
	for event_class in enabled_events:
		if event_class.is_this_event(content):
			try:
				return event_class(packet, packet.ws)
			except:
				print('failed to create event: {}'.format(packet.data))
	return None

def create_command(packet):
	global enabled_commands
	content = packet.data['content']
	for command_class in enabled_commands:
		if command_class.is_this_command(content):
			return command_class(packet, packet.ws)
	return None


db = TinyDB('./MusicBotDB.json')

def message_id_exists(uid):
	exists = db.search(where('uid') == uid)
	return len(exists) > 0


event_queue = asyncio.Queue()
action_queue = asyncio.Queue()
message_queue = asyncio.Queue()
command_queue = asyncio.Queue()
prep_queue = asyncio.Queue()
database_queue = asyncio.Queue()

state = {
	'db': db,
	'play_next_handle': None
}

@asyncio.coroutine
def database_task():
	global state
	while True:
		db_item = yield from database_queue.get()
		if not message_id_exists(db_item['uid']):
				state['db'].insert(db_item)

@asyncio.coroutine
def execute_actions_task():
	global state
	def mid():
		i = 0
		while True:
			yield i
	while True:
		action = yield from action_queue.get()
		#print('processing action: {}'.format(action))
		action.process_state(state)
		task = action.get_task(mid, action_queue)
		asyncio.Task(task())

@asyncio.coroutine
def message_task():
	while True:
		message = yield from message_queue.get()
		if message_id_exists(message.uid):
			print('ignoring {}'.format(message.data['content']))
			continue
		command = create_command(message)
		if command:
			print('Command:{}'.format(message.data['content']))
			yield from command_queue.put(command)
		else:
			event = create_event(message)
			if event:
				print('Event:{}'.format(message.data['content']))
				yield from event_queue.put(event)
		

@asyncio.coroutine
def command_task():
	while True:
		command = yield from command_queue.get()
		if command.is_prepared:
			yield from database_queue.put(command.to_db_dict())
			for action in command.get_actions():
				yield from action_queue.put(action)
		else:
			yield from prep_queue.put(command)

@asyncio.coroutine
def event_task():
	while True:
		event = yield from event_queue.get()
		if event.is_prepared:
			yield from database_queue.put(event.to_db_dict())
		else:
			yield from prep_queue.put(event)

@asyncio.coroutine
def prep_task():
	while True:
		prep_item = yield from prep_queue.get()
		try:
			prep_item.prepare()
		except:
			print('Failed to process: {}'.format(prep_item))
			continue
		if Command.DB_TAG in prep_item.DB_TAG:
			yield from command_queue.put(prep_item)
		elif Event.DB_TAG in prep_item.DB_TAG:
			yield from event_queue.put(prep_item)


@asyncio.coroutine
def loop():
	ws = yield from websockets.connect('wss://euphoria.io/room/music/ws')
	yield from action_queue.put(SetNickAction(ws, "♬|NeonDJBot"))
	yield from action_queue.put(DebugListCurrentSong(ws))
	yield from action_queue.put(DebugListQueue(ws))
	yield from action_queue.put(PlayNextSong(ws))

	while True:
		while True:
			try:
				packet = yield from ws.recv()
				break
			except:
				try:
					ws = yield from websockets.connect('wss://euphoria.io/room/music/ws')
				except:
					continue

		packet = Packet(ws, packet)

		if packet.type == 'ping-event':
			yield from action_queue.put(PingAction(ws))
		else:
			for message in packet.messages():
				yield from message_queue.put(message)

asyncio.Task(event_task())
asyncio.Task(database_task())
asyncio.Task(execute_actions_task())
asyncio.Task(message_task())
asyncio.Task(command_task())
asyncio.Task(prep_task())

asyncio.get_event_loop().run_until_complete(loop())