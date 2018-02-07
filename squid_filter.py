#!/usr/bin/python3

import shelve
import os
import os.path
import datetime
import sys
import syslog
import requests
from urllib.parse import urlparse, parse_qs


class TimeKeeper:
    def __init__(self, limit):
        self._latest = datetime.datetime.now()
        self._total = datetime.timedelta()
        self._TIME_LIMIT = datetime.timedelta(minutes=limit)

    def update(self):
        now = datetime.datetime.now()

        if self._latest.day == now.day:
            if self._total < self._TIME_LIMIT:
                delta = now - self._latest
                if delta.total_seconds() > 10*60:
                    self._latest = now
                    self._total += datetime.timedelta(minutes=10)
                    syslog.syslog("squid_filter: time add")
                else:
                    syslog.syslog("squid_filter: advantage")
            else:
                pass
        else:
            syslog.syslog("squid_filter: reset time")

            self._total = datetime.timedelta()
            self._latest = now
            self._total += datetime.timedelta(minutes=10)

    def is_ok(self):
        if self._latest.day != datetime.datetime.now().day:
            return True

        return self._total < self._TIME_LIMIT

    @property
    def total(self):
        return self._total


def get_stat(key, vid):
    params = {"part": "snippet,statistics",
              "id": vid,
              "fields": "items/statistics,items/snippet",
              "key": key}
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos", params=params)
    except requests.exceptions.RequestException:
        return "----", 0, 0, 0

    j = r.json()
    if "items" in j and len(j["items"]) > 0:
        j = j["items"][0]

        title = j["snippet"]["title"]
        view_count = int(j["statistics"]["viewCount"])
        like_count = int(j["statistics"]["likeCount"])
        dislike_count = int(j["statistics"]["dislikeCount"])

        return title, view_count, like_count, dislike_count
    else:
        return "----", 0, 0, 0


def is_play_ok(view, like, dislike, low_watermark, good_bad_rate):
    if view < low_watermark:
        return False

    if like * 100.0 / (like + dislike + 1) < good_bad_rate:
        return False
    return True


def main(key, limit, low_watermark, good_bad_rate):

    d = shelve.open("/tmp/squid_filter")
    if "tk" not in d:
        d["tk"] = TimeKeeper(limit)

    tk = d["tk"]

    while True:
        line = sys.stdin.readline().strip()

        tok = line.split()
        if len(tok) > 1:
            o = urlparse(tok[0])
            if o.netloc.find("youtube") != -1:
                q = parse_qs(o.query)
                vid = q.get("v", [""])[0]
                if vid != "":
                    tk.update()
                    d["tk"] = tk
                    title, view_count, like_count, dislike_count = get_stat(key, vid)
                    if title != "----" and not is_play_ok(view_count, like_count, dislike_count, low_watermark, good_bad_rate):
                        syslog.syslog("squid_filter: REJECT because of elsa check: {};"
                                      " from {}; view:{}; like:{}; dislike:{}"
                                      .format(title, tok[1], view_count, like_count, dislike_count))
                        sys.stdout.write('OK rewrite-url="https://www.google.com"\n')
                        sys.stdout.flush()
                        continue

                    if not tk.is_ok():
                        syslog.syslog("squid_filter: REJECT because of time limit: {}; from {}; total_time:{}[min]"
                                      .format(title, tok[1], tk.total.total_seconds()/60))
                        sys.stdout.write('OK rewrite-url="https://www.google.com"\n')
                        sys.stdout.flush()
                        continue

                    syslog.syslog("squid_filter: ACCEPT: {}; from {}; total_time: {}[min]; view:{}; like:{}; dislike:{}"
                                  .format(title, tok[1], tk.total.total_seconds()/60, view_count, like_count, dislike_count))

        sys.stdout.write("OK\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main(os.getenv("SQUID_YT_API_KEY", ""), # youtube v3 API key
         int(os.getenv("SQUID_YT_LIMIT_MINUTES", "120")), # view time limit[min] per day.
         int(os.getenv("SQUID_YT_VIEW_LOW_WATERMARK", "1000")), # minimum viewed count.
         int(os.getenv("SQUID_YT_GOOD_BAD_RATE", "70"))) # good/bad rate[%] threshhold.
