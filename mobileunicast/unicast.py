#!/usr/bin/env python3
"""
IPTV直播源处理工具（重构版）
功能：
1. 从多源URL下载直播源文件（支持代理）
2. 解析并合并所有频道（自动去重）
3. 多线程对流媒体地址进行测速
4. 按速度排序，保留每个频道前N个最快源
5. 按频道类型智能分组
6. 生成标准M3U和TXT格式播放列表

使用说明：
- 基础用法：python unicast.py --top 20
- 带代理：python unicast.py --top 10 --proxy http://127.0.0.1:10808
"""

import os
import re
import sys
import time
import socket
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urljoin


@dataclass
class ChannelInfo:
    """频道信息数据类，存储频道名称、URL和测速结果"""
    name: str          # 频道名称（标准化处理后）
    url: str           # 流媒体地址
    speed: float = 0.0 # 测速结果（MB/s，0表示无效）


class ChannelGroup:
    """频道分组常量类，定义所有可能的频道分组"""
    CCTV = "央视频道"
    WEI_SHI = "卫视频道"
    LOCAL = "省级频道"
    HKMOTW = "港澳台频道"
    CITY = "市级频道"
    OTHER = "其它频道"


class UnicastProcessor:
    """IPTV直播源处理器核心类，封装所有处理逻辑"""
    
    # 数据源URL列表（覆盖全国多省份移动网络源）
    DATA_SOURCES = [
        # 基础源
        "https://live.zbds.org/tv/yd.txt",
        "https://live.zbds.org/tv/iptv6.txt",
        # 省级移动源
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
        # 第三方优质源
        "https://mycode.zhoujie218.top/me/jsyd.txt",
        "https://raw.githubusercontent.com/q1017673817/iptv_zubo/refs/heads/main/hnyd.txt",
        "https://raw.githubusercontent.com/suxuang/myIPTV/refs/heads/main/%E7%A7%BB%E5%8A%A8%E4%B8%93%E4%BA%AB.txt",
        # 补充源
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
        "https://vdyun.com/shxcm.txt",
        "https://vdyun.com/shxcm1.txt"
    ]
    
    # 分组关键字配置（用于频道分类）
    _LOCAL_KEYWORDS = (
        "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
        "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
        "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙",
        "广西", "西藏", "宁夏", "新疆", "东南", "东方"
    )
    
    _HKMOTW_KEYWORDS = (
        "凤凰", "香港", "TVB", "tvb", "RTHK", "港台", "翡翠", "面包", "人间",
        "唯心", "星空", "无线", "无线电视", "无线新闻", "无线娱乐", "大爱",
        "番薯", "亚洲", "华视", "中天", "中视", "民视", "东森", "三立", "台视",
        "公视", "台湾", "澳门", "澳视", "澳亚", "澳广"
    )
    
    _WEISHI_KEYWORDS = ("卫视",)
    
    _CITY_KEYWORDS = (
        "石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德",
        "太原", "大同", "阳泉", "长治", "晋城", "沈阳", "大连", "鞍山", "抚顺",
        "长春", "吉林", "四平", "哈尔滨", "齐齐哈尔", "南京", "无锡", "徐州",
        "杭州", "宁波", "温州", "合肥", "福州", "厦门", "南昌", "济南", "青岛",
        "郑州", "武汉", "长沙", "广州", "深圳", "南宁", "海口", "成都", "贵阳",
        "昆明", "拉萨", "西安", "兰州", "西宁", "银川", "乌鲁木齐"
    )

    def __init__(self, top_count: int = 20, proxy: Optional[str] = None):
        """初始化处理器
        
        Args:
            top_count: 每个频道保留的最大源数量
            proxy: 下载时使用的代理地址（格式：http://host:port）
        """
        self.top_count = top_count
        self.proxy = proxy
        
        # 路径配置
        self.download_dir = Path("downloads")  # 下载文件存储目录
        self.output_dir = Path("output")       # 输出文件目录
        self.temp_file = Path("txt.tmp")       # 汇总临时文件
        self.speed_log = Path("speed.log")     # 测速日志文件
        
        # 创建必要目录
        self._init_directories()

    def _init_directories(self) -> None:
        """初始化必要的目录（若不存在则创建）"""
        self.download_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)

    def run(self) -> None:
        """执行完整处理流程：下载 → 解析 → 去重 → 测速 → 分组 → 生成文件"""
        print("=== IPTV直播源处理工具 ===")
        
        # 1. 下载源文件
        downloaded_files = self._download_source_files()
        if not downloaded_files:
            print("没有可用的源文件，程序退出")
            return
        
        # 2. 解析文件提取频道
        all_channels = self._parse_source_files(downloaded_files)
        if not all_channels:
            print("没有解析到有效频道，程序退出")
            return
        
        # 3. 去重处理
        unique_channels = self._remove_duplicates(all_channels)
        print(f"去重后剩余 {len(unique_channels)} 个频道")
        
        # 4. 测速处理
        tested_channels = self._test_channels_speed(unique_channels)
        valid_channels = [c for c in tested_channels if c.speed > 0]
        print(f"测速完成，有效频道: {len(valid_channels)}")
        
        # 5. 按频道分组并保留前N个最快源
        grouped_channels = self._group_and_filter_channels(valid_channels)
        
        # 6. 生成输出文件
        self._generate_output_files(grouped_channels)
        print("=== 处理完成 ===")
        print(f"输出文件:")
        print(f"  M3U格式: {self.output_dir / 'iptv.m3u'}")
        print(f"  TXT格式: {self.output_dir / 'iptv.txt'}")

    def _download_source_files(self) -> List[Path]:
        """下载所有数据源文件（多线程）
        
        Returns:
            成功下载的文件路径列表
        """
        print("开始下载直播源文件...")
        if self.proxy:
            print(f"使用代理: {self.proxy}")
        
        # 多线程下载（最大10线程）
        downloaded_files = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(self._download_single_file, url)
                for url in self.DATA_SOURCES
            ]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    downloaded_files.append(result)
        
        print(f"下载完成，共获得 {len(downloaded_files)} 个文件")
        return downloaded_files

    def _download_single_file(self, url: str) -> Optional[Path]:
        """下载单个源文件
        
        Args:
            url: 源文件URL
        
        Returns:
            下载后的文件路径（失败返回None）
        """
        try:
            # 生成唯一文件名
            filename = self._generate_unique_filename(url)
            filepath = self.download_dir / filename
            
            # 配置代理
            proxies = {}
            if self.proxy:
                proxies = {"http": self.proxy, "https": self.proxy}
            
            # 发送请求（模拟浏览器UA）
            response = requests.get(
                url,
                timeout=30,
                proxies=proxies,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            response.raise_for_status()  # 抛出HTTP错误
            
            # 保存文件
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(response.text)
            
            print(f"✓ 下载成功: {filename}")
            return filepath
        
        except Exception as e:
            print(f"✗ 下载失败 {url}: {str(e)[:50]}")  # 截断长错误信息
            return None

    def _generate_unique_filename(self, url: str) -> str:
        """根据URL生成唯一文件名（避免重复）
        
        Args:
            url: 源文件URL
        
        Returns:
            唯一文件名（含.txt扩展名）
        """
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        
        # 提取原始文件名
        original_filename = path_parts[-1] if path_parts else "unknown.txt"
        if not original_filename.endswith(".txt"):
            original_filename += ".txt"
        
        # 生成前缀（域名+路径片段）
        domain = parsed.netloc.replace(".", "_")
        prefix = path_parts[-2] if len(path_parts) > 1 else domain.split("_")[0]
        
        # 组合唯一文件名
        name_without_ext = original_filename.rsplit(".", 1)[0]
        return f"{prefix}_{name_without_ext}.txt"

    def _parse_source_files(self, filepaths: List[Path]) -> List[ChannelInfo]:
        """解析所有下载的源文件，提取频道信息
        
        Args:
            filepaths: 源文件路径列表
        
        Returns:
            提取的频道信息列表
        """
        print("解析直播源文件...")
        all_channels = []
        all_content = []  # 用于生成汇总临时文件
        
        for filepath in filepaths:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # 收集内容用于汇总
                all_content.append(f"# 来源文件: {filepath.name}\n{content}\n")
                
                # 解析单文件内容
                channels = self._parse_single_file(content)
                all_channels.extend(channels)
                print(f"✓ 解析 {filepath.name}: 获得 {len(channels)} 个频道")
            
            except Exception as e:
                print(f"✗ 解析失败 {filepath}: {str(e)}")
        
        # 生成汇总临时文件
        self._save_merged_temp_file(all_content)
        print(f"总共解析出 {len(all_channels)} 个频道")
        return all_channels

    def _parse_single_file(self, content: str) -> List[ChannelInfo]:
        """解析单个文件内容，提取频道信息
        
        Args:
            content: 文件内容字符串
        
        Returns:
            频道信息列表
        """
        channels = []
        lines = [line.strip() for line in content.split("\n") if line.strip()]
        
        for line in lines:
            # 跳过分组标记行（如"央视,#genre#"）
            if line.endswith("#genre#"):
                continue
            
            # 解析格式："频道名,url1#url2#..."
            if "," in line:
                name_part, url_part = line.split(",", 1)
                name = self._normalize_channel_name(name_part.strip())
                urls = [u.strip() for u in url_part.split("#") if u.strip().startswith("http")]
                
                # 为每个有效URL创建频道
                for url in urls:
                    channels.append(ChannelInfo(name=name, url=url))
        
        return channels

    def _normalize_channel_name(self, name: str) -> str:
        """标准化频道名称（统一格式）
        
        Args:
            name: 原始频道名称
        
        Returns:
            标准化后的名称
        """
        # 统一CCTV格式（如CCTV-1 → CCTV1）
        name = re.sub(r"CCTV-(\d+)", r"CCTV\1", name, flags=re.IGNORECASE)
        # 统一CGTN格式（如CGTN-英语 → CGTN英语）
        name = re.sub(r"CGTN-(\w+)", r"CGTN\1", name, flags=re.IGNORECASE)
        # 清理CCTV频道中的特殊符号（保留CCTV5+的+号）
        if re.match(r"CCTV", name, re.IGNORECASE) and not re.match(r"CCTV5\+", name, re.IGNORECASE):
            name = re.sub(r"[+\-\s*]", "", name)
        
        return name

    def _save_merged_temp_file(self, all_content: List[str]) -> None:
        """保存汇总的临时文件（所有源文件内容合并）
        
        Args:
            all_content: 所有源文件内容列表
        """
        try:
            with open(self.temp_file, "w", encoding="utf-8") as f:
                f.write("# IPTV直播源汇总临时文件\n")
                f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.writelines(all_content)
            print(f"✓ 汇总临时文件已生成: {self.temp_file}")
        except Exception as e:
            print(f"✗ 生成汇总临时文件失败: {str(e)}")

    def _remove_duplicates(self, channels: List[ChannelInfo]) -> List[ChannelInfo]:
        """移除重复的频道（同一名称+同一URL视为重复）
        
        Args:
            channels: 原始频道列表
        
        Returns:
            去重后的频道列表
        """
        seen = set()
        unique = []
        for channel in channels:
            # 使用名称+URL作为唯一标识
            key = (channel.name, channel.url)
            if key not in seen:
                seen.add(key)
                unique.append(channel)
        return unique

    def _test_channels_speed(self, channels: List[ChannelInfo]) -> List[ChannelInfo]:
        """多线程测试所有频道的速度
        
        Args:
            channels: 待测试的频道列表
        
        Returns:
            测试完成的频道列表（含速度信息）
        """
        print(f"开始测速 {len(channels)} 个频道...")
        tested_channels = []
        
        # 多线程测速（最大10线程，避免网络拥堵）
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(self._test_single_channel_speed, channel)
                for channel in channels
            ]
            
            # 跟踪进度
            for i, future in enumerate(as_completed(futures), 1):
                channel = future.result()
                tested_channels.append(channel)
                # 打印进度（每10个输出一次，避免刷屏）
                if i % 10 == 0 or i == len(channels):
                    print(f"[{i}/{len(channels)}] 测速中...")
        
        # 保存测速日志
        self._save_speed_log(tested_channels)
        return tested_channels

    def _test_single_channel_speed(self, channel: ChannelInfo) -> ChannelInfo:
        """测试单个频道的流媒体速度
        
        Args:
            channel: 待测试的频道
        
        Returns:
            包含测速结果的频道信息（速度为0表示测试失败）
        """
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            
            # 根据流类型选择测试方法
            if channel.url.endswith(".m3u8"):
                return self._test_m3u8_stream(session, channel)
            else:
                return self._test_direct_stream(session, channel)
        
        except Exception:
            channel.speed = 0.0
            return channel

    def _test_m3u8_stream(self, session: requests.Session, channel: ChannelInfo) -> ChannelInfo:
        """测试M3U8格式流的速度
        
        Args:
            session: 共享的requests会话
            channel: 待测试的频道
        
        Returns:
            包含测速结果的频道信息
        """
        try:
            # 1. 获取M3U8文件
            m3u8_resp = session.get(channel.url, timeout=5)
            m3u8_resp.raise_for_status()
            
            # 2. 提取TS分片URL
            ts_urls = self._extract_ts_urls(m3u8_resp.text, channel.url)
            if not ts_urls:
                channel.speed = 0.0
                return channel
            
            # 3. 测试第一个TS分片的速度（最多下载2MB或5秒）
            start_time = time.time()
            resp = session.get(ts_urls[0], stream=True, timeout=5)
            resp.raise_for_status()
            
            downloaded = 0
            target_size = 2 * 1024 * 1024  # 2MB
            min_size = 256 * 1024          # 最小有效数据量
            
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    downloaded += len(chunk)
                # 超时或达到目标大小则停止
                if (time.time() - start_time) > 5 or downloaded >= target_size:
                    break
            
            # 计算速度（MB/s）
            elapsed = time.time() - start_time
            if elapsed > 0 and downloaded >= min_size:
                channel.speed = round(downloaded / elapsed / 1024 / 1024, 2)
            else:
                channel.speed = 0.0
            
            return channel
        
        except Exception:
            channel.speed = 0.0
            return channel

    def _extract_ts_urls(self, m3u8_content: str, base_url: str) -> List[str]:
        """从M3U8内容中提取TS分片URL
        
        Args:
            m3u8_content: M3U8文件内容
            base_url: 基础URL（用于拼接相对路径）
        
        Returns:
            TS分片URL列表
        """
        ts_urls = []
        for line in m3u8_content.split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):  # 跳过注释行
                ts_url = urljoin(base_url, line) if not line.startswith("http") else line
                ts_urls.append(ts_url)
        return ts_urls

    def _test_direct_stream(self, session: requests.Session, channel: ChannelInfo) -> ChannelInfo:
        """测试直接流媒体（非M3U8）的速度
        
        Args:
            session: 共享的requests会话
            channel: 待测试的频道
        
        Returns:
            包含测速结果的频道信息
        """
        try:
            # 流式下载，最多2MB或5秒
            resp = session.get(channel.url, stream=True, timeout=8)
            resp.raise_for_status()
            
            downloaded = 0
            target_size = 2 * 1024 * 1024  # 2MB
            min_size = 256 * 1024          # 最小有效数据量
            start_time = time.time()
            
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    downloaded += len(chunk)
                # 超时或达到目标大小则停止
                if (time.time() - start_time) > 5 or downloaded >= target_size:
                    break
            
            # 计算速度（MB/s）
            elapsed = time.time() - start_time
            if elapsed > 0 and downloaded >= min_size:
                channel.speed = round(downloaded / elapsed / 1024 / 1024, 2)
            else:
                channel.speed = 0.0
            
            return channel
        
        except Exception:
            channel.speed = 0.0
            return channel

    def _save_speed_log(self, channels: List[ChannelInfo]) -> None:
        """保存测速日志到文件
        
        Args:
            channels: 已测试的频道列表
        """
        try:
            with open(self.speed_log, "w", encoding="utf-8") as f:
                f.write(f"# 测速日志 {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("频道名称,URL,速度(MB/s)\n")
                for channel in sorted(channels, key=lambda x: x.speed, reverse=True):
                    f.write(f"{channel.name},{channel.url},{channel.speed}\n")
            print(f"✓ 测速日志已保存: {self.speed_log}")
        except Exception as e:
            print(f"✗ 保存测速日志失败: {str(e)}")

    def _group_and_filter_channels(self, channels: List[ChannelInfo]) -> Dict[str, List[ChannelInfo]]:
        """按频道类型分组，并保留每个频道前N个最快源
        
        Args:
            channels: 已测速的有效频道列表
        
        Returns:
            分组后的频道字典（键：分组名称，值：频道列表）
        """
        print(f"为每个频道选择速度最快的前 {self.top_count} 个URL源...")
        
        # 1. 按频道名称分组
        name_groups: Dict[str, List[ChannelInfo]] = {}
        for channel in channels:
            if channel.name not in name_groups:
                name_groups[channel.name] = []
            name_groups[channel.name].append(channel)
        
        # 2. 每个频道保留前N个最快源
        filtered: List[ChannelInfo] = []
        for name, group in name_groups.items():
            # 按速度降序排序
            sorted_group = sorted(group, key=lambda x: x.speed, reverse=True)
            # 保留前N个
            keep_count = min(self.top_count, len(sorted_group))
            filtered.extend(sorted_group[:keep_count])
            print(f"  {name}: 从 {len(group)} 个源中保留前 {keep_count} 个")
        
        # 3. 按频道类型分组
        final_groups: Dict[str, List[ChannelInfo]] = {
            ChannelGroup.CCTV: [],
            ChannelGroup.WEI_SHI: [],
            ChannelGroup.LOCAL: [],
            ChannelGroup.HKMOTW: [],
            ChannelGroup.CITY: [],
            ChannelGroup.OTHER: []
        }
        
        for channel in filtered:
            group = self._determine_channel_group(channel.name)
            final_groups[group].append(channel)
        
        return final_groups

    def _determine_channel_group(self, name: str) -> str:
        """判断频道所属分组
        
        Args:
            name: 频道名称
        
        Returns:
            分组名称（ChannelGroup中的常量）
        """
        # 优先匹配更具体的分组（如央视 → 卫视频道的优先级低于央视）
        if re.match(r"^CCTV\d+", name, re.IGNORECASE) or name in ("CGTN", "CGTN中文", "CGTN英语"):
            return ChannelGroup.CCTV
        
        for keyword in self._HKMOTW_KEYWORDS:
            if keyword in name:
                return ChannelGroup.HKMOTW
        
        for keyword in self._WEISHI_KEYWORDS:
            if keyword in name:
                return ChannelGroup.WEI_SHI
        
        for keyword in self._CITY_KEYWORDS:
            if keyword in name:
                return ChannelGroup.CITY
        
        for keyword in self._LOCAL_KEYWORDS:
            if keyword in name:
                return ChannelGroup.LOCAL
        
        return ChannelGroup.OTHER

    def _generate_output_files(self, grouped_channels: Dict[str, List[ChannelInfo]]) -> None:
        """生成M3U和TXT格式的输出文件
        
        Args:
            grouped_channels: 分组后的频道字典
        """
        # 1. 生成M3U文件
        m3u_path = self.output_dir / "iptv.m3u"
        with open(m3u_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U x-tvg-url=\"https://live.fanmingming.com/e.xml\"\n")  # 附加EPG信息
            for group_name, channels in grouped_channels.items():
                # 按速度降序排序
                sorted_channels = sorted(channels, key=lambda x: x.speed, reverse=True)
                for channel in sorted_channels:
                    # 格式：#EXTINF:-1 tvg-name="名称" group-title="分组",名称
                    f.write(f'#EXTINF:-1 tvg-name="{channel.name}" group-title="{group_name}",{channel.name}\n')
                    f.write(f"{channel.url}\n")
        
        # 2. 生成TXT文件
        txt_path = self.output_dir / "iptv.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            for group_name, channels in grouped_channels.items():
                # 分组标记行
                f.write(f"{group_name},#genre#\n")
                # 按速度降序排序
                sorted_channels = sorted(channels, key=lambda x: x.speed, reverse=True)
                for channel in sorted_channels:
                    f.write(f"{channel.name},{channel.url}\n")
        
        # 打印分组统计
        print(f"生成M3U文件: {m3u_path}")
        print("M3U文件已生成，包含以下分组:")
        for group_name, channels in grouped_channels.items():
            print(f"  {group_name}: {len(channels)} 个频道源")
        print(f"生成TXT文件: {txt_path}")


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="IPTV直播源处理工具")
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help=f"每个频道保留的最大源数量（默认：20）"
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=None,
        help=f"下载源文件时使用的代理（格式：http://host:port）"
    )
    args = parser.parse_args()

    # 验证参数
    if args.top < 1:
        print("错误：--top 参数必须大于0")
        sys.exit(1)

    # 执行处理流程
    processor = UnicastProcessor(top_count=args.top, proxy=args.proxy)
    processor.run()


if __name__ == "__main__":
    main()
