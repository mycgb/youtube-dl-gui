# -*- coding: utf-8 -*-
"""
youtube_dl_gui.py
YouTube 视频下载图形界面（tkinter）
功能：解析视频 -> 选清晰度 -> 下载封面 / 纯视频 / 音频 / 「视频+音频合成」
运行：D:\\Python\\python.exe youtube_dl_gui.py
"""

import io
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox
import tkinter.ttk as ttk
import urllib.request
import webbrowser

import youtube_dl_core as core

APP_TITLE = "YouTube 视频下载器 · 解析 + 音视频合成"
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "YouTube下载")
TMP_SUFFIX = "_tmpparts"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "youtube_dl_config.json")


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1000x780")
        self.root.minsize(880, 700)
        self.msgq = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.info = None
        self.variants = []
        self.cfg = self._load_config()
        self.ffmpeg_path = self.cfg.get("ffmpeg_path") or core.find_ffmpeg() or ""
        self.cover_photo = None
        self._save_job = None
        # 配置优先；没有配置时才自动探测本地代理
        self.auto_proxy_mode, self.auto_proxy_url = self._detect_local_proxy()
        if self.cfg.get("proxy_mode"):
            self.default_proxy_mode = self.cfg["proxy_mode"]
            self.default_proxy_url = self.cfg.get("proxy_url", "")
        else:
            self.default_proxy_mode = self.auto_proxy_mode
            self.default_proxy_url = self.auto_proxy_url

        self._init_style()
        self._build_ui()
        self._set_busy(False)
        self._drain_job = self.root.after(80, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._log("就绪。粘贴 YouTube 链接后点击「解析视频」。")
        if self.default_proxy_url:
            self._log("代理：%s %s（设置会自动保存）" %
                      (self.default_proxy_mode, self.default_proxy_url))
        else:
            self._log("提示：若视频流下载失败，请切换代理模式为「自定义」并填入可用代理，再点「测试连接」。")
        if not self.ffmpeg_path:
            self._log("提示：未找到 ffmpeg，「音视频合成」和音频转 MP3 不可用，可在下方手动指定 ffmpeg.exe。")

    @staticmethod
    def _detect_local_proxy():
        for port in (7890, 10809, 1080):
            s = socket.socket()
            s.settimeout(0.25)
            try:
                s.connect(("127.0.0.1", port))
                return "自定义", "http://127.0.0.1:%d" % port
            except OSError:
                pass
            finally:
                s.close()
        return "系统代理", ""

    # ---------------- 配置持久化 ----------------
    @staticmethod
    def _load_config():
        try:
            if os.path.isfile(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_config(self):
        self._save_job = None
        data = {
            "output_dir": self.outdir_var.get().strip(),
            "proxy_mode": self.proxy_var.get(),
            "proxy_url": self.proxy_url_var.get().strip(),
            "ffmpeg_path": self.ffmpeg_var.get().strip(),
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log("保存配置失败：%s" % e)

    def _schedule_save(self, *_args):
        if self._save_job is not None:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.root.after(400, self._save_config)

    def _on_close(self):
        for job in (self._save_job, getattr(self, "_drain_job", None)):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self._save_config()
        self.root.destroy()

    # ---------------- UI ----------------
    def _init_style(self):
        try:
            self.root.tk.call("source", "azure.tcl")
            self.root.tk.call("set_theme", "light")
        except Exception:
            pass
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"),
                        foreground="#1f2937")
        style.configure("Sub.TLabel", font=("Microsoft YaHei UI", 9),
                        foreground="#6b7280")
        style.configure("Card.TFrame", background="#ffffff", relief="flat")
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                        foreground="#ffffff", background="#2563eb")
        style.map("Accent.TButton",
                  background=[("active", "#1d4ed8"), ("disabled", "#93c5fd")])
        style.configure("Green.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                        foreground="#ffffff", background="#059669")
        style.map("Green.TButton",
                  background=[("active", "#047857"), ("disabled", "#6ee7b7")])
        style.configure("Treeview", rowheight=30, fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))

    def _build_ui(self):
        root = self.root
        root.configure(bg="#f3f4f6")
        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)

        # 标题
        head = ttk.Frame(main)
        head.pack(fill="x", pady=(0, 10))
        ttk.Label(head, text=APP_TITLE, style="Title.TLabel").pack(side="left")

        # 链接输入
        linkbox = ttk.LabelFrame(main, text=" 视频链接 ", padding=10)
        linkbox.pack(fill="x")
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(linkbox, textvariable=self.url_var)
        self.url_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.url_entry.bind("<Return>", lambda e: self.parse())
        self.parse_btn = ttk.Button(linkbox, text="解析视频", style="Accent.TButton",
                                    command=self.parse, width=12)
        self.parse_btn.pack(side="left", padx=(8, 0), ipady=3)

        # 信息卡片
        info_frame = ttk.LabelFrame(main, text=" 视频信息 ", padding=10)
        info_frame.pack(fill="x", pady=(10, 0))
        self.cover_label = tk.Label(info_frame, bg="#e5e7eb", width=18, height=4,
                                    text="封面预览", font=("Microsoft YaHei UI", 9),
                                    foreground="#9ca3af")
        self.cover_label.pack(side="left", padx=(0, 12))
        info_right = ttk.Frame(info_frame)
        info_right.pack(side="left", fill="x", expand=True)
        self.title_var = tk.StringVar(value="尚未解析")
        ttk.Label(info_right, textvariable=self.title_var,
                  font=("Microsoft YaHei UI", 11, "bold"),
                  foreground="#111827", wraplength=790).pack(anchor="w")
        self.meta_var = tk.StringVar(value="作者：-    时长：-    清晰度：-")
        ttk.Label(info_right, textvariable=self.meta_var,
                  foreground="#4b5563").pack(anchor="w", pady=(6, 2))
        self.src_var = tk.StringVar(value="")
        ttk.Label(info_right, textvariable=self.src_var, style="Sub.TLabel",
                  wraplength=790).pack(anchor="w")

        # 清晰度列表
        list_frame = ttk.LabelFrame(main, text=" 清晰度 / 格式（选择一行后下载） ", padding=8)
        list_frame.pack(fill="both", expand=True, pady=(10, 0))
        cols = ("quality", "fps", "format", "vsize", "audio")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=3)
        for c, text, w in [("quality", "清晰度", 100), ("fps", "帧率", 70),
                           ("format", "视频格式 / 编码", 165),
                           ("vsize", "视频大小", 110), ("audio", "可合成音频", 120)]:
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor="center")
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.download("mux"))

        # 操作按钮
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(10, 0))
        self.action_buttons = []
        specs = [
            ("视频+音频合成", "green", lambda: self.download("mux")),
            ("仅下载视频", "", lambda: self.download("video")),
            ("下载音频(MP3)", "", lambda: self.download("audio")),
            ("下载封面", "", lambda: self.download("cover")),
            ("快速下载 360p(含声音)", "", lambda: self.download("progressive")),
        ]
        for text, style, cmd in specs:
            b = ttk.Button(btn_frame, text=text,
                           style="Green.TButton" if style else "TButton",
                           command=cmd)
            b.pack(side="left", padx=(0, 6), ipady=4)
            self.action_buttons.append(b)
        self.cancel_btn = ttk.Button(btn_frame, text="取消任务", command=self.cancel,
                                     state="disabled")
        self.cancel_btn.pack(side="right", ipady=4)

        # 设置区
        opt = ttk.LabelFrame(main, text=" 设置 ", padding=10)
        opt.pack(fill="x", pady=(10, 0))
        row1 = ttk.Frame(opt)
        row1.pack(fill="x")
        ttk.Label(row1, text="输出目录：", width=10).pack(side="left")
        self.outdir_var = tk.StringVar(value=self.cfg.get("output_dir") or DEFAULT_DIR)
        ttk.Entry(row1, textvariable=self.outdir_var).pack(side="left", fill="x",
                                                            expand=True, ipady=3)
        ttk.Button(row1, text="浏览", width=7,
                   command=self.pick_outdir).pack(side="left", padx=4)
        ttk.Button(row1, text="打开", width=7,
                   command=self.open_outdir).pack(side="left")

        row2 = ttk.Frame(opt)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="网络代理：", width=10).pack(side="left")
        self.proxy_var = tk.StringVar(value=self.default_proxy_mode)
        ttk.Combobox(row2, textvariable=self.proxy_var, width=11, state="readonly",
                     values=("系统代理", "不使用代理", "自定义")).pack(side="left")
        self.proxy_url_var = tk.StringVar(value=self.default_proxy_url)
        self.proxy_entry = ttk.Entry(row2, width=24, textvariable=self.proxy_url_var)
        self.proxy_entry.pack(side="left", padx=(8, 0), ipady=3)
        ttk.Button(row2, text="测试连接", width=9,
                   command=self.test_proxy).pack(side="left", padx=6)
        ttk.Label(row2, text="ffmpeg：", style="Sub.TLabel").pack(side="left", padx=(12, 0))
        self.ffmpeg_var = tk.StringVar(value=self.ffmpeg_path)
        ttk.Entry(row2, textvariable=self.ffmpeg_var, width=34).pack(side="left", padx=4, ipady=3)
        ttk.Button(row2, text="选择", width=7, command=self.pick_ffmpeg).pack(side="left")

        # 设置改动后自动保存（延迟 400ms，避免逐字写入）
        for var in (self.outdir_var, self.proxy_var, self.proxy_url_var, self.ffmpeg_var):
            var.trace_add("write", self._schedule_save)

        # 进度
        prog = ttk.Frame(main)
        prog.pack(fill="x", pady=(10, 0))
        self.pbar = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.pbar.pack(side="left", fill="x", expand=True, ipady=3)
        self.status_var = tk.StringVar(value="空闲")
        ttk.Label(prog, textvariable=self.status_var, width=30,
                  anchor="w").pack(side="left", padx=10)

        # 日志
        log_frame = ttk.LabelFrame(main, text=" 运行日志 ", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.logtext = tk.Text(log_frame, height=2, wrap="word", bg="#ffffff",
                               fg="#374151", font=("Consolas", 9),
                               relief="flat", borderwidth=0)
        self.logtext.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.logtext.yview)
        self.logtext.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")

        # 菜单
        menubar = tk.Menu(root)
        fm = tk.Menu(menubar, tearoff=0)
        fm.add_command(label="选择输出目录", command=self.pick_outdir)
        fm.add_command(label="打开输出目录", command=self.open_outdir)
        fm.add_separator()
        fm.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=fm)
        tm = tk.Menu(menubar, tearoff=0)
        tm.add_command(label="检查 ffmpeg", command=self.check_ffmpeg)
        tm.add_command(label="清理未完成文件(.part)", command=self.clean_parts)
        menubar.add_cascade(label="工具", menu=tm)
        hm = tk.Menu(menubar, tearoff=0)
        hm.add_command(label="使用说明", command=self.show_help)
        hm.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=hm)
        root.configure(menu=menubar)

    # ---------------- 队列 / 日志 ----------------
    def _post(self, kind, payload=None):
        self.msgq.put((kind, payload))

    def _log(self, msg):
        self._post("log", str(msg))

    def _drain_queue(self):
        try:
            while True:
                kind, data = self.msgq.get_nowait()
                if kind == "log":
                    self.logtext.insert("end", data + "\n")
                    self.logtext.see("end")
                elif kind == "status":
                    self.status_var.set(data)
                elif kind == "progress":
                    done, total, speed = data
                    if self.pbar["mode"] != "determinate":
                        self.pbar.stop()
                        self.pbar["mode"] = "determinate"
                    if total > 0:
                        pct = min(100.0, done * 100.0 / total)
                        self.pbar["value"] = pct
                        self.status_var.set("%.1f%%  %s  %s/%s" % (
                            pct, core.format_size(speed) + "/s",
                            core.format_size(done), core.format_size(total)))
                    else:
                        self.status_var.set(core.format_size(done))
                elif kind == "indeterminate":
                    # 先 stop 再 start，避免多条消息叠加导致进度条越滚越快
                    self.pbar.stop()
                    self.pbar["mode"] = "indeterminate"
                    self.pbar.start(12)
                    self.status_var.set(data)
                elif kind == "result":
                    self._show_result(data)
                elif kind == "cover":
                    self._show_cover(data)
                elif kind == "done":
                    self._finish(data)
                elif kind == "error":
                    messagebox.showerror("出错了", data)
        except queue.Empty:
            pass
        self._drain_job = self.root.after(80, self._drain_queue)

    def _show_cover(self, raw):
        try:
            from PIL import Image, ImageOps
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            img = ImageOps.fit(img, (180, 100))
            self.cover_photo = tk.PhotoImage(data=self._to_gif_bytes(img))
            self.cover_label.configure(image=self.cover_photo, text="")
        except Exception as e:
            self.cover_label.configure(text="封面加载失败", foreground="#9ca3af")
            self._log("封面加载失败：%s" % e)

    @staticmethod
    def _to_gif_bytes(img):
        buf = io.BytesIO()
        img.save(buf, format="GIF")
        return buf.getvalue()

    # ---------------- 状态 ----------------
    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.parse_btn["state"] = state
        for b in self.action_buttons:
            b["state"] = state
        self.cancel_btn["state"] = "normal" if busy else "disabled"
        # 关键：切回 determinate 之前必须先 stop()，否则滚动动画会一直跑
        self.pbar.stop()
        self.pbar["mode"] = "determinate"
        if busy:
            self.pbar["value"] = 0

    def _finish(self, msg):
        self._set_busy(False)
        self.status_var.set(msg or "空闲")
        self._log(msg or "任务结束")

    def _proxy_args(self):
        mode = {"系统代理": "system", "不使用代理": "none", "自定义": "custom"}[
            self.proxy_var.get()]
        url = self.proxy_entry.get().strip()
        if mode == "custom" and not url:
            raise core.DownloadError("自定义代理模式需要填写代理地址")
        return mode, url

    # ---------------- 动作 ----------------
    def parse(self):
        if self.busy:
            return
        url = core.normalize_url(self.url_var.get())
        if not url:
            messagebox.showwarning("提示", "请先粘贴视频链接")
            return
        self.url_var.set(url)
        self._set_busy(True)
        self._log("正在解析：%s" % url)
        self._post("indeterminate", "解析中，请稍候…")
        self.cancel_event = threading.Event()
        threading.Thread(target=self._parse_worker, args=(url,), daemon=True).start()

    def _parse_worker(self, url):
        try:
            mode, purl = self._proxy_args()
            data = core.extract(url, proxy_mode=mode, proxy_url=purl)
            info = core.parse_result(data)
            self.info = info
            self.variants = info["variants"]
            self._post("result", info)
            self._log("解析成功：%s（共 %d 个清晰度）" % (info["title"], len(self.variants)))
            if info["cover"]:
                try:
                    dl = core.MediaDownloader(mode, purl, log=lambda m: None)
                    self._post("cover", dl.fetch_bytes(info["cover"]))
                except Exception as e:
                    self._log("封面获取失败：%s" % e)
            self._post("done", "解析完成")
        except Exception as e:
            self._log("解析失败：%s" % e)
            self._post("error", str(e))
            self._post("done", "解析失败")

    def _show_result(self, info):
        self.info = info
        self.variants = info.get("variants") or []
        self.title_var.set(info["title"])
        self.meta_var.set("作者：%s    时长：%s    清晰度：%d 种" % (
            info["author"] or "-", core.format_duration(info["duration"]),
            len(info["variants"])))
        self.src_var.set(info["post_url"])
        self.tree.delete(*self.tree.get_children())
        for i, v in enumerate(self.variants):
            self.tree.insert("", "end", iid=str(i), values=(
                v["label"] + (" (HDR)" if v["quality"] >= 2160 else ""),
                ("%d fps" % v["fps"]) if v["fps"] else "-",
                "%s / %s" % (v["video_ext"].upper(), v["video_codec"] or "-"),
                core.format_size(v["video_size"]),
                "是" if v["audio_url"] else "可借用",
            ))
        if self.variants:
            # 默认选中 720p（没有则选最小的），避免一上来就下载几百 MB 的 4K
            idx = next((i for i, v in enumerate(self.variants)
                        if v["quality"] == 720), None)
            if idx is None:
                idx = len(self.variants) - 1
            iid = str(idx)
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
            v = self.variants[idx]
            total = v["video_size"] + (v["audio_size"] or 0)
            self._log("已选中 %s（约 %s）。可在列表双击直接合成下载。" %
                      (v["label"], core.format_size(total)))

    def _selected_variant(self):
        sel = self.tree.selection()
        if not sel:
            if self.variants:
                return self.variants[0]
            raise core.DownloadError("请先解析视频并选择清晰度")
        return self.variants[int(sel[0])]

    def download(self, mode):
        if self.busy:
            return
        if not self.info:
            messagebox.showwarning("提示", "请先解析视频")
            return
        self._set_busy(True)
        self.cancel_event = threading.Event()
        self.pbar["value"] = 0
        self._post("indeterminate", "准备下载…")
        threading.Thread(target=self._download_worker, args=(mode,), daemon=True).start()

    def cancel(self):
        self.cancel_event.set()
        self._log("已发送取消请求，正在停止…")

    def _dl(self, dl, url, dest, label, kind, variant=None):
        """下载媒体流；直链失效（403/404）时自动重新解析刷新地址并重试。"""
        for round_no in range(3):
            try:
                dl.download(url, dest, label)
                return
            except core.DownloadError as e:
                text = str(e)
                expired = any(k in text for k in ("403", "404", "410", "Forbidden", "过期"))
                if not expired or round_no == 2:
                    raise
                self._log("直链失效（%s），正在重新解析获取新地址…（第 %d/3 次）" %
                          (label, round_no + 1))
                try:
                    mode, purl = self._proxy_args()
                    fresh = core.parse_result(core.extract(
                        self.url_var.get(), proxy_mode=mode, proxy_url=purl))
                    self.info = fresh
                    if kind == "progressive":
                        url = fresh["progressive_url"]
                    elif kind == "audio":
                        url = fresh["audio_url"]
                    elif kind == "variant_video":
                        url = next((v["video_url"] for v in fresh["variants"]
                                    if v["label"] == variant["label"]), url)
                    else:
                        url = next((v["audio_url"] for v in fresh["variants"]
                                    if v["label"] == variant["label"]), fresh["audio_url"])
                except Exception as re:
                    self._log("重新解析失败：%s" % re)
                    raise
                part = dest + ".part"
                if os.path.isfile(part):
                    os.remove(part)

    def _download_worker(self, mode):
        info = self.info
        outdir = self.outdir_var.get().strip() or DEFAULT_DIR
        os.makedirs(outdir, exist_ok=True)
        tmpdir = os.path.join(outdir, TMP_SUFFIX)
        os.makedirs(tmpdir, exist_ok=True)
        base = core.safe_filename(info["title"])
        mode_name = {"mux": "音视频合成", "video": "纯视频", "audio": "音频",
                     "cover": "封面", "progressive": "快速下载"}[mode]
        self._log("开始任务：%s" % mode_name)
        try:
            proxy_mode, proxy_url = self._proxy_args()
            dl = core.MediaDownloader(
                proxy_mode, proxy_url, log=lambda m: self._log(m),
                progress=lambda d, t, s: self._post("progress", (d, t, s)),
                cancel=lambda: self.cancel_event.is_set())

            if mode == "cover":
                if not info["cover"]:
                    raise core.DownloadError("该视频没有封面地址")
                dest = os.path.join(outdir, base + "_封面.jpg")
                raw = dl.fetch_bytes(info["cover"])
                with open(dest, "wb") as f:
                    f.write(raw)
                self._post("done", "封面已保存：" + dest)
                return

            if mode == "progressive":
                if not info["progressive_url"]:
                    raise core.DownloadError("该视频没有可直接下载（含声音）的地址")
                dest = os.path.join(outdir, base + "_360p_含声音.mp4")
                self._post("indeterminate", "下载 360p（含声音）…")
                self._dl(dl, info["progressive_url"], dest, "360p 视频", "progressive")
                self._post("done", "下载完成：" + dest)
                return

            if mode == "audio":
                dest = os.path.join(outdir, base + "_音频")
                raw_path = os.path.join(tmpdir, base + "_audio_raw")
                self._post("indeterminate", "下载音频流…")
                self._dl(dl, info["audio_url"], raw_path, "音频", "audio")
                ffmpeg = self.ffmpeg_var.get().strip() or core.find_ffmpeg()
                target = dest + ".mp3"
                if ffmpeg:
                    self._log("正在转换为 MP3…")
                    core.convert_audio(raw_path, target, True, ffmpeg,
                                       lambda m: self._log(m))
                    os.remove(raw_path)
                else:
                    target = dest + ".m4a"
                    os.replace(raw_path, target)
                    self._log("未找到 ffmpeg，已保存原始音频")
                self._post("done", "音频已保存：" + target)
                return

            variant = self._selected_variant()
            ext = variant["video_ext"].lower()
            tag = variant["label"].replace(" ", "")
            video_tmp = os.path.join(tmpdir, base + "_" + tag + "_video." + ext)
            if mode in ("mux", "video"):
                self._post("indeterminate", "下载 %s 视频流…" % variant["label"])
                self._dl(dl, variant["video_url"], video_tmp, "视频流",
                         "variant_video", variant)

            if mode == "video":
                dest = os.path.join(outdir, "%s_%s_无声.%s" % (base, tag, ext))
                os.replace(video_tmp, dest)
                self._post("done", "视频已保存：" + dest)
                return

            # mux 模式：视频 + 音频 -> 一个带声音的文件
            audio_url = variant["audio_url"] or info["audio_url"]
            if not audio_url:
                raise core.DownloadError("该清晰度没有匹配的音频流，可尝试「快速下载 360p」")
            ffmpeg = self.ffmpeg_var.get().strip() or core.find_ffmpeg()
            if not ffmpeg:
                raise core.DownloadError("未找到 ffmpeg，无法合成音频。请手动指定 ffmpeg.exe，或改用「快速下载 360p（含声音）」")
            audio_tmp = os.path.join(tmpdir, base + "_" + tag + "_audio.src")
            self._post("indeterminate", "下载音频流…")
            self._dl(dl, audio_url, audio_tmp, "音频流", "variant_audio", variant)

            dest = os.path.join(outdir, "%s_%s_有声.%s" % (base, tag, ext))
            self._post("indeterminate", "正在合成音视频，请稍候…")
            src_dur = core.get_duration(video_tmp, ffmpeg)
            core.mux_video_audio(video_tmp, audio_tmp, dest, ext, ffmpeg,
                                 lambda m: self._log(m))
            streams = core.probe_streams(dest, ffmpeg)
            if streams:
                self._log("输出校验：视频流=%s，音频流=%s" % (
                    "有" if streams["video"] else "无",
                    "有" if streams["audio"] else "无"))
            out_dur = core.get_duration(dest, ffmpeg)
            if src_dur and out_dur and src_dur - out_dur > 2:
                self._log("警告：合成结果比源视频短 %.1f 秒，音频流可能不完整；可重新解析后再试。" %
                          (src_dur - out_dur))
            for p in (video_tmp, audio_tmp):
                try:
                    os.remove(p)
                except OSError:
                    pass
            self._post("done", "合成完成：" + dest)
        except core.Cancelled:
            self._post("done", "任务已取消")
        except Exception as e:
            self._log("任务失败：%s" % e)
            self._post("error", str(e))
            self._post("done", "任务失败")

    # ---------------- 杂项 ----------------
    def pick_outdir(self):
        d = filedialog.askdirectory(initialdir=self.outdir_var.get() or DEFAULT_DIR)
        if d:
            self.outdir_var.set(d)

    def open_outdir(self):
        d = self.outdir_var.get() or DEFAULT_DIR
        if os.path.isdir(d):
            try:
                os.startfile(d)
            except Exception:
                subprocess.Popen(["explorer", d])
        else:
            messagebox.showinfo("提示", "目录不存在：%s" % d)

    def pick_ffmpeg(self):
        p = filedialog.askopenfilename(title="选择 ffmpeg.exe",
                                       filetypes=[("ffmpeg", "ffmpeg*.exe;ffmpeg"), ("所有文件", "*.*")])
        if p:
            self.ffmpeg_var.set(p)
            self.ffmpeg_path = p

    def test_proxy(self):
        """快速检查当前代理能否访问 YouTube 与解析接口。"""
        def worker():
            try:
                mode, url = self._proxy_args()
                opener = core.build_opener(mode, url)
                results = []
                for target in ("https://www.youtube.com/", core.API_BASE):
                    try:
                        with opener.open(urllib.request.Request(
                                target, headers={"User-Agent": core.UA}), timeout=15) as r:
                            results.append("%s -> HTTP %s" % (target, r.status))
                    except Exception as e:
                        results.append("%s -> 失败：%s" % (target, e))
                self._log("代理测试（%s %s）：\n  %s" % (
                    mode, url or "-", "\n  ".join(results)))
            except Exception as e:
                self._log("代理测试失败：%s" % e)
        threading.Thread(target=worker, daemon=True).start()

    def check_ffmpeg(self):
        p = self.ffmpeg_var.get().strip() or core.find_ffmpeg()
        if p and os.path.isfile(p):
            messagebox.showinfo("ffmpeg", "可用：\n" + p)
        else:
            messagebox.showwarning("ffmpeg", "未找到 ffmpeg。可运行 pip install imageio-ffmpeg，或手动下载后在此指定。")

    def clean_parts(self):
        d = self.outdir_var.get() or DEFAULT_DIR
        targets = []
        for root, _, files in os.walk(d):
            for f in files:
                if f.endswith(".part") or f.endswith(".part_tmp"):
                    targets.append(os.path.join(root, f))
        if not targets:
            messagebox.showinfo("清理", "没有发现未完成的临时文件")
            return
        if messagebox.askyesno("清理", "将删除 %d 个 .part 文件（仅临时文件，不影响已完成视频）：\n%s" %
                               (len(targets), "\n".join(targets[:8]))):
            for p in targets:
                try:
                    os.remove(p)
                except OSError:
                    pass
            self._log("已清理 %d 个临时文件" % len(targets))

    def show_help(self):
        messagebox.showinfo("使用说明", """1. 粘贴 YouTube 视频链接，点「解析视频」。
2. 在列表中选择清晰度（4K/1080p 等为纯视频流）。
3. 「视频+音频合成」：下载纯视频 + 音频并用 ffmpeg 合成一个带声音的文件（不重编码视频，速度快）。
4. 「仅下载视频」：只要画面，没有声音。
5. 「下载音频(MP3)」：提取音频并转 MP3。
6. 「下载封面」：保存视频缩略图。
7. 「快速下载 360p(含声音)」：使用站点返回的已混流地址，不需要 ffmpeg。
8. 网络不通时可在设置里切换代理模式；断点续传会保留 .part 文件。""")

    def show_about(self):
        GITHUB_URL = "https://github.com/mycgb/youtube-dl-gui"
        win = tk.Toplevel(self.root)
        win.title("关于")
        win.resizable(False, False)
        win.transient(self.root)
        ttk.Label(win, text=APP_TITLE,
                  font=("Microsoft YaHei UI", 12, "bold")).pack(padx=24, pady=(18, 6))
        ttk.Label(win, text="支持多清晰度解析与音视频合成的视频下载工具",
                  foreground="#6b7280").pack(padx=24)
        ttk.Label(win, text="项目地址：", foreground="#374151").pack(anchor="w", padx=24, pady=(18, 0))
        link = tk.Label(win, text=GITHUB_URL, fg="#2563eb", cursor="hand2",
                        font=("Microsoft YaHei UI", 10, "underline"))
        link.pack(anchor="w", padx=24, pady=(2, 4))
        link.bind("<Button-1>", lambda e: webbrowser.open(GITHUB_URL))
        ttk.Label(win, text="（点击链接可在浏览器中打开）",
                  foreground="#9ca3af", font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=24)
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(16, 18), ipadx=12)


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
