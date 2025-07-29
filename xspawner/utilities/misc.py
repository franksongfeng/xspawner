# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

import queue
import time
import datetime
import json
import os
import shutil
import sys
import base64
import signal
import io
import mimetypes
import urllib.parse
import typing
import ssl
import tempfile
import socket
import subprocess
import requests
from requests.exceptions import RequestException
import re
import importlib
import inspect
import hashlib
import tornado.escape
import json


# better than getCurTimeStr
def getCurMSecTimeStr():
    return str(datetime.datetime.now())


def getCurMSecTimeISOStr(utc=False, offset_seconds=0):
    if utc and offset_seconds:
        now_offset = datetime.datetime.utcnow() + datetime.timedelta(seconds=offset_seconds)
    elif utc and not offset_seconds:
        now_offset = datetime.datetime.utcnow()
    elif not utc and offset_seconds:
        now_offset = datetime.datetime.now() + datetime.timedelta(seconds=offset_seconds)
    else:
        now_offset = datetime.datetime.now()
    return now_offset.isoformat()


def getMSecStr():
    now = datetime.datetime.now()
    return "{}:{}:{}.{}".format(now.hour, now.minute, now.second, now.microsecond)


def getCurTimeStr():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))


def getCurTimeStrOffset(offset_seconds):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + offset_seconds))


def getCurDateTimeOffset(offset_seconds):
    return datetime.datetime.fromtimestamp(time.time() + offset_seconds)


def getTimeStrWithOffset(tm_str, offset_seconds):
    if offset_seconds == 0:
        return tm_str
    else:
        return time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(
                                 time.mktime(
                                     time.strptime(
                                         tm_str,
                                         "%Y-%m-%d %H:%M:%S"))
                                 + offset_seconds))


def getTimeFromStr(tm_str):
    return time.strptime(tm_str, "%Y-%m-%d %H:%M:%S")


def getDatetimeStr(d):
    return d.strftime("%Y-%m-%d %H:%M:%S")


def convertTime2Datetime(t):
    return datetime.datetime(*t[:6])


def getCurDateTimeMS():
    return datetime.datetime.now()


def getCurDateTimeNMS():
    return convertTime2Datetime(time.localtime(time.time()))


def getElapseTime(start):
    return time.time() - start


def parse_datetime(time_str):
    return convertTime2Datetime(getTimeFromStr(time_str))


def getFirstOne(seq, ifEmpty=None):
    return seq[0] if seq else ifEmpty


def getListPartition(lst, lmt):
    if lmt == 0:
        return [lst]
    elif len(lst) <= lmt:
        return [lst]
    else:
        import math
        cnt = math.ceil(len(lst) / lmt)
        return [lst[i * lmt:(i + 1) * lmt] for i in range(cnt)]


def suicide():
    import os
    import signal
    os.kill(os.getpid(), signal.SIGTERM)


def makeRootUrl(host, port):
    return "http://{}:{}/".format(host, port)


def runCoroutines(c_list):
    import tornado.ioloop
    ioloop = tornado.ioloop.IOLoop.current()
    for c in c_list:
        if isinstance(c[0], int) or isinstance(c[0], float):
            ioloop.add_timeout(c[0], *c[1:])
        else:
            ioloop.add_callback(*c)
    ioloop.start()
    ioloop.close()


def suicide():
    os.kill(os.getpid(), signal.SIGTERM)


def runPy(dir, script):
    subprocess.Popen(
        args="python3 {}".format(script),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True,
        cwd=dir,
        env=None)


# return 0 if succeed
def runCmd(cmdstr):
    return subprocess.call(
        args=cmdstr.split(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=False,
        timeout=None)


def runShell(cmdstr, blocked=False):
    # return CompletedProcess(args: str, returncode: int, stdout: bytes, stderr: bytes)
    if blocked:
        return subprocess.run(
            args=cmdstr,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True)
    else:
        return subprocess.run(
            args=cmdstr,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True)


def runProc(fun, args=None):
    import multiprocessing
    if args != None:
        multiprocessing.Process(target=fun, args=args).start()
    else:
        multiprocessing.Process(fun).start()


def encript(raw_str, type):
    import hashlib
    if type == "md5":
        hl = hashlib.md5()
    elif type == "sha256":
        hl = hashlib.sha256()
    hl.update(raw_str.encode("utf-8"))
    res = hl.hexdigest()
    return res


def get_public_ip(timeout=5):
    services = [
        "https://ifconfig.me/ip",
        "https://ipinfo.io/ip",
        "https://checkip.amazonaws.com",
        "https://icanhazip.com"
    ]
    for url in services:
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == 200:
                ip = response.text.strip()
                if len(ip) >= 7 and '.' in ip or ':' in ip:
                    return ip
        except RequestException:
            continue
    return None

def start_background_process(command):
    proc = subprocess.Popen(
        command,
        shell=False
    )
    time.sleep(0.5) # need a delay
    child_pid = os.popen(f"pgrep -P {proc.pid}").read().strip()
    return child_pid


def is_port_used(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def sha256_encrypt(input_str):
    # create an sha256 object
    sha_signature = hashlib.sha256(input_str.encode()).hexdigest()
    return sha_signature

# file type list:
# =============================================================================
# .txt: text/plain
# .csv: text/csv
# .css: text/css
# .html:text/html
# .java:text/x-java
# .jar: text/java-archive
# .js:  text/javascript
# .py:  text/x-python
# .c:   text/x-csrc
# .cpp: text/x-c++src
# .pas: text/x-pascal
# .pl:  text/x-perl
# .h:   text/x-chdr
# .hpp: text/x-c++hdr
# .sh:  text/x-sh
# .csh: text/x-csh
# .md:  text/markdown
# .xml: application/xml
# .wsdl:application/xml
# .pyc: application/x-python-code
# .pyo: application/x-python-code
# .pdf: application/pdf
# .hdf: application/x-hdf
# .tar: application/tar
# .gz:  application/tar
# .tgz: application/tar
# .zip: application/zip
# .rar: application/rar
# .7z:  application/x-7z-compressed
# .exe: application/x-msdos-program
# .dll: application/x-msdos-program
# .o:   application/x-object
# .a:   application/octet-stream
# .so:  application/octet-stream
# .torrent: application/x-bittorrent
# .gif: image/gif
# .png: image/png
# .jpg: image/jpeg
# .avi: video/x-msvideo
# .mpeg:video/mpeg
# .mp4: video/mp4
# .mp3: audio/mpeg
# .wav: audio/x-wav
def get_file_type(filename):
    ftype = mimetypes.guess_type(filename)[0]
    return ftype if isinstance(ftype, str) else ""

# filenames is like
# {"<K1>": ["<File Path1>"], ...}
# return docs like
# {
#     "<K1>": [
#         {
#             "filename": "<File Path1>",
#             "content_type": "<File Type>",
#             "body": b"..."
#         }
#     ],
#     ...
# }
def make_docs(filenames):
    def get_content_type(filename):
        return get_file_type(filename) if get_file_type(filename) else ''

    def read_file(filename):
        if os.path.isfile(filename):
            return open(filename, "rb").read()
        else:
            return r''

    return {
        key: [
            {
                "filename": os.path.basename(fn),
                "content_type": get_content_type(fn),
                "body": read_file(fn)
            }
            for fn in filenames[key]
        ]
        for key in filenames
    }


def make_data_uri(filename, use_base64=True):
    with open(filename, 'rb') as f:
        bdata = f.read()
        # import chardet
        # res_det = chardet.detect(bdata)
        # if "encoding" in res_det and (res_det['encoding'] == "utf-8" or res_det['encoding'] == "ascii"):
        file_type = get_file_type(filename)
        if file_type:
            if use_base64:
                b64data = base64.b16encode(bdata)
                data_uri = "data:{};base64,{}".format(file_type, b64data.decode())
            else:
                data_uri = "data:{};{}".format(file_type, bdata)
            return data_uri
        else:
            return None


# return like {"content_type": "text/plain", "base64": False, "data": ""}
def parse_data_uri(data_uri):
    assert isinstance(data_uri, str) and data_uri[:5] == "data:"
    [_, d] = data_uri.split(':')
    [fmt, da] = d.split(',')
    if fmt:
        res = dict()
        if ';' in fmt:
            [fmt1, fmt2] = fmt.split(';')
            res["content_type"] = fmt1
            res["base64"] = (fmt2.lower() == "base64")
        else:
            res["content_type"] = fmt
            res["base64"] = False
        res["data"] = da
        return res
    else:
        # undefined data
        return {}

def make_multipart_form(boundary, args, docs):
    def write_line_into_buf(buf, *args):
        for x in args:
            if isinstance(x, str):
                buf.write(x.encode())
            else:
                buf.write(x)
        buf.write(b"\r\n")

    buf = io.BytesIO()
    if docs:
        for key in docs:
            for fs in docs[key]:
                filename = fs['filename']
                body = fs['body']
                content_type = fs['content_type']
                write_line_into_buf(buf, '--', boundary)
                write_line_into_buf(buf, 'Content-Disposition: form-data; name="{}"; filename="{}"'.format(key, filename))
                write_line_into_buf(buf, 'Content-Type: {}'.format(content_type))
                write_line_into_buf(buf)
                write_line_into_buf(buf, body)

    if args:
        for key in args:
            write_line_into_buf(buf, '--', boundary)
            write_line_into_buf(buf, 'Content-Disposition: form-data; name="{}"'.format(key))
            write_line_into_buf(buf)
            write_line_into_buf(buf, args[key][0])

    write_line_into_buf(buf, '--', boundary, '--')

    return buf.getvalue()


# args is dict like {"v1": [b"xyz"]}
# docs is dict like {"file": [{"filename": "xFEs3r8w.html", "body": b"....", "content_type": "text/html"}]
# !! the key "file" is fixed in docs!!
def make_multipart_request(args, docs):
    boundary = "----FormBoundary6ebd851a13baed30"
    body = make_multipart_form(tornado.escape.utf8(boundary), args, docs)
    headers = {
        "Content-Type": "multipart/form-data; boundary={}".format(boundary),
        "Content-Length": str(len(body))
    }
    return headers, body

def parse_reply(res):
    ct = res.headers.get_list('Content-Type')[0]
    if ct == "application/json":            
        rdata = res.body.decode()
        return json.loads(rdata)
    elif ct == "application/octet-stream":
        cd = res.headers.get_list('Content-Disposition')[0]
        # cd is like 'attachment; filename=...'
        fname = cd.split('=')[1].strip()
        fdata = res.buffer.read()
        return fdata, fname
    else:
        return res.body

def parse_net_url(fq_url):
    uo = urllib.parse.urlparse(fq_url)
    return uo.netloc


def timespan(func):
    def wrapper(*args, **kwargs):
        t1 = time.time()
        res = func(*args, **kwargs)
        t2 = time.time()
        print("{} took seconds {}".format(func, t2-t1))
        return res
    return wrapper


def getCurUTCTimeStr():
    dt = datetime.datetime.utcnow()
    return datetime.datetime.utcfromtimestamp(
        time.mktime(
            time.strptime(
                dt.strftime('%Y-%m-%d %H:%M:%S'), '%Y-%m-%d %H:%M:%S'
            )
        )
    ).strftime('%Y-%m-%dT%H:%M:%SZ')


def rshift_strs(strs, n):
    return strs.replace("\n", "\n" + " " * n)


def save_data_to_file(bdata, filename):
    with open(filename, 'wb') as f:
        f.write(bdata)
        return True


def read_data_from_file(filename):
    with open(filename, 'rb') as f:
        return f.read()


def read_text_file(filename):
  with open(filename, "r", encoding="utf-8") as file:
      return file.read()


def kill_process_on_port(port):
    cmdstr = "ps -ef|grep python3|grep %s|%s" % (port, "awk '{print $2;}'")
    result = subprocess.run(
        args=cmdstr,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True)
    if result.returncode == 0:
        res_str = result.stdout.decode()
        pids = res_str.split()
        if len(pids) > 2:
            for p in pids[:2]:
                os.system("sudo kill -9 %s" % p)
            return True
        else:
            return False
    else:
        print(result.stderr)
        return False

def get_child_cls(mod, base_cls_name, inherit=True):
    try:
        mod_obj = importlib.import_module(mod) if isinstance(mod, str) else mod
        if mod_obj:
            for name in dir(mod_obj):
                obj = getattr(mod_obj, name)
                if inspect.isclass(obj):
                    if inherit and is_descendant_cls(obj, base_cls_name):
                        return obj
                    else:
                        if obj.__base__.__name__ == base_cls_name:
                            return obj
    except Exception as e:
        print("get_child_cls exception: ", e)
        return None

def is_descendant_cls(kls, base_cls_name):
    if not kls:
        return False
    if kls.__name__ == "object":
        return False
    if kls.__base__.__name__ == base_cls_name:
        return True
    return is_descendant_cls(kls.__base__, base_cls_name)

def is_module_available(module_name):
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False

def import_package_modules(package_path):
    import pkgutil
    import importlib
    package = importlib.import_module(package_path)
    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        full_name = f"{package_path}.{module_name}"
        module = importlib.import_module(full_name)
        globals()[full_name] = module


def path_to_pkg(path):
    without_extension = path.replace('.py', '')
    return without_extension.replace('/', '.')

def pkg_to_path(mod):
    path = mod.replace('.', '/')
    return f"{path}.py"

def delete_file(file):
    if os.path.isfile(file):
        os.remove(file)

def delete_dir(dir):
    if os.path.exists(dir):
        shutil.rmtree(dir)

def delete_entries(entries):
    for entry in entries:
        if os.path.isfile(entry):
            delete_file(entry)
        else:
            delete_dir(entry)

def filter_logs(days, log_file):
    def get_first_timestamp(lines, pattern):
        for line in lines:
            match = pattern.match(line)
            if match:
                return match.group(1)
        return "No Timestamp"

    def get_last_timestamp(lines, pattern):
        for line in reversed(lines):
            match = pattern.match(line)
            if match:
                return match.group(1)
        return "No Timestamp"

    now = datetime.datetime.utcnow()
    three_days_ago = now - datetime.timedelta(days=days)

    with open(log_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    timestamp_pattern = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')

    filtered_lines = []
    line_count = 0
    removed_count = 0

    for line in lines:
        line_count += 1
        match = timestamp_pattern.match(line)
        if not match:
            filtered_lines.append(line)
            continue
        try:
            timestamp = datetime.datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
            if timestamp >= three_days_ago:
                filtered_lines.append(line)
            else:
                removed_count += 1
        except ValueError as e:
            print(f"Line {line_count} timestamp format error: {e}")
            filtered_lines.append(line)

    if not filtered_lines:
        os.remove(log_file)
    else:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.writelines(filtered_lines)


# the result is ssl.SSLContext object
# it can be applied for ssl_options, a parameter  of tornado's client.fetch
# cert_file can be file path or file text
def getSSLContext(cert_file):
    if isinstance(cert_file, str) and cert_file:
        if os.path.isfile(cert_file):
            context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
            context.verify_mode = ssl.VERIFY_DEFAULT
            context.load_cert_chain(certfile=cert_file)
            return context
        else:
            filepath = tempfile.mktemp()
            with open(filepath, 'wb') as fp:
                fp.write(cert_file.encode())
            context = getSSLContext(filepath)
            os.unlink(filepath)
            return context


# add serialization support on python datetime/bytes/function type
# usage: json.dumps(s, cls=JSONEncoderWDT)
class JSONEncoderWDT(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.strftime('%Y%m%dT%H:%M:%S')
        elif isinstance(obj, bytes):
            return str(obj, encodings='utf-8')
        elif isinstance(obj, typing.types.FunctionType):
            return str(obj)
        elif isinstance(obj, queue.Queue):
            return str(obj)
        else:
            return json.JSONEncoder.default(self, obj)


class Singleton(type):
    _instance = {}

    def __call__(cls, *args, **kwargs):
        if cls not in Singleton._instance:
            Singleton._instance[cls] = type.__call__(cls, *args, **kwargs)
        return Singleton._instance[cls]


class Sole(type):
    _instance = None

    def __call__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(Sole, cls).__call__(*args, **kwargs)
        return cls._instance