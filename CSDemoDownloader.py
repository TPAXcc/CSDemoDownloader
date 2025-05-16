import tkinter as tk
from tkinter import filedialog
import ctypes
import aioconsole
from bs4 import BeautifulSoup
import os
from urllib.parse import urlparse
import asyncio
import aiohttp
from tqdm.asyncio import tqdm_asyncio
import math
from pathlib import Path
from typing import List, Tuple, Dict
import aiofiles
import aiofiles
import bz2
import re
from datetime import datetime, timezone
import json
from  concurrent.futures import ThreadPoolExecutor
import threading
from tqdm import tqdm
import shutil

script_dir = os.path.dirname(os.path.abspath(__file__))



def select(_title="请选择文件", select_type="file", _multiple=False):
    """打开文件选择窗口，允许用户多选文件并返回列表"""
    ctypes.windll.shcore.SetProcessDpiAwareness(1)

    root = tk.Tk()
    # root.iconbitmap(os.path.join(script_dir, 'assets', 'explorer_icon.ico'))
    root.withdraw()  # 隐藏主窗口
    
    if select_type == "file":
        # 设置文件对话框参数
        paths = filedialog.askopenfilenames(
            title=_title,
            filetypes=[ ("CS2比赛记录", "*.html"), ("所有文件", "*.*")],  # 支持自定义过滤器
            multiple=_multiple
        )
    elif select_type == "folder":
        # 文件夹选择模式
        paths = filedialog.askdirectory(
            title=_title,
        ) 
    else:
        raise ValueError("无效的select_type参数，仅支持'file'或'directory'")
    
    root.destroy()  # 销毁窗口
    
    if _multiple == False:
        return paths  # 返回单个路径
    elif _multiple == True:
        return list(paths)  # 返回路径列表
    
def update_file_mtime(file_list=[], demo_data=[], target_folder=script_dir):
    processed_count = 0  # 初始化计数器
    # 构建文件名到时间的映射（去除.bz2后缀）
    time_map = {}
    
    print("Debug - demo_data entries:")
    for i, entry in enumerate(demo_data):
        print(f"Entry {i}: {entry.get('demo_name', '')} | available: {entry.get('demo_availability', False)}")
    
    for entry in demo_data:
        if not entry.get('demo_availability', False):
            continue
        demo_name = entry['demo_name']
        # 移除可能的.bz2后缀并规范化文件名
        clean_name = demo_name.replace('.bz2', '')  
        time_str = entry['time'].replace(' GMT', '')  # 去除GMT时区标识
        time_map[clean_name] = time_str
    
    print("\nDebug - time_map keys:")
    print(list(time_map.keys())[:10])  # 打印前10个键避免刷屏

    # 遍历文件列表并修改时间
    for file in file_list:
        try:
            # 提取文件名并与字典键匹配
            filename = os.path.basename(file)
            print(f"正在处理文件 {filename}")
            print()
            if filename not in time_map:
                print(f"找不到文件 {filename} 对应的时间记录")
                continue
            
            # 解析时间字符串为datetime对象（视为UTC时间）
            time_str = time_map[filename]
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            dt_utc = dt.replace(tzinfo=timezone.utc)  # 标记为UTC时间
            timestamp = dt_utc.timestamp()  # 转换为时间戳
            
            # 修改文件访问和修改时间
            os.utime(file, (timestamp, timestamp))
            print(f"成功更新文件 {filename} 的时间为 {time_str}")
            processed_count += 1  # 成功处理后增加计数
            
        except FileNotFoundError:
            print(f"文件 {file} 不存在")
        except Exception as e:
            print(f"处理文件 {file} 时发生错误: {e}")
    
    return processed_count  # 返回处理成功的文件数量
                   
class AsyncCSGOParser:
    def __init__(self, html_files: List[str]):
        self.html_files = html_files

    async def _parse_tbody(self, tbody) -> Dict[str, str]:
        """解析单个tbody元素"""
        trs = tbody.find_all('tr')
        if len(trs) < 2:  # 至少需要name和time两个字段
            return None

        try:
            # 提取基础字段
            name = trs[0].td.get_text(strip=True)
            time = trs[1].td.get_text(strip=True)

            # 初始化可选字段
            ranked = ""
            wait_time = ""
            match_duration = ""

            # 使用正则表达式提取字段
            patterns = {
                'ranked': r'Ranked:\s*(.*)',
                'waitTime': r'Wait Time:\s*(.*)',
                'matchDuration': r'Match Duration:\s*(.*)'
            }

            # 遍历剩余的行
            for tr in trs[2:]:
                text = tr.td.get_text(strip=True)
                for field, pattern in patterns.items():
                    match = re.match(pattern, text)
                    if match:
                        value = match.group(1).strip()
                        if field == 'ranked':
                            ranked = value
                        elif field == 'waitTime':
                            wait_time = value
                        elif field == 'matchDuration':
                            match_duration = value
                        break  # 匹配到后跳出当前循环

            # 提取演示文件信息
            demo_a = tbody.find("a", href=True)
            demo_url = demo_a["href"] if demo_a else ""
            demo_name = demo_url.split("/")[-1] if demo_url else ""
            
            demo_availability  = True if demo_url else False

            return {
                "name": name,
                "time": time,
                "ranked": ranked,
                "waitTime": wait_time,
                "matchDuration": match_duration,
                "demo_availability": demo_availability ,
                "demo_url": demo_url,
                "demo_name": demo_name
            }
        except (AttributeError, IndexError, KeyError):
            return None

    async def _process_file(self, file_path: str) -> List[Dict]:
        """处理单个HTML文件"""
        async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
            content = await f.read()
        
        soup = BeautifulSoup(content, "html.parser")
        results = []
        
        for tbody in soup.find_all("tbody"):
            if parsed := await self._parse_tbody(tbody):
                results.append(parsed)
        
        return results

    def _deduplicate(self, results: List[Dict]) -> List[Dict]:
        """去重所有字段完全相同的条目"""
        seen = set()
        deduped = []
        for item in results:
            # 将字典按key排序后序列化为字符串作为唯一标识
            identifier = json.dumps(item, sort_keys=True)
            if identifier not in seen:
                seen.add(identifier)
                deduped.append(item)
        return deduped

    async def run(self) -> List[Dict]:
        """异步处理所有文件并返回合并去重后的结果"""
        tasks = [self._process_file(file) for file in self.html_files]
        results = await asyncio.gather(*tasks)
        merged_results = [item for sublist in results for item in sublist]
        return self._deduplicate(merged_results)
    
class AsyncDownloader:
    def __init__(
        self,
        urls: List[str],
        download_dir: str = Path(),
        max_workers: int = 4,
        retries: int = 5,
        chunk_size: int = 1024 * 1024,  # 1MB
        max_chunks_per_task: int = 4    # 分片限制
    ):
        self.urls = urls
        self.max_workers = max_workers
        self.retries = retries
        self.chunk_size = chunk_size
        self.max_chunks = max_chunks_per_task
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.progress_bars = {}
        self.downloaded_count = 0  # 下载计数器
        self.files_count = 0  # 文件计数器
        self.filenames = []  # 所有文件名

    async def _get_file_info(self, session: aiohttp.ClientSession, url: str) -> Tuple[int, bool]:
        """获取文件大小和分片支持情况"""
        async with session.head(url) as response:
            if response.status != 200:
                raise ValueError(f"HEAD请求失败: {response.status}")
            
            supports_range = 'bytes' in response.headers.get('Accept-Ranges', '')
            total_size = int(response.headers.get('Content-Length', 0))
            return total_size, supports_range

    def _calculate_chunks(self, total_size: int) -> List[Tuple[int, int]]:
        """计算分片范围"""
        chunk_count = min(math.ceil(total_size / (self.chunk_size * 8)), 8)  # 文件大小除以8自动分片
        chunk_size = total_size // chunk_count
        chunks = []
        for i in range(chunk_count):
            start = i * chunk_size
            end = (i+1)*chunk_size - 1 if i < chunk_count-1 else total_size-1
            chunks.append((start, end))
        return chunks

    async def _download_chunk(
        self,
        session: aiohttp.ClientSession,
        url: str,
        filepath: Path,
        chunk_range: Tuple[int, int]
    ):
        """分片下载核心逻辑"""
        start, end = chunk_range
        headers = {"Range": f"bytes={start}-{end}"}
        progress_key = f"{url}_{start}-{end}"

        for attempt in range(self.retries):
            try:
                async with session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    
                    async with aiofiles.open(filepath, "r+b") as f:
                        await f.seek(start)
                        async for chunk in response.content.iter_chunked(self.chunk_size):
                            await f.write(chunk)
                            self.progress_bars[url].update(len(chunk))
                    return
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == self.retries - 1:
                    print(f"\n分片下载失败: {filepath.name} [{start}-{end}] ({str(e)})")
                await asyncio.sleep(2 ** attempt)

    async def _download_file(self, session: aiohttp.ClientSession, url: str):
        """文件下载主逻辑"""
        filename = url.split("/")[-1] or f"file_{hash(url)}"
        filepath = self.download_dir / filename
        
        
        # 获取服务器端文件信息
        total_size, supports_range = await self._get_file_info(session, url)
        if total_size == 0:
            raise ValueError("无法获取文件大小")

        # 检查本地文件是否存在并比较大小
        if filepath.exists():
            local_size = filepath.stat().st_size
            if local_size == total_size:
                print(f"文件 {filename} 已存在且大小相同，跳过下载。")
                self.files_count += 1
                self.filenames.append(filename)
                return  # 直接跳过下载
            else:
                # 用户选择是否覆盖（异步输入）
                choice = await aioconsole.ainput(
                    f"文件 {filename} 已存在但大小不同（本地: {local_size}，服务器: {total_size}） 是否覆盖？(y/n): "
                ).strip().lower()
                if choice != 'y':
                    print(f"跳过下载 {filename}")
                    self.filenames.append(filename)
                    return
                filepath.unlink(missing_ok=True)  # 删除旧文件
                
        try:
            total_size, supports_range = await self._get_file_info(session, url)
            if total_size == 0:
                raise ValueError("无法获取文件大小")

            # 初始化进度条
            self.progress_bars[url] = tqdm_asyncio(
                total=total_size,
                desc=f"下载 {filename}",
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                leave=False
            )

            # 预创建空文件
            if not filepath.exists():
                async with aiofiles.open(filepath, "wb") as f:
                    await f.truncate(total_size)

            # 分片下载逻辑
            if supports_range:
                chunks = self._calculate_chunks(total_size)
                semaphore = asyncio.Semaphore(self.max_chunks)  # 分片专用信号量
                
                tasks = []
                for chunk in chunks:
                    task = asyncio.create_task(self._download_chunk(session, url, filepath, chunk))
                    task.add_done_callback(lambda _: semaphore.release())
                    tasks.append(task)
                
                await asyncio.gather(*tasks)
            else:
                # 回退到整体下载
                async with session.get(url) as response:
                    async with aiofiles.open(filepath, "wb") as f:
                        async for chunk in response.content.iter_chunked(self.chunk_size):
                            await f.write(chunk)
                            self.progress_bars[url].update(len(chunk))
            self.downloaded_count += 1
            self.files_count += 1
            self.filenames.append(filename)
    
        finally:
            if url in self.progress_bars:
                self.progress_bars[url].close()
                del self.progress_bars[url]

    def files_name(self) -> List[str]:
        """返回所有服务器文件名列表"""
        return self.filenames
    
    def downloaded_num(self) -> int:
        """返回已成功下载的文件总数"""
        return self.downloaded_count
    
    def files_num(self) -> int:
        """返回文件总数"""
        return self.files_count

    async def run(self):
        """任务运行入口"""
        semaphore = asyncio.Semaphore(self.max_workers)
        
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False),
            timeout=aiohttp.ClientTimeout(total=3600)
        ) as session:
            tasks = []
            for url in self.urls:
                await semaphore.acquire()
                task = asyncio.create_task(self._download_file(session, url))
                task.add_done_callback(lambda _: semaphore.release())
                tasks.append(task)
            
            await tqdm_asyncio.gather(*tasks, desc=f"总进度",)

class AsyncBZ2Decompressor:
    def __init__(
        self,
        bz2_files_list: List[str],
        extract_dir: str,
        max_workers: int = os.cpu_count() * 2,
        chunk_size: int = 4*1024*1024  # 4MB
    ):
        self.bz2_files_list = [Path(f) for f in bz2_files_list]
        self.extract_dir = Path(extract_dir)
        self.max_workers = max_workers
        self.chunk_size = chunk_size
        self._extracted_files = []
        self._lock = threading.Lock()
        self.extract_dir.mkdir(parents=True, exist_ok=True)

    def _stream_extract(self, src_path: Path) -> bool:
        """同步解压单个BZ2文件"""
        dest_path = self.extract_dir / src_path.stem
        total_size = src_path.stat().st_size
        
        try:
            with (
                bz2.BZ2File(src_path, 'rb') as f_in,
                open(dest_path, 'wb') as f_out,
                tqdm(total=total_size, 
                    desc=f"解压 {src_path.name}",
                    unit='B',
                    unit_scale=True,
                    unit_divisor=1024,
                    ascii=True,
                    dynamic_ncols=True,
                    position=1  # 子进度条位置
                ) as pbar
            ):
                while True:
                    chunk = f_in.read(self.chunk_size)
                    if not chunk:
                        break
                    f_out.write(chunk)
                    pbar.update(len(chunk))

            with self._lock:
                self._extracted_files.append(str(dest_path))
                
            return True
        except Exception as e:
            print(f"解压失败 {src_path.name}: {str(e)}")
            return False

    def files_num(self) -> int:
        """已解压文件数量"""
        return len(self._extracted_files)

    def files_name(self) -> List[str]:
        """已解压文件路径列表"""
        return self._extracted_files
    
    async def run(self):  
        """异步入口：将同步任务提交到线程池"""
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [
                loop.run_in_executor(executor, self._stream_extract, src_path)
                for src_path in self.bz2_files_list
            ]

            with tqdm(total=len(futures), desc="总进度", unit="文件", position=0) as main_pbar:
                for future in asyncio.as_completed(futures):
                    await future  # 异步等待任务完成
                    main_pbar.update(1)
                    
        return all(future.result() for future in futures)
def main():
    # 选择文件
    print("请选择 CS2 比赛记录文件")
    selected_files = select("请选择 CS2 比赛记录文件", "file", True)
    if not selected_files:
        print("未选择CS2 比赛记录文件，程序退出。")
        
    print(f"已选择 {len(selected_files)} 个 CS2 比赛记录文件：")
    for file in selected_files:
        print("- ", file)
    
    # 解析文件
    print("开始解析 html 文件...")   
    demo = AsyncCSGOParser(
        html_files=selected_files
    )
    demo_data = asyncio.run(demo.run())
    print(f"已解析到 {len(demo_data)} 个 Demo：")
    selected_fields = ['name', 'time', 'matchDuration', 'ranked', 'demo_availability']
    # header = " | ".join(selected_fields)
    header = "   模式/地图  |     时间     | 比赛时长 | (排名) | 可以下载 Demo "
    print(header + "\n" + "-" * len(header)*2)    
    for match in demo_data:
        row = []
        for field in selected_fields:
            value = match.get(field, 'N/A')  # 若字段缺失，默认返回'N/A'
            # 可选：对特定字段自定义格式（如时长添加单位）
            # if field == 'matchDuration':
            #     value = f"时长: {value}"
            row.append(str(value))
        print(" | ".join(row))
    
    
    # 处理 demo
    print("请选择 Demo 下载文件夹") 
    traget_folder = select("请选择 Demo 下载文件夹", "folder")     
    print(f"已选择的 Demo 文件夹 {traget_folder} ")
    
    demo_links = []
    for match in demo_data:
        if match.get('demo_availability') is True:
            demo_links.append(match.get('demo_url'))
    print("开始下载 Demo 文件...")
    tmp_folder = traget_folder + "/tmp"
    downloader = AsyncDownloader(
        urls=demo_links,  # 下载链接列表
        download_dir=tmp_folder,  # 下载目录
        max_workers=4,  # 最大并发下载任务数
        max_chunks_per_task=4  # 单任务最大分片数
    )
    asyncio.run(downloader.run())
    print(f"\n已下载 {downloader.downloaded_num()} 个 Demo 文件, 当前存在 {downloader.files_num()} 个 Demo 文件:")
    for file in downloader.files_name():
        print("- ", file)

    print("开始解压 Demo 文件至目标文件夹...")
    decompress_files = [os.path.join(tmp_folder, name) for name in downloader.files_name()]
    decompressor = AsyncBZ2Decompressor(
        bz2_files_list=decompress_files,  # 下载的文件列表
        extract_dir=traget_folder  # 解压目录
    )
    asyncio.run(decompressor.run())
    print(f"已解压 {len(decompressor.files_name())} 个 Demo 文件至 {traget_folder} 文件夹：")
    for file in decompressor.files_name():
        print("- ", file)
    
    print("开始处理 Demo 文件时间...")
    files_num = update_file_mtime(decompressor.files_name(), demo_data)
    print(f"已处理 {files_num} 个  Demo 文件时间")
        
    shutil.rmtree(tmp_folder)  # 删除临时文件夹
    print(f"已删除 .bz2 缓存文件 {downloader.files_num()} 个")

if __name__ == "__main__":
    print("""欢迎使用 CS2 Demo 下载器
作者：TPAX_
版本：1.0.0
更新日期：2025-05-13
如有问题请联系作者：TPAX_
""")

    print("""请选择需要下载的比赛类型：
a. 官匹优先
b. 官匹竞技
c. 官匹其它类型
输入 a/b/c/ab/ac/bc/abc 选择需要下载的比赛类型""")
    match_group_select = input().strip().lower()
    
    dict_match_group = {
        "a": ["官匹优先"],
        "b": ["官匹竞技"],
        "c": ["官匹其它类型"],
        "ab": ["官匹优先", "官匹竞技"],
        "ac": ["官匹优先", "官匹其它类型"],
        "bc": ["官匹竞技", "官匹其它类型"],
        "abc": ["官匹优先", "官匹竞技", "官匹其它类型"]
    }
    print("\n已选择 "+"".join(dict_match_group.get(match_group_select, [])))
    dict_match_group_grade = {
        "a": "-官匹优先下载教程\n 3a.点击浏览器URL框，在后面添加 gcpd/730/?tab=matchhistorypremier\n 4a.右键页面，选择保存/另存为",
        "b": "-官匹竞技下载教程\n 3b.点击浏览器URL框，在后面添加 gcpd/730/?tab=matchhistorycompetitivepermap\n 4a.右键页面，选择保存/另存为",
        "c": "-官匹其它类型下载教程\n 3c.点击浏览器URL框，在后面添加 gcpd/730/\n 4a.打开你要下载的比赛类型（确保你能看到“下载回放”按钮），右键页面,目标保存/另存为",
    }
    
    print("1.使用浏览器（推荐 Edge）打开 Steam 官网 https://store.steampowered.com/login/ 并登录需要下载 Demo 的 Steam 账号") 
    print("2.点击上方的个人资料")
    def option_a():
        print(dict_match_group_grade.get("a"))

    def option_b():
        print(dict_match_group_grade.get("b"))

    def option_c():
        print(dict_match_group_grade.get("c"))
    actions = {
        'a': option_a,
        'b': option_b,
        'c': option_c
    }
    for choice in match_group_select:
        actions[choice]()
    print("5.选择所有保存的 CS2 比赛记录 (html 文件)")
    input("\n按回车键继续...")
    main()
    exit(1)