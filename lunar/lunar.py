#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Lunar. A simple WSGI based webframework for learning.

lunar是一个玩具式的网络框架，基于PEP333和它的进化版PEP3333，它包括一个Router，
一个Template engine，一个简单的WSGI Request和Response的封装，和一個小型的
SQLite的ORM框架。没有任何第三方包的依赖 。

查看example来看看这是怎么运作的。

Lunar is a WSGI based webframework in pure Python, without any third-party dependency.
lunar include a simple router, which provide the request routing, a template engine
for template rendering, a simple wrapper for WSGI request and response, and
a ORM framework for sqlite3.

Happy hacking.

"""

import json
import os
import time
import sys
import traceback
import threading
import mimetypes

from functools import wraps

if sys.version < '3':
    from Queue import Queue
    from urllib import quote
    import sys
    reload(sys)
    sys.setdefaultencoding('utf8')
else:
    from queue import Queue
    from urllib.parse import quote

try:
    import pkg_resources
    pkg_resources.resource_stream
except (ImportError, AttributeError):
    pkg_resources = None

from .server import ServerAdapter
from .server import WSGIRefServer
from .template import Loader, unescape
from .wrappers import Request, Response
from .router import Router, RouterException

"""
The Main object of lunar.
"""


class _Stack(threading.local):

    def __init__(self):
        self._Stack = []

    def push(self, app):
        self._Stack.append(app)

    def pop(self):
        try:
            self._Stack.pop()
        except IndexError:
            return None

    def top(self):
        try:
            return self._Stack[-1]
        except IndexError:
            return None

    def __len__(self):
        return len(self._Stack)

    def empty(self):
        return True if len(self._Stack) == 0 else False

    def __repr__(self):
        return "app_stack with %s applications" % (len(self))


class LunarException(Exception):

    def __init__(self, code, response, server_handler, DEBUG=False):
        self._DEBUG = DEBUG
        self._response = response
        self._response.set_status(code)
        self._server_handler = server_handler

    def __call__(self):
        body = self._response.status
        if self._DEBUG:
            body = '<br>'.join(
                [self._response.status, traceback.format_exc().replace('\n', '<br>')])
        self._response.set_body(body)
        self._server_handler(self._response.status, self._response.headerlist)
        return [self._response.body]


class Lunar(object):

    """Main object of this funny web frameWork
    """

    def __init__(self, pkg_name, template='template', static='static'):
        # router
        self._router = Router()

        # request and response
        self._request = Request()
        self._response = Response(None)

        # template
        self.package_name = pkg_name
        #: where is the app root located?
        self.root_path = self._get_package_path(
            self.package_name).replace('\\', '\\\\')  # '\u' escape

        self.loader = Loader(os.sep.join([self.root_path, template]))

        # static file
        self.static_folder = static
        self.static_url_cache = {}

        # session
        self._session = self._request.cookies

        # request_queue
        self._req_arg_queue = Queue()
        self._server_handler = None

        # debug
        self.DEBUG = False

        # config
        self.config = {}
        self.config.setdefault('DATABASE_NAME', 'lunar.db')

        # push to the _app_stack
        global app_stack
        app_stack.push(self)

    def set_template_engine(self, engine):
        self.loader.update_template_engine(engine)

    def _get_package_path(self, name):
        """Returns the path to a package or cwd if that cannot be found."""
        try:
            return os.path.abspath(os.path.dirname(sys.modules[name].__file__))
        except (KeyError, AttributeError):
            return os.getcwd()

    def route(self, path=None, methods=['GET']):
        if path is None:
            raise RouterException()
        methods = [m.upper() for m in methods]

        def wrapper(fn):
            self._router.register(path, fn, methods)
            return fn
        return wrapper

    @property
    def session(self):
        return self._session

    def run(self, server=WSGIRefServer, host='localhost', port=8000, DEBUG=False):
        self.DEBUG = DEBUG
        if isinstance(server, type) and issubclass(server, ServerAdapter):
            server = server(host=host, port=port)

        if not isinstance(server, ServerAdapter):
            raise RuntimeError("Server must be a subclass of ServerAdapter.")

        print("running on %s:%s" % (host, port))
        try:
            server.run(self)
        except KeyboardInterrupt:
            pass

    def jsonify(self, *args, **kwargs):
        response = Response(body=json.dumps(dict(*args, **kwargs)), code=200)
        response.set_content_type('application/json')
        return response 
        
    def render_template(self, file, **context):
        # print(self.loader.load(file).r_co)
        app_namespace = sys.modules[self.package_name].__dict__
        context.update(globals())
        context.update(app_namespace)
        return self.loader.load(file).render(**context)

    def not_found(self):
        response = Response(body='<h1>404 Not Found</h1>', code=404)
        return response

    def not_modified(self):
        response = Response('', code=304)
        # We don't need Content-Type here.
        del(response.headers['Content-Type'])
        return response

    def redirect(self, location, code=302):
        response = Response(body='<p>Redirecting...</p>', code=code)
        response.headers['Location'] = location
        return response

    def url_for(self, fn, filename=None, **kwargs):
        # Static file URL like these:
        # <link type="text/css" rel="stylesheet" href="{{ app.url_for('static', 'style.css') }}" />
        # <link type="text/css" rel="stylesheet" href="{{ app.url_for('static', 'css/style.css') }}" />
        if fn == self.static_folder and filename:
            if filename in self.static_url_cache.keys():
                return self.static_url_cache[filename]
            else:
                url = self.construct_url(filename)
                # Cache the url
                self.static_url_cache[filename] = url
                return url
        # Router function URL
        if kwargs:
            return self._router.url_for(fn, **kwargs)
        return self._router.url_for(fn)

    def construct_url(self, filename):
        environ = self._request.headers
        url = environ['wsgi.url_scheme'] + '://'
        if environ.get('HTTP_HOST'):
            url += environ['HTTP_HOST']
        else:
            url += environ['SERVER_NAME']

            if environ['wsgi.url_scheme'] == 'https':
                if environ['SERVER_PORT'] != '443':
                    url += ':' + environ['SERVER_PORT']
            else:
                if environ['SERVER_PORT'] != '80':
                    url += ':' + environ['SERVER_PORT']

        url += quote(environ.get('SCRIPT_NAME', ''))
        if environ.get('QUERY_STRING'):
            url += '?' + environ['QUERY_STRING']

        url += '/' + '/'.join([self.static_folder, filename])
        return url

    @property
    def request(self):
        return self._request

    @property
    def response(self):
        return self._response

    def handle_static(self, path):
        response = Response(None)

        # This is the path of a static file on the filesystem
        path = self.root_path + path

        if not os.path.exists(path) or not os.path.isfile(path):
            return self.not_found()

        mimetype = 'text/plain'
        guess_type = mimetypes.guess_type(path)[0]
        if guess_type:
            response.set_content_type(guess_type)
        else:
            response.set_content_type(mimetype)

        stats = os.stat(path)

        last_modified_time = time.gmtime(stats.st_mtime)
        last_modified_str = time.strftime(
            "%a, %d %b %Y %H:%M:%S UTC", last_modified_time)

        # Handle If-Modified-Since
        if_modified_since_str = self._request.if_modified_since
        if if_modified_since_str:
            if_modified_since_time = time.strptime(
                if_modified_since_str, "%a, %d %b %Y %H:%M:%S %Z")
            if if_modified_since_time >= last_modified_time:
                return self.not_modified()

        if 'Last-Modified' not in response.headers.keys():
            response.headers['Last-Modified'] = last_modified_str

        with open(path, 'r') as f:
            response.set_body(body=(f.read()))
        return response

    def handle_router(self):
        try:
            handler, args = self._router.get(
                self._request.path, self._request.method)
        # 404
        except TypeError:
            return self.not_found()

        if args:
            r = handler(**args)
        else:
            r = handler()
        return r

    def __call__(self, environ, start_response):
        self._response = Response(None)
        self._request = Request(None)
        self._server_handler = start_response
        # start_response.im_self._flush()
        self._request.bind(environ)
        r = None
        # Static files
        if self._request.path is not None and self._request.path.lstrip('/').startswith(self.static_folder):
            r = self.handle_static(self._request.path)
        else:
            try:
                r = self.handle_router()
            # 500
            except Exception as e:
                return LunarException(500, self._response, self._server_handler, self.DEBUG)()

        # Static files, 302, 304 and 404
        if isinstance(r, Response):
            self._response = r
            self._server_handler(r.status, r.headerlist)
            return [r.body]

        # Normal html
        self._response.set_body(body=r)
        self._response.set_status(200)
        start_response(self._response.status, self._response.headerlist)
        return [self._response.body]


"""
default methods and properties
"""

# global app stack.
app_stack = _Stack()

# default app
default_app = app_stack.top()
if not default_app:  # hack for shell
    default_app = Lunar('/')

# shell
request = app_stack.top().request
response = app_stack.top().response
session = app_stack.top().session
