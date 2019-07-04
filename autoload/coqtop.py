import os
import re
import subprocess
import xml.etree.ElementTree as ET
import signal
import HTMLParser
import urllib2
import sys
import time
import vim
import select

poll = select.poll()

from collections import deque, namedtuple

#DEBUGFILE = open('/tmp/DEBUGFILE', 'a')
#def debugln(s): DEBUGFILE.write(s + '\n'); DEBUGFILE.flush()

Ok = namedtuple('Ok', ['val', 'msg'])
Err = namedtuple('Err', ['err'])

Inl = namedtuple('Inl', ['val'])
Inr = namedtuple('Inr', ['val'])

RouteId = namedtuple('RouteId', ['id'])
StateId = namedtuple('StateId', ['id'])
Option = namedtuple('Option', ['val'])

OptionState = namedtuple('OptionState', ['sync', 'depr', 'name', 'value'])
OptionValue = namedtuple('OptionValue', ['val'])

Status = namedtuple('Status', ['path', 'proofname', 'allproofs', 'proofnum'])

Goals = namedtuple('Goals', ['fg', 'bg', 'shelved', 'given_up'])
Goal = namedtuple('Goal', ['id', 'hyp', 'ccl'])
Evar = namedtuple('Evar', ['info'])

def parse_response(xml):
    assert xml.tag == 'value'
    if xml.get('val') == 'good':
        return Ok(parse_value(xml[0]), None)
    elif xml.get('val') == 'fail':
        return Err(parse_error(xml))
    else:
        assert False, 'expected "good" or "fail" in <value>'

def parse_value(xml):
    if xml.tag == 'unit':
        return ()
    elif xml.tag == 'bool':
        if xml.get('val') == 'true':
            return True
        elif xml.get('val') == 'false':
            return False
        else:
            assert False, 'expected "true" or "false" in <bool>'
    elif xml.tag == 'string':
        return xml.text or ''
    elif xml.tag == 'int':
        return int(xml.text)
    elif xml.tag == 'state_id':
        return StateId(int(xml.get('val')))
    elif xml.tag == 'list':
        return [parse_value(c) for c in xml]
    elif xml.tag == 'option':
        if xml.get('val') == 'none':
            return Option(None)
        elif xml.get('val') == 'some':
            return Option(parse_value(xml[0]))
        else:
            assert False, 'expected "none" or "some" in <option>'
    elif xml.tag == 'pair':
        return tuple(parse_value(c) for c in xml)
    elif xml.tag == 'union':
        if xml.get('val') == 'in_l':
            return Inl(parse_value(xml[0]))
        elif xml.get('val') == 'in_r':
            return Inr(parse_value(xml[0]))
        else:
            assert False, 'expected "in_l" or "in_r" in <union>'
    elif xml.tag == 'option_state':
        sync, depr, name, value = map(parse_value, xml)
        return OptionState(sync, depr, name, value)
    elif xml.tag == 'option_value':
        return OptionValue(parse_value(xml[0]))
    elif xml.tag == 'status':
        path, proofname, allproofs, proofnum = map(parse_value, xml)
        return Status(path, proofname, allproofs, proofnum)
    elif xml.tag == 'goals':
        return Goals(*map(parse_value, xml))
    elif xml.tag == 'goal':
        return Goal(*map(parse_value, xml))
    elif xml.tag == 'evar':
        return Evar(*map(parse_value, xml))
    elif xml.tag == 'xml' or xml.tag == 'richpp':
        return ''.join(xml.itertext())

def parse_error(xml):
    return ET.fromstring(re.sub(r"<state_id val=\"\d+\" />", '', ET.tostring(xml)))

htmlparser = HTMLParser.HTMLParser()
fsencoding = sys.getfilesystemencoding()

def decode_xml_text(xml):
    xml = re.sub(r"</_>", '\n', xml)
    xml = re.sub(r"<(\w|/\w)[\s\S]*?>", '', xml)
    try:
        xml = urllib2.unquote(xml).decode('utf-8')
    except:
        #debugln('invaid XML:\n' + str(xml))
        xml = xml.decode('utf-8')
    return htmlparser.unescape(xml).encode(fsencoding)

def build(tag, val=None, children=()):
    attribs = {'val': val} if val is not None else {}
    xml = ET.Element(tag, attribs)
    xml.extend(children)
    return xml

def encode_call(name, arg, encoding):
    return build('call', name, [encode_value(arg, encoding)])

def encode_value(v, encoding):
    if v == ():
        return build('unit')
    elif isinstance(v, bool):
        xml = build('bool', str(v).lower())
        xml.text = str(v)
        return xml
    elif isinstance(v, str): #or isinstance(v, unicode):
        xml = build('string')
        xml.text = v.decode(encoding)
        return xml
    elif isinstance(v, int):
        xml = build('int')
        xml.text = str(v)
        return xml
    elif isinstance(v, RouteId):
        return build('route_id', str(v.id))
    elif isinstance(v, StateId):
        return build('state_id', str(v.id))
    elif isinstance(v, list):
        return build('list', None, [encode_value(c, encoding) for c in v])
    elif isinstance(v, Option):
        xml = build('option')
        if v.val is not None:
            xml.set('val', 'some')
            xml.append(encode_value(v.val, encoding))
        else:
            xml.set('val', 'none')
        return xml
    elif isinstance(v, Inl):
        return build('union', 'in_l', [encode_value(v.val, encoding)])
    elif isinstance(v, Inr):
        return build('union', 'in_r', [encode_value(v.val, encoding)])
    # NB: `tuple` check must be at the end because it overlaps with () and
    # namedtuples.
    elif isinstance(v, tuple):
        return build('pair', None, [encode_value(c, encoding) for c in v])
    else:
        assert False, 'unrecognized type in encode_value: %r' % (type(v),)

coqtop = None
states = []
state_id = None
root_state = None

def kill_coqtop():
    global coqtop
    if coqtop:
        try:
            poll.unregister(coqtop.stdout.fileno())
        except KeyError:
            pass
        try:
            coqtop.terminate()
            coqtop.communicate()
        except OSError:
            pass
        coqtop = None

def ignore_sigint():
    signal.signal(signal.SIGINT, signal.SIG_IGN)

def unescape(cmd):
    return cmd.replace("&nbsp;", ' ') \
              .replace("&apos;", '\'') \
              .replace("&#40;", '(') \
              .replace("&#41;", ')')

def get_answer(do_timeout=False, timeout=None):
    messageNode = None
    starttime = time.time()
    if timeout == None:
        timeout=float(vim.eval('g:coquille_timeout'))
    data = ''
    while True:
        if do_timeout and time.time() - starttime >= timeout:
            return Err(ET.fromstring('<coqtoproot>Timeout: ' + str(timeout) + ' seconds\nCoq is still up and running!\nChange timeout with let g:coquille_timeout=SECONDS</coqtoproot>'))
        try:
            p = poll.poll(timeout*1000)
            if p == []:
                kill_coqtop()
                return Err(ET.fromstring('<coqtoproot>Coq has died!\nTimeout: ' + str(timeout) + ' seconds\nChange timeout with let g:coquille_timeout=SECONDS</coqtoproot>'))

            fd = p[0][0]

            data += unescape(os.read(fd, 0x4000))
            data = unescape(data)
            #debugln('read data: ' + data)
            try:
                elt = ET.fromstring('<coqtoproot>' + data + '</coqtoproot>')
                shouldWait = True
                valueNode = None
                for c in elt:
                    if c.tag == 'value':
                        shouldWait = False
                        valueNode = c
                    if c.tag == 'message':
                        messageNode = c[1]
                    if c.tag == 'feedback' and c[1].attrib['val'] == 'message':
                        for m in c[1]:
                            messageNode = m[1]
                if shouldWait:
                    continue
                else:
                    vp = parse_response(valueNode)
                    if messageNode is not None:
                        if isinstance(vp, Ok):
                            return Ok(vp.val, decode_xml_text(data))
                    return vp
            except ET.ParseError:
                continue
        except OSError:
            # coqtop died
            return None

def call(name, arg, encoding='utf-8', do_timeout=False, timeout=None):
    xml = encode_call(name, arg, encoding)
    msg = ET.tostring(xml, encoding)
    send_cmd(msg)
    response = get_answer(do_timeout, timeout)
    return response

def send_cmd(cmd):
    #debugln('send_cmd: ' + cmd);
    coqtop.stdin.write(cmd)

def do_parse_CoqProject_arg(line, dq, sq, acc):
    if not len(line):
        return [acc]
    c = line[0]
    if re.match("\s", c) and not dq and not sq:
        return [acc] + do_parse_CoqProject_arg(line.strip(), False, False, "")
    elif (c == '"' and dq) or (c == "'" and sq):
        return do_parse_CoqProject_arg(line[1:], False, False, acc)
    elif c == '"' and not sq:
        return do_parse_CoqProject_arg(line[1:], True, False, acc)
    elif c == "'" and not dq:
        return do_parse_CoqProject_arg(line[1:], False, True, acc)
    return do_parse_CoqProject_arg(line[1:], dq, sq, acc + line[0])

def parse_CoqProject_arg(line):
    # filter(lambda s: s != '', map(lambda s: s.strip(), ln.split()))
    line = line.strip()
    if line[0] == '"':
        return do_parse_CoqProject_arg(line[1:], True, False, "")
    if line[0] == "'":
        return do_parse_CoqProject_arg(line[1:], False, True, "")
    return do_parse_CoqProject_arg(line[1:], False, False, line[0:1])

def find_CoqProject_flags():
    def read_CoqProject(d):
        files = os.listdir(d)
        for f in files:
            if f == '_CoqProject':
                with open(d + '/' + f, 'r') as g: return g.read()
        c = os.path.dirname(d)
        return '' if c == d else read_CoqProject(c)
    s = read_CoqProject(os.getcwd())
    ret = []
    for ln in s.split('\n'):
        ln = ln.strip()
        if not len(ln):
            continue
        if ln[0] == '-':
            args = re.split("\s+", ln, 1)
            if len(args) > 0:
                ret += [args[0]]
            if len(args) > 1:
                ret += parse_CoqProject_arg(args[1])
    #debugln('_CopProject flags:')
    #for r in ret:
    #    debugln(r)
    return filter(lambda s: s != "-arg", ret)

def restart_coq(*args):
    global coqtop, root_state, state_id
    if coqtop: kill_coqtop()
    #executable = '/home/andreas/Source/HoTT/hoqidetop'; extra = []
    #executable = '/home/andreas/Source/HoTT/hoqidetop'; extra = ['-allow-sprop']
    #executable = '/home/andreas/Source/HoTT-Local/hoqidetop'; extra = []
    #executable = '/home/andreas/Source/HoTT/hoqidetop'; extra = []
    #executable = 'hoqidetop'; extra = []
    #executable = 'hoqidetop'; extra = ['-I', '/home/andreas/Source/paramcoq/src/']
    #executable = 'coqidetop'; extra = ['-allow-sprop']
    #executable = 'coqidetop'; extra = ['-noinit']
    #executable = '/home/andreas/Source/coq/bin/coqidetop.opt'; extra = ['-coqlib', '/home/andreas/Source/coq', '-q', '-native-compiler', 'yes', '-allow-sprop']
    #executable = '/home/andreas/Source/coq/bin/coqidetop'; extra = []
    executable = vim.eval('g:coquille_exe')
    extra = vim.eval('g:coquille_args')
    options = [ executable
              # , '-ideslave'
              , '-quiet'
              , '-main-channel'
              , 'stdfds'
              , '-async-proofs'
              , 'on'
              ]
    options += extra
    options += find_CoqProject_flags()
    try:
        if os.name == 'nt':
            coqtop = subprocess.Popen(
                options + list(args)
              , stdin = subprocess.PIPE
              , stdout = subprocess.PIPE
              , stderr = subprocess.STDOUT
            )
        else:
            coqtop = subprocess.Popen(
                options + list(args)
              , stdin = subprocess.PIPE
              , stdout = subprocess.PIPE
              , preexec_fn = ignore_sigint
            )

        poll.register(coqtop.stdout.fileno())

        r = call('Init', Option(None), do_timeout=True, timeout=1)
        if isinstance(r, Err):
            raise RuntimeError(r.err)
        root_state = r.val
        state_id = r.val
        return True
    except:
        vim.command('echohl ErrorMsg | echom "Error: couldn\'t launch coqtop" | echohl None')
        return False

def launch_coq(*args):
    return restart_coq(*args)

def cur_state():
    if len(states) == 0:
        return root_state
    else:
        return state_id

def advance(cmd, encoding = 'utf-8'):
    global state_id
    r = call('Add', ((cmd, -1), (cur_state(), True)), encoding, do_timeout=True)
    if r is None:
        return r
    if isinstance(r, Err):
        return r
    states.append(state_id)
    state_id = r.val[0]
    return r

def rewind(step = 1):
    global states, state_id
    assert step <= len(states)
    idx = len(states) - step
    state_id = states[idx]
    states = states[0:idx]
    return call('Edit_at', state_id)

def raw_query(cmd, encoding = 'utf-8'):
    r = call('Query', (RouteId(0), (cmd, cur_state())), encoding)
    return r

def query(cmd, encoding = 'utf-8'):
    r = call('Query', (cmd, cur_state()), encoding)
    return r

def goals(encoding = 'utf-8'):
    return call('Goal', (), encoding, do_timeout=True)

def read_states():
    return states

def isrunning(): return coqtop != None
