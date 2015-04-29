import asyncio
import sys
from db_object import DBItem
from time import time, localtime, strftime
from core import BotMiddleware, UsesMiddleware, FakeRAWMiddleware, FakeActionMiddleware
import traceback

from actions import (
	QueuedNotificationAction, 
	PlayOneSongAction, 
	ListQueueAction,
	DumpQueue,
	PlayQueueAction
	)

from tinydb import TinyDB, where
from event import PlayEvent

from command import (
	QueueCommand, 
	SkipCommand, 
	ClearQueueCommand, 
	ListQueueCommand, 
	TestSkipCommand,
	NeonLightShowCommand, 
	HelpCommand, 
	DumpQueueCommand
	)

class Log(object):
	VERBOSE = 4
	DEBUG = 3
	WARN = 2
	ERROR = 1
	EXCEPTION = 0

	def __init__(self, level, source='', *args):
		super(Log, self).__init__()
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
		info_string = '[{}|{:.4}|{}]'.format(timestring, self.source, level_string[self.level])
		try:
			arg_string = str(self.args[0]).format(*self.args[1:])
		except Exception as e:
			arg_string = 'malformed log: {} -> {}'.format(self, e)
			self.level = Log.EXCEPTION

		full_log = arg_string
		if self.level == Log.EXCEPTION:
			full_log = self.add_traceback(full_log)

		log_lines = []
		first = True
		for line in full_log.split('\n'):
			if first:
				log_lines.append('{}: {}'.format(info_string, line))
				first = True
			else:
				log_lines.append('{}| {}'.format(info_string, line))

		return '\n'.join(log_lines)

	def __str__(self):
		return 'source: {}, level: {}, args: {}'.format(self.source, self.level, self.args)

class LoggerMiddleware(BotMiddleware):
	TAG = 'tag_logger_middleware'
	TYPE = BotMiddleware.INPUT

	def __init__(self):
		super(LoggerMiddleware, self).__init__()
		default_log = sys.argv[0].replace('.py', '') + '.log'
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
	def handle_event(self, log):
		if isinstance(log, Log):
			file_name = log.file if log.file else self.default_file
			fh = self.log_files[file_name]
			print(log.log_str())
			if log.level < Log.VERBOSE:
				fh.write(log.log_str() + '\n')
				fh.flush()

class UsesLogger(UsesMiddleware):
	PRODUCES = LoggerMiddleware
	LOG_NAME = ''

	@classmethod
	def setup_self(cls, self):
		self._exception_handler = self.handle_exception

	def log(self, level, *args):
		l = Log(level, self.LOG_NAME, *args)
		asyncio.async(
			self.send(l, tag=LoggerMiddleware.TAG)
			)

	def exception(self, *args):
		return self.log(Log.EXCEPTION, *args)

	def error(self, *args):
		return self.log(Log.ERROR, *args)

	def debug(self, *args):
		return self.log(Log.DEBUG, *args)

	def verbose(self, *args):
		return self.log(Log.VERBOSE, *args)

	@asyncio.coroutine
	def handle_exception(self, e):
		self.exception('Caught exception in {}.loop: {}', self.__class__, e)
		yield from asyncio.sleep(1)

class UsesRaw(UsesMiddleware):
	CONSUMES = FakeRAWMiddleware

class SendsActions(UsesMiddleware):
	PRODUCES = FakeActionMiddleware

	@asyncio.coroutine
	def send_action(self, action):
		yield from self.send(action, tag=FakeActionMiddleware.TAG)


class PacketMiddleware(BotMiddleware, UsesLogger, UsesRaw):
	TAG = 'tag_bot_message'
	CONTROL_TAG = 'tag_bot_message_control'
	TYPE = BotMiddleware.OUTPUT

	CONTROL_BACKLOG_START = 'bot_message_backlog_start'
	CONTROL_BACKLOG_END = 'bot_message_backlog_end'

	LOG_NAME = 'Packet'

	ENABLED_MESSAGES = [
		NeonLightShowCommand,
		HelpCommand,
	]

	def __init__(self):
		super(PacketMiddleware, self).__init__()
		self.enabled_messages = []
		self.enabled_messages.extend(self.ENABLED_MESSAGES)
		UsesRaw.set_handler(self, self.handle_event)

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

	def save_to_db(self, db_item):
		#seld.debug('adding {} to DB' db_item)
		if hasattr(db_item, 'to_db_dict'):
			db_dict = db_item.to_db_dict()
			self.db.insert(db_dict)

	@asyncio.coroutine
	def send_control_message(self, message):
		#def _send(queue_list, tag, message):
		yield from self._send(self._output[self.TAG], self.CONTROL_TAG, message)

	@asyncio.coroutine
	def handle_event(self, packet):
		if packet.type == 'snapshot-event':
			yield from self.send_control_message(self.CONTROL_BACKLOG_START)
		for message in packet.messages():
			if self.message_id_exists(message.uid):
				if not message.past:
					self.verbose('ignoring {}', message.data['content'])
				continue

			#self.verbose('message: {}', message.data['content'])
			db_object = self.create_db_object(message)
			if db_object:
				#self.debug('DB Object: {}', db_object)
				if not db_object.is_prepared():
					try:
						yield from db_object.prepare() 
					except Exception as e:
						self.error('Failed to process: {}; Exception: {}', db_object, e)
						continue

				self.save_to_db(db_object)

				if db_object.DB_TAG == HelpCommand.DB_TAG:
					db_object.set_commands(self.enabled_messages)

				yield from self.send(db_object)
		if packet.type == 'snapshot-event':
			yield from self.send_control_message(self.CONTROL_BACKLOG_END)

class UsesPackets(UsesMiddleware):
	CONSUMES = PacketMiddleware

	@classmethod
	def setup_self(cls, self):
		self._recv_functions[PacketMiddleware.CONTROL_TAG] = self.handle_control_message

	@asyncio.coroutine
	def handle_control_message(self, message):
		if message == PacketMiddleware.CONTROL_BACKLOG_START:
			self.in_backlog = True
		elif message == PacketMiddleware.CONTROL_BACKLOG_END:
			self.in_backlog = False


class SimpleActionMiddleware(BotMiddleware, UsesPackets, UsesLogger, SendsActions):
	TAG = 'tag_simple_action_middleware'
	TYPE = BotMiddleware.INPUT	

	LOG_NAME = 'Action'

	@asyncio.coroutine
	def setup(self, db):
		yield from super(SimpleActionMiddleware, self).setup(db)
		UsesCommands.set_handler(self, self.handle_event)

	@asyncio.coroutine
	def handle_event(self, command):
		if hasattr(command, 'get_actions'):
			for action in command.get_actions():
				yield from self.send_action(action)


class QueueUpdate(object):
	NEW_SONGS = 'queue_update_type_new_songs'
	SONGS_REMOVED = 'queue_update_type_removed_songs'

	def __init__(self, queue, old_queue):
		self.queue = queue
		if len(queue > old_queue):
			self.type = self.NEW_SONGS
			self.change = list(set(queue) - set(old_queue))
		else:
			self.type = self.SONGS_REMOVED
			self.change = list(set(old_queue) - set(queue))

class QueueMiddleware(BotMiddleware, UsesPackets, UsesLogger, SendsActions):
	TAG = 'tag_queue_status'
	TYPE = BotMiddleware.OUTPUT

	LOG_NAME = 'Q_Mgr'
	MIDDLEWARE_SUPPORT_REQUESTS = {
		PacketMiddleware.TAG: [
			QueueCommand, SkipCommand, ClearQueueCommand, ListQueueCommand, DumpQueueCommand
		]
	}

	def __init__(self):
		super(PlayQueuedSongsMiddleware, self).__init__()
		self.song_queue = []
		UsesCommands.set_handler(self, self.handle_event)

	def load_state_from_db(self, db):
		super(QueueMiddleware, self).load_state_from_db(db)
		self.debug('load state from db')
		saved_state = db.search(where('type') == self.TAG)
		if saved_state:
			queue = [db.search(where('uid') == uid)[0] for uid in saved_state[0]['queue']]
			self.song_queue = [DBItem.create_object_from_db_entry(song) for song in queue]
			self.song_queue.sort()
			self.debug('loaded queue: {}', self.song_queue)

	def save_state_to_db(self, db):
		db_dict = {
			'type': self.TAG,
			'queue': [str(item.uid) for item in self.song_queue]
		}
		if db.search(where('type') == self.TAG):
			db.update(db_dict, where('type') == self.TAG)
		else:
			db.insert(db_dict)

	@asyncio.coroutine
	def handle_event(self, message):
		self.verbose('Got Message: {}', message.DB_TAG)
		reply_to = message.uid

		action = None

		if QueueCommand.DB_TAG in message.DB_TAG:
			old_queue = self.song_queue
			self.song_queue.append(message)
			yield from self.send(QueueUpdate(self.song_queue, old_queue))
			#action = QueuedNotificationAction(self.song_queue, self.current_song, message.youtube_info, reply_to)
		elif ClearQueueCommand.DB_TAG in message.DB_TAG:
			yield from self.send(QueueUpdate(self.song_queue, []]))
			self.song_queue = []
		elif ListQueueCommand.DB_TAG in message.DB_TAG:
			action = ListQueueAction(self.song_queue, self.current_song, reply_to)
		elif DumpQueueCommand.DB_TAG in message.DB_TAG:
			action = DumpQueue(self.song_queue)
			yield from self.send(QueueUpdate(self.song_queue, []))
			self.song_queue = []

		if action:
			if self.in_backlog:
				self.verbose('In backlog, would have sent: {}', action)
			else:
				yield from self.send_action(action)

		self.save_state_to_db(self.db)
		#self.debug(self.status_string())

class UsesQueue(UsesMiddleware):
	CONSUMES = QueueMiddleware

class SongEvent(object):
	def __init__(self, song, user, start_time, end_time):
		self.song = song
		self.user = user
		self.start = start_time
		self.end = end_time

	def remaining_duration(self):
		return self.end - int(time())

	def duration(self):
		return self.end = self.start

class SongPlaying(SongEvent):
	pass

class SongEnded(SongEvent):
	def __init__(self, song):
		super(SongEnded, self).__init__(
			song.song, song.user,
			song.start_time, int(time())
			)


class NowPlayingMiddleware(BotMiddleware, UsesPackets, UsesLogger):
	TAG = 'tag_now_playing_middleware'
	TYPE = BotMiddleware.OUTPUT

	LOG_NAME = 'NowPlaying'
	MIDDLEWARE_SUPPORT_REQUESTS = {
		PacketMiddleware.TAG: [
			PlayEvent
		]
	}

	def __init__(self):
		super(NowPlayingMiddleware, self).__init__()
		self.song_ended_task = None
		UsesPackets.set_handler(self, self.handle_event)

	def load_state_from_db(self, db):
		super(NowPlayingMiddleware, self).load_state_from_db(db)
		events = db.search(where('type') == PlayEvent.DB_TAG)
		if events:
			events = sorted(events, key=lambda x: x['timestamp'])
			event = DBItem.create_object_from_db_entry(events[-1]) 
			if event.is_active():
				self.current_song = self.create_song_playing(event)
			self.debug('loded current song: {}', self.current_song)

	def create_song_playing(self, play_event):
		return SongPlaying(
			play_event.youtube_info,
			play_event.user,
			play_event.timestamp,
			play_event.timestamp + play_event.remaining_duration())

	@asyncio.coroutine
	def send_song_ended(self, song):
		yield from asyncio.sleep(song.remaining_duration())
		print('sending scheduled end song: ', SongEnded(self.current_song))
		yield from self.send(SongEnded(song))


	def schedule_song_ended(self):
		if self.song_ended_task:
			self.song_ended_task.cancel()
		self.song_ended_task = asyncio.get_event_loop().create_task(
			self.send_song_ended(self.current_song)
			)

	@asyncio.coroutine
	def handle_event(self, event):
		if PlayEvent.DB_TAG in event.DB_TAG:
			if self.current_song:
				print('sending end song: ', SongEnded(self.current_song))
				yield from self.send(SongEnded(self.current_song))
			self.current_song = self.create_song_playing(event)
			self.schedule_song_ended()
			print('sending current song: ', self.current_song)
			yield from self.senf(self.current_song)

class UsesNowPlaying(UsesMiddleware):
	CONSUMES = NowPlayingMiddleware

class PlayQueuedSongsMiddleware(BotMiddleware, UsesNowPlaying, UsesQueue, UsesLogger, SendsActions):
	TAG = 'tag_play_queued_songs'
	TYPE = BotMiddleware.OUTPUT	

	LOG_NAME = 'PlaySong'

	def __init__(self):
		super(NowPlayingMiddleware, self).__init__()
		self.queue = []
		self.current_song = None
		UsesNowPlaying.set_handler(self, self.handle_songs)
		UsesQueue.set_handler(self, self.handle_queues)

	@asyncio.coroutine
	def handle_songs(self, song):
		if type(song) is SongPlaying:
			self.current_song = song
		elif type(song) is SongEnded:
			yield from self.send_action(PlayQueueAction(self.queue))

	@asyncio.coroutine
	def handle_queues(self, queue):

		if self.queue != queue:

		self.queue = queue.queue





class PlayQueuedSongsMiddleware(BotMiddleware, UsesPackets, UsesLogger, SendsActions):
	TAG = 'tag_queue_events'
	TYPE = BotMiddleware.OUTPUT

	LOG_NAME = 'Queue'
	MIDDLEWARE_SUPPORT_REQUESTS = {
		PacketMiddleware.TAG: [
			QueueCommand, SkipCommand, ClearQueueCommand, ListQueueCommand, TestSkipCommand, DumpQueueCommand, PlayEvent
		]
	}

	def __init__(self):
		super(PlayQueuedSongsMiddleware, self).__init__()
		self.song_queue = []
		self.current_song = None
		self.play_callback = None
		# queued a song, waiting to see if it turns up
		self.expecting_song = False
		self.in_backlog = False
		UsesPackets.set_handler(self, self.handle_event)

	def status_string(self):
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
		while self.in_backlog:
			self.verbose("In backlog messages, holding off on play")
			yield from asyncio.sleep(0.5)

		self.expecting_song = True
		yield from self.send_action(PlayOneSongAction(song_one, song_two))
		#yield from self.action_queue.put(PlayOneSongAction(song_one, song_two))

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
		#self.debug('handle_queue_command: ', command)
		self.song_queue.append(command)
		#self.debug('\tqueue:', self.song_queue)

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
	def handle_event(self, message):
		self.verbose('Got Message: {}', message.DB_TAG)
		reply_to = message.uid

		action = None
		if self.current_song and self.current_song.remaining_duration() == 0:
			self.current_song = None

		if QueueCommand.DB_TAG in message.DB_TAG:
			self.handle_queue_command(message)
			action = QueuedNotificationAction(self.song_queue, self.current_song, message.youtube_info, reply_to)
		elif PlayEvent.DB_TAG in message.DB_TAG:
			self.expecting_song = False
			self.handle_play_event(message)
		elif ClearQueueCommand.DB_TAG in message.DB_TAG:
			self.song_queue = []
		elif ListQueueCommand.DB_TAG in message.DB_TAG:
			action = ListQueueAction(self.song_queue, self.current_song, reply_to)
		elif DumpQueueCommand.DB_TAG in message.DB_TAG:
			action = DumpQueue(self.song_queue)

		if action:
			if self.in_backlog:
				self.verbose('In backlog, would have sent: {}', action)
			else:
				yield from self.send_action(action)

		self.play_song()
		self.save_state_to_db(self.db)
		self.debug(self.status_string())
