import socket
import ssl
import datetime

sockets = {}
cached_responses = {}


class CacheValue:
    def __init__(self, value, max_age):
        self.value = value
        self.max_age = max_age
        self.cache_time = datetime.datetime.now()

    def expired(self):
        if self.max_age is None:
            return False

        return self.cache_time + datetime.timedelta(seconds=self.max_age) <= datetime.datetime.now()


class URL:
    redirect_count = 0

    def __init__(self, url: str):
        self.scheme, url = url.split(":", 1)
        assert self.scheme in ["http", "https", "file", "data", "view-source"]

        if self.scheme == "data":
            self.datatype, self.data = url.split(",", 1)
            assert self.datatype == "text/html"
            return

        if self.scheme == "view-source":
            self.inner_url = URL(url)
            assert not self.inner_url.scheme in ["data", "view-source"]
            return

        url = url.replace("//", "", 1)

        if self.scheme == "file":
            self.filename = url
            return

        if self.scheme == "http":
            self.port = 80
        elif self.scheme == "https":
            self.port = 443

        if not "/" in url:
            url = url + "/"
        self.host, url = url.split("/", 1)
        if ":" in self.host:
            self.host, port = self.host.split(":", 1)
            self.port = int(port)
        self.path = "/" + url

    def request(self):
        if self.scheme == "data":
            return self.data

        if self.scheme == "file":
            f = open(self.filename)
            content = f.read()
            return content

        if self.scheme == "view-source":
            return self.inner_url.request()

        from_cache = response_from_cache(self)
        if from_cache is not None:
            return from_cache

        # Open socket connection
        s = sockets.get((self.host, self.port, self.scheme))
        if s is None:
            s = socket.socket(
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP
            )
            s.connect((self.host, self.port))
            if self.scheme == "https":
                ctx = ssl.create_default_context()
                s = ctx.wrap_socket(s, server_hostname=self.host)
            sockets[(self.host, self.port, self.scheme)] = s

        # Send request (as bytes)
        request_headers = [
            ("Host", self.host),
            ("Connection", "keep-alive"),
            ("User-Agent", "ej-browser")
        ]
        request = f"GET {self.path} HTTP/1.0\r\n"
        for (header, value) in request_headers:
            request += f"{header}: {value}\r\n"
        request += "\r\n"
        s.send(request.encode("utf8"))
        # Read response into file-like object
        response = s.makefile("r", encoding="utf8", newline="\r\n")
        # Read version, status, and explanation
        statusline = response.readline()
        version, status, explanation = statusline.split(" ", 2)
        status = int(status)
        # Read headers
        response_headers = {}
        while True:
            line = response.readline()
            if line == "\r\n":
                break
            header, value = line.split(":", 1)
            # Headers are case insensitive
            response_headers[header.casefold()] = value.strip()

        if status >= 300 and status < 400:
            assert "location" in response_headers
            location = response_headers["location"]
            if location.startswith("/"):
                location = f"{self.scheme}://{self.host}{location}"
            url = URL(location)
            url.redirect_count += 1
            assert url.redirect_count < 1024
            return url.request()

        # Ensure no tricky headers have been sent down
        assert "transfer-encoding" not in response_headers
        assert "content-encoding" not in response_headers
        assert "content-length" in response_headers
        content_length = int(response_headers["content-length"])
        content = response.read(content_length)
        store_in_cache(self, content, response_headers.get("cache-control"))
        return content


def response_from_cache(url: URL):
    cached = cached_responses.get(
        (url.scheme, url.host, url.port, url.path))
    if cached is None:
        return None

    if cached.expired():
        del cached_responses[(url.scheme, url.host, url.port, url.path)]
        return None

    return cached.value


def store_in_cache(url: URL, content: str, cache_control: str):
    if cache_control is not None and not cache_control.startswith("max-age="):
        return

    max_age = None
    if cache_control is not None:
        _, max_age = cache_control.split("=", 1)
        max_age = int(max_age)

    cached_responses[(url.scheme, url.host, url.port, url.path)
                     ] = CacheValue(content, max_age)

    return content


def show(body: str):
    entities = {"&lt;": "<", "&gt;": ">"}
    in_tag = False
    i = 0
    body_len = len(body)
    while i < body_len:
        c = body[i]
        if c == "&":
            end_index = body.find(";", i + 1)
            entity = body[i:end_index + 1]
            if end_index == -1 or not entity in entities:
                print(c, end="")
            else:
                print(entities.get(entity), end="")
                i = end_index
        elif c == "<":
            in_tag = True
        elif c == ">":
            in_tag = False
        elif not in_tag:
            print(c, end="")
        i += 1


def load(url: URL):
    body = url.request()
    if url.scheme == "view-source":
        print(body, end="")
    else:
        show(body)


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        load(URL(arg))
