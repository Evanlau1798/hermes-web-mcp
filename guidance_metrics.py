"""Task-local reading counters; never retain instruction bodies."""
import copy


def record(client, topic, offset, text):
    state = client.setdefault('reading_metrics', {
        'read_calls': 0, 'returned_chars': 0, 'repeated_reads': 0, 'topics': {}, 'bounded': False})
    state['read_calls'] += 1
    state['returned_chars'] += len(text)
    key = (topic, offset)
    pages = client.setdefault('reading_pages', set())
    state['repeated_reads'] += int(key in pages)
    if len(pages) < 512:
        pages.add(key)
    else:
        state['bounded'] = True
    if topic in state['topics'] or len(state['topics']) < 64:
        row = state['topics'].setdefault(topic, {'read_calls': 0, 'returned_chars': 0})
        row['read_calls'] += 1
        row['returned_chars'] += len(text)
    else:
        state['bounded'] = True
    return copy.deepcopy(state)
