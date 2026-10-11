import unittest
import admin_stats

class AttentionTests(unittest.TestCase):
    def test_threshold_and_reporting_period(self):
        now = 2000000000
        def row(rid, age, resolved=None):
            return dict(request_id=rid, transcript='Water please', room='415 A', created=now-age*60,
                        resolved=resolved, recommended='Food Services')
        stats = admin_stats.stats([row(1, 14.9), row(2, 15), row(3, 22),
                                  row(4, 40, now), row(5, 8*24*60)], 7, 'test', now)
        attention = stats['attention']
        self.assertEqual((attention['open'], attention['long_waits']), (3, 2))
        self.assertEqual(attention['longest_minutes'], 22)
        self.assertEqual([r['request_id'] for r in attention['requests']], [3, 2, 1])
        self.assertEqual(attention['threshold_minutes'], 15)
        empty = admin_stats.stats([], 1, 'test', now)['attention']
        self.assertEqual(empty['long_waits'], 0)
        self.assertIsNone(empty['longest_minutes'])
