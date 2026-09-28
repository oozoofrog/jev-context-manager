"""Catch up admitted sources and judge a fixed queue frontier without product work."""
from .adapter import recover_sources
from .health import capture_health
from .worker import drain
from .util import now


def sync(store, provider=None, capture_only=False, follow=True):
    store.policy()
    new_events = recover_sources(store)
    capture = capture_health(store)
    frontier = store.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
    eligible = store.db.execute("SELECT COUNT(*) FROM jobs j JOIN events e ON e.id=j.event_id WHERE e.seq<=? AND j.state!='succeeded'", (frontier,)).fetchone()[0]
    result = {'processed': 0, 'errors': [], 'decision_refs': [], 'lane': 'not_requested'}
    if not capture_only and not capture['current_errors']:
        result = drain(store, provider, limit=eligible, through_seq=frontier)
    followers = []
    if follow and capture['state'] == 'caught_up':
        from .follower import start
        # One follower per session; it discovers subsequent pages itself.
        sessions = {}
        for source in capture['sources']:
            sessions[source['session']] = source['source_key']
        followers = [start(store, key) for key in sessions.values()]
    remaining = store.db.execute("SELECT COUNT(*) FROM jobs j JOIN events e ON e.id=j.event_id WHERE e.seq<=? AND j.state!='succeeded'", (frontier,)).fetchone()[0]
    stage = 'blocked' if capture['current_errors'] else 'degraded' if result['errors'] else 'pending' if remaining and not capture_only else 'complete'
    result = {'origin': 'jcm', 'stage': stage, 'capture': capture, 'new_events': new_events,
            'judgment': {**result, 'through_seq': frontier, 'remaining_at_frontier': remaining,
                         'requested': not capture_only}, 'followers': followers,
            'scope_changed': False, 'product_work_started': False,
            'completion_scope': 'registered_sources_and_jobs_observed_at_start',
            'gaps': sorted(set(capture['current_errors'] + result['errors']))}
    store.save_metadata('sync:last', {'stage': stage, 'observed_at': now(), 'capture': capture['state'],
                        'new_events': new_events, 'through_seq': frontier, 'remaining_at_frontier': remaining,
                        'judgments_processed': result['judgment']['processed'], 'judgment_requested': not capture_only,
                        'gaps': result['gaps']}, store.policy()['epoch'])
    return result
