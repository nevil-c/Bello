"""Open the administrative dashboard with synthetic demonstration data."""
import time
import tkinter as tk
from datetime import date, timedelta

from admin import Admin


class SampleAnalytics:
    def get(self, path, days=7):
        if path == '/api/languages':
            return {'languages': [(code, name, '') for code, name in
                                  [('en', 'English'), ('yue', 'Cantonese'),
                                   ('es', 'Spanish'), ('zh', 'Mandarin')]]}
        daily = [dict(date=str(date.today() - timedelta(days=days-i-1)),
                      count=38 + (i*13 + days) % 31, median_minutes=5.5 + (i % 5)*0.8)
                 for i in range(days)]
        total = sum(d['count'] for d in daily)

        def groups(names, weights, medians):
            counts = [int(total*w/sum(weights)) for w in weights]
            counts[0] += total - sum(counts)
            return [dict(name=name, count=count, resolved=int(count*.88),
                         median_minutes=median, p90_minutes=round(median*1.8, 1))
                    for name, count, median in zip(names, counts, medians)]

        categories = groups(['Pain / Discomfort', 'Toileting', 'Food / Drink',
                             'Room / Housekeeping', 'Mobility assistance', 'Equipment', 'Other'],
                            [27, 23, 18, 13, 9, 6, 4], [5.2, 6.8, 11.4, 14.2, 8.5, 12.1, 9.3])
        departments = groups(['Primary Nurse', 'Food Services', 'Housekeeping & Facilities',
                              'Unit Clerk / IT Maintenance'], [59, 18, 15, 8], [6.1, 11.4, 14.2, 12.1])
        languages = groups(['en', 'yue', 'es', 'zh'], [75, 15, 6, 4], [6, 7, 8, 7])
        hours = [int(total*w/240) for w in [3, 2, 2, 2, 3, 5, 8, 15, 20, 16, 12, 16, 18, 13, 12, 14, 16, 19, 10, 9, 8, 7, 6, 6]]
        hours[8] += total - sum(hours)
        resolved = sum(d['resolved'] for d in departments)
        return dict(site='Synthetic demonstration', days=days, generated=time.time(),
                    totals=dict(requests=total, resolved=resolved, open=total-resolved,
                                median_minutes=7.4, p90_minutes=16.8, avg_rating=4.6, ratings=int(resolved*.7)),
                    categories=categories, departments=departments, languages=languages,
                    hours=hours, daily=daily, agreement=dict(compared=resolved, agreed=int(resolved*.87)),
                    common_terms=[dict(term=t, count=max(1, int(total*w))) for t,w in
                                  [('water', .18), ('pain', .16), ('bathroom', .15), ('meal', .12),
                                   ('blanket', .09), ('cleaning', .08), ('help walking', .06), ('remote', .04)]],
                    slowest=[dict(request_id=1200+i, room=f'{410+i} · A', category=categories[i%7]['name'],
                                  department=departments[i%4]['name'], minutes=round(32-i*2.7, 1))
                             for i in range(8)])


if __name__ == '__main__':
    root = tk.Tk()
    app = Admin(root, SampleAnalytics(), refresh=False)
    app.names = {'en': 'English', 'yue': 'Cantonese', 'es': 'Spanish', 'zh': 'Mandarin'}
    app.show(app.client.get('/api/admin/stats', days=7))
    root.title('Bello Admin Analytics · Sample data')
    root.geometry('1440x850+40+40')
    root.mainloop()
