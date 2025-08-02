#!/usr/bin/env python3
"""
IPTV直播源处理工具
功能：从指定URL下载直播源，合并去重、测速、分组并生成标准M3U文件
"""

import os
import re
import sys
import time
import requests
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from pathlib import Path
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class ChannelInfo:
    """频道信息数据类"""
    name: str
    url: str
    speed: float = 0.0  # 单位：MB/s
    source: str = ""    # 来源URL


class ChannelGroup:
    """频道分组常量类"""
    CCTV = "央视频道"
    WEI_SHI = "卫视频道"
    LOCAL = "省级频道"
    HKMOTW = "港澳台频道"
    CITY = "市级频道"
    MOVIE = "电影频道"
    TV_SERIES = "电视剧频道"
    SPORT = "体育频道"
    OTHER = "其它频道"


class IPTVProcessor:
    """IPTV直播源处理器"""
    
    # 数据源URL列表（增加备用源，提高可用性）
    SOURCE_URLS = [
        "https://live.zbds.org/tv/yd.txt",
        "https://raw.githubusercontent.com/xisohi/CHINA-IPTV/main/Unicast/anhui/mobile.txt",
        "https://raw.githubusercontent.com/xisohi/CHINA-IPTV/main/Unicast/zhejiang/mobile.txt",
        "https://mycode.zhoujie218.top/me/jsyd.txt",
        "https://live.zbds.org/tv/zjyd.txt",
        "https://live.zbds.org/tv/zjyd1.txt",
        "https://live.zbds.org/tv/jxyd.txt"
    ]
    
    # 分组匹配关键字（优化正则表达式）
    _group_patterns = {
        ChannelGroup.CCTV: re.compile(r'^(CCTV|央视|中央|cctv)'),
        ChannelGroup.WEI_SHI: re.compile(r'卫视$'),
        ChannelGroup.HKMOTW: re.compile(r'香港|台湾|澳门|TVB|凤凰|翡翠|星空|华视|中天'),
        ChannelGroup.LOCAL: re.compile(r'北京|上海|广东|江苏|浙江|山东|河南|河北|四川|湖南|湖北|福建|安徽|江西|山西|陕西|甘肃|青海|辽宁|吉林|黑龙江|内蒙古|宁夏|新疆|西藏|云南|贵州|广西|海南'),
        ChannelGroup.CITY: re.compile(r'石家庄|唐山|广州|深圳|杭州|南京|成都|武汉|重庆|西安|沈阳|哈尔滨|济南|青岛|大连|苏州|无锡|厦门|长沙|郑州|合肥|福州'),
        ChannelGroup.MOVIE: re.compile(r'电影|影院|影视|院线'),
        ChannelGroup.TV_SERIES: re.compile(r'剧集|电视剧|卫视剧场|影视剧场'),
        ChannelGroup.SPORT: re.compile(r'体育|NBA|足球|篮球|赛事|奥运')
    }

    def __init__(self, top_count=10, output_dir: str = "output"):
        self.top_count = top_count if top_count > 0 else 10
        self.work_dir = Path(os.getcwd())
        self.output_dir = self.work_dir / output_dir
        
        # 确保输出目录存在
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"输出目录设置为: {self.output_dir.absolute()}")
        except Exception as e:
            logger.error(f"创建输出目录失败: {str(e)}")
            sys.exit(1)

    def _download_source(self, url: str) -> Tuple[str, str]:
        """
        下载单个源文件内容
        返回: (源URL, 内容)
        """
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': '*/*',
                'Connection': 'keep-alive'
            }
            
            logger.debug(f"开始下载: {url}")
            response = requests.get(url, timeout=20, headers=headers, allow_redirects=True)
            response.raise_for_status()
            
            # 处理编码问题
            if response.encoding is None:
                response.encoding = 'utf-8'
                
            return (url, response.text)
        except requests.exceptions.Timeout:
            logger.warning(f"下载超时: {url}")
        except requests.exceptions.HTTPError as e:
            logger.warning(f"HTTP错误 {url}: {str(e)}")
        except Exception as e:
            logger.warning(f"下载失败 {url}: {str(e)}")
            
        return (url, "")

    def download_all_sources(self) -> List[Tuple[str, str]]:
        """并发下载所有源文件内容"""
        logger.info("开始下载直播源文件...")
        valid_contents = []
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(self._download_source, url): url for url in self.SOURCE_URLS}
            
            for future in as_completed(futures):
                url = futures[future]
                try:
                    source_url, content = future.result()
                    if content and len(content.strip()) > 0:
                        valid_contents.append((source_url, content))
                        logger.info(f"成功下载: {url.split('/')[-1]} (大小: {len(content)//1024}KB)")
                    else:
                        logger.warning(f"源文件内容为空: {url}")
                except Exception as e:
                    logger.error(f"处理源文件失败 {url}: {str(e)}")
        
        logger.info(f"下载完成，共获取 {len(valid_contents)} 个有效源文件")
        return valid_contents

    def parse_channels(self, sources: List[Tuple[str, str]]) -> Dict[str, List[ChannelInfo]]:
        """解析源文件内容为频道字典（去重处理）"""
        logger.info("开始解析频道信息...")
        channel_dict: Dict[str, List[ChannelInfo]] = {}
        url_set = set()  # 用于URL去重
        
        for source_url, content in sources:
            lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith('#')]
            logger.debug(f"解析源文件: {source_url} (行数: {len(lines)})")
            
            for line_num, line in enumerate(lines, 1):
                try:
                    if ',' not in line:
                        continue  # 跳过没有逗号分隔的行
                        
                    # 支持两种格式："频道名,url" 或 "url,频道名"
                    parts = line.split(',', 1)
                    if len(parts) != 2:
                        continue
                        
                    part1, part2 = parts[0].strip(), parts[1].strip()
                    
                    # 判断哪个是URL
                    if part1.startswith(('http://', 'https://', 'rtsp://')):
                        url, name = part1, part2
                    elif part2.startswith(('http://', 'https://', 'rtsp://')):
                        name, url = part1, part2
                    else:
                        continue  # 都不是URL，跳过
                        
                    # 基本验证
                    if not name or not url:
                        continue
                        
                    # URL去重
                    if url in url_set:
                        continue
                    url_set.add(url)
                    
                    # 标准化频道名称
                    name = re.sub(r'\s+', ' ', name)  # 合并空格
                    
                    # 添加到字典
                    if name not in channel_dict:
                        channel_dict[name] = []
                    channel_dict[name].append(ChannelInfo(
                        name=name,
                        url=url,
                        source=source_url
                    ))
                    
                except Exception as e:
                    logger.warning(f"解析行失败 (源: {source_url}, 行号: {line_num}): {str(e)}")
                    continue
        
        logger.info(f"解析完成，共发现 {len(channel_dict)} 个独特频道")
        return channel_dict

    def _test_speed(self, channel: ChannelInfo) -> ChannelInfo:
        """测试单个频道的速度"""
        try:
            start_time = time.time()
            timeout = 10  # 超时时间（秒）
            max_download = 4 * 1024 * 1024  # 最大下载4MB
            
            # 处理M3U8格式，获取实际TS片段
            test_url = channel.url
            if '.m3u8' in channel.url.lower():
                try:
                    resp = requests.get(channel.url, timeout=5)
                    if resp.status_code == 200:
                        for line in resp.text.splitlines():
                            line = line.strip()
                            if line and not line.startswith('#'):
                                # 处理相对路径
                                if line.startswith(('http://', 'https://')):
                                    test_url = line
                                else:
                                    base_url = channel.url.rsplit('/', 1)[0] if '/' in channel.url else channel.url
                                    test_url = f"{base_url}/{line}"
                                break
                except Exception:
                    pass  # 使用原始URL继续测试
            
            # 开始测速下载
            downloaded = 0
            with requests.get(test_url, stream=True, timeout=timeout, allow_redirects=True) as r:
                r.raise_for_status()
                for chunk in r.iter_content(chunk_size=1024*1024):  # 1MB块
                    if chunk:
                        downloaded += len(chunk)
                    # 达到最大下载量或超时则停止
                    if downloaded >= max_download or (time.time() - start_time) > timeout:
                        break
            
            duration = time.time() - start_time
            if duration > 0 and downloaded > 0:
                channel.speed = round((downloaded / (1024 * 1024)) / duration, 3)  # MB/s
                logger.debug(f"测速完成: {channel.name} - {channel.speed} MB/s")
            else:
                logger.debug(f"测速失败: {channel.name} (下载量: {downloaded}B, 耗时: {duration:.2f}s)")
                
        except Exception as e:
            logger.debug(f"测速错误 {channel.name}: {str(e)}")
            
        return channel

    def test_channel_speeds(self, channel_dict: Dict[str, List[ChannelInfo]]) -> Dict[str, List[ChannelInfo]]:
        """批量测试频道速度"""
        logger.info("开始测试频道速度...")
        result_dict: Dict[str, List[ChannelInfo]] = {}
        
        # 收集所有需要测试的频道
        all_channels = []
        for channels in channel_dict.values():
            all_channels.extend(channels)
            
        logger.info(f"准备测试 {len(all_channels)} 个频道源")
        
        # 并发测速
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(self._test_speed, channel) for channel in all_channels]
            
            for future in as_completed(futures):
                channel = future.result()
                if channel.speed > 0.01:  # 只保留速度大于0.01 MB/s的有效频道
                    if channel.name not in result_dict:
                        result_dict[channel.name] = []
                    result_dict[channel.name].append(channel)
        
        # 按速度排序并保留前N个
        for name in result_dict:
            # 先按速度降序，再按URL长度升序（优先选择短链接）
            result_dict[name].sort(key=lambda x: (-x.speed, len(x.url)))
            result_dict[name] = result_dict[name][:self.top_count]
        
        # 统计有效频道数量
        total_sources = sum(len(channels) for channels in result_dict.values())
        logger.info(f"测速完成，有效频道: {len(result_dict)} 个，有效源: {total_sources} 个")
        return result_dict

    def classify_channel(self, name: str) -> str:
        """对频道进行分类"""
        name_lower = name.lower()
        for group, pattern in self._group_patterns.items():
            if pattern.search(name) or pattern.search(name_lower):
                return group
        return ChannelGroup.OTHER

    def generate_output_files(self, channel_dict: Dict[str, List[ChannelInfo]]) -> None:
        """生成M3U文件和TXT文件"""
        # 定义输出文件路径
        m3u_path = self.output_dir / "iptv_live.m3u"
        txt_path = self.output_dir / "iptv_live.txt"
        speed_path = self.output_dir / "iptv_speed.txt"
        
        group_counts: Dict[str, int] = {}
        
        # 生成M3U文件
        try:
            with open(m3u_path, 'w', encoding='utf-8') as f:
                # M3U头部，包含EPG信息
                f.write("#EXTM3U x-tvg-url=\"https://epg.51zmt.top:8000/e.xml\"\n")
                
                # 按频道名排序输出
                for name in sorted(channel_dict.keys()):
                    channels = channel_dict[name]
                    group = self.classify_channel(name)
                    group_counts[group] = group_counts.get(group, 0) + len(channels)
                    
                    for channel in channels:
                        f.write(f'#EXTINF:-1 group-title="{group}",{name} ({"{:.2f}".format(channel.speed)}MB/s)\n')
                        f.write(f'{channel.url}\n')
            
            logger.info(f"成功生成M3U文件: {m3u_path.absolute()}")
        except Exception as e:
            logger.error(f"生成M3U文件失败: {str(e)}")
            return

        # 生成TXT文件
        try:
            with open(txt_path, 'w', encoding='utf-8') as f:
                # 按分组输出
                groups = {}
                for name in sorted(channel_dict.keys()):
                    channels = channel_dict[name]
                    group = self.classify_channel(name)
                    if group not in groups:
                        groups[group] = []
                    groups[group].extend([(name, channel.url) for channel in channels])
                
                # 按分组顺序写入
                for group in sorted(groups.keys()):
                    f.write(f"{group},#genre#\n")
                    for name, url in groups[group]:
                        f.write(f"{name},{url}\n")
                    f.write("\n")  # 分组间空行
            
            logger.info(f"成功生成TXT文件: {txt_path.absolute()}")
        except Exception as e:
            logger.error(f"生成TXT文件失败: {str(e)}")

        # 生成速度信息文件
        try:
            with open(speed_path, 'w', encoding='utf-8') as f:
                f.write("频道名称,URL,速度(MB/s),来源\n")
                for name in sorted(channel_dict.keys()):
                    for channel in channel_dict[name]:
                        source = channel.source.split('/')[-1] if channel.source else "未知"
                        f.write(f"{name},{channel.url},{channel.speed},{source}\n")
            
            logger.info(f"成功生成速度信息文件: {speed_path.absolute()}")
        except Exception as e:
            logger.error(f"生成速度信息文件失败: {str(e)}")

        # 输出分组统计
        logger.info("\n频道分组统计:")
        for group in sorted(group_counts.keys()):
            logger.info(f"  {group}: {group_counts[group]}个源")
        logger.info(f"  总计: {sum(group_counts.values())}个有效源")

    def run(self):
        """执行完整处理流程"""
        start_time = time.time()
        logger.info("=== IPTV直播源处理工具启动 ===")
        
        # 1. 下载源文件
        sources = self.download_all_sources()
        if not sources:
            logger.error("没有获取到任何有效源文件，程序退出")
            return
        
        # 2. 解析频道
        raw_channels = self.parse_channels(sources)
        if not raw_channels:
            logger.error("没有解析到任何有效频道，程序退出")
            return
        
        # 3. 测速
        valid_channels = self.test_channel_speeds(raw_channels)
        if not valid_channels:
            logger.error("没有通过测速的有效频道，程序退出")
            return
        
        # 4. 生成输出文件
        self.generate_output_files(valid_channels)
        
        end_time = time.time()
        logger.info(f"\n=== 处理完成 (总耗时: {end_time - start_time:.2f}秒) ===")
        logger.info(f"所有文件已保存至: {self.output_dir.absolute()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IPTV直播源处理工具")
    parser.add_argument("--top", type=int, default=5, help="每个频道保留的最大源数量（默认5）")
    parser.add_argument("--output", type=str, default="output", help="输出目录（默认output）")
    parser.add_argument("--debug", action="store_true", help="显示调试信息")
    args = parser.parse_args()
    
    # 调整日志级别
    if args.debug:
        logger.setLevel(logging.DEBUG)
    
    processor = IPTVProcessor(
        top_count=args.top,
        output_dir=args.output
    )
    processor.run()
