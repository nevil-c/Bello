"""Talks to the Bello LAN server for the staff apps. Standard library only.

BELLO_SERVER     the server's address (default http://127.0.0.1:8766)
BELLO_API_TOKEN  the shared token; if unset, the app asks for it when it starts
"""
import json
import queue
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

class Unauthorized(Exception):
    pass

class ApiError(Exception):
    pass

class Client:
    def __init__(self, server, token):
        self.server = server.rstrip('/')
        self.token = token

    def call(self, path, body=None, timeout=15):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.server + path, data=data, method='GET' if data is None else 'POST',
                          headers={'X-Bello-Token': self.token, 'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code == 401:
                raise Unauthorized('The access token was not accepted.') from None
            try:
                message = json.load(error).get('error')
            except ValueError:
                message = None
            raise ApiError(message or f'Server error {error.code}') from None
        except (URLError, OSError, ValueError):
            raise ApiError(f'Cannot reach the Bello server at {self.server}.') from None

    def get(self, path, **params):
        return self.call(path + ('?' + urlencode(params) if params else ''))

    def post(self, path, body):
        return self.call(path, body)

def parse_events(lines):
    """Yield the JSON payload of each server-sent event in an iterable of text lines."""
    data = []
    for line in lines:
        line = line.rstrip('\r\n')
        if not line:
            if data:
                yield json.loads('\n'.join(data))
                data = []
        elif line.startswith('data: '):
            data.append(line[6:])
        # Lines starting with ':' are keep-alive comments.

class LiveRequests(threading.Thread):
    """The request list, kept current from the server's event stream.

    After three failed connections in a row it polls every two seconds instead. Results arrive on
    `self.updates` as ('requests', list) or ('status', text) for the UI thread to pick up.
    """
    def __init__(self, client):
        super().__init__(daemon=True)
        self.client = client
        self.updates = queue.Queue()
        self.stop = threading.Event()

    def status(self, text):
        self.updates.put(('status', text))

    def run(self):
        failures = 0
        while not self.stop.is_set():
            if failures >= 3:
                return self.poll()
            try:
                request = Request(self.client.server + '/api/stream', headers={'X-Bello-Token': self.client.token})
                # The server sends a keep-alive every 15 s, so a silent minute means the link is gone.
                with urlopen(request, timeout=60) as response:
                    self.status('Live')
                    for event in parse_events(raw.decode('utf-8') for raw in response):
                        failures = 0
                        self.updates.put(('requests', event['requests']))
                        if self.stop.is_set():
                            return
            except HTTPError as error:
                if error.code == 401:
                    return self.status('The access token was not accepted.')
            except (URLError, OSError, ValueError):
                pass
            failures += 1
            if not self.stop.is_set():
                self.status('Reconnecting…')
                self.stop.wait(failures)

    def poll(self):
        while not self.stop.is_set():
            try:
                self.updates.put(('requests', self.client.get('/api/requests')['requests']))
                self.status('Live (polling)')
            except Unauthorized as error:
                return self.status(str(error))
            except ApiError as error:
                self.status('Offline: ' + str(error))
            self.stop.wait(2)
