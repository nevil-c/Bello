# Bello staff apps (Python/Tkinter)

Desktop apps for hospital staff. They get all their data from the Bello LAN server ([../hospital-dashboard](../hospital-dashboard/README.md)). They need only Python 3.9+ with Tkinter: no other packages, and no models.

| App | Who | What |
|---|---|---|
| `clerk.py` | Unit clerk | **Care Queue**: the request worklist, live. Each row shows the request in English (with the original if it was translated), the routing model's suggested department and urgency, and the room, with **Send + call** for the clerk to send it to that department's inbox and dial their phone with Twilio. Nothing is sent or dialled on its own: the suggestion is prepared, the clerk places it. Nursing is never dialled — the clerk is already talking to the nurses — so selecting Primary Nurse hides the send button while leaving the department dropdown available. Choosing another department shows **Send + call**. Click a request for the patient dialog: the full text, model details, history, and **Hold to speak** — press and hold to talk with the room |
| `inbox.py` | Departments | **Shared department inbox**: the requests sent to one department, in English, with Acknowledge |
| `admin.py` | Hospital admin | **Admin analytics** from the AWS archive: the most common issues, response time by department and by issue, languages, busiest hours, requests per day, common words and the slowest responses |

The clerk dashboard and department inbox follow the layout, wording and colours of the original HTML pages. The clerk's "Department inbox" link opens `inbox.py`, and the inbox's "Unit clerk dashboard" link opens `clerk.py`.

## Run

```sh
export BELLO_SERVER=http://<server>:8766
python3 staff-app/clerk.py
```

When the bedside Pi connects over Bluetooth, the server listens only on the Mac itself: use `BELLO_SERVER=http://127.0.0.1:8766`. Each app asks for the access token when it starts. Set `BELLO_API_TOKEN` to skip the prompt on a staff computer you trust. On a Raspberry Pi or other Linux machine, install Tkinter (`sudo apt install python3-tk`) and `fonts-noto-cjk` for Chinese text. Use a screen at least 1400 pixels wide for the clerk dashboard.

The clerk dashboard updates as soon as anything changes, over the server's event stream. If the stream fails three times in a row it polls every two seconds instead; the pill at the top right shows which (Live, Live (polling), Reconnecting…). The admin dashboard refreshes every minute. If the archive cannot be reached, it keeps the last successful results visible with a connection-error banner and their original date range. Charts show counts, percentages, and response times directly. Hover details use a delayed floating popup that stays in one position while hovering over the same data point, and closes when leaving, scrolling, or refreshing.

## Test

`python -m unittest discover -s staff-app -p 'test_*.py'`. Some tests open windows briefly, so they need a display.
