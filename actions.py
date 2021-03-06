import asyncio
from time import time, localtime
from datetime import timedelta, datetime
from websockets.exceptions import InvalidState
import json

class Action(object):
	def __init__(self):
		self.ws = None
		self.timestamp = int(time())
		self.log_queue = None
		self.done = False
		self.log_executing = True

	def set_log_queue(self, queue):
		self.log_queue = queue

	#do doo do, poor engineering practices
	# copied and pasted from logging middleware because I am a bad person
	@asyncio.coroutine
	def log(self, level, *args):
		from middleware import LoggerMiddleware, Log
		if self.log_queue:
			l = Log(level, 'ACTION', *args)
			yield from self.log_queue.put((LoggerMiddleware.TAG ,l))

	def exception(self, *args):
		from middleware import Log
		asyncio.async(self.log(Log.EXCEPTION, *args))

	def error(self, *args):
		from middleware import Log
		asyncio.async(self.log(Log.ERROR, *args))

	def debug(self, *args):
		from middleware import Log
		asyncio.async(self.log(Log.DEBUG, *args))

	def verbose(self, *args):
		from middleware import Log
		asyncio.async(self.log(Log.VERBOSE, *args))

	def packet_to_send(self, db):
		return None

	def get_coroutine(self, db, id_generator, action_queue):
		@asyncio.coroutine
		def task():
			if self.log_executing:
				self.debug('{}: Executing.', self)
			message = self.packet_to_send(db)
			if message:
				# set id
				message['id'] = str(next(id_generator))
				try:
					yield from self.ws.send(json.dumps(message))
				except InvalidState:
					# websocket closed
					self.error('{}: websocket closed!', self)
					yield from action_queue.put(self)
				except TypeError:
					self.error('{}: Failed to send: {}', self, json.dumps(message))
				finally:
					self.done = True

			self.done = True

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
		#print("Send: {}".format(text))
		return cls._wrap('send', {"content":text,"parent":parent_message})

	@classmethod
	def nick_packet(cls, nick):
		#print("Nick: {}".format(nick))
		return cls._wrap('nick', {"name":nick})

class PingAction(PacketAction):
	def __init__(self):
		super(PingAction, self).__init__()
		self.log_executing = False

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
		self.log_executing = False

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
			song_to_play.youtube_info.get_play_url(),
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
	def __init__(self, queue, reply_to=''):
		super(DumpQueue, self).__init__()
		self.reply_to = reply_to
		self.queue = queue

	def packet_to_send(self, db):
		song_queue = self.queue
		message = 'Nothing Queued'
		if song_queue:
			strings = []
			for song in song_queue:
				strings.append('{} added by @{}\n command(copy & paste w/ !): play {}'.format(str(song.youtube_info.display()), song.user.name, str(song.youtube_info.get_play_url())))

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