#!/usr/bin/env python3
"""
IPTV直播源处理工具（GitHub Actions适配版）
功能：从指定URL下载直播源，合并去重、测速、分组并生成标准M3U文件
"""

import os
import re
import sys
import time
import socket
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ChannelInfo:
    """频道信息数据类"""
    name: str
    url: str
    speed: float = 0.0  # 单位：MB/s


class ChannelGroup:
    """频道分组常量类"""
    CCTV = "央视频道"
    WEI_SHI = "卫视频道"
    LOCAL = "省级频道"
    HKMOTW = "港澳台频道"
    CITY = "市级频道"
    OTHER = "其它频道"


class IPTVProcessor:
    """IPTV直播源处理器"""
    
    # 数据源URL列表（仅保留稳定可用源）
    SOURCE_URLS = [
        "https://live.zbds.org/tv/yd.txt",
        "https://raw.githubusercontent.com/xisohi/CHINA-IPTV/main/Unicast/anhui/mobile.txt",
        "https://raw.githubusercontent.com/xisohi/CHINA-IPTV/main/Unicast/zhejiang/mobile.txt",
        "https://mycode.zhoujie218.top/me/jsyd.txt",
        "https://live.zbds.org/tv/zjyd.txt"
    ]
    
    # 分组匹配关键字
    _group_patterns = {
        ChannelGroup.CCTV: re.compile(r'^CCTV|央视|中央'),
        ChannelGroup.WEI_SHI: re.compile(r'卫视$'),
        ChannelGroup.HKMOTW: re.compile(r'香港|台湾|澳门|TVB|凤凰|翡翠'),
        ChannelGroup.LOCAL: re.compile(r'北京|上海|广东|江苏|浙江|山东|河南|河北|四川|湖南|湖北|福建|安徽|江西|山西|陕西|甘肃|青海|辽宁|吉林|黑龙江|内蒙古|宁夏|新疆|西藏|云南|贵州|广西|海南'),
        ChannelGroup.CITY: re.compile(r'石家庄|唐山|广州|深圳|杭州|南京|成都|武汉|重庆|西安|沈阳|哈尔滨|济南|青岛|大连|苏州|无锡|厦门')
    }

    def __init__(self, top_count=10):
        self.top_count = top_count
        self.work_dir = Path(os.getenv('GITHUB_WORKSPACE', '.'))  # 适配GitHub Actions环境
        self.output_dir = self.work_dir / "dist"
        self.output_dir.mkdir(exist_ok=True)

    def _download_source(self, url: str) -> str:
        """下载单个源文件内容"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, timeout=15, headers=headers)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"⚠️ 下载失败 {url}: {str(e)}")
            return ""

    def download_all_sources(self) -> List[str]:
        """并发下载所有源文件内容"""
        print("📥 开始下载直播源...")
        contents = []
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(self._download_source, url): url for url in self.SOURCE_URLS}
            
            for future in as_completed(futures):
                url = futures[future]
                try:
                    content = future.result()
                    if content:
                        contents.append(content)
                        print(f"✅ 成功下载: {url.split('/')[-1]}")
                except Exception as e:
                    print(f"❌ 处理失败 {url}: {str(e)}")
        
        print(f"📊 下载完成，共获取 {len(contents)} 个有效源文件")
        return contents

    def parse_channels(self, contents: List[str]) -> Dict[str, List[ChannelInfo]]:
        """解析源文件内容为频道字典"""
        print("🔍 开始解析频道...")
        channel_dict: Dict[str, List[ChannelInfo]] = {}
        
        for content in contents:
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            for line in lines:
                # 支持两种格式："频道名,url" 或 "url,频道名"
                if ',' in line:
                    parts = line.split(',', 1)
                    if len(parts) == 2:
                        name, url = (parts[0], parts[1]) if parts[0].strip() else (parts[1], parts[0])
                        name = name.strip()
                        url = url.strip()
                        if name and url and (url.startswith('http') or url.startswith('rtsp')):
                            if name not in channel_dict:
                                channel_dict[name] = []
                            channel_dict[name].append(ChannelInfo(name=name, url=url))
        
        print(f"📋 解析完成，共发现 {len(channel_dict)} 个独特频道")
        return channel_dict

    def _test_speed(self, channel: ChannelInfo) -> ChannelInfo:
        """测试单个频道的速度"""
        try:
            start_time = time.time()
            timeout = 8  # 超时时间（秒）
            test_size = 2 * 1024 * 1024  # 测试下载2MB
            
            # 对于M3U8，找第一个TS分片
            if '.m3u8' in channel.url:
                resp = requests.get(channel.url, timeout=5)
                if resp.status_code == 200:
                    for line in resp.text.splitlines():
                        if line and not line.startswith('#'):
                            ts_url = line if line.startswith('http') else f"{channel.url.rsplit('/', 1)[0]}/{line}"
                            channel.url = ts_url
                            break
            
            # 测速下载
            with requests.get(channel.url, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                downloaded = 0
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        downloaded += len(chunk)
                        if downloaded >= test_size:
                            break
                        if time.time() - start_time > timeout:
                            break
            
            duration = time.time() - start_time
            if duration > 0 and downloaded > 0:
                channel.speed = (downloaded / (1024 * 1024)) / duration  # MB/s
            return channel
        except Exception:
            return channel

    def test_channel_speeds(self, channel_dict: Dict[str, List[ChannelInfo]]) -> Dict[str, List[ChannelInfo]]:
        """批量测试频道速度"""
        print("⚡ 开始测速...")
        result_dict: Dict[str, List[ChannelInfo]] = {}
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = []
            for name, channels in channel_dict.items():
                for channel in channels:
                    futures.append(executor.submit(self._test_speed, channel))
            
            for future in as_completed(futures):
                channel = future.result()
                if channel.speed > 0:  # 只保留有效速度的频道
                    if channel.name not in result_dict:
                        result_dict[channel.name] = []
                    result_dict[channel.name].append(channel)
        
        # 按速度排序并保留前N个
        for name in result_dict:
            result_dict[name].sort(key=lambda x: x.speed, reverse=True)
            result_dict[name] = result_dict[name][:self.top_count]
        
        print(f"🏁 测速完成，有效频道: {len(result_dict)}")
        return result_dict

    def classify_channel(self, name: str) -> str:
        """对频道进行分类"""
        for group, pattern in self._group_patterns.items():
            if pattern.search(name):
                return group
        return ChannelGroup.OTHER

    def generate_m3u(self, channel_dict: Dict[str, List[ChannelInfo]]) -> None:
        """生成标准M3U文件"""
        m3u_path = self.output_dir / "iptv_live.m3u"
        group_counts: Dict[str, int] = {}
        
        with open(m3u_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U x-tvg-url=\"https://epg.51zmt.top:8000/e.xml\"\n")  # 附加EPG信息
            
            for name, channels in sorted(channel_dict.items()):
                group = self.classify_channel(name)
                group_counts[group] = group_counts.get(group, 0) + len(channels)
                
                for channel in channels:
                    f.write(f'#EXTINF:-1 group-title="{group}",{name}\n')
                    f.write(f'{channel.url}\n')
        
        print("\n📄 生成M3U文件:")
        for group, count in sorted(group_counts.items()):
            print(f"  {group}: {count}个频道")
        print(f"  总计: {sum(group_counts.values())}个频道源")

    def run(self):
        """执行完整处理流程"""
        start_time = time.time()
        print("=== IPTV直播源处理工具 ===")
        
        contents = self.download_all_sources()
        if not contents:
            print("❌ 没有获取到任何源文件，程序退出")
            return
        
        raw_channels = self.parse_channels(contents)
        if not raw_channels:
            print("❌ 没有解析到任何频道，程序退出")
            return
        
        valid_channels = self.test_channel_speeds(raw_channels)
        if not valid_channels:
            print("❌ 没有有效频道通过测速，程序退出")
            return
        
        self.generate_m3u(valid_channels)
        
        end_time = time.time()
        print(f"\n=== 处理完成 (耗时: {end_time - start_time:.2f}秒) ===")
        print(f"输出目录: {self.output_dir.absolute()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IPTV直播源处理工具")
    parser.add_argument("--top", type=int, default=10, help="每个频道保留的最大源数量")
    args = parser.parse_args()
    
    processor = IPTVProcessor(top_count=args.top)
    processor.run()
