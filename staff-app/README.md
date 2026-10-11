# Bello staff apps (Python/Tkinter)

Desktop apps for hospital staff. They get all their data from the Bello LAN server ([../hospital-dashboard](../hospital-dashboard/README.md)). They need only Python 3.9+ with Tkinter: no other packages, and no models.

| App | Who | What |
|---|---|---|
| `clerk.py` | Unit clerk | **Care Queue**: the request worklist, live. Each row shows the request in English (with the original if it was translated), the routing model's department and urgency, the room, and **Send + call**: one click sends the request to the department inbox and places the department call (the call is a placeholder for now). Click a request for the patient dialog: the full text, model details, room audio preview and history |
| `inbox.py` | Departments | **Shared department inbox**: the requests sent to one department, in English, with Acknowledge |
| `admin.py` | Hospital admin | **Admin analytics** from the AWS archive: the most common issues, response time by department and by issue, languages, busiest hours, requests per day, common words and the slowest responses |

The clerk dashboard and department inbox follow the layout, wording and colours of the original HTML pages. The clerk's "Department inbox" link opens `inbox.py`, and the inbox's "Unit clerk dashboard" link opens `clerk.py`.

## Run

```sh
export BELLO_SERVER=http://<server>:8766
python3 staff-app/clerk.py
```

Each app asks for the access token when it starts. Set `BELLO_API_TOKEN` to skip the prompt on a staff computer you trust. On a Raspberry Pi or other Linux machine, install Tkinter (`sudo apt install python3-tk`) and `fonts-noto-cjk` for Chinese text. Use a screen at least 1400 pixels wide for the clerk dashboard.

The clerk dashboard updates as soon as anything changes, over the server's event stream. If the stream fails three times in a row it polls every two seconds instead; the pill at the top right shows which (Live, Live (polling), Reconnecting…). The admin dashboard refreshes every minute and shows an error, rather than stale numbers, if the archive cannot be reached.

## Test

`python -m unittest discover -s staff-app -p 'test_*.py'`. Some tests open windows briefly, so they need a display.
