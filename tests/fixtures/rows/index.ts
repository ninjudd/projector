// Every case file, for `hooks/rows.test.ts`, which cannot list a directory. `tests/test_rows.py` fails when
// a case file is missing here.
import checks from './checks'
import facts from './facts'
import labels from './labels'
import loading from './loading'
import malformed from './malformed'
import notes from './notes'
import roles from './roles'
import sections from './sections'
import states from './states'
import threads from './threads'

export default [checks, facts, labels, loading, malformed, notes, roles, sections, states, threads]
