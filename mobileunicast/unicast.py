#!/usr/bin/env python3
"""
IPTV 直播源处理工具（优化版）
改进说明：
- 只保留可访问的频道源，彻底剔除测速失败和假源
- 处理更健壮，防止部分源格式异常导致整体失败
- 合并去重逻辑更智能，防止同名频道因小差异重复
- 生成的 M3U 文件兼容 VLC、PotPlayer、电视盒子等多种播放器
- 支持命令行参数设置代理、频道保留数等

基础用法：
  python mobileunicast/unicast.py --top 10
带代理：
  python mobileunicast/unicast.py --top 10 --proxy http://127.0.0.1:7890
"""

import os
import re
import sys
import time
import socket
import argparse
import requests
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

# --------- 数据模型 ---------
@dataclass
class ChannelSource:
    name: str
    url: str
    speed: float = 0.0
    status: bool = False      # 是否可播放

# --------- 常量分组 ---------
GROUPS = {
    "央视频道": ["CCTV", "CGTN"],
    "卫视频道": ["卫视"],
    "港澳台频道": ["香港", "TVB", "RTHK", "港台", "澳门", "台湾", "澳视", "澳亚", "澳广"],
    "省级频道": [
        "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
        "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
        "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙",
        "广西", "西藏", "宁夏", "新疆", "东南", "东方"
    ],
    "市级频道": [
        "石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德",
        "太原", "大同", "阳泉", "长治", "晋城", "沈阳", "大连", "鞍山", "抚顺",
        "长春", "吉林", "四平", "哈尔滨", "齐齐哈尔", "南京", "无锡", "徐州",
        "杭州", "宁波", "温州", "合肥", "福州", "厦门", "南昌", "济南", "青岛",
        "郑州", "武汉", "长沙", "广州", "深圳", "南宁", "海口", "成都", "贵阳",
        "昆明", "拉萨", "西安", "兰州", "西宁", "银川", "乌鲁木齐"
    ]
}
GROUP_OTHER = "其它频道"

DATA_SOURCES = [
    # 基础源/全国源/部分省份
    "https://live.zbds.org/tv/yd.txt",
    "https://live.zbds.org/tv/iptv6.txt",
    "https://chinaiptv.pages.dev/Unicast/anhui/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/fujian/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/guangxi/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/hebei/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/heilongjiang/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/henan/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/hubei/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/jiangxi/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/jiangsu/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/shan3xi/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/shandong/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/zhejiang/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/shanghai/mobile.txt",
    "https://chinaiptv.pages.dev/Unicast/liaoning/mobile.txt",
    "https://mycode.zhoujie218.top/me/jsyd.txt",
    "https://raw.githubusercontent.com/q1017673817/iptv_zubo/refs/heads/main/hnyd.txt",
    "https://raw.githubusercontent.com/suxuang/myIPTV/refs/heads/main/%E7%A7%BB%E5%8A%A8%E4%B8%93%E4%BA%AB.txt",
    "https://live.zbds.org/tv/zjyd.txt",
    "https://live.zbds.org/tv/zjyd1.txt",
    "https://live.zbds.org/tv/jxyd.txt",
    "https://live.zbds.org/tv/sxyd.txt",
    "https://vdyun.com/hbm3u.txt",
    "https://vdyun.com/hbcm.txt",
    "https://vdyun.com/hbcm1.txt",
    "https://vdyun.com/hbcm2.txt",
    "https://vdyun.com/yd.txt",
    "https://vdyun.com/yd2.txt",
    "https://vdyun.com/ipv6.txt",
    "https://vdyun.com/sjzcm1.txt",
    "https://vdyun.com/sjzcm2.txt",
    "https://vdyun.com/hljcm.txt",
    "https://d.kstore.dev/download/15114/TVSolo.txt",
    "https://gh.catmak.name/https://raw.githubusercontent.com/alantang1977/aTV/refs/heads/master/output/result.m3u"
]

# --------- 工具函数 ---------
def normalize_name(name: str) -> str:
    """统一频道名格式"""
    name = name.replace(' ', '').replace('-', '')
    name = re.sub(r"CCTV[-\s]*(\d+)", r"CCTV\1", name, flags=re.IGNORECASE)
    name = re.sub(r"CGTN[-\s]*(\w+)", r"CGTN\1", name, flags=re.IGNORECASE)
    return name.strip()

def classify_channel(name: str) -> str:
    for group, keywords in GROUPS.items():
        if any(kw in name for kw in keywords):
            return group
    return GROUP_OTHER

def parse_line(line: str) -> Optional[Tuple[str, List[str]]]:
    """解析一行频道，返回 (name, [urls]) 或 None"""
    line = line.strip()
    if not line or line.endswith("#genre#") or "," not in line:
        return None
    try:
        name, urls = line.split(",", 1)
        urls = [u for u in urls.split("#") if u.startswith("http")]
        if not urls:
            return None
        return normalize_name(name), urls
    except Exception:
        return None

def validate_url(url: str) -> bool:
    """基本格式校验"""
    return url.startswith("http")

def test_url_playable(url: str, proxy: Optional[str]=None, timeout: int=8) -> float:
    """只测试是否能快速连接并下载部分内容，返回速度，失败则为0"""
    try:
        proxies = {"http": proxy, "https": proxy} if proxy else None
        headers = {"User-Agent": "Mozilla/5.0"}
        # m3u8特殊处理:拿到第一个ts分片
        if url.endswith(".m3u8"):
            resp = requests.get(url, timeout=timeout, proxies=proxies, headers=headers)
            if resp.status_code != 200: return 0.0
            ts_lines = [l for l in resp.text.split('\n') if l and not l.startswith("#")]
            if not ts_lines: return 0.0
            ts_url = urljoin(url, ts_lines[0]) if not ts_lines[0].startswith("http") else ts_lines[0]
            url = ts_url
        # 测试下载部分内容
        t0 = time.time()
        r = requests.get(url, timeout=timeout, stream=True, proxies=proxies, headers=headers)
        r.raise_for_status()
        size = 0
        for chunk in r.iter_content(8192):
            size += len(chunk)
            if size > 256*1024 or time.time() - t0 > timeout:  # 256KB+即认为可用
                break
        elapsed = time.time() - t0
        return round(size/elapsed/1024/1024, 2) if elapsed > 0 else 0.0
    except Exception:
        return 0.0

def sort_cctv(channels: List[ChannelSource]) -> List[ChannelSource]:
    """央视频道排序"""
    def cctv_order(name):
        m = re.match(r"^CCTV(\d{1,2})$", name)
        if m: return int(m.group(1))
        return 99
    # CCTV1-17先, 其余后
    return sorted(channels, key=lambda x: (cctv_order(x.name), x.name, -x.speed), reverse=False)

# --------- 主处理类 ---------
class IPTVProcessor:
    def __init__(self, top: int = 10, proxy: Optional[str]=None):
        self.top = top
        self.proxy = proxy
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})
        socket.setdefaulttimeout(15)
        self.download_dir = Path("mobileunicast/downloads")
        self.output_dir = Path("output")
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def download_sources(self) -> List[str]:
        """下载所有源内容，返回所有文本内容字符串"""
        def fetch(url):
            try:
                proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
                resp = self.session.get(url, timeout=25, proxies=proxies)
                if resp.ok:
                    print(f"✓ 下载 {url}")
                    return resp.text
                print(f"✗ 下载失败 {url}")
                return ""
            except Exception:
                print(f"✗ 下载异常 {url}")
                return ""
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(fetch, DATA_SOURCES))
        # 合并所有文本
        return [x for x in results if x.strip()]

    def parse_channels(self, contents: List[str]) -> List[ChannelSource]:
        """解析所有文本内容获得频道源"""
        all_channels = []
        for content in contents:
            for line in content.splitlines():
                parsed = parse_line(line)
                if not parsed: continue
                name, urls = parsed
                for url in urls:
                    if validate_url(url):
                        all_channels.append(ChannelSource(name=name, url=url))
        return all_channels

    def deduplicate(self, channels: List[ChannelSource]) -> List[ChannelSource]:
        """频道名+url完全去重"""
        seen = set()
        result = []
        for c in channels:
            key = (c.name, c.url)
            if key not in seen:
                seen.add(key)
                result.append(c)
        return result

    def filter_playable(self, channels: List[ChannelSource]) -> List[ChannelSource]:
        """多线程测试源可播放性和速度，只保留可播放的"""
        print(f"开始频道测速（仅保留可播放的源）...")
        tested = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            future2ch = {pool.submit(test_url_playable, c.url, self.proxy): c for c in channels}
            i = 0
            for fut in as_completed(future2ch):
                ch = future2ch[fut]
                try:
                    speed = fut.result(timeout=15)
                except Exception:
                    speed = 0.0
                ch.status = speed > 0
                ch.speed = speed
                if ch.status:
                    tested.append(ch)
                i += 1
                if i % 10 == 0 or i == len(future2ch):
                    print(f"  [{i}/{len(future2ch)}] 已检测...")
        print(f"测速完成，保留 {len(tested)} 个可播放频道源")
        return tested

    def pick_fastest(self, channels: List[ChannelSource]) -> Dict[str, List[ChannelSource]]:
        """每个频道只保留速度最快的N个源，并分组"""
        grouped: Dict[str, List[ChannelSource]] = {}
        # 先按频道名分组
        name2channels: Dict[str, List[ChannelSource]] = {}
        for c in channels:
            name2channels.setdefault(c.name, []).append(c)
        # 对每组选速度最快的N个
        for name, group in name2channels.items():
            fastest = sorted(group, key=lambda x: x.speed, reverse=True)[:self.top]
            grouped[name] = fastest
        return grouped

    def group_channels(self, name2channels: Dict[str, List[ChannelSource]]) -> Dict[str, List[ChannelSource]]:
        """分组"""
        result: Dict[str, List[ChannelSource]] = {g: [] for g in list(GROUPS.keys()) + [GROUP_OTHER]}
        for name, chs in name2channels.items():
            group = classify_channel(name)
            result[group].extend(chs)
        # CCTV排序
        if "央视频道" in result:
            result["央视频道"] = sort_cctv(result["央视频道"])
        return result

    def save_outputs(self, grouped: Dict[str, List[ChannelSource]]) -> None:
        """输出 m3u 和 txt 文件"""
        m3u_path = self.output_dir / "iptv.m3u"
        txt_path = self.output_dir / "iptv.txt"
        # M3U
        with open(m3u_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for group, chs in grouped.items():
                for ch in chs:
                    f.write(f'#EXTINF:-1 group-title="{group}",{ch.name}\n{ch.url}\n')
        # TXT
        with open(txt_path, "w", encoding="utf-8") as f:
            for group, chs in grouped.items():
                if not chs: continue
                f.write(f"{group},#genre#\n")
                for ch in chs:
                    f.write(f"{ch.name},{ch.url}\n")
                f.write("\n")
        print(f"已生成: {m3u_path} (M3U格式)")
        print(f"已生成: {txt_path} (TXT格式)")

    def run(self):
        print("=== IPTV直播源处理工具（可播放优化版） ===")
        print(f"数据源数量：{len(DATA_SOURCES)}，每频道保留最快{self.top}个源")
        if self.proxy:
            print(f"使用代理: {self.proxy}")
        # 1. 下载
        contents = self.download_sources()
        if not contents:
            print("未能获取任何源内容，退出。")
            return
        # 2. 解析
        raw_channels = self.parse_channels(contents)
        print(f"解析获得原始频道源: {len(raw_channels)}")
        # 3. 去重
        channels = self.deduplicate(raw_channels)
        print(f"去重后频道源: {len(channels)}")
        # 4. 可用性检测+测速
        playable_channels = self.filter_playable(channels)
        # 5. 每频道只保留最快的N个
        name2channels = self.pick_fastest(playable_channels)
        # 6. 分组
        grouped = self.group_channels(name2channels)
        # 7. 输出
        self.save_outputs(grouped)
        print("=== 处理完成 ===")

# --------- 命令行入口 ---------
def main():
    parser = argparse.ArgumentParser(description="IPTV直播源处理工具（可播放优化版）")
    parser.add_argument("--top", type=int, default=10, help="每频道最多保留最快N个源")
    parser.add_argument("--proxy", type=str, help="全局HTTP代理（如 http://127.0.0.1:7890 ）")
    args = parser.parse_args()
    if args.top < 1:
        print("错误：--top 必须为正整数")
        sys.exit(1)
    IPTVProcessor(top=args.top, proxy=args.proxy).run()

if __name__ == "__main__":
    main()
