#!/bin/python3
from ssdpy import SSDPClient
from urllib.parse import urlparse
from xml.dom import minidom
from threading import Thread
from datetime import timedelta
from pathlib import Path
import http.server
import socketserver
import socket
import tidalapi
import requests
import json
import time
import sys
import os
from icecream import ic 

quality = tidalapi.Quality('LOSSLESS')
config = tidalapi.Config(quality)
session = tidalapi.Session(config)
cache_dir = './cache/'
port = 8000

class Handler(http.server.SimpleHTTPRequestHandler):
	# Extend SimpleHTTPRequestHandler to serve a given directory.
	def __init__(self, *args, **kwargs):
		super().__init__(*args, directory=cache_dir+'media/', **kwargs)
	# While we're at it, silence the noise.
	def log_message(self, format, *args):
		pass

class Server():
	# Server needed threading as to not halt the rest of the program.
	def __init__(self):
		self.server = socketserver.TCPServer(('', port), Handler)
		self.thread = Thread(target=self.run)
	def run(self):
		self.server.serve_forever() 
	def start(self):
		self.thread.start()
	def shutdown(self):
		self.server.shutdown()

class PlayList():
	# Creates a list of tracks with associated metadata required for playback.
	# Keep in mind that we may want to add to the playlist in future.
	def __init__(self, album_no):
		self.album = session.album(album_no)
		self.tracks = self.album.tracks()
		self.items = []
		for i in range(len(self.tracks)):
			# Maybe also assign album_no?
			track = {}
			track['id'] = str(self.tracks[i].id)
			track['number'] = str(self.tracks[i].track_num)
			track['name'] = self.tracks[i].name
			track['artist'] = self.album.artist.name
			track['album'] = self.album.name
			track['cover'] = self.album.image(640) # Dimensions (+/- ^2) - 1280 max.
			track['duration'] = self.tracks[i].duration
			track['album_id'] = str(self.album.id)
			track['url'] = tidalapi.media.Track(session, self.tracks[i].id).get_url()
			self.items.append(track)
	def add(something):
		pass

def login():
	# Create required dirs if not exist.
	Path(cache_dir + 'media/').mkdir(parents=True, exist_ok=True)
	if os.path.isfile(cache_dir + '/login.json'):
		file = open(cache_dir + 'login.json')
		login = json.load(file)
		# Login to TIDAL using details from a previous OAuth login, 
		# automatically refreshes expired access tokens when refresh_token is supplied.
		session.load_oauth_session(login['token_type'], login['access_token'], 
								login['refresh_token'], login['expiry_time'])
	else: 
		# Login to TIDAL using a remote link.
		session.login_oauth_simple()
	
	# Check that we're now logged in.
	if session.check_login():
		# Create dictionary from session data.
		login = {'token_type': session.token_type, 'access_token': session.access_token, 
			'refresh_token': session.refresh_token, 'expiry_time': str(session.expiry_time)}
		# Write dictionary to json file.
		with open(cache_dir + 'login.json', 'w') as file:
			json.dump(login, file)
		# Only owner should read & write this file.
		os.chmod(cache_dir + 'login.json', 0o600)
		print("Logged in :)")
		return
	else: 
		print("Login failed :(")
		sys.exit(1)

def download(playlist, *args):
	# Benefits from threading, as to run in parallel to the main thread.
	# Tracks are streamed so that we can start playback before completion.
	for i in (range(len(playlist.items))):
		dl_dir = cache_dir + 'media/' + playlist.items[i]['album_id'] + '/'
		Path(dl_dir).mkdir(exist_ok=True)
		with requests.get(playlist.items[i]['url'], stream=True) as r:
			with open(dl_dir + playlist.items[i]['id'] + '.flac', 'wb') as f:
				for chunk in r.iter_content(chunk_size=1024):
					f.write(chunk)

def discover():
	print("Discovering local UPnP/DLNA AV renderers...")
	# This seems like the easiest way to perform an m-search and return upnp device details.
	# Set service to 'ssdp:all' to return all devices. I just want AVTransport for now.
	client = SSDPClient(timeout=3)
	service = 'urn:schemas-upnp-org:service:AVTransport:1'
	devices = client.m_search(service)
	device_list = []

	# Loop to process the required data to access each device's services.
	for d in devices:
		device = {}
		location =  d.get('location')
		# Parse the location to derive the device's base url.
		loc_url = urlparse(location)
		device['base_url'] = loc_url.scheme + '://' + loc_url.netloc
		# Find serviceType & controlURL elements.
		# Loop through serviceType elements to match defined service.
		# The required controlURL will share an index with the matched serviceType.
		parsedXML = minidom.parseString(requests.get(location).text)
		service_elements = parsedXML.getElementsByTagName('serviceType')
		control_elements = parsedXML.getElementsByTagName('controlURL')
		friendly_name = parsedXML.getElementsByTagName('friendlyName')
		device['friendly_name'] = friendly_name[0].firstChild.nodeValue
		for index, element in enumerate(service_elements): 
			if element.firstChild.nodeValue == service:
				device['ctl_url'] = control_elements[index].firstChild.nodeValue
				break
		device_list.append(device)

	# Output the discovered renderers.
	for i in range(len(device_list)):
		print('[' + str(i) + '] ' + device_list[i]['friendly_name'] + " found at " + 
									device_list[i]['base_url'])
	selected = int(input("Select by number (0-" + str(len(device_list)-1) + "): ")) # Needs validation.
	control = device_list[selected]['base_url'] + device_list[selected]['ctl_url']
	return control

def lookmeup(control):
	# Simple helper to find local IP, as seen by our selected renderer.
	device_ip = urlparse(control).hostname
	s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	s.connect((device_ip, 80))
	lip = s.getsockname()[0]
	s.close()
	return lip

def metadata(eid, track, local_ip):
	# eid is the desired element id for the returned element, i.e. CurrentURIMetaData.
	# Here we take the metadata collected for a given track 
	# & use it to compile a metadata element for a SOAP request.
	# Format captured from BubbleUPnP using Wireshark.
	head = '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/" xmlns:sec="http://www.sec.co.kr/" xmlns:pv="http://www.pv.com/pvns/">'
	item = '<item id="tidal/albums/' + track['album_id'] + '/' + track['id'] + '" parentID="tidal/albums/' + track['album_id'] + '" restricted="1">'
	upnp_class = '<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
	title = '<dc:title>' + track['name'] + '</dc:title>'
	creator = '<dc:creator>' + track['artist'] + '</dc:creator>'
	artist = '<upnp:artist>' + track['artist'] + '</upnp:artist>'
	cover = '<upnp:albumArtURI>' + track['cover'] + '</upnp:albumArtURI>'
	album = '<upnp:album>' + track['album'] + '</upnp:album>'
	number = '<upnp:originalTrackNumber>' + track['number'] + '</upnp:originalTrackNumber>'
	res = '<res protocolInfo="http-get:*:audio/x-flac:DLNA.ORG_OP=01;DLNA.ORG_FLAGS=01700000000000000000000000000000" bitsPerSample="16" sampleFrequency="44100" nrAudioChannels="2" duration="' + str(timedelta(seconds=track['duration'])) + '.000">http://' + local_ip + ':' + str(port) + '/' + track['album_id'] + '/' + track['id'] + '.flac</res>'
	close = '</item></DIDL-Lite>'
	# Use minidom to escape the above, creating new element (as sent via args).
	text = minidom.Text()
	element = minidom.Element(eid)
	text.data = head + item + upnp_class + title + creator + artist + cover + album + number + res + close
	element.appendChild(text)
	return element.toxml()

def soap(action, track, local_ip, control, time):
	# Create appropriate soap message for the required action.
	# Soap actions captured from BubbleUPnP using Wireshark.
	# TODO: Consider **kwargs or setting defaults - all are not always needed.
	head = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>'
	schema = '<s:Envelope s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
	instance = '<InstanceID>0</InstanceID>'
	return_info = False # Used for the 'info' posts.
	match action:
		case 'setCurrentURI':
			urn = '<u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
			uri = '<CurrentURI>' + 'http://' + local_ip + ':' + str(port) + '/' + track['album_id'] + '/' + track['id'] + '.flac</CurrentURI>'
			meta = metadata('CurrentURIMetaData', track, local_ip)
			close = '</u:SetAVTransportURI></s:Body></s:Envelope>'
			soap_msg = head + schema + urn + instance + uri + meta + close
			soap_action = '"urn:schemas-upnp-org:service:AVTransport:1#SetAVTransportURI"'
			
		case 'setNextURI':
			urn = '<u:SetNextAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
			uri = '<NextURI>' + 'http://' + local_ip + ':' + str(port) + '/' + track['album_id'] + '/' + track['id'] + '.flac</NextURI>'
			meta = metadata('NextURIMetaData', track, local_ip)
			close = '</u:SetNextAVTransportURI></s:Body></s:Envelope>'
			soap_msg = head + schema + urn + instance + uri + meta + close
			soap_action = '"urn:schemas-upnp-org:service:AVTransport:1#SetNextAVTransportURI"'
			
		case 'setPlayMode':
			# Posted after each CurrentURI post from BubbleUPnP but doesn't appear essential.
			urn = '<u:SetPlayMode xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
			npm = '<NewPlayMode>NORMAL</NewPlayMode>'
			close = '</u:SetPlayMode></s:Body></s:Envelope>'
			soap_msg = head + schema + urn + instance + npm + close
			soap_action = '"urn:schemas-upnp-org:service:AVTransport:1#SetPlayMode"'
			
		case 'play':
			urn = '<u:Play xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
			speed = '<Speed>1</Speed>'
			close = '</u:Play></s:Body></s:Envelope>'
			soap_msg = head + schema + urn + instance + speed + close
			soap_action = '"urn:schemas-upnp-org:service:AVTransport:1#Play"'
			
		case 'pause':
			urn = '<u:Pause xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
			close = '</u:Pause></s:Body></s:Envelope>'
			soap_msg = head + schema + urn + instance + close
			soap_action = '"urn:schemas-upnp-org:service:AVTransport:1#Pause"'
			
		case 'seek':
			urn = '<u:Seek xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
			target = '<Unit>REL_TIME</Unit><Target>' + time + '</Target>'
			close = '</u:Seek></s:Body></s:Envelope>'
			soap_msg = head + schema + urn + instance + target + close
			soap_action = '"urn:schemas-upnp-org:service:AVTransport:1#Seek"'
			
		case 'stop':
			urn = '<u:Stop xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
			close = '</u:Stop></s:Body></s:Envelope>'
			soap_msg = head + schema + urn + instance + close
			soap_action = '"urn:schemas-upnp-org:service:AVTransport:1#Stop"'

		case 'getPosInfo':
			# Returns track metadata, URI, duration & positional positions in seconds. 
			# Posts at least each second from Bubble UPnP.
			urn = '<u:GetPositionInfo xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
			close = '</u:GetPositionInfo></s:Body></s:Envelope>'
			soap_msg = head + schema + urn + instance + close
			soap_action = '"urn:schemas-upnp-org:service:AVTransport:1#GetPositionInfo"'
			return_info = True
			
		case 'getTransInfo':
			# Returns state (i.e. STOPPED or PLAYING), status (i.e. OK) and speed of transport.
			# Posts once for every 3 of the above from BubbleUPnP.
			urn = '<u:GetTransportInfo xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
			close = '</u:GetTransportInfo></s:Body></s:Envelope>'
			soap_msg = head + schema + urn + instance + close
			soap_action = '"urn:schemas-upnp-org:service:AVTransport:1#GetTransportInfo"'
			
	# Use requests to post soap_msg.
	soap_head = {
		'SOAPAction': soap_action, 
		'Content-Type': 'text/xml; charset=utf-8', 
		'Connection': 'keep-alive'
	}
	response = requests.post(control, data=soap_msg, headers=soap_head)
	# Response object properties: https://www.w3schools.com/python/ref_requests_response.asp
	ic(response.ok)
	#return response.ok
	if return_info: 
		parsedInfo = minidom.parseString(response.text)
		return parsedInfo

def int_secs(time_str):
	# Simple helper to convert time strings such as '0:03:52' to int seconds.
	h, m, s = [int(x) for x in time_str.split(':')]
	return int(timedelta(hours=h, minutes=m, seconds=s).total_seconds())

def play(playlist, local_ip, control):
	# Let's get the party started.
	i, s, l = 0, 1, len(playlist.items) - 1
	soap('setCurrentURI', playlist.items[i], local_ip, control, 0)
	soap('play', 0, 0, control, 0)
	# TODO: Verify playing before starting loop.
	# NOTE: This loop could be controlled via soap request responses (i.e. playing/stopped).
	# Anyhow, what we're doing is checking every second to find the relTime of the current track.
	# Then we compare that to the track duration. If it's within 2 seconds, we set the nextURI.
	while True:
		time.sleep(s)
		pos_info = soap('getPosInfo', 0, 0, control, 0)
		abs_secs = int_secs(pos_info.getElementsByTagName('AbsTime')[0].firstChild.nodeValue)
		print(f"{abs_secs}/{playlist.items[i]['duration']}")
		# Set nextURI in last 2 seconds of current track (minimum CD track length).
		if i < l and abs_secs >= playlist.items[i]['duration'] - 2:
			i, s = i + 1, 3 # Increment index & temporarily extend sleep.
			soap('setNextURI', playlist.items[i], local_ip, control, 0)
		elif i < l: s = 1
		# Quit when we're done. -1 as it may not be exact.
		elif i == l and abs_secs >= playlist.items[i]['duration'] - 1: break

def goggle(mode=1):
	# Search for Tidal albums via one of two modes:
	# 1 - Perform an unlimited search (artist and/or title) and display only album results.
	# 2 - Search for artists and then get their albums.
	if mode == 1:
		query = input("Enter an album or artist name to return a list of albums: ")
		search = session.search(query, limit=10) # Returns dictionary.
		album_count = 0
		
		print("Albums matching '" + query + "': ")
		for a, album in enumerate(search['albums']):
			print('[' + str(a) + '] ' + search['albums'][a].name + ' by ' + search['albums'][a].artist.name)
			album_count = a
			
		selected = int(input("Select album number to play (0-" + str(album_count) + "): "))
		#ic(search['albums'][selected].id)
		return search['albums'][selected].id
	
	elif mode == 2:
		query = input("Enter an artist name: ")
		search = session.search(query, limit=10)
		artists = 0
		
		print("Artists matching '" + query + "': ")
		for a, artist in enumerate(search['artists']):
			print('[' + str(a) + '] ' + search['artists'][a].name)
			artists = a
			
		selected = int(input("Select artist number (0-" + str(artists) + "): "))
		artist_albums = search['artists'][selected].get_albums(limit=10) # Returns list.
		
		print("Albums by " + query + ": ")
		for a in range(len(artist_albums)):
			print('[' + str(a) + '] ' + artist_albums[a].name)
			
		selected = int(input("Select album number (0-" + str(len(artist_albums) - 1) + "): "))
		#ic(artist_albums[selected].id)
		return artist_albums[selected].id
	
def close():
	# Simple helper to delete cached albums & shutdown.
	# NOTE: Tempted to keep cache, verify complete & skip future requests.
	import glob, shutil
	pause = input("Press Enter to close.")
	httpd.shutdown()
	for path in glob.glob(cache_dir + "media/*"): shutil.rmtree(path)
	print("Done")
	sys.exit(0)
	
login()
httpd = Server()
httpd.start()

album_no = goggle(mode=1)
playlist = PlayList(album_no)

# Download tracks in separate thread as to not hold things up.
download = Thread(target=download, args=(playlist,))
download.start()

# Determine the relevant control URL for our renderer.
control = discover()
local_ip = lookmeup(control) # Needed for meta & soap.

# Do something with our playlist.
play(playlist, local_ip, control)


close()
