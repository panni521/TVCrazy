#!/usr/bin/env python3
"""
UDPXY 源生成器
根据 UDPXY 服务自动生成可播放的 IPTV 源文件

使用方法:
    python3 udpxysourcemake.py IP:PORT [选项]

选项:
    --notest         只测试 UDPXY 服务可用性，不生成源文件
    --test-count N   每个组播文件测试的地址数量（默认20）
    --timeout N      测试超时时间（默认5秒）
    --proxy URL      代理服务器地址 (格式: http://host:port)
    --force-update   强制更新组播文件，即使本地已存在

示例:
    python3 udpxysourcemake.py 192.168.1.100:8098
    python3 udpxysourcemake.py 192.168.1.100:8098 --notest
    python3 udpxysourcemake.py 192.168.1.100:8098 --test-count 10
    python3 udpxysourcemake.py 192.168.1.100:8098 --proxy http://127.0.0.1:10808
"""

import argparse
import os
import re
import requests
import socket
import sys
import time
import json
from pathlib import Path
from urllib.parse import urlparse, urljoin, quote
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading


class UDPXYSourceMaker:
    def __init__(self, udpxy_server, test_count=20, timeout=5, notest=False, proxy=None, force_update=False, max_workers=5):
        self.udpxy_server = udpxy_server
        self.test_count = test_count
        self.timeout = timeout
        self.notest = notest
        self.proxy = proxy
        self.force_update = force_update
        self.max_workers = max_workers  # 最大线程数
        
        # 线程锁，用于同步输出
        self.print_lock = threading.Lock()
        
        # 解析 UDPXY 服务器地址
        if ':' not in udpxy_server:
            raise ValueError("UDPXY 服务器地址格式错误，应为 IP:PORT")
        
        self.udpxy_ip, self.udpxy_port = udpxy_server.split(':', 1)
        try:
            self.udpxy_port = int(self.udpxy_port)
        except ValueError:
            raise ValueError("端口号必须是数字")
        
        # 设置基础目录 - 修改为适应GitHub环境的路径
        self.base_dir = Path("multicast_sources")
        self.output_dir = Path("output")  # 改为根目录下的output文件夹
        
        # 创建目录
        self.base_dir.mkdir(exist_ok=True)
        self.output_dir.mkdir(exist_ok=True)
        
        # 组播源基础URL
        self.base_url = "https://chinaiptv.pages.dev/"
        
        # 省份和运营商映射
        self.provinces = [
            "anhui", "beijing", "chongqing", "fujian", "gansu", "guangdong", 
            "guangxi", "guizhou", "hainan", "hebei", "heilongjiang", "henan", 
            "hubei", "hunan", "jiangsu", "jiangxi", "jilin", "liaoning", 
            "neimenggu", "ningxia", "qinghai", "shaanxi", "shandong", "shanghai", 
            "shanxi", "sichuan", "tianjin", "xinjiang", "xizang", "yunnan", "zhejiang"
        ]
        
        self.isps = ["telecom", "unicom", "mobile"]
        
        # IP归属地查询API
        self.ip_api_url = "http://ip-api.com/json/"
        
        # 配置代理
        self.session = requests.Session()
        if self.proxy:
            self.session.proxies = {
                'http': self.proxy,
                'https': self.proxy
            }
            self._print(f"使用代理: {self.proxy}")
        
        self._print(f"初始化 UDPXY 源生成器")
        self._print(f"UDPXY 服务器: {self.udpxy_server}")
        self._print(f"测试地址数量: {self.test_count}")
        self._print(f"超时时间: {self.timeout}秒")
        self._print(f"最大线程数: {self.max_workers}")
        self._print(f"仅测试模式: {'是' if self.notest else '否'}")
        self._print(f"强制更新: {'是' if self.force_update else '否'}")
    
    def _print(self, message):
        """线程安全的打印方法"""
        with self.print_lock:
            print(message)
    
    def test_udpxy_service(self):
        """测试 UDPXY 服务是否可用"""
        self._print(f"\n==== 测试 UDPXY 服务 {self.udpxy_server} ====")
        
        try:
            # 1. 测试端口连通性
            self._print(f"1. 测试端口连通性...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            result = sock.connect_ex((self.udpxy_ip, self.udpxy_port))
            sock.close()
            
            if result != 0:
                self._print(f"✗ 端口连接失败: {self.udpxy_ip}:{self.udpxy_port}")
                return False
            
            self._print(f"✓ 端口连接成功")
            
            # 2. 测试 UDPXY 服务
            self._print(f"2. 测试 UDPXY 服务...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            
            try:
                sock.connect((self.udpxy_ip, self.udpxy_port))
                
                # 发送HTTP GET请求
                request = f"GET / HTTP/1.1\r\nHost: {self.udpxy_ip}:{self.udpxy_port}\r\nConnection: close\r\nUser-Agent: udpxy-test\r\n\r\n"
                sock.send(request.encode())
                
                # 接收响应
                response = b""
                start_time = time.time()
                
                while True:
                    try:
                        sock.settimeout(2)
                        chunk = sock.recv(1024)
                        if not chunk:
                            break
                        response += chunk
                        
                        if len(response) > 512 or (time.time() - start_time) > 3:
                            break
                            
                    except socket.timeout:
                        break
                
                sock.close()
                
                # 解码响应并检查是否包含udpxy标识
                text = response.decode(errors="ignore")
                text_lower = text.lower()
                
                # udpxy判断标准
                udpxy_indicators = [
                    'server:' in text_lower and 'udpxy' in text_lower,
                    'udpxy' in text_lower and 'unrecognized request' in text_lower,
                    'udpxy' in text_lower and any(version in text_lower for version in ['1.0-', '0.', 'prod', 'standard']),
                    '400' in text_lower and 'unrecognized request' in text_lower and 'server:' in text_lower,
                    'server: udpxy' in text_lower,
                    'udpxy' in text_lower,
                ]
                
                is_udpxy = any(udpxy_indicators)
                
                if is_udpxy:
                    self._print(f"✓ 确认为 UDPXY 服务")
                    
                    # 3. 获取状态信息
                    self._print(f"3. 获取 UDPXY 状态...")
                    status_info = self.get_udpxy_status()
                    if status_info.get('status_available'):
                        self._print(f"✓ UDPXY 状态: 活跃连接 {status_info.get('active_connections', 0)} 个")
                    else:
                        self._print(f"! UDPXY 状态页面无法访问，但服务可用")
                    
                    return True
                else:
                    self._print(f"✗ 不是 UDPXY 服务，响应内容: {text[:100]}...")
                    return False
                    
            except Exception as e:
                sock.close()
                self._print(f"✗ UDPXY 服务测试失败: {e}")
                return False
                
        except Exception as e:
            self._print(f"✗ UDPXY 服务测试异常: {e}")
            return False
    
    def get_udpxy_status(self):
        """获取 UDPXY 状态信息"""
        try:
            status_url = f"http://{self.udpxy_server}/status"
            response = requests.get(status_url, timeout=self.timeout)
            response.raise_for_status()
            
            html_content = response.text
            
            # 解析HTML页面
            try:
                soup = BeautifulSoup(html_content, "html.parser")
                
                # 查找状态表格
                client_table = soup.find('table', attrs={'cellspacing': '0'})
                
                if client_table:
                    td_tags = client_table.find_all('td')
                    
                    if len(td_tags) >= 4:
                        addr = td_tags[2].text.strip() if len(td_tags) > 2 else "N/A"
                        actv = td_tags[3].text.strip() if len(td_tags) > 3 else "0"
                        
                        try:
                            actv_count = int(actv)
                        except ValueError:
                            actv_count = 0
                        
                        return {
                            'address': addr,
                            'active_connections': actv_count,
                            'status_available': True
                        }
                        
            except Exception as e:
                self._print(f"状态页面解析失败: {e}")
            
            return {
                'address': "N/A",
                'active_connections': 0,
                'status_available': False
            }
                
        except Exception as e:
            return {
                'address': "N/A", 
                'active_connections': 0,
                'status_available': False,
                'error': f"请求失败: {e}"
            }
    
    def get_ip_location(self):
        """获取IP地址归属地"""
        self._print(f"\n==== 查询IP归属地 ====")
        try:
            response = self.session.get(f"{self.ip_api_url}{self.udpxy_ip}", timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') == 'success':
                country = data.get('country', '')
                region = data.get('regionName', '')
                city = data.get('city', '')
                isp = data.get('isp', '')
                
                self._print(f"IP地址: {self.udpxy_ip}")
                self._print(f"国家: {country}")
                self._print(f"省份/地区: {region}")
                self._print(f"城市: {city}")
                self._print(f"ISP: {isp}")
                
                # 尝试匹配省份
                region_lower = region.lower()
                matched_province = None
                
                # 省份名称映射
                province_mapping = {
                    'anhui': ['anhui', '安徽'],
                    'beijing': ['beijing', '北京'],
                    'chongqing': ['chongqing', '重庆'],
                    'fujian': ['fujian', '福建'],
                    'gansu': ['gansu', '甘肃'],
                    'guangdong': ['guangdong', '广东'],
                    'guangxi': ['guangxi', '广西'],
                    'guizhou': ['guizhou', '贵州'],
                    'hainan': ['hainan', '海南'],
                    'hebei': ['hebei', '河北'],
                    'heilongjiang': ['heilongjiang', '黑龙江'],
                    'henan': ['henan', '河南'],
                    'hubei': ['hubei', '湖北'],
                    'hunan': ['hunan', '湖南'],
                    'jiangsu': ['jiangsu', '江苏'],
                    'jiangxi': ['jiangxi', '江西'],
                    'jilin': ['jilin', '吉林'],
                    'liaoning': ['liaoning', '辽宁'],
                    'neimenggu': ['inner mongolia', 'neimenggu', '内蒙古'],
                    'ningxia': ['ningxia', '宁夏'],
                    'qinghai': ['qinghai', '青海'],
                    'shaanxi': ['shaanxi', '陕西'],
                    'shandong': ['shandong', '山东'],
                    'shanghai': ['shanghai', '上海'],
                    'shanxi': ['shanxi', '山西'],
                    'sichuan': ['sichuan', '四川'],
                    'tianjin': ['tianjin', '天津'],
                    'xinjiang': ['xinjiang', '新疆'],
                    'xizang': ['tibet', 'xizang', '西藏'],
                    'yunnan': ['yunnan', '云南'],
                    'zhejiang': ['zhejiang', '浙江']
                }
                
                for province, aliases in province_mapping.items():
                    for alias in aliases:
                        if alias in region_lower:
                            matched_province = province
                            break
                    if matched_province:
                        break
                
                if matched_province:
                    self._print(f"匹配省份: {matched_province}")
                    return matched_province
                else:
                    self._print(f"未能匹配到已知省份，将测试所有省份")
                    return None
                    
            else:
                self._print(f"IP归属地查询失败: {data.get('message', '未知错误')}")
                return None
                
        except Exception as e:
            self._print(f"IP归属地查询异常: {e}")
            return None
    
    def fetch_multicast_index(self):
        """获取组播源网站的省份和运营商列表"""
        self._print(f"\n==== 获取组播源列表 ====")
        try:
            response = self.session.get(self.base_url, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 查找表格中的链接
            links = soup.find_all('a', href=True)
            multicast_links = []
            seen_combinations = set()  # 用于去重
            
            for link in links:
                href = link.get('href', '')
                if 'Multicast/' in href and href.endswith('.txt'):
                    # 解析省份和运营商
                    # 例如: Multicast/anhui/telecom.txt
                    parts = href.split('/')
                    if len(parts) >= 3:
                        province = parts[-2].lower()
                        isp_file = parts[-1].lower()
                        isp = isp_file.replace('.txt', '')
                        
                        # 创建唯一标识符进行去重
                        combination_key = f"{province}_{isp}"
                        
                        if province in self.provinces and isp in self.isps and combination_key not in seen_combinations:
                            seen_combinations.add(combination_key)
                            full_url = urljoin(self.base_url, href)
                            multicast_links.append({
                                'province': province,
                                'isp': isp,
                                'url': full_url,
                                'filename': f"{province}_{isp}.txt"
                            })
            
            self._print(f"发现 {len(multicast_links)} 个组播源文件")
            return multicast_links
            
        except Exception as e:
            self._print(f"获取组播源列表失败: {e}")
            return []
    
    def download_multicast_file(self, multicast_info):
        """下载单个组播文件"""
        try:
            province = multicast_info['province']
            isp = multicast_info['isp']
            url = multicast_info['url']
            
            # 创建目录
            target_dir = self.base_dir / province
            target_dir.mkdir(exist_ok=True)
            
            target_file = target_dir / f"{isp}.txt"
            
            # 检查是否需要下载
            should_download = self.force_update or not target_file.exists() or target_file.stat().st_size == 0
            
            if not should_download:
                # 检查文件是否需要更新（比较远程文件大小或最后修改时间）
                try:
                    # 发送HEAD请求获取远程文件信息
                    head_response = self.session.head(url, timeout=10)
                    if head_response.status_code == 200:
                        remote_size = head_response.headers.get('content-length')
                        if remote_size:
                            remote_size = int(remote_size)
                            local_size = target_file.stat().st_size
                            if remote_size != local_size:
                                self._print(f"  检测到文件大小变化，需要更新: {province}/{isp}.txt")
                                should_download = True
                except Exception as e:
                    self._print(f"  无法检查远程文件信息，使用本地文件: {province}/{isp}.txt")
            
            if not should_download:
                self._print(f"  跳过已存在的文件: {province}/{isp}.txt")
                return str(target_file)
            
            self._print(f"  下载: {province}/{isp}.txt")
            
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            
            # 保存文件
            with open(target_file, 'w', encoding='utf-8') as f:
                f.write(response.text)
            
            return str(target_file)
            
        except Exception as e:
            self._print(f"  下载失败 {multicast_info.get('filename', '未知文件')}: {e}")
            return None
    
    def download_all_multicast_files(self):
        """下载所有组播文件"""
        self._print(f"\n==== 下载组播源文件 ====")
        
        multicast_links = self.fetch_multicast_index()
        if not multicast_links:
            self._print("未找到组播源文件")
            # 如果无法获取在线列表，尝试使用本地已有的文件
            return self.get_local_multicast_files()
        
        downloaded_files = []
        
        # 使用线程池下载
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.download_multicast_file, info): info for info in multicast_links}
            
            for future in as_completed(futures):
                info = futures[future]
                try:
                    file_path = future.result()
                    if file_path:
                        downloaded_files.append({
                            'path': file_path,
                            'province': info['province'],
                            'isp': info['isp']
                        })
                except Exception as e:
                    self._print(f"处理 {info.get('filename', '未知文件')} 时出错: {e}")
        
        self._print(f"成功下载 {len(downloaded_files)} 个组播文件")
        return downloaded_files
    
    def get_local_multicast_files(self):
        """获取本地已有的组播文件"""
        self._print("尝试使用本地已有的组播文件...")
        local_files = []
        
        if not self.base_dir.exists():
            return local_files
        
        for province_dir in self.base_dir.iterdir():
            if province_dir.is_dir() and province_dir.name in self.provinces:
                for isp_file in province_dir.glob("*.txt"):
                    isp_name = isp_file.stem
                    if isp_name in self.isps:
                        local_files.append({
                            'path': str(isp_file),
                            'province': province_dir.name,
                            'isp': isp_name
                        })
        
        self._print(f"找到 {len(local_files)} 个本地组播文件")
        return local_files
    
    def parse_multicast_file(self, file_path):
        """解析组播文件，提取频道信息"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            channels = []
            current_group = ""
            
            for line in content.split('\n'):
                line = line.strip()
                if not line:
                    continue
                
                # 检查是否是分组标识
                if line.endswith(',#genre#'):
                    current_group = line.replace(',#genre#', '')
                    continue
                
                # 检查是否是频道信息
                if ',' in line and ('rtp://' in line or 'udp://' in line):
                    parts = line.split(',', 1)
                    if len(parts) == 2:
                        channel_name = parts[0].strip()
                        stream_url = parts[1].strip()
                        
                        # 提取组播地址和端口
                        match = re.match(r'(udp|rtp)://([\d\.]+):(\d+)', stream_url)
                        if match:
                            protocol, ip, port = match.groups()
                            channels.append({
                                'name': channel_name,
                                'protocol': protocol,
                                'ip': ip,
                                'port': port,
                                'original_url': stream_url,
                                'group': current_group
                            })
            
            self._print(f"解析 {file_path} 完成，找到 {len(channels)} 个频道")
            return channels
            
        except Exception as e:
            self._print(f"解析 {file_path} 失败: {e}")
            return []
    
    def test_channel(self, channel):
        """测试单个频道是否可用"""
        try:
            # 构建通过udpxy访问的URL
            # 格式: http://udpxy_ip:port/udp/ multicast_ip:port
            encoded_url = f"http://{self.udpxy_server}/udp/{channel['ip']}:{channel['port']}"
            
            # 发送HEAD请求测试
            response = requests.head(encoded_url, timeout=self.timeout)
            
            # 即使返回404也可能是正常的，因为udpxy可能不支持HEAD请求
            # 所以只要能建立连接就认为可用
            return True
            
        except Exception as e:
            # 超时或连接错误表示不可用
            return False
    
    def process_multicast_file(self, file_info):
        """处理单个组播文件：解析并测试频道"""
        file_path = file_info['path']
        province = file_info['province']
        isp = file_info['isp']
        
        self._print(f"\n处理 {province}/{isp} 组播文件...")
        
        # 解析文件
        channels = self.parse_multicast_file(file_path)
        if not channels:
            return []
        
        # 限制测试数量
        test_channels = channels[:self.test_count]
        self._print(f"测试 {len(test_channels)} 个频道 (共 {len(channels)} 个)")
        
        # 测试频道
        working_channels = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.test_channel, channel): channel for channel in test_channels}
            
            for future in as_completed(futures):
                channel = futures[future]
                try:
                    if future.result():
                        working_channels.append(channel)
                except Exception as e:
                    self._print(f"测试频道 {channel['name']} 时出错: {e}")
        
        self._print(f"{province}/{isp} 测试完成，可用频道: {len(working_channels)}/{len(test_channels)}")
        return working_channels
    
    def generate_playlist(self, all_channels):
        """生成M3U和TXT格式的播放列表"""
        if not all_channels:
            self._print("没有可用的频道，不生成播放列表")
            return
        
        self._print(f"\n==== 生成播放列表 ====")
        
        # 按省份和运营商分组
        grouped_channels = {}
        for channel_info in all_channels:
            key = f"{channel_info['province']}_{channel_info['isp']}"
            if key not in grouped_channels:
                grouped_channels[key] = []
            grouped_channels[key].append(channel_info['channel'])
        
        # 生成合并的M3U和TXT文件
        m3u_path = self.output_dir / "all_channels.m3u"
        txt_path = self.output_dir / "all_channels.txt"
        
        with open(m3u_path, 'w', encoding='utf-8') as m3u_file, \
             open(txt_path, 'w', encoding='utf-8') as txt_file:
            
            # M3U头部
            m3u_file.write("#EXTM3U x-tvg-url=\"https://epg.51zmt.top:8000/e.xml.gz\"\n")
            
            # 按组写入
            for key, channels in grouped_channels.items():
                province, isp = key.split('_', 1)
                group_name = f"{province.capitalize()} {isp.capitalize()}"
                
                self._print(f"写入 {group_name} 频道: {len(channels)} 个")
                
                # 写入M3U
                for channel in channels:
                    # 构建udpxy播放地址
                    play_url = f"http://{self.udpxy_server}/udp/{channel['ip']}:{channel['port']}"
                    
                    # M3U格式: #EXTINF:-1 tvg-name="频道名" group-title="分组",频道名
                    m3u_file.write(f'#EXTINF:-1 tvg-name="{channel["name"]}" group-title="{group_name}",{channel["name"]}\n')
                    m3u_file.write(f"{play_url}\n")
                    
                    # TXT格式: 频道名,播放地址
                    txt_file.write(f"{channel['name']},{play_url}\n")
        
        self._print(f"播放列表生成完成:")
        self._print(f"  M3U格式: {m3u_path}")
        self._print(f"  TXT格式: {txt_path}")
        
        # 生成按省份和运营商分类的文件
        for key, channels in grouped_channels.items():
            province, isp = key.split('_', 1)
            m3u_cat_path = self.output_dir / f"{province}_{isp}.m3u"
            txt_cat_path = self.output_dir / f"{province}_{isp}.txt"
            
            with open(m3u_cat_path, 'w', encoding='utf-8') as m3u_file, \
                 open(txt_cat_path, 'w', encoding='utf-8') as txt_file:
                
                m3u_file.write("#EXTM3U x-tvg-url=\"https://epg.51zmt.top:8000/e.xml.gz\"\n")
                
                for channel in channels:
                    play_url = f"http://{self.udpxy_server}/udp/{channel['ip']}:{channel['port']}"
                    m3u_file.write(f'#EXTINF:-1 tvg-name="{channel["name"]}" group-title="{province.capitalize()} {isp.capitalize()}",{channel["name"]}\n')
                    m3u_file.write(f"{play_url}\n")
                    txt_file.write(f"{channel['name']},{play_url}\n")
        
        self._print(f"已生成 {len(grouped_channels)} 个分类播放列表")
    
    def run(self):
        """运行主流程"""
        start_time = time.time()
        
        # 测试UDPXY服务
        if not self.test_udpxy_service():
            self._print("UDPXY服务不可用，程序退出")
            return
        
        # 如果只是测试模式，不继续处理
        if self.notest:
            self._print("\n仅测试模式，完成测试后退出")
            return
        
        # 获取IP归属地（用于优先处理本地省份）
        local_province = self.get_ip_location()
        
        # 下载组播文件
        multicast_files = self.download_all_multicast_files()
        if not multicast_files:
            self._print("没有可用的组播文件，程序退出")
            return
        
        # 如果有本地省份，优先处理
        if local_province:
            # 分离本地省份和其他省份的文件
            local_files = [f for f in multicast_files if f['province'] == local_province]
            other_files = [f for f in multicast_files if f['province'] != local_province]
            # 优先处理本地文件
            multicast_files = local_files + other_files
        
        # 处理所有组播文件
        all_working_channels = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self.process_multicast_file, file_info): file_info for file_info in multicast_files}
            
            for future in as_completed(futures):
                file_info = futures[future]
                try:
                    working_channels = future.result()
                    if working_channels:
                        all_working_channels.extend([{
                            'channel': ch,
                            'province': file_info['province'],
                            'isp': file_info['isp']
                        } for ch in working_channels])
                except Exception as e:
                    self._print(f"处理 {file_info['path']} 时出错: {e}")
        
        # 生成播放列表
        self.generate_playlist(all_working_channels)
        
        end_time = time.time()
        self._print(f"\n所有操作完成，耗时 {end_time - start_time:.2f} 秒")


def main():
    parser = argparse.ArgumentParser(description='UDPXY 源生成器，根据 UDPXY 服务自动生成可播放的 IPTV 源文件')
    parser.add_argument('udpxy_server', help='UDPXY 服务器地址 (格式: IP:PORT)')
    parser.add_argument('--notest', action='store_true', help='只测试 UDPXY 服务可用性，不生成源文件')
    parser.add_argument('--test-count', type=int, default=20, help='每个组播文件测试的地址数量 (默认20)')
    parser.add_argument('--timeout', type=int, default=5, help='测试超时时间 (默认5秒)')
    parser.add_argument('--proxy', help='代理服务器地址 (格式: http://host:port)')
    parser.add_argument('--force-update', action='store_true', help='强制更新组播文件，即使本地已存在')
    parser.add_argument('--max-workers', type=int, default=5, help='最大线程数 (默认5)')
    
    args = parser.parse_args()
    
    try:
        maker = UDPXYSourceMaker(
            udpxy_server=args.udpxy_server,
            test_count=args.test_count,
            timeout=args.timeout,
            notest=args.notest,
            proxy=args.proxy,
            force_update=args.force_update,
            max_workers=args.max_workers
        )
        maker.run()
    except Exception as e:
        print(f"程序出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
