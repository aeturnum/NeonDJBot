import asyncio
import websockets
import json
import time

def convert_timestamp(base, timestamp):
	now = int(time.time())
	delta = timestamp - base
	return now + delta

def update_timestamps(item, start_timestamp):
	for field_name in ['time', 'next']:
		if field_name in item:
			value = item[field_name]
			if field_name == 'time' and not start_timestamp:
				start_timestamp = value

			item[field_name] = convert_timestamp(start_timestamp, value)

	return start_timestamp

@asyncio.coroutine
def loop(websocket, path):
	f = open('music_room.log' , 'r')
	start_timestamp = None
	
	line = f.readline()
	count = 0
	while line != '':
		line_json = json.loads(line.strip())

		if line_json['type'] == 'snapshot-event':
			for message in line_json['data']['log']:
				update_timestamps(message, start_timestamp)
		else:
			start_timestamp = update_timestamps(line_json['data'], start_timestamp)

		if count < 1:
			yield from websocket.send(json.dumps(line_json))
		yield from asyncio.sleep(5)
		line = f.readline()

start_server = websockets.serve(loop, 'localhost', 8765)

asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()
