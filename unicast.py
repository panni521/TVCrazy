#!/usr/bin/env python3
"""
IPTV直播源下载、合并、测速与分组工具
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
from urllib.parse import urlparse, urljoin


@dataclass
class ChannelInfo:
    """频道信息"""
    name: str
    url: str
    speed: float = 0.0


class ChannelGroup:
    """频道分组枚举类"""
    CCTV = "央视频道"
    WEI_SHI = "卫视频道"
    LOCAL = "省级频道"
    HKMOTW = "港澳台频道"
    CITY = "市级频道"
    OTHER = "其它频道"


class UnicastProcessor:
    """IPTV直播源处理器"""
    
    # URL列表
    URLS = [
        "https://live.zbds.org/tv/yd.txt",
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
        "https://mycode.zhoujie218.top/me/jsyd.txt",
        "https://live.zbds.org/tv/zjyd.txt",
        "https://live.zbds.org/tv/zjyd1.txt",
        "https://live.zbds.org/tv/jxyd.txt",
        "https://live.zbds.org/tv/sxyd.txt",
        "https://live.zbds.org/tv/iptv4.txt"
    ]
    
    # 分组关键字
    locals = ("北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", 
              "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", 
              "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙", 
              "广西", "西藏", "宁夏", "新疆", "东南", "东方")
    
    hkmotw = ("凤凰", "香港", "TVB", "tvb", "星空", "无线", "无线电视", "无线新闻", "无线娱乐","大爱", "亚洲", "华视", "中天", "中视", "民视", "东森", "三立", "台视", "公视", "台湾","澳门", "澳视", "澳亚", "澳广")
    
    wei_shi = ("卫视",)
    
    citys = ("石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德", "沧州", "廊坊", "衡水",
"太原", "大同", "阳泉", "长治", "晋城", "朔州", "晋中", "运城", "忻州", "临汾", "吕梁",
"呼和浩特", "包头", "乌海", "赤峰", "通辽", "鄂尔多斯", "呼伦贝尔", "巴彦淖尔", "乌兰察布",
"沈阳", "大连", "鞍山", "抚顺", "本溪", "丹东", "锦州", "营口", "阜新", "辽阳", "盘锦", "铁岭", "朝阳", "葫芦岛",
"长春", "吉林", "四平", "辽源", "通化", "白山", "松原", "白城", "延边朝鲜族自治州",
"哈尔滨", "齐齐哈尔", "鸡西", "鹤岗", "双鸭山", "大庆", "伊春", "佳木斯", "七台河", "牡丹江", "黑河", "绥化", "大兴安岭地区",
"南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁",
"杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水",
"合肥", "芜湖", "蚌埠", "淮南", "马鞍山", "淮北", "铜陵", "安庆", "黄山", "滁州", "阜阳", "宿州", "六安", "亳州", "池州", "宣城",
"福州", "厦门", "莆田", "三明", "泉州", "漳州", "南平", "龙岩", "宁德",
"南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶",
"济南", "青岛", "淄博", "枣庄", "东营", "烟台", "潍坊", "济宁", "泰安", "威海", "日照", "临沂", "德州", "聊城", "滨州", "菏泽",
"郑州", "开封", "洛阳", "平顶山", "安阳", "鹤壁", "新乡", "焦作", "濮阳", "许昌", "漯河", "三门峡", "南阳", "商丘", "信阳", "周口", "驻马店",
"武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州", "荆门", "孝感", "荆州", "黄冈", "咸宁", "随州", "恩施土家族苗族自治州",
"长沙", "株洲", "湘潭", "衡阳", "邵阳", "岳阳", "常德", "张家界", "益阳", "郴州", "永州", "怀化", "娄底", "湘西土家族苗族自治州",
"广州", "韶关", "深圳", "珠海", "汕头", "佛山", "江门", "湛江", "茂名", "肇庆", "惠州", "梅州", "汕尾", "河源", "阳江", "清远", "东莞", "中山", "潮州", "揭阳", "云浮",
"南宁", "柳州", "桂林", "梧州", "北海", "防城港", "钦州", "贵港", "玉林", "百色", "贺州", "河池", "来宾", "崇左",
"海口", "三亚", "三沙", "儋州",
"重庆",
"成都", "自贡", "攀枝花", "泸州", "德阳", "绵阳", "广元", "遂宁", "内江", "乐山", "南充", "眉山", "宜宾", "广安", "达州", "雅安", "巴中", "资阳", "阿坝藏族羌族自治州", "甘孜藏族自治州", "凉山彝族自治州",
"贵阳", "六盘水", "遵义", "安顺", "毕节", "铜仁", "黔东南苗族侗族自治州", "黔南布依族苗族自治州", "黔西南布依族苗族自治州",
"昆明", "曲靖", "玉溪", "保山", "昭通", "丽江", "普洱", "临沧", "楚雄彝族自治州", "红河哈尼族彝族自治州", "文山壮族苗族自治州", "西双版纳傣族自治州", "大理白族自治州", "德宏傣族景颇族自治州", "怒江傈僳族自治州", "迪庆藏族自治州",
"拉萨", "日喀则", "昌都", "林芝", "山南", "那曲", "阿里地区",
"西安", "铜川", "宝鸡", "咸阳", "渭南", "延安", "汉中", "榆林", "安康", "商洛",
"兰州", "嘉峪关", "金昌", "白银", "天水", "武威", "张掖", "平凉", "酒泉", "庆阳", "定西", "陇南", "临夏回族自治州", "甘南藏族自治州",
"西宁", "海东", "海北藏族自治州", "黄南藏族自治州", "海南藏族自治州", "果洛藏族自治州", "玉树藏族自治州", "海西蒙古族藏族自治州",
"银川", "石嘴山", "吴忠", "固原", "中卫",
"乌鲁木齐", "克拉玛依", "吐鲁番", "哈密", "昌吉回族自治州", "博尔塔拉蒙古自治州", "巴音郭楞蒙古自治州", "阿克苏地区", "克孜勒苏柯尔克孜自治州", "喀什地区", "和田地区", "伊犁哈萨克自治州", "塔城地区", "阿勒泰地区")
    
    def __init__(self, top_count=20):
        self.top_count = top_count
        # 调整路径适应GitHub Actions环境
        self.root_dir = Path(__file__).parent
        self.download_dir = self.root_dir / "downloads"
        self.output_dir = self.root_dir / "output"
        self.temp_file = self.root_dir / "txt.tmp"
        self.speed_log = self.root_dir / "speed.log"
        self._create_directories()
        
    def _create_directories(self):
        """创建必要的目录"""
        self.download_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)
        
    def download_files(self):
        """下载所有txt文件"""
        print("开始下载直播源文件...")
        
        def download_single_file(url):
            try:
                filename = self._generate_unique_filename(url)
                filepath = self.download_dir / filename
                
                response = requests.get(url, timeout=30, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                response.raise_for_status()
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(response.text)
                    
                print(f"✓ 下载成功: {filename}")
                return filepath
                
            except Exception as e:
                print(f"✗ 下载失败 {url}: {e}")
                return None
        
        # 并发下载
        downloaded_files = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(download_single_file, url) for url in self.URLS]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    downloaded_files.append(result)
        
        print(f"下载完成，共获得 {len(downloaded_files)} 个文件")
        return downloaded_files
    
    def _generate_unique_filename(self, url):
        """根据URL生成唯一的文件名"""
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split('/') if part]
        
        original_filename = path_parts[-1] if path_parts else "unknown.txt"
        if not original_filename.endswith('.txt'):
            original_filename = f"{original_filename}.txt"
        
        domain = parsed.netloc.replace('.', '_')
        if len(path_parts) > 1:
            prefix = path_parts[-2]
        else:
            prefix = domain.split('_')[0]
        
        name_without_ext = original_filename.rsplit('.', 1)[0]
        unique_filename = f"{prefix}_{name_without_ext}.txt"
        
        return unique_filename
    
    def parse_txt_files(self, filepaths):
        """解析txt文件并提取频道信息"""
        print("解析直播源文件...")
        all_channels = []
        all_content = []
        
        for filepath in filepaths:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                all_content.append(f"# 来源文件: {filepath.name}\n{content}\n")
                
                channels = self._parse_content(content)
                all_channels.extend(channels)
                print(f"✓ 解析 {filepath.name}: 获得 {len(channels)} 个频道")
                
            except Exception as e:
                print(f"✗ 解析失败 {filepath}: {e}")
        
        self._create_merged_temp_file(all_content)
        print(f"总共解析出 {len(all_channels)} 个频道")
        return all_channels
    
    def _create_merged_temp_file(self, all_content):
        """创建合并的临时文件"""
        try:
            with open(self.temp_file, 'w', encoding='utf-8') as f:
                f.write("# IPTV直播源汇总临时文件\n")
                f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.writelines(all_content)
            
            print(f"✓ 汇总临时文件已生成: {self.temp_file}")
            
        except Exception as e:
            print(f"✗ 生成汇总临时文件失败: {e}")
    
    def _parse_content(self, content):
        """解析txt内容提取频道信息"""
        channels = []
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        
        for line in lines:
            if line.endswith('#genre#'):
                continue
                
            if ',' in line:
                parts = line.split(',', 1)
                if len(parts) == 2:
                    name = parts[0].strip()
                    url_part = parts[1].strip()
                    
                    name = self._normalize_channel_name(name)
                    urls = [url.strip() for url in url_part.split('#') if url.strip()]
                    
                    for url in urls:
                        if url and url.startswith('http'):
                            channels.append(ChannelInfo(name, url))
        
        return channels
    
    def _normalize_channel_name(self, name):
        """统一频道名称格式"""
        name = re.sub(r'CCTV-(\d+)', r'CCTV\1', name, flags=re.IGNORECASE)
        name = re.sub(r'CGTN-(\w+)', r'CGTN\1', name, flags=re.IGNORECASE)
        return name
    
    def test_stream_speed(self, channel: ChannelInfo, timeout=8):
        """测试单个流媒体速度"""
        try:
            session = requests.Session()
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            
            if channel.url.endswith('.m3u8'):
                return self._test_m3u8_speed(session, channel, timeout)
            else:
                return self._test_direct_stream_speed(session, channel, timeout)
            
        except Exception as e:
            pass
        
        channel.speed = 0.0
        return channel
    
    def _test_m3u8_speed(self, session, channel: ChannelInfo, timeout=8):
        """测试M3U8流媒体速度"""
        try:
            m3u8_response = session.get(channel.url, timeout=5)
            m3u8_response.raise_for_status()
            m3u8_content = m3u8_response.text
            
            ts_urls = self._extract_ts_urls(m3u8_content, channel.url)
            if not ts_urls:
                channel.speed = 0.0
                return channel
            
            ts_url = ts_urls[0]
            start_time = time.time()
            
            try:
                response = session.get(ts_url, stream=True, timeout=5)
                response.raise_for_status()
                
                downloaded_size = 0
                target_size = 2 * 1024 * 1024
                
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        downloaded_size += len(chunk)
                        
                    current_time = time.time()
                    if (current_time - start_time) > 5:
                        break
                        
                    if downloaded_size >= target_size:
                        break
                
                elapsed_time = time.time() - start_time
                min_size = 256 * 1024
                
                if elapsed_time > 0 and downloaded_size >= min_size:
                    speed = downloaded_size / elapsed_time / 1024 / 1024
                    channel.speed = round(speed, 2)
                else:
                    channel.speed = 0.0
                    
            except Exception:
                channel.speed = 0.0
                
            return channel
            
        except Exception:
            channel.speed = 0.0
            return channel
    
    def _extract_ts_urls(self, m3u8_content, base_url):
        """从M3U8内容中提取TS文件URL"""
        ts_urls = []
        lines = m3u8_content.split('\n')
        
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                if not line.startswith('http'):
                    ts_url = urljoin(base_url, line)
                else:
                    ts_url = line
                ts_urls.append(ts_url)
        
        return ts_urls
    
    def _test_direct_stream_speed(self, session, channel: ChannelInfo, timeout=8):
        """测试直接流媒体速度"""
        try:
            response = session.get(channel.url, stream=True, timeout=timeout)
            response.raise_for_status()
            
            downloaded_size = 0
            target_size = 2 * 1024 * 1024
            min_size = 256 * 1024
            
            data_start_time = time.time()
            
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    downloaded_size += len(chunk)
                    current_time = time.time()
                    
                    if (current_time - data_start_time) > 5:
                        break
                        
                    if downloaded_size >= target_size:
                        break
            
            elapsed_time = time.time() - data_start_time
            if elapsed_time > 0 and downloaded_size >= min_size:
                speed = downloaded_size / elapsed_time / 1024 / 1024
                channel.speed = round(speed, 2)
            else:
                channel.speed = 0.0
                
            return channel
            
        except Exception:
            channel.speed = 0.0
            return channel
    
    def speed_test_channels(self, channels):
        """并发测速所有频道"""
        print(f"开始测速 {len(channels)} 个频道...")
        
        self._init_speed_log()
        
        def test_single_channel(index, channel):
            import threading
            
            result_container = [None]
            exception_container = [None]
            
            def test_worker():
                try:
                    result_container[0] = self.test_stream_speed(channel, timeout=8)
                except Exception as e:
                    exception_container[0] = str(e)
            
            test_thread = threading.Thread(target=test_worker)
            test_thread.daemon = True
            test_thread.start()
            test_thread.join(timeout=12)
            
            if test_thread.is_alive():
                channel.speed = 0.0
                result = channel
                print(f"[{index+1}/{len(channels)}] {channel.name}: 超时")
            elif exception_container[0]:
                channel.speed = 0.0
                result = channel
                print(f"[{index+1}/{len(channels)}] {channel.name}: 测试失败")
            elif result_container[0]:
                result = result_container[0]
                if result.speed > 0:
                    print(f"[{index+1}/{len(channels)}] {channel.name}: {result.speed:.2f} MB/s")
                else:
                    print(f"[{index+1}/{len(channels)}] {channel.name}: 速度为0")
            else:
                channel.speed = 0.0
                result = channel
                print(f"[{index+1}/{len(channels)}] {channel.name}: 未知错误")
                
            # 记录测速结果
            self._log_speed_result(result)
            return result
        
        # 并发测试
        tested_channels = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(test_single_channel, i, channel) 
                      for i, channel in enumerate(channels)]
            
            for future in as_completed(futures):
                tested_channels.append(future.result())
        
        print("测速完成")
        return tested_channels
    
    def _init_speed_log(self):
        """初始化测速日志"""
        try:
            with open(self.speed_log, 'w', encoding='utf-8') as f:
                f.write(f"# 测速日志 - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("# 频道名称 | 速度(MB/s) | URL\n")
        except Exception as e:
            print(f"✗ 初始化测速日志失败: {e}")
    
    def _log_speed_result(self, channel: ChannelInfo):
        """记录测速结果到日志"""
        try:
            with open(self.speed_log, 'a', encoding='utf-8') as f:
                f.write(f"{channel.name} | {channel.speed} | {channel.url}\n")
        except Exception as e:
            print(f"✗ 记录测速日志失败: {e}")
    
    def deduplicate_channels(self, channels):
        """去重频道（基于名称和URL）"""
        seen = set()
        unique_channels = []
        
        for channel in channels:
            key = (channel.name, channel.url)
            if key not in seen:
                seen.add(key)
                unique_channels.append(channel)
        
        print(f"去重完成: 原始 {len(channels)} 个，去重后 {len(unique_channels)} 个")
        return unique_channels
    
    def group_channels(self, channels):
        """对频道进行分组"""
        groups = {
            ChannelGroup.CCTV: [],
            ChannelGroup.WEI_SHI: [],
            ChannelGroup.LOCAL: [],
            ChannelGroup.HKMOTW: [],
            ChannelGroup.CITY: [],
            ChannelGroup.OTHER: []
        }
        
        for channel in channels:
            name = channel.name
            group = self._determine_group(name)
            groups[group].append(channel)
        
        # 按速度排序每个分组
        for group in groups:
            groups[group].sort(key=lambda x: x.speed, reverse=True)
            # 只保留前N个
            groups[group] = groups[group][:self.top_count]
        
        return groups
    
    def _determine_group(self, name):
        """确定频道所属分组"""
        # 检查是否为央视频道
        if re.match(r'^CCTV\d+', name, re.IGNORECASE) or \
           re.match(r'^CGTN', name, re.IGNORECASE) or \
           name in ("中央一台", "中央二台", "中央三台", "中央四台", "中央五台", 
                   "中央六台", "中央七台", "中央八台", "中央九台", "中央十台"):
            return ChannelGroup.CCTV
            
        # 检查是否为港澳台频道
        for keyword in self.hkmotw:
            if keyword in name:
                return ChannelGroup.HKMOTW
                
        # 检查是否为市级频道
        for keyword in self.citys:
            if keyword in name:
                return ChannelGroup.CITY
                
        # 检查是否为卫视频道
        for keyword in self.wei_shi:
            if keyword in name:
                return ChannelGroup.WEI_SHI
                
        # 检查是否为省级频道
        for keyword in self.locals:
            if keyword in name:
                return ChannelGroup.LOCAL
                
        # 其他频道
        return ChannelGroup.OTHER
    
    def generate_output_files(self, groups):
        """生成M3U和TXT格式的输出文件"""
        m3u_path = self.output_dir / "unicast_result.m3u"
        txt_path = self.output_dir / "unicast_result.txt"
        
        # 生成M3U文件
        try:
            with open(m3u_path, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
                f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# 每个分组保留前 {self.top_count} 个最快的频道\n\n")
                
                for group_name, channels in groups.items():
                    if channels:
                        f.write(f"#EXTINF:-1 group-title=\"{group_name}\"\n")
                        for channel in channels:
                            f.write(f"#EXTINF:-1 tvg-name=\"{channel.name}\" tvg-logo=\"\" group-title=\"{group_name}\",{channel.name}\n")
                            f.write(f"{channel.url}\n")
                        f.write("\n")
            
            print(f"✓ M3U文件已生成: {m3u_path}")
        except Exception as e:
            print(f"✗ 生成M3U文件失败: {e}")
        
        # 生成TXT文件
        try:
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(f"# IPTV直播源列表 - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# 每个分组保留前 {self.top_count} 个最快的频道\n\n")
                
                for group_name, channels in groups.items():
                    if channels:
                        f.write(f"#{group_name}#genre#\n")
                        for channel in channels:
                            f.write(f"{channel.name},{channel.url}  # 速度: {channel.speed} MB/s\n")
                        f.write("\n")
            
            print(f"✓ TXT文件已生成: {txt_path}")
        except Exception as e:
            print(f"✗ 生成TXT文件失败: {e}")
        
        return m3u_path, txt_path
    
    def run(self):
        """执行主流程"""
        start_time = time.time()
        print("===== 开始处理IPTV直播源 =====")
        
        # 1. 下载文件
        downloaded_files = self.download_files()
        if not downloaded_files:
            print("没有下载到任何文件，程序退出")
            return
        
        # 2. 解析文件
        channels = self.parse_txt_files(downloaded_files)
        if not channels:
            print("没有解析到任何频道，程序退出")
            return
        
        # 3. 去重
        unique_channels = self.deduplicate_channels(channels)
        
        # 4. 测速
        tested_channels = self.speed_test_channels(unique_channels)
        
        # 过滤掉速度为0的频道
        valid_channels = [c for c in tested_channels if c.speed > 0]
        print(f"有效频道: {len(valid_channels)} 个 (速度>0)")
        
        if not valid_channels:
            print("没有有效的频道，程序退出")
            return
        
        # 5. 分组
        groups = self.group_channels(valid_channels)
        
        # 6. 生成输出文件
        self.generate_output_files(groups)
        
        end_time = time.time()
        print(f"\n===== 处理完成，耗时 {end_time - start_time:.2f} 秒 =====")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='IPTV直播源处理工具')
    parser.add_argument('--top', type=int, default=20, help='每个分组保留的频道数量')
    args = parser.parse_args()
    
    processor = UnicastProcessor(top_count=args.top)
    processor.run()
