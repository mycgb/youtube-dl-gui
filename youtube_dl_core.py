# -*- coding: utf-8 -*-
"""
youtube_dl_core.py
逆向 youtube.iiilab.com 的解析接口，提供：
  * 视频信息解析（标题、作者、时长、封面、各清晰度视频/音频直链）
  * 支持断点续传的流式下载
  * 使用 ffmpeg 将「纯视频流 + 纯音频流」合成为一个带声音的视频
  * 音频提取 / 转码
仅依赖 Python 标准库；ffmpeg 可自动从 PATH / imageio-ffmpeg / 常见安装目录探测。
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://webapi.iiilab.com"
API_EXTRACT = API_BASE + "/api/web/extract"
SITE = "youtube"

# 从站点前端 bundle 中提取的签名参数
HEADER_TS = "G-Timestamp"
HEADER_FOOTER = "G-Footer"
SECRET = "JSnHKQfP" + "1IlzIQzs"          # base64: SlNuSEtRZlA= / MUlseklRenM=

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')


class ExtractError(Exception):
    """解析失败"""


class DownloadError(Exception):
    """下载失败"""


class Cancelled(Exception):
    """用户取消"""


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------
def log_default(msg):
    print(msg)


def safe_filename(name, maxlen=80):
    name = ILLEGAL_CHARS.sub("_", name or "youtube_video").strip(" .")
    name = re.sub(r"\s+", " ", name)
    return (name[:maxlen] or "youtube_video")


def format_size(num):
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "-"
    if num <= 0:
        return "-"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return "%.1f %s" % (num, unit) if unit != "B" else "%d B" % num
        num /= 1024
    return "%.1f TB" % num


def format_duration(sec):
    try:
        sec = int(float(sec))
    except (TypeError, ValueError):
        return "-"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return ("%d:%02d:%02d" % (h, m, s)) if h else ("%d:%02d" % (m, s))


def normalize_url(text):
    """从粘贴的文本里提取出第一个 http(s) 链接，兼容被编码包裹的链接。"""
    if not text:
        return ""
    text = text.strip()
    m = re.search(r"https?://[^\s<>\"']+", text)
    url = m.group(0) if m else text
    url = url.rstrip(".,;:!?)】」』")
    if re.search(r"%3[aA]|%2[fF]", url):
        try:
            decoded = urllib.parse.unquote(url)
            m2 = re.search(r"https?://[^\s<>\"']+", decoded)
            if m2:
                url = m2.group(0)
        except Exception:
            pass
    return url


def is_youtube_url(url):
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    return host in ("youtube.com", "www.youtube.com", "m.youtube.com",
                    "youtu.be", "www.youtu.be", "music.youtube.com",
                    "youtube-nocookie.com", "www.youtube-nocookie.com")


def build_opener(proxy_mode="system", proxy_url=""):
    """proxy_mode: system / none / custom"""
    if proxy_mode == "none":
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if proxy_mode == "custom" and proxy_url:
        p = proxy_url.strip()
        if not re.match(r"^[a-zA-Z]+://", p):
            p = "http://" + p
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": p, "https": p}))
    return urllib.request.build_opener()  # 跟随环境变量 / 系统代理


def find_ffmpeg():
    """按 PATH -> imageio-ffmpeg -> 常见安装目录 的顺序查找 ffmpeg。"""
    env = os.environ.get("FFMPEG_PATH")
    if env and os.path.isfile(env):
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    candidates = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
        os.path.expanduser(r"~\scoop\apps\ffmpeg\current\bin\ffmpeg.exe"),
        "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


# --------------------------------------------------------------------------
# 解析接口
# --------------------------------------------------------------------------
def extract(url, proxy_mode="system", proxy_url="", timeout=45):
    """
    调用站点解析接口。返回原始 JSON（含 medias/title/author/duration...）。
    """
    url = normalize_url(url)
    if not is_youtube_url(url):
        raise ExtractError("这不是一个 YouTube 链接，请输入 youtube.com 或 youtu.be 视频地址")
    ts = int(time.time())
    footer = hashlib.md5((url + SITE + str(ts) + SECRET).encode("utf-8")).hexdigest()
    payload = json.dumps({"url": url, "site": SITE}).encode("utf-8")
    req = urllib.request.Request(
        API_EXTRACT,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            HEADER_TS: str(ts),
            HEADER_FOOTER: footer,
            "User-Agent": UA,
            "Origin": "https://youtube.iiilab.com",
            "Referer": "https://youtube.iiilab.com/",
        },
    )
    opener = build_opener(proxy_mode, proxy_url)
    try:
        with opener.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        raise ExtractError("解析接口返回 HTTP %s %s" % (e.code, body))
    except Exception as e:
        raise ExtractError("请求解析接口失败：%s" % e)

    if not isinstance(data, dict) or not data.get("medias"):
        raise ExtractError(data.get("message", "未获取到视频信息，请检查链接是否正确") if isinstance(data, dict) else "解析结果异常")
    return data


def parse_result(data):
    """把接口结果整理成易于界面展示的字典。"""
    medias = data.get("medias") or []
    video_media = next((m for m in medias if m.get("media_type") == "video"), None)
    if not video_media:
        raise ExtractError("解析结果中没有视频")

    variants = []
    for v in video_media.get("variants") or []:
        if not v.get("video_url"):
            continue
        variants.append({
            "quality": v.get("quality") or 0,
            "label": v.get("quality_label") or ("%sp" % v.get("quality")),
            "fps": v.get("fps") or 0,
            "video_ext": v.get("video_ext") or "mp4",
            "video_codec": v.get("video_codec") or "",
            "video_size": v.get("video_filesize") or 0,
            "audio_ext": v.get("audio_ext") or "",
            "audio_codec": v.get("audio_codec") or "",
            "audio_size": v.get("audio_filesize") or 0,
            "video_url": v["video_url"],
            "audio_url": v.get("audio_url") or "",
        })
    variants.sort(key=lambda x: x["quality"], reverse=True)

    audio_media = next((m for m in medias if m.get("media_type") == "audio"), None)
    audio_url = audio_media.get("resource_url") if audio_media else ""
    if not audio_url:
        for v in variants:
            if v["audio_url"]:
                audio_url = v["audio_url"]
                break

    author = data.get("author") or ""
    if isinstance(author, dict):
        author = author.get("username") or author.get("name") or author.get("id") or ""

    return {
        "title": data.get("title") or "youtube_video",
        "author": author,
        "duration": video_media.get("duration") or data.get("duration") or 0,
        "cover": video_media.get("preview_url") or "",
        "progressive_url": video_media.get("resource_url") or "",
        "audio_url": audio_url,
        "variants": variants,
        "post_url": data.get("post_url") or "",
    }


# --------------------------------------------------------------------------
# 下载
# --------------------------------------------------------------------------
class MediaDownloader:
    """流式下载器，支持断点续传、进度回调、取消。"""

    def __init__(self, proxy_mode="system", proxy_url="", log=None,
                 progress=None, cancel=None):
        self.opener = build_opener(proxy_mode, proxy_url)
        self.log = log or log_default
        self.progress = progress or (lambda done, total, speed: None)
        self.cancel = cancel or (lambda: False)

    def _fetch(self, url, dest, label="文件"):
        """带临时错误重试的下载；每次重试都从 .part 断点继续。"""
        last = None
        for attempt in range(5):
            try:
                return self._fetch_once(url, dest, label)
            except Cancelled:
                raise
            except DownloadError as e:
                last = e
                text = str(e)
                transient = any(k in text for k in (
                    "SSL", "EOF", "timed out", "Timeout", "reset", "IncompleteRead",
                    "RemoteDisconnected", "502", "503", "504", "Bad Gateway",
                    "403", "404", "410", "Forbidden", "try again",
                    "中断", "不完整"))
                if not transient or attempt == 4:
                    raise
                self.log("%s：网络波动，%d 秒后重试（第 %d/5 次）" %
                         (label, 1 + attempt, attempt + 2))
                time.sleep(1 + attempt)
        raise last

    def _fetch_once(self, url, dest, label="文件"):
        part = dest + ".part"
        start = os.path.getsize(part) if os.path.isfile(part) else 0
        if start:
            self.log("%s：发现未完成文件，从 %s 处续传" % (label, format_size(start)))
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.youtube.com/",
            "Accept-Encoding": "identity",
        }
        if start:
            headers["Range"] = "bytes=%d-" % start
        req = urllib.request.Request(url, headers=headers)
        try:
            resp = self.opener.open(req, timeout=60)
        except Exception as e:
            raise DownloadError("%s 下载失败：%s" % (label, e))

        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype:
            resp.close()
            raise DownloadError("%s 下载失败：服务器返回了网页而非媒体数据（直链可能已过期，请重新解析）" % label)

        raw_len = resp.headers.get("Content-Range")
        if raw_len and "/" in raw_len:
            total = int(raw_len.split("/")[-1])
        else:
            cl = resp.headers.get("Content-Length")
            total = (int(cl) + start) if cl and start else (int(cl) if cl else 0)

        mode = "ab" if (start and getattr(resp, "status", 200) == 206) else "wb"
        if mode == "wb":
            start = 0
        done = start
        t0 = time.time()
        last_report = 0.0
        try:
            with open(part, mode) as f:
                while True:
                    if self.cancel():
                        raise Cancelled()
                    chunk = resp.read(128 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last_report >= 0.25:
                        speed = done - start
                        elapsed = max(now - t0, 0.001)
                        self.progress(done, total, speed / elapsed)
                        last_report = now
        except Cancelled:
            resp.close()
            self.log("%s：已取消，进度保留在 .part 文件" % label)
            raise
        except Exception as e:
            resp.close()
            raise DownloadError("%s 下载中断：%s" % (label, e))
        resp.close()
        if total > 0 and done < total:
            raise DownloadError("%s 下载不完整（%s / %s），将自动续传重试" %
                                (label, format_size(done), format_size(total)))
        self.progress(done, total, (done - start) / max(time.time() - t0, 0.001))
        try:
            os.replace(part, dest)
        except OSError:
            if os.path.exists(dest):
                os.remove(dest)
            os.rename(part, dest)
        self.log("%s 完成：%s" % (label, dest))
        return dest

    def download(self, url, dest, label="文件"):
        if not url:
            raise DownloadError("%s：没有可用的下载地址" % label)
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        return self._fetch(url, dest, label)

    def fetch_bytes(self, url, max_bytes=8 * 1024 * 1024, timeout=30):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with self.opener.open(req, timeout=timeout) as resp:
            if resp.headers.get("Content-Length") and int(resp.headers["Content-Length"]) > max_bytes:
                raise DownloadError("文件过大，跳过下载")
            return resp.read(max_bytes)


# --------------------------------------------------------------------------
# ffmpeg 合成 / 转码
# --------------------------------------------------------------------------
def _run_ffmpeg(cmd, log=None, cancel=None):
    log = log or log_default
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    log("执行：ffmpeg " + " ".join(cmd[1:]))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="ignore",
                            creationflags=creationflags)
    try:
        out, _ = proc.communicate()
    except Exception:
        proc.kill()
        raise
    if proc.returncode != 0:
        tail = "\n".join((out or "").strip().splitlines()[-12:])
        raise DownloadError("ffmpeg 处理失败（退出码 %s）：\n%s" % (proc.returncode, tail))
    return out


def mux_video_audio(video_path, audio_path, out_path, video_ext="mp4",
                    ffmpeg=None, log=None):
    """将无声音视频与音频流合成为单一文件（优先 stream copy，不重编码）。"""
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        raise DownloadError("未找到 ffmpeg，无法合成音频。请在界面中指定 ffmpeg.exe 路径，或安装 imageio-ffmpeg。")
    for p in (video_path, audio_path):
        if not p or not os.path.isfile(p):
            raise DownloadError("合成所需的临时文件不存在：%s" % p)
    base = [
        exe, "-y", "-hide_banner", "-loglevel", "error",
        "-fflags", "+genpts",
        "-i", video_path, "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest", "-avoid_negative_ts", "make_zero",
    ]
    if (video_ext or "").lower() == "webm":
        cmd = base + ["-c:v", "copy", "-c:a", "copy", out_path]
    else:
        cmd = base + ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                      "-movflags", "+faststart", out_path]
    _run_ffmpeg(cmd, log)
    if not os.path.isfile(out_path) or os.path.getsize(out_path) < 1024:
        raise DownloadError("合成结果文件异常")
    return out_path


def convert_audio(src_path, out_path, to_mp3=True, ffmpeg=None, log=None):
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        raise DownloadError("未找到 ffmpeg，无法转换音频格式")
    if to_mp3:
        cmd = [exe, "-y", "-hide_banner", "-loglevel", "error",
               "-i", src_path, "-vn", "-c:a", "libmp3lame", "-q:a", "2", out_path]
    else:
        cmd = [exe, "-y", "-hide_banner", "-loglevel", "error",
               "-i", src_path, "-vn", "-c:a", "copy", out_path]
    _run_ffmpeg(cmd, log)
    return out_path


def get_duration(path, ffmpeg=None):
    """返回媒体时长（秒），失败返回 None。"""
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        return None
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    p = subprocess.Popen([exe, "-hide_banner", "-i", path],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="ignore",
                         creationflags=creationflags)
    out, _ = p.communicate()
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", out)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def probe_streams(path, ffmpeg=None):
    """用 ffmpeg -i 输出粗略判断文件中是否包含视频/音频流（用于自检）。"""
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        return None
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    p = subprocess.Popen([exe, "-hide_banner", "-i", path],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="ignore",
                         creationflags=creationflags)
    out, _ = p.communicate()
    return {"video": bool(re.search(r"Stream #.*Video:", out)),
            "audio": bool(re.search(r"Stream #.*Audio:", out)), "raw": out}
