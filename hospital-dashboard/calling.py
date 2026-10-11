"""Place the department call with Twilio. Stdlib only: the REST call is a form POST with basic auth,
so this needs no `twilio` package.

The clerk's click is the human in the loop: nothing here decides to dial, it only places the call the
clerk asked for. Nursing is filtered out in backend.py before this is reached.

Credentials live outside git, in ~/.bello-twilio (bello-server.sh sources it):
    TWILIO_ACCOUNT_SID   console.twilio.com -> Account Info
    TWILIO_AUTH_TOKEN    ...same page (the "Auth Token" is the API key secret for testing)
    TWILIO_FROM          the number you bought, E.164, e.g. +19084568235
The department's number is `phone` in departments.json, also E.164.

Nothing identifying is spoken: request ID, room and department only. Twilio must be under a BAA /
on the HIPAA-eligible list before any real patient data goes near this.
"""
import base64
import json
import os
from urllib import error, parse, request
from xml.sax.saxutils import escape

API = 'https://api.twilio.com/2010-04-01/Accounts/{}/Calls.json'
TIMEOUT = 20
REQUIRED = ('TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_FROM')


def settings():
    """(account_sid, auth_token, from_number), or (None, None, reason) when this is not set up yet."""
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        return None, None, 'Twilio is not configured (set ' + ', '.join(missing) + ' in ~/.bello-twilio)'
    return os.environ['TWILIO_ACCOUNT_SID'], os.environ['TWILIO_AUTH_TOKEN'], None


def twiml(request_id, room, department):
    """What the department hears. One-way and self-terminating: no <Gather> and no <Redirect>, so there
    is nothing to reply to and Twilio hangs up when the document ends. Escaped, because
    'Housekeeping & Facilities' is a department name and a bare & is not XML — Twilio would refuse
    the whole call."""
    return ('<Response><Say>Bello: request {rid}, room {room}, {team}. Goodbye.</Say></Response>').format(
        rid=escape(str(request_id)), room=escape(str(room or 'unassigned')), team=escape(department))


def build(to, message, sid, token):
    """The exact POST Twilio receives. Split out so the request shape is checkable without dialling."""
    return request.Request(API.format(sid), method='POST', headers={
        'Authorization': 'Basic ' + base64.b64encode(f'{sid}:{token}'.encode()).decode(),
        'Content-Type': 'application/x-www-form-urlencoded'},
        data=parse.urlencode({'To': to, 'From': os.environ['TWILIO_FROM'], 'Twiml': message}).encode('utf-8'))


def place_call(to, message):
    """Dial `to` and speak `message` (TwiML). Returns (placed, detail); never raises, so a dispatch is
    never lost to a network fault — the caller logs `detail` and tells the clerk."""
    sid, token, reason = settings()
    if reason:
        return False, reason
    if not to:
        return False, 'no number configured for this department in departments.json'
    call = build(to, message, sid, token)
    try:
        with request.urlopen(call, timeout=TIMEOUT) as reply:
            placed = json.loads(reply.read().decode('utf-8'))
    except error.HTTPError as failure:
        detail = failure.read().decode('utf-8', 'replace')
        try:
            detail = json.loads(detail).get('message') or detail
        except ValueError:
            pass
        return False, f'Twilio refused the call ({failure.code}): {detail}'
    except (error.URLError, TimeoutError, OSError) as failure:
        return False, f'Twilio unreachable: {failure}'
    return True, f"Twilio call {placed.get('sid')} to {to} ({placed.get('status')})"


if __name__ == '__main__':
    # Self-check: the XML escaping, the request shape, and that an unset-up Twilio fails loudly
    # instead of dialling.
    said = twiml(8, '203 B', 'Housekeeping & Facilities')
    assert '&amp;' in said and said.startswith('<Response><Say>'), said
    assert '<Say>Bello: request 8, room 203 B, Housekeeping &amp; Facilities. Goodbye.</Say>' in said, said
    assert '<Gather' not in said and '<Redirect' not in said, said   # one-way: nothing to reply to
    assert twiml(9, None, 'Food Services').count('unassigned') == 1
    os.environ['TWILIO_FROM'] = '+19084568235'
    sent = build('+17325327539', said, 'ACsid', 'secret')
    assert sent.full_url == 'https://api.twilio.com/2010-04-01/Accounts/ACsid/Calls.json', sent.full_url
    raw = sent.data
    assert isinstance(raw, bytes)
    form = dict(parse.parse_qsl(raw.decode()))
    assert form['To'] == '+17325327539' and form['From'] == '+19084568235', form
    assert form['Twiml'] == said, form
    assert sent.get_header('Authorization') == 'Basic ' + base64.b64encode(b'ACsid:secret').decode()
    saved = {name: os.environ.pop(name, None) for name in REQUIRED}
    assert place_call('+17325327539', 'x') == (False, 'Twilio is not configured (set TWILIO_ACCOUNT_SID, '
                                                      'TWILIO_AUTH_TOKEN, TWILIO_FROM in ~/.bello-twilio)')
    os.environ.update({k: v for k, v in saved.items() if v})
    assert place_call('', 'x')[0] is False          # no number: no dial
    print('calling.py: ok (nothing was dialled)')
