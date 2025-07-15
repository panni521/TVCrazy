import argparse
import time
import datetime
import concurrent.futures
import requests
import re
import os
import threading
from queue import Queue
import csv
import asyncio
import aiohttp

# 三个csv对应fofa上的搜索指纹分别是：
# jsmpeg-streamer fid="OBfgOOMpjONAJ/cQ1FpaDQ=="
# txiptv fid="7v4hVyd8x6RxODJO2Q5u5Q=="
# zhgxtv fid="IVS0q72nt9BgY+hjPVH+ZQ=="

# 智慧光迅平台(广东公司) body="ZHGXTV"
# /ZHGXTV/Public/json/live_interface.txt
# http://ip:port/hls/1/index.m3u8
# 智慧桌面 智能KUTV(陕西公司) body="/iptv/live/zh_cn.js"
# http://ip:port/tsfile/live/0001_1.m3u8
# 华视美达 华视私云(浙江公司) body="华视美达"
# http://ip:port/newlive/live/hls/1/live.m3u8

# 频道分类关键词
HONGKONG_KEYWORDS = ["TVB翡翠台", "翡翠台", "明珠台", "凤凰香港", "凤凰卫视", "凤凰资讯"]
GUANGDONG_KEYWORDS = ["广东新闻", "广东珠江"]
GUANGZHOU_KEYWORDS = ["广州综合", "广州新闻"]

# ===================== 通用工具 =====================
def channel_name_normalize(name):
    name = name.replace("cctv", "CCTV")
    name = name.replace("中央", "CCTV")
    name = name.replace("央视", "CCTV")
    for rep in ["高清", "超高", "HD", "标清", "频道", "-", " ", "PLUS", "＋", "(", ")"]:
        name = name.replace(rep, "" if rep not in ["PLUS", "＋"] else "+")
    name = re.sub(r"CCTV(\d+)台", r"CCTV\1", name)
    name_map = {
        "CCTV1综合": "CCTV1", "CCTV2财经": "CCTV2", "CCTV3综艺": "CCTV3", "CCTV4国际": "CCTV4",
        "CCTV4中文国际": "CCTV4", "CCTV4欧洲": "CCTV4", "CCTV5体育": "CCTV5", "CCTV6电影": "CCTV6",
        "CCTV7军事": "CCTV7", "CCTV7军农": "CCTV7", "CCTV7农业": "CCTV7", "CCTV7国防军事": "CCTV7",
        "CCTV8电视剧": "CCTV8", "CCTV9记录": "CCTV9", "CCTV9纪录": "CCTV9", "CCTV10科教": "CCTV10",
        "CCTV11戏曲": "CCTV11", "CCTV12社会与法": "CCTV12", "CCTV13新闻": "CCTV13", "CCTV新闻": "CCTV13",
        "CCTV14少儿": "CCTV14", "CCTV15音乐": "CCTV15", "CCTV16奥林匹克": "CCTV16",
        "CCTV17农业农村": "CCTV17", "CCTV17农业": "CCTV17", "CCTV5+体育赛视": "CCTV5+",
        "CCTV5+体育赛事": "CCTV5+", "CCTV5+体育": "CCTV5+"
    }
    name = name_map.get(name, name)
    return name

def channel_key(channel_name):
    match = re.search(r'\d+', channel_name)
    if match:
        return int(match.group())
    else:
        return float('inf')

def generate_ip_range_urls(base_url, ip_address, port, suffix=None):
    """生成同一C段的所有IP的URL，确保IP合法"""
    # ip_address 形如 '192.168.1.' 或 '192.168.1.1'，需取前三段
    ip_parts = ip_address.split('.')
    if len(ip_parts) < 3:
        return []
    c_prefix = '.'.join(ip_parts[:3])
    urls = []
    for i in range(1, 256):
        full_ip = f"{c_prefix}.{i}"
        url = f"{base_url}{full_ip}{port}"
        if suffix:
            url += suffix
        urls.append(url)
    return urls

def check_urls_concurrent(urls, timeout=1, max_workers=100, print_valid=True):
    """并发检测URL可用性，返回可用URL列表"""
    valid_urls = []
    def is_url_accessible(url):
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == 200:
                return url
        except requests.RequestException:
            pass
        return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(is_url_accessible, url) for url in urls]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                valid_urls.append(result)
                if print_valid:
                    print(result)
    return valid_urls

# 频道分类函数
def classify_channel(name):
    if any(keyword in name for keyword in HONGKONG_KEYWORDS):
        return "香港频道"
    elif any(keyword in name for keyword in GUANGDONG_KEYWORDS):
        return "广东频道"
    elif any(keyword in name for keyword in GUANGZHOU_KEYWORDS):
        return "广州频道"
    elif name.startswith("CCTV"):
        return "央视频道"
    else:
        return "其他频道"

# 生成输出文件
def generate_output_files(channels, output_prefix):
    output_dir = "output"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # 生成 .txt 文件
    txt_file_path = os.path.join(output_dir, f"{output_prefix}.txt")
    with open(txt_file_path, 'w', encoding='utf-8') as txt_file:
        channel_groups = {}
        for name, url in channels:
            category = classify_channel(name)
            if category not in channel_groups:
                channel_groups[category] = []
            channel_groups[category].append((name, url))

        for category, group in channel_groups.items():
            txt_file.write(f"{category},#genre#\n")
            for name, url in group:
                txt_file.write(f"{name},{url}\n")
            txt_file.write("\n")

    # 生成 .m3u 文件
    m3u_file_path = os.path.join(output_dir, f"{output_prefix}.m3u")
    with open(m3u_file_path, 'w', encoding='utf-8') as m3u_file:
        m3u_file.write("#EXTM3U\n")
        for name, url in channels:
            category = classify_channel(name)
            m3u_file.write(f"#EXTINF:-1 group-title=\"{category}\",{name}\n")
            m3u_file.write(f"{url}\n")

    # 生成 speed.txt 文件（这里简单示例，可根据实际测速逻辑完善）
    speed_file_path = os.path.join(output_dir, "speed.txt")
    with open(speed_file_path, 'w', encoding='utf-8') as speed_file:
        for name, url in channels:
            speed_file.write(f"{name},{url},0.0 MB/s\n")

# ===================== 1. jsmpeg模式 =====================
def get_channels_alltv(csv_file):
    urls = set()
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if not fieldnames or 'host' not in fieldnames:
            raise ValueError('CSV文件缺少host列')
        for row in reader:
            host = row['host'].strip()
            if host:
                if host.startswith('http://') or host.startswith('https://'):
                    urls.add(host)
                else:
                    urls.add(f"http://{host}")
    ip_range_urls = []
    for url in urls:
        url = url.strip()
        ip_start_index = url.find("//") + 2
        ip_end_index = url.find(":", ip_start_index)
        ip_dot_start = url.find(".") + 1
        ip_dot_second = url.find(".", ip_dot_start) + 1
        ip_dot_three = url.find(".", ip_dot_second) + 1
        base_url = url[:ip_start_index]
        ip_address = url[ip_start_index:ip_end_index]  # 修正为取 host 部分
        port = url[ip_end_index:]
        ip_range_urls.extend(generate_ip_range_urls(base_url, ip_address, port))
    valid_urls = check_urls_concurrent(set(ip_range_urls))
    channels = []
    for url in valid_urls:
        json_url = url.rstrip('/') + '/streamer/list'
        try:
            response = requests.get(json_url, timeout=1)
            json_data = response.json()
            host = url.rstrip('/')
            for item in json_data:
                name = item.get('name', '').strip()
                key = item.get('key', '').strip()
                if not name or not key:
                    continue
                channel_url = f"{host}/hls/{key}/index.m3u8"
                name = channel_name_normalize(name)
                channels.append((name, channel_url))
        except Exception:
            continue
    return channels

# ===================== 2. txiptv模式（异步） =====================
async def get_channels_newnew(csv_file):
    urls = []
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        url_set = set()
        for row in reader:
            link = row.get('link', '').strip()
            if link:
                url_set.add(link)
        urls = list(url_set)
    async def modify_urls(url):
        modified_urls = []
        ip_start_index = url.find("//") + 2
        ip_end_index = url.find(":", ip_start_index)
        base_url = url[:ip_start_index]
        ip_address = url[ip_start_index:ip_end_index]
        ip_parts = ip_address.split('.')
        if len(ip_parts) < 3:
            return []
        c_prefix = '.'.join(ip_parts[:3])
        port = url[ip_end_index:]
        ip_end = "/iptv/live/1000.json?key=txiptv"
        for i in range(1, 256):
            full_ip = f"{c_prefix}.{i}"
            modified_url = f"{base_url}{full_ip}{port}{ip_end}"
            modified_urls.append(modified_url)
        return modified_urls
    async def is_url_accessible(session, url, semaphore):
        async with semaphore:
            try:
                async with session.get(url, timeout=1) as response:
                    if response.status == 200:
                        return url
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
        return None
    async def check_urls(session, urls, semaphore):
        tasks = []
        for url in urls:
            url = url.strip()
            modified_urls = await modify_urls(url)
            for modified_url in modified_urls:
                task = asyncio.create_task(is_url_accessible(session, modified_url, semaphore))
                tasks.append(task)
        results = await asyncio.gather(*tasks)
        valid_urls = [result for result in results if result]
        for url in valid_urls:
            print(url)  # 新增：打印可访问的url
        return valid_urls
    async def fetch_json(session, url, semaphore):
        async with semaphore:
            try:
                ip_start_index = url.find("//") + 2
                ip_dot_start = url.find(".") + 1
                ip_index_second = url.find("/", ip_dot_start)
                base_url = url[:ip_start_index]
                ip_address = url[ip_start_index:ip_index_second]
                url_x = f"{base_url}{ip_address}"
                json_url = f"{url}"
                async with session.get(json_url, timeout=1) as response:
                    json_data = await response.json()
                    channels = []
                    try:
                        for item in json_data['data']:
                            if isinstance(item, dict):
                                name = item.get('name')
                                urlx = item.get('url')
                                if ',' in urlx:
                                    urlx = "aaaaaaaa"
                                if 'http' in urlx:
                                    urld = f"{urlx}"
                                else:
                                    urld = f"{url_x}{urlx}"
                                if name and urlx:
                                    name = channel_name_normalize(name)
                                    channels.append((name, urld))
                    except Exception:
                        pass
                    return channels
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return []
    x_urls = []
    for url in urls:
        url = url.strip()
        ip_start_index = url.find("//") + 2
        ip_end_index = url.find(":", ip_start_index)
        ip_dot_start = url.find(".") + 1
        ip_dot_second = url.find(".", ip_dot_start) + 1
        ip_dot_three = url.find(".", ip_dot_second) + 1
        base_url = url[:ip_start_index]
        ip_address = url[ip_start_index:ip_dot_three]
        port = url[ip_end_index:]
        ip_end = "1"
        modified_ip = f"{ip_address}{ip_end}"
        x_url = f"{base_url}{modified_ip}{port}"
        x_urls.append(x_url)
    unique_urls = set(x_urls)
    semaphore = asyncio.Semaphore(500)
    async with aiohttp.ClientSession() as session:
        valid_urls = await check_urls(session, unique_urls, semaphore)
        channels = []
        for url in valid_urls:
            channels.extend(await fetch_json(session, url, semaphore))
        return channels

# 主函数
def main():
    parser = argparse.ArgumentParser(description='IPTV 频道批量探测与测速工具')
    parser.add_argument('--jsmpeg', help='指定jsmpeg-streamer模式的CSV文件')
    parser.add_argument('--txiptv', help='指定txiptv模式的CSV文件')
    parser.add_argument('--zhgxtv', help='指定zhgxtv模式的CSV文件')
    parser.add_argument('--output', default='itvlist', help='输出文件前缀（默认：itvlist）')
    args = parser.parse_args()

    all_channels = []

    if args.jsmpeg:
        all_channels.extend(get_channels_alltv(args.jsmpeg))

    if args.txiptv:
        loop = asyncio.get_event_loop()
        all_channels.extend(loop.run_until_complete(get_channels_newnew(args.txiptv)))

    # 目前未实现 zhgxtv 模式，可根据需求添加
    if args.zhgxtv:
        pass

    # 生成输出文件
    generate_output_files(all_channels, args.output)

if __name__ == "__main__":
    main()
