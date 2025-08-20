#!/usr/bin/env python3
"""
IPTV直播源下载、合并、测速与分组工具

功能：
1. 从指定URL列表下载直播源txt文件
2. 合并所有频道（忽略原分组）
3. 对流媒体地址进行测速
4. 按速度排序并保留前N个
5. 重新分组并生成结果文件

用法：
python t.py --top 20
python t.py --top 20 --proxy http://127.0.0.1:10808

项目主页: https://github.com/vitter/iptv-sources
问题反馈: https://github.com/vitter/iptv-sources/issues
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


def load_env_file(env_path=".env"):
    """加载环境变量文件，支持多行值"""
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 使用正则表达式解析环境变量，支持多行值
        pattern = r'^([A-Z_][A-Z0-9_]*)=(.*)$'
        lines = content.split('\n')
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line and not line.startswith('#') and '=' in line:
                match = re.match(pattern, line)
                if match:
                    key = match.group(1)
                    value = match.group(2)
                    
                    # 处理引号包围的多行值
                    if value.startswith('"') and not value.endswith('"'):
                        # 多行值，继续读取直到找到结束引号
                        i += 1
                        while i < len(lines):
                            next_line = lines[i]
                            value += '\n' + next_line
                            if next_line.rstrip().endswith('"'):
                                break
                            i += 1
                    
                    # 移除首尾引号
                    value = value.strip().strip('"').strip("'")
                    os.environ[key] = value
            i += 1


def load_urls_from_env():
    """从环境变量加载URL列表"""
    urls_env = os.getenv('IPTV_URLS', '')
    if urls_env:
        # 支持多种分隔符：换行符、逗号、分号
        urls = []
        for url in re.split(r'[,;\n]+', urls_env):
            url = url.strip()
            if url:
                urls.append(url)
        return urls
    return None


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
    
    # 默认URL列表（作为备用）
    DEFAULT_URLS = [
        "https://live.zbds.org/tv/yd.txt",
        "https://chinaiptv.pages.dev/Unicast/anhui/mobile.txt"
    ]
    
    # 分组关键字
    locals = ("北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", 
              "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", 
              "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙", 
              "广西", "西藏", "宁夏", "新疆", "东南", "东方")
    
    hkmotw = ("凤凰", "香港", "TVB", "tvb", "RTHK", "港台", "明珠", "翡翠", "面包", "人间", "唯心", "星空", "无线", "有线", "无线电视", "无线新闻", "无线娱乐", "大爱", "番薯", "亚洲", "华视", "中天", "中视", "民视", "东森", "三立", "台视", "公视", "台湾","澳门", "澳视", "澳亚", "澳广")
    
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
    
    def __init__(self, top_count=20, proxy=None):
        self.top_count = top_count
        self.proxy = proxy
        self.download_dir = Path("downloads")
        self.output_dir = Path("output")
        self.temp_file = Path("txt.tmp")  # 汇总临时文件
        self.speed_log = Path("speed.log")  # 测速日志文件
        
        # 加载环境变量文件
        load_env_file()
        
        # 从环境变量或使用默认URL列表
        env_urls = load_urls_from_env()
        if env_urls:
            self.URLS = env_urls
            print(f"✓ 从环境变量加载了 {len(env_urls)} 个URL")
        else:
            self.URLS = self.DEFAULT_URLS
            print(f"! 未找到环境变量IPTV_URLS，使用默认的 {len(self.DEFAULT_URLS)} 个URL")
            
        self._create_directories()
        
    def _create_directories(self):
        """创建必要的目录"""
        self.download_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)
        
    def download_files(self):
        """下载所有txt文件"""
        print("开始下载直播源文件...")
        if self.proxy:
            print(f"使用代理: {self.proxy}")
        
        def download_single_file(url):
            try:
                # 解析URL生成唯一文件名
                filename = self._generate_unique_filename(url)
                filepath = self.download_dir / filename
                
                # 设置代理
                proxies = {}
                if self.proxy:
                    proxies = {
                        'http': self.proxy,
                        'https': self.proxy
                    }
                
                print(f"⏳ 正在下载: {url}")
                response = requests.get(url, timeout=15, proxies=proxies, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                response.raise_for_status()
                
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(response.text)
                    
                print(f"✓ 下载成功: {filename}")
                return filepath
                
            except requests.exceptions.Timeout:
                print(f"✗ 下载超时: {url}")
                return None
            except requests.exceptions.ConnectionError:
                print(f"✗ 连接失败: {url}")
                return None
            except requests.exceptions.HTTPError as e:
                print(f"✗ HTTP错误 {e.response.status_code}: {url}")
                return None
            except Exception as e:
                print(f"✗ 下载失败 {url}: {e}")
                return None
        
        # 减少并发数量，避免网络拥堵
        downloaded_files = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(download_single_file, url) for url in self.URLS]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    downloaded_files.append(result)
        
        print(f"下载完成，共获得 {len(downloaded_files)} 个文件，失败 {len(self.URLS) - len(downloaded_files)} 个")
        return downloaded_files
    
    def _generate_unique_filename(self, url):
        """根据URL生成唯一的文件名"""
        from urllib.parse import urlparse
        
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split('/') if part]
        
        # 获取原始文件名
        original_filename = path_parts[-1] if path_parts else "unknown.txt"
        
        # 如果没有.txt扩展名，添加它
        if not original_filename.endswith('.txt'):
            original_filename = f"{original_filename}.txt"
        
        # 生成前缀：使用域名和路径
        domain = parsed.netloc.replace('.', '_')
        
        # 如果路径有多个部分，使用倒数第二个作为前缀
        if len(path_parts) > 1:
            prefix = path_parts[-2]  # 使用目录名作为前缀
        else:
            prefix = domain.split('_')[0]  # 使用域名第一部分
        
        # 组合生成唯一文件名
        name_without_ext = original_filename.rsplit('.', 1)[0]
        unique_filename = f"{prefix}_{name_without_ext}.txt"
        
        return unique_filename
    
    def parse_txt_files(self, filepaths):
        """解析txt文件并提取频道信息"""
        print("解析直播源文件...")
        all_channels = []
        all_content = []  # 收集所有文件内容用于合并
        
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
        
        # 生成汇总临时文件
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
            # 跳过分组行
            if line.endswith('#genre#'):
                continue
                
            # 解析频道行：频道名,url或url1#url2#url3
            if ',' in line:
                parts = line.split(',', 1)
                if len(parts) == 2:
                    name = parts[0].strip()
                    url_part = parts[1].strip()
                    
                    # 统一频道名称格式：将CCTV-1统一为CCTV1
                    name = self._normalize_channel_name(name)
                    
                    # 处理多个URL用#分隔的情况
                    urls = [url.strip() for url in url_part.split('#') if url.strip()]
                    
                    # 为每个URL创建频道条目
                    for url in urls:
                        if url and url.startswith('http'):
                            channels.append(ChannelInfo(name, url))
        
        return channels
    
    def _normalize_channel_name(self, name):
        """统一频道名称格式"""
        # 将CCTV-1统一为CCTV1，CGTN-英语统一为CGTN英语等
        name = re.sub(r'CCTV-(\d+)', r'CCTV\1', name, flags=re.IGNORECASE)
        name = re.sub(r'CGTN-(\w+)', r'CGTN\1', name, flags=re.IGNORECASE)
        
        # CCTV频道特殊处理：除了CCTV5+，其他CCTV频道去除+、-、空格、*符号
        if re.match(r'CCTV', name, re.IGNORECASE):
            # 保护CCTV5+不被修改
            if not re.match(r'CCTV5\+', name, re.IGNORECASE):
                # 去除+、-、空格、*符号
                name = re.sub(r'[+\-\s*]', '', name)
        
        return name
    
    def test_stream_speed(self, channel: ChannelInfo, timeout=8):
        """测试单个流媒体速度"""
        try:
            # 创建会话，设置更短的超时
            session = requests.Session()
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            
            # 如果是M3U8流，先获取M3U8文件内容
            if channel.url.endswith('.m3u8'):
                return self._test_m3u8_speed(session, channel, timeout)
            else:
                return self._test_direct_stream_speed(session, channel, timeout)
            
        except Exception as e:
            # 可以记录具体错误信息用于调试
            pass
        
        channel.speed = 0.0
        return channel
    
    def _test_m3u8_speed(self, session, channel: ChannelInfo, timeout=8):
        """测试M3U8流媒体速度"""
        try:
            # 1. 获取M3U8文件 - 缩短超时时间
            m3u8_response = session.get(channel.url, timeout=5)
            m3u8_response.raise_for_status()
            m3u8_content = m3u8_response.text
            
            # 2. 解析M3U8文件，提取TS分片URL
            ts_urls = self._extract_ts_urls(m3u8_content, channel.url)
            
            if not ts_urls:
                channel.speed = 0.0
                return channel
            
            # 3. 只测试第一个TS分片的速度，减少测试时间
            ts_url = ts_urls[0]
            start_time = time.time()
            
            try:
                response = session.get(ts_url, stream=True, timeout=5)
                response.raise_for_status()
                
                downloaded_size = 0
                target_size = 2 * 1024 * 1024  # 降低到2MB
                
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        downloaded_size += len(chunk)
                        
                    # 如果测试时间超过5秒就停止
                    current_time = time.time()
                    if (current_time - start_time) > 5:
                        break
                        
                    # 达到目标大小就停止
                    if downloaded_size >= target_size:
                        break
                
                elapsed_time = time.time() - start_time
                min_size = 256 * 1024  # 最少256KB才计算速度
                
                if elapsed_time > 0 and downloaded_size >= min_size:
                    speed = downloaded_size / elapsed_time / 1024 / 1024  # MB/s
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
                # 如果是相对路径，拼接完整URL
                if not line.startswith('http'):
                    from urllib.parse import urljoin
                    ts_url = urljoin(base_url, line)
                else:
                    ts_url = line
                ts_urls.append(ts_url)
        
        return ts_urls
    
    def _test_direct_stream_speed(self, session, channel: ChannelInfo, timeout=8):
        """测试直接流媒体速度"""
        try:
            # 下载前2MB数据计算速度，缩短测试时间
            response = session.get(channel.url, stream=True, timeout=timeout)
            response.raise_for_status()
            
            downloaded_size = 0
            target_size = 2 * 1024 * 1024  # 2MB
            start_time = time.time()
            
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    downloaded_size += len(chunk)
                    
                # 超时控制
                current_time = time.time()
                if (current_time - start_time) > 5:  # 最多测试5秒
                    break
                    
                # 达到目标大小
                if downloaded_size >= target_size:
                    break
            
            elapsed_time = time.time() - start_time
            min_size = 256 * 1024  # 最少256KB才计算速度
            
            if elapsed_time > 0 and downloaded_size >= min_size:
                speed = downloaded_size / elapsed_time / 1024 / 1024  # MB/s
                channel.speed = round(speed, 2)
            else:
                channel.speed = 0.0
                
            return channel
            
        except Exception:
            channel.speed = 0.0
            return channel
    
    def batch_test_speed(self, channels: List[ChannelInfo]):
        """批量测试频道速度"""
        print(f"开始测速，共 {len(channels)} 个频道...")
        
        # 过滤掉重复的相同(名称+URL)组合
        unique_channels = []
        seen = set()
        for channel in channels:
            key = (channel.name, channel.url)
            if key not in seen:
                seen.add(key)
                unique_channels.append(channel)
        
        print(f"去重后剩余 {len(unique_channels)} 个频道")
        
        # 记录开始时间
        start_time = time.time()
        
        # 并发测试，控制并发数
        max_workers = min(10, len(unique_channels))  # GitHub Actions中限制并发数
        results = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self.test_stream_speed, channel) for channel in unique_channels]
            
            # 进度跟踪
            total = len(futures)
            completed = 0
            
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                completed += 1
                
                # 每完成10个打印一次进度
                if completed % 10 == 0 or completed == total:
                    print(f"测速进度: {completed}/{total} ({(completed/total)*100:.1f}%)")
        
        # 记录测速结果到日志
        self._log_speed_results(results)
        
        # 计算耗时
        elapsed = time.time() - start_time
        print(f"测速完成，耗时 {elapsed:.2f} 秒")
        
        # 过滤掉速度为0的频道
        valid_channels = [c for c in results if c.speed > 0]
        print(f"有效频道: {len(valid_channels)}/{len(results)}")
        
        return valid_channels
    
    def _log_speed_results(self, channels: List[ChannelInfo]):
        """记录测速结果到日志文件"""
        try:
            with open(self.speed_log, 'w', encoding='utf-8') as f:
                f.write(f"# 测速日志 - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# 共 {len(channels)} 个频道\n\n")
                
                # 按速度降序排列
                sorted_channels = sorted(channels, key=lambda x: x.speed, reverse=True)
                
                for channel in sorted_channels:
                    f.write(f"{channel.name},{channel.url},{channel.speed}MB/s\n")
            
            print(f"✓ 测速日志已保存: {self.speed_log}")
            
        except Exception as e:
            print(f"✗ 保存测速日志失败: {e}")
    
    def group_channels(self, channels: List[ChannelInfo]):
        """对频道进行分组"""
        print("开始对频道进行分组...")
        
        # 按频道名称分组，并保留每个频道速度最快的前N个
        grouped_by_name: Dict[str, List[ChannelInfo]] = {}
        for channel in channels:
            if channel.name not in grouped_by_name:
                grouped_by_name[channel.name] = []
            grouped_by_name[channel.name].append(channel)
        
        # 每个频道只保留速度最快的前N个
        filtered_groups: Dict[str, List[ChannelInfo]] = {}
        for name, channel_list in grouped_by_name.items():
            # 按速度降序排序
            sorted_list = sorted(channel_list, key=lambda x: x.speed, reverse=True)
            # 保留前N个
            filtered_groups[name] = sorted_list[:self.top_count]
        
        # 按类别分组
        final_groups: Dict[str, List[ChannelInfo]] = {
            ChannelGroup.CCTV: [],
            ChannelGroup.WEI_SHI: [],
            ChannelGroup.LOCAL: [],
            ChannelGroup.HKMOTW: [],
            ChannelGroup.CITY: [],
            ChannelGroup.OTHER: []
        }
        
        # 遍历所有频道，分配到对应的分组
        for name, channel_list in filtered_groups.items():
            # 复制频道列表，避免修改原始数据
            channels_to_add = [ChannelInfo(name, c.url, c.speed) for c in channel_list]
            
            # 判断分组
            group = self._determine_group(name)
            final_groups[group].extend(channels_to_add)
        
        # 对每个分组内的频道按名称排序
        for group_name in final_groups:
            # 先按名称排序，再按速度排序
            final_groups[group_name].sort(key=lambda x: (x.name, -x.speed))
        
        print(f"分组完成: {', '.join([f'{k}({len(v)})' for k, v in final_groups.items()])}")
        return final_groups
    
    def _determine_group(self, name: str) -> str:
        """确定频道所属分组"""
        # 优先判断CCTV频道
        if re.match(r'^CCTV\d+', name) or re.match(r'^CGTN', name, re.IGNORECASE):
            return ChannelGroup.CCTV
            
        # 判断港澳台频道
        for keyword in self.hkmotw:
            if keyword in name:
                return ChannelGroup.HKMOTW
                
        # 判断卫视频道
        for keyword in self.wei_shi:
            if keyword in name:
                # 特殊处理：确保"卫视"不被归类到省级频道
                return ChannelGroup.WEI_SHI
                
        # 判断市级频道
        for keyword in self.citys:
            if keyword in name:
                return ChannelGroup.CITY
                
        # 判断省级频道
        for keyword in self.locals:
            if keyword in name:
                return ChannelGroup.LOCAL
                
        # 其他频道
        return ChannelGroup.OTHER
    
    def generate_output_files(self, grouped_channels: Dict[str, List[ChannelInfo]]):
        """生成输出文件（M3U和TXT格式）"""
        print("开始生成输出文件...")
        
        # 生成M3U文件
        m3u_path = self.output_dir / "iptv.m3u"
        try:
            with open(m3u_path, 'w', encoding='utf-8') as f:
                # M3U文件头
                f.write("#EXTM3U x-tvg-url=\"https://epg.51zmt.top:8000/e.xml.gz\"\n\n")
                
                # 按分组写入
                for group_name, channels in grouped_channels.items():
                    if channels:  # 只写入有内容的分组
                        f.write(f"#EXTINF:-1 group-title=\"{group_name}\",{channels[0].name}\n")
                        f.write(f"{channels[0].url}\n\n")
                
            print(f"✓ M3U文件已生成: {m3u_path}")
            
        except Exception as e:
            print(f"✗ 生成M3U文件失败: {e}")
        
        # 生成TXT文件（包含所有可用源）
        txt_path = self.output_dir / "iptv.txt"
        try:
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(f"# IPTV直播源列表\n")
                f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# 总频道数: {sum(len(channels) for channels in grouped_channels.values())}\n\n")
                
                # 按分组写入
                for group_name, channels in grouped_channels.items():
                    if channels:  # 只写入有内容的分组
                        f.write(f"# {group_name}\n")
                        
                        # 去重频道名（每个频道只保留第一个）
                        written_names = set()
                        for channel in channels:
                            if channel.name not in written_names:
                                written_names.add(channel.name)
                                f.write(f"{channel.name},{channel.url}\n")
                        
                        f.write("\n")
            
            print(f"✓ TXT文件已生成: {txt_path}")
            
        except Exception as e:
            print(f"✗ 生成TXT文件失败: {e}")
        
        # 生成包含所有速度最快源的TXT文件
        all_urls_path = self.output_dir / "all_urls.txt"
        try:
            with open(all_urls_path, 'w', encoding='utf-8') as f:
                f.write(f"# 所有直播源URL（按速度排序）\n")
                f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                
                # 收集所有频道并按速度排序
                all_channels = []
                for channels in grouped_channels.values():
                    all_channels.extend(channels)
                
                # 按速度降序排序
                all_channels.sort(key=lambda x: x.speed, reverse=True)
                
                # 写入URL
                for channel in all_channels:
                    f.write(f"{channel.url}\n")
            
            print(f"✓ 全URL文件已生成: {all_urls_path}")
            
        except Exception as e:
            print(f"✗ 生成全URL文件失败: {e}")
    
    def run(self):
        """执行主流程"""
        start_time = time.time()
        print("===== IPTV直播源处理工具开始运行 =====")
        
        try:
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
            
            # 3. 测速
            valid_channels = self.batch_test_speed(channels)
            if not valid_channels:
                print("没有有效的频道，程序退出")
                return
            
            # 4. 分组
            grouped_channels = self.group_channels(valid_channels)
            
            # 5. 生成输出文件
            self.generate_output_files(grouped_channels)
            
        except Exception as e:
            print(f"程序运行出错: {e}")
            import traceback
            traceback.print_exc()
        
        # 计算总耗时
        elapsed = time.time() - start_time
        print(f"\n===== 处理完成，总耗时 {elapsed:.2f} 秒 =====")


def main():
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='IPTV直播源处理工具')
    parser.add_argument('--top', type=int, default=20, help='每个频道保留的最大源数量')
    parser.add_argument('--proxy', type=str, default=None, help='使用代理服务器，如http://127.0.0.1:10808')
    
    args = parser.parse_args()
    
    # 创建处理器并运行
    processor = UnicastProcessor(top_count=args.top, proxy=args.proxy)
    processor.run()


if __name__ == "__main__":
    main()
