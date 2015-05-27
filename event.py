
import asyncio

from db_object import DBItem
from core import Message
from datetime import timedelta, datetime

from media import YoutubeInfo

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
		count = 0
		if cls.EVENT_TEXT in message.lower():
			count = len(cls.EVENT_TEXT)
		return count

	def __repr__(self):
		return str(self)


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

DBItem.SUBCLASSES.update(
	{PlayEvent.DB_TAG: PlayEvent}
	)