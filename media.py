import re
import asyncio
import aiohttp
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

class YoutubeInfo(object):

	INFO_URL = 'http://gdata.youtube.com/feeds/api/videos/{}?alt=json'
	INFO_URL2 = 'https://content.googleapis.com/youtube/v3/videos?part=snippet%2C+status%2C+contentDetails&id={id}&key=AIzaSyAnpCOhbLezLbMwDOY0YdHXH4OWhYjXx-o'
	PLAY_URL = 'https://www.youtube.com/watch?v={id}'
	REGEX = r'((https?://)?youtube\S+)'

	@classmethod
	def create_from_message(cls, message):
		# returns list of tuples
		urls = re.findall(cls.REGEX, message.data['content'], flags=re.I)
		yt_url = None
		for url in urls:
			parsed_url = urlparse(url[0])
			if 'v=' not in parsed_url.query or ('youtube' not in parsed_url.path and 'youtube' not in parsed_url.netloc):
				continue
			yt_url = url[0]
		return YoutubeInfo(yt_url)

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
		self.url = None
		if url:
			self.url = url if url.find('http') == 0 else 'https://' + url
		self.prepared = False
		
	def set_data(self,youtube_id, title, sub_title, thumbnails, duration):
		self.youtube_id = youtube_id
		self.title = title
		self.sub_title = sub_title
		self.thumbnails = thumbnails
		if type(duration) is str:
			duration = duration[2:]
			total_seconds = 0
			while len(duration):
				i = 0
				time_str = ''
				while duration[i] in '0123456789':
					time_str += duration[i]
					i += 1
				unit = duration[i]
				i += 1

				if unit == 'H':
					total_seconds += int(time_str)*60*60
				elif unit == 'M':
					total_seconds += int(time_str)*60
				elif unit == 'S':
					total_seconds += int(time_str)

				duration = duration[i:]

			self.duration = {'seconds':total_seconds}
		else:
			self.duration = duration
		self.prepared = True

	@asyncio.coroutine
	def prepare(self):
		if not self.url:
			return
		query = urlparse(self.url).query
		youtube_id = parse_qs(query)['v'][0]
		url = self.INFO_URL2.format(id=youtube_id)
		resp = yield from aiohttp.request('GET', url)
		youtube_info = yield from resp.json()
		youtube_info = youtube_info['items'][0]

		self.set_data(youtube_id,
			youtube_info['snippet']['title'], youtube_info['snippet']['description'],
			youtube_info['snippet']['thumbnails'], youtube_info['contentDetails']['duration'])
			

	def time_seconds(self):
		if not self.prepared:
			return 0
		assert(len(self.duration.keys()) == 1)
		return int(self.duration['seconds'])

	def get_play_url(self):
		if self.prepared:
			return self.PLAY_URL.format(id=self.youtube_id)
		else:
			return self.url

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
