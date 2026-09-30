"""Exact JSON leaves or text blocks, with structural parent context."""
import json

from .task_state import blocks
from .util import digest


def string_boundaries(text, start, end, decoded):
    """Map decoded character boundaries back to the exact JSON string bytes.

    Offsets are Python character offsets, like all journal spans. A surrogate
    pair is one decoded character and must never be cut between its escapes.
    """
    decoded.encode('utf-8')  # Invalid lone surrogates stay in their raw JSON form.
    cursor = start + 1
    boundaries = [cursor]
    while cursor < end - 1:
        if text[cursor] != '\\':
            cursor += 1
        elif text[cursor + 1] != 'u':
            cursor += 2
        else:
            code = int(text[cursor + 2:cursor + 6], 16)
            cursor += 6
            if (0xD800 <= code <= 0xDBFF and text[cursor:cursor + 2] == '\\u'
                    and 0xDC00 <= int(text[cursor + 2:cursor + 6], 16) <= 0xDFFF):
                cursor += 6
        boundaries.append(cursor)
    if len(boundaries) != len(decoded) + 1 or cursor != end - 1:
        raise ValueError('invalid decoded string boundaries')
    return boundaries


def leaves(text, depth=0):
    decoder = json.JSONDecoder()
    result = []
    def skip(position):
        while position < len(text) and text[position].isspace():
            position += 1
        return position
    def parse(position, path, field_start=None):
        position = skip(position)
        start = position if field_start is None else field_start
        char = text[position]
        if char not in '{[':
            value, end = decoder.raw_decode(text, position)
            if isinstance(value, str) and value.lstrip().startswith(('{', '[')) and depth < 32:
                try:
                    nested = json.loads(value)
                    if isinstance(nested, (dict, list)):
                        boundaries = string_boundaries(text, position, end, value)
                        for part in leaves(value, depth + 1):
                            result.append({**part, 'start':boundaries[part['start']],
                                'end':boundaries[part['end']],
                                'path':path + ['$json'] + part['path'],
                                'reading_text':part.get('reading_text',value[part['start']:part['end']])})
                        return end
                except (ValueError, IndexError, RecursionError):
                    pass  # An unparsed value remains whole, never disappears.
            if isinstance(value,str) and '\n' in value:
                paragraphs=list(blocks(value))
                if len(paragraphs)>1:
                    try:
                        boundaries=string_boundaries(text,position,end,value)
                        result.extend({'start':boundaries[a],'end':boundaries[b],
                            'path':path+['$paragraph',i], 'reading_text':value[a:b]}
                            for i,(a,b) in enumerate(paragraphs))
                        return end
                    except (ValueError, IndexError):
                        pass
            result.append({'start': start, 'end': end, 'path': path})
            return end
        closing = '}' if char == '{' else ']'
        cursor = skip(position + 1)
        if text[cursor] == closing:
            result.append({'start': start, 'end': cursor + 1, 'path': path})
            return cursor + 1
        index = 0
        while True:
            field = cursor
            if char == '{':
                key, cursor = decoder.raw_decode(text, cursor)
                if not isinstance(key, str) or text[skip(cursor)] != ':':
                    raise ValueError('invalid object')
                cursor = skip(cursor) + 1
            else:
                key = index
            cursor = skip(parse(cursor, path + [key], field if char == '{' else None))
            if text[cursor] == closing:
                return cursor + 1
            if text[cursor] != ',':
                raise ValueError('invalid container')
            cursor = skip(cursor + 1)
            index += 1
    try:
        if not text.lstrip().startswith(('{', '[')) or skip(parse(0, [])) != len(text):
            raise ValueError('not complete JSON')
        return result
    except (ValueError, IndexError, RecursionError):
        return [{'start': a, 'end': b, 'path': []} for a, b in blocks(text)]


def units(material):
    text = material['text']
    fragments = leaves(text)
    source_hash = digest(text)
    # Index scalar/object conditions by their owning object. Array peers are
    # independent records. Avoid comparing every nested leaf with every other
    # leaf in a large transcript response.
    ancestors = {}
    for index, fragment in enumerate(fragments):
        path = fragment['path']
        after_array = max((i + 1 for i,key in enumerate(path) if isinstance(key,int)),default=0)
        for i in range(after_array,len(path)):
            ancestors.setdefault(tuple(path[:i]),set()).add(index)
    for index, fragment in enumerate(fragments):
        path = fragment['path']
        # Carry nested sibling conditions at each object ancestor. Do not cross
        # array peers: records with equal labels can be different artifacts.
        related = set().union(*(ancestors.get(tuple(path[:i]),set()) for i,key in enumerate(path)
                                if isinstance(key,str))) if path else set()
        context = [{**fragments[i], 'text':text[fragments[i]['start']:fragments[i]['end']]}
                   for i in sorted(related - {index})]
        a, b = fragment['start'], fragment['end']
        yield {k: material[k] for k in ('event_id', 'revision', 'role', 'kind', 'basis', 'session')} | {
            'text': text[a:b], 'source_path': path,
            'reading_text': fragment.get('reading_text',text[a:b]),
            'parent_context': [p['path'] for p in context], '_parents': context,
            'span': {'start': a, 'end': b, 'total_chars': len(text), 'source_hash': source_hash}}


def assessment_units(material, structural=None, fits=None):
    """Coalesce adjacent leaves whose mandatory structural context is identical.

    This changes judgment granularity, not the evidence set: choosing any leaf
    already requires every field in its closure. Array peers have different
    closures and cannot merge. Exact leaf units remain available for completion.
    """
    structural = list(units(material)) if structural is None else structural
    if fits is not None:
        # Prefer a complete array record, including its nested conditions. Only
        # descend into its child records when that assessment cannot fit. This
        # avoids separately asking about dozens of metadata fields in a copied
        # transcript item while keeping sibling artifacts separate.
        def combine(group):
            first=group[0]; a=first['span']['start']; b=group[-1]['span']['end']
            path=list(first['source_path'])
            for unit in group[1:]:
                while path!=unit['source_path'][:len(path)]:
                    path.pop()
            parents={(p['start'],p['end']):p for u in group for p in u['_parents']
                     if p['end']<=a or p['start']>=b}
            parents=[parents[k] for k in sorted(parents)]
            raw=material['text'][a:b]; reading=raw
            for _ in range(path.count('$json')):
                try:
                    reading=json.loads('"'+reading+'"')
                except ValueError:
                    reading=raw
                    break
            return {**first,'text':raw,'reading_text':reading,'source_path':path,
                    'parent_context':[p['path'] for p in parents], '_parents':parents,
                    'span':{**first['span'],'start':a,'end':b}}
        def descend(group, depth):
            partitions=[]
            for unit in group:
                indices=[i for i,key in enumerate(unit['source_path']) if isinstance(key,int)]
                if len(indices)>depth:
                    key=('record',tuple(unit['source_path'][:indices[depth]+1]))
                else:
                    key=('fields',tuple(unit['source_path'][:indices[-1]+1]) if indices else ())
                if partitions and partitions[-1][0]==key:
                    partitions[-1][1].append(unit)
                else:
                    partitions.append((key,[unit]))
            for key,part in partitions:
                merged=combine(part)
                deeper=any(sum(isinstance(k,int) for k in u['source_path'])>depth+1 for u in part)
                prose=any('$paragraph' in u['source_path'][len(key[1]):] for u in part)
                if key[0]=='record' and deeper and (prose or not fits(merged)):
                    yield from descend(part,depth+1)
                else:
                    yield merged
        yield from descend(structural,0)
        return
    groups=[]
    for unit in structural:
        closure=tuple(sorted({(unit['span']['start'],unit['span']['end'])} |
                             {(p['start'],p['end']) for p in unit['_parents']}))
        if groups and unit['source_path'] and groups[-1][0]==closure:
            groups[-1][1].append(unit)
        else:
            groups.append((closure,[unit]))
    for _,group in groups:
        if len(group)==1:
            yield group[0]
            continue
        first=group[0]; a=first['span']['start']; b=group[-1]['span']['end']
        covered={(u['span']['start'],u['span']['end']) for u in group}
        parents=[p for p in first['_parents'] if (p['start'],p['end']) not in covered]
        path=list(first['source_path'])
        for unit in group[1:]:
            while path!=unit['source_path'][:len(path)]:
                path.pop()
        yield {**first,'text':material['text'][a:b],
            'reading_text':'\n'.join(u['reading_text'] for u in group),
            'source_path':path,'parent_context':[p['path'] for p in parents], '_parents':parents,
            'span':{**first['span'],'start':a,'end':b}}
