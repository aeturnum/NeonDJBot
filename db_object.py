import asyncio
from core import Message

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