#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import ssl
import urllib.request
from urllib.error import HTTPError
from pkg.utils.console import write_stderr
from cfg.defines import DEBUG

G_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)


def request(url, req_data, headers=None, return_resp_obj=False, method='POST',
            ignore_errors=(403, 404), error_callback=None, use_https=True):
    data = urllib.parse.urlencode(req_data) if req_data else None
    if type(data) == str:
        data = data.encode('utf-8')
    hdrs = headers if headers else {}
    req = urllib.request.Request(url, data, headers=hdrs, method=method)
    try:
        ctx = G_CONTEXT if use_https else None
        resp = urllib.request.urlopen(req, context=ctx)
        if return_resp_obj:
            return resp
        return resp.read().decode('utf-8')
    except HTTPError as e:
        print(e)
        err_text = e.read().decode('utf-8')
        if e.code not in ignore_errors or DEBUG:
            write_stderr(err_text + '\n')
        if error_callback:
            error_callback(Exception(err_text))
